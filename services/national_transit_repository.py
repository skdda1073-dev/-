"""Search the published national railway schedules without inventing missing trips."""

from bisect import bisect_left
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
import gzip
import heapq
from itertools import groupby
from math import asin, ceil, cos, isfinite, radians, sin, sqrt
from operator import itemgetter
from pathlib import Path
import re
from threading import RLock
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
DAY = 24 * 60 * 60
WEEKDAYS = frozenset(range(5))
EVERY_DAY = frozenset(range(8))
REST_DAYS = frozenset((5, 6, 7))
MAX_SEARCH = 30 * 60 * 60
MAX_ACCESS_METERS = 6000
MAX_BUS_CONNECTION_METERS = 8000
MAX_BUS_CONNECTION_STATIONS = 8
MAX_BUS_CONNECTION_ARRIVALS = 3
MAX_TRANSFERS = 6
LOCAL_MODE = "subway"
TRAIN_MODE = "train"
BOARDING_SECONDS = {LOCAL_MODE: 60, TRAIN_MODE: 300}
CHUNCHEON_TRAIN_IDS = ("NAT140873", "NAT140840")
ITX_SEOUL_TRAIN_IDS = ("NAT130126", "NAT010032")  # 청량리, 용산


@dataclass(frozen=True, slots=True)
class _Station:
    name: str
    lat: float
    lon: float
    address: str
    line: str
    links: frozenset

    def place(self):
        return {"name": self.name, "lat": self.lat, "lon": self.lon}


@dataclass(frozen=True, slots=True)
class _StopTime:
    station: str
    arrival: int | None
    departure: int | None


@dataclass(frozen=True, slots=True)
class _Trip:
    mode: str
    route_name: str
    service_name: str
    source: str
    provider: str | None
    stops: tuple
    days: frozenset
    effective_from: date | None
    only_on: date | None = None
    fare: int | None = None
    notes: str = ""
    effective_until: date | None = None


@dataclass(frozen=True, slots=True)
class _Walk:
    start: dict
    end: dict
    seconds: int
    meters: int | None
    transfer: bool = False


@dataclass(frozen=True, slots=True)
class _Access:
    segments: tuple
    warnings: tuple


@dataclass(frozen=True, slots=True)
class _Ride:
    trip_index: int
    start: int
    end: int
    shift: int


@dataclass(frozen=True, slots=True)
class _Label:
    time: int
    station: str
    rides: int
    move: _Walk | _Access | _Ride
    previous: "_Label | None" = None
    walk_seconds: int = 0

    @property
    def preference(self):
        return self.rides, self.walk_seconds


def _rows(path):
    if not path.is_file():
        return
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as source:
        yield from csv.DictReader(source)


def _dated(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _seconds(value):
    if value:
        match = re.fullmatch(r"([0-4]?\d):([0-5]\d)(?::([0-5]\d))?", value.strip())
        if match:
            hour, minute, second = (int(v or 0) for v in match.groups())
            if hour < 48:
                return hour * 3600 + minute * 60 + second
    return None


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if not isinstance(value, bool) and isfinite(number) else None


def _coords(lat, lon):
    lat, lon = _number(lat), _number(lon)
    if lat is not None and lon is not None and 32.5 <= lat <= 39.5 and 123 <= lon <= 132.5:
        return lat, lon
    return None


def _station_name(value):
    return re.sub(r"\s+", "", value.split("(", 1)[0]).removesuffix("역").casefold()


def _station_place_key(place):
    return _station_name(place["name"]), place["lat"], place["lon"]


def _street(value):
    match = re.search(r"([가-힣\d]+(?:대로|로|길))\s*(?:지하\s*)?(\d+(?:-\d+)?)", value)
    return match.groups() if match else None


def _distance(lat1, lon1, lat2, lon2):
    delta_lat, delta_lon = radians(lat2 - lat1), radians(lon2 - lon1)
    chord = sin(delta_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(delta_lon / 2) ** 2
    return 12_742_000 * asin(sqrt(min(1, chord)))


def _station_distance(a, b):
    return _distance(a.lat, a.lon, b.lat, b.lon)


def _metro_days(value):
    value = value.strip()
    if value in {"매일", "평일+토요일+공휴일"}:
        return EVERY_DAY
    result = set()
    for part in re.split(r"[+,/]", value):
        part = part.strip()
        if part == "평일":
            result.update(WEEKDAYS)
        elif part in {"토", "토요일"}:
            result.add(5)
        elif part in {"일", "일요일"}:
            result.add(6)
        elif part == "공휴일":
            result.update((6, 7))
        elif part in {"휴일", "주말"}:
            result.update(REST_DAYS)
    return frozenset(result)


def _rail_days(note):
    # The published regular table gives weekday exceptions in the notes column.
    condition = note.partition("|")[0].strip()
    if condition in {"", "매일"} or condition.endswith("경유"):
        return EVERY_DAY
    weekdays = "월화수목금토일"
    if all(day in weekdays for day in condition):
        return frozenset(weekdays.index(day) for day in condition)
    return frozenset()


def _add_stop(stops, station, arrival, departure):
    if station and (arrival is not None or departure is not None):
        stops.append(_StopTime(station, arrival, departure))


def _trip_ordered(stops):
    last_time = -1
    for stop in stops:
        for clock in (stop.arrival, stop.departure):
            if clock is not None:
                if clock < last_time:
                    return False
                last_time = clock
    return True


def _load_stations(data):
    stations, metro, rail_by_name, rail_by_id = {}, {}, {}, {}
    by_name = defaultdict(list)
    for row in _rows(data / "subway/kric_stations_20260630.csv"):
        point = _coords(row["lat"], row["lon"])
        if not point or not row["station_id"] or not row["line_id"]:
            continue
        line, code = row["line_id"], row["station_id"]
        key = f"metro:{line}:{code}"
        if key in stations:
            continue
        name = row["station_name"]
        links = frozenset(re.findall(r"[A-Z]\d{2}[A-Z0-9]{2}", row["connecting_line_ids_raw"]))
        stations[key] = _Station(name, *point, row["street_address"], line, links)
        metro[line, code] = key
        by_name[_station_name(name)].append(key)

    crosswalk = {}
    for row in _rows(data / "subway/korail_gyeongchun_shared_stations_20260901.csv"):
        crosswalk[row["service_line_id"], _station_name(row["schedule_station_name"])] = metro.get(
            (row["reference_line_id"], row["reference_station_id"])
        )

    for row in _rows(data / "rail/rail_station_locations.csv"):
        name, address = row["station_name"], row["address"]
        alias = _station_name(name)
        point = _coords(row["latitude"], row["longitude"])
        street = _street(address)
        # Some source rail coordinates are outside their reported city. An exact
        # same-name, same-road-address subway record is stronger evidence.
        references = [stations[key] for key in by_name[alias]
                      if street and _street(stations[key].address) == street]
        if references:
            point = references[0].lat, references[0].lon
        if point and address.startswith("서울") and not (37.40 <= point[0] <= 37.72 and 126.74 <= point[1] <= 127.22):
            continue
        if point and address.startswith("부산") and not (34.90 <= point[0] <= 35.42 and 128.72 <= point[1] <= 129.32):
            continue
        if not point:
            continue
        key = f"rail:{row['source_record']}"
        stations[key] = _Station(name, *point, address, "rail", frozenset())
        if alias in rail_by_name:
            rail_by_name[alias] = None
        else:
            rail_by_name[alias] = key
        by_name[alias].append(key)

    for row in _rows(data / "rail/chuncheon_rail_facility_crosswalk.csv"):
        point = _coords(row["latitude"], row["longitude"])
        if not point:
            continue
        key = f"rail:{row['tago_train_station_id']}"
        name = row["facility_name"]
        stations[key] = _Station(name, *point, row["address"], "rail", frozenset())
        alias = _station_name(name)
        rail_by_id[row["tago_train_station_id"]] = key
        rail_by_name[alias] = key
        by_name[alias].append(key)

    for row in _rows(data / "rail/tago_train_station_ids.csv"):
        key = rail_by_name.get(_station_name(row["station_name"]))
        if key:
            rail_by_id.setdefault(row["station_id"], key)
    return stations, metro, rail_by_name, rail_by_id, by_name, crosswalk


def _load_metro_trips(data, metro, crosswalk):
    native_runs = (
        ("korail_gyeongchun_weekday_20260901.csv", "I41K2", "경춘선"),
        ("korail_gyeongchun_holiday_20260901.csv", "I41K2", "경춘선"),
        ("korail_daegyeong_20260901.csv", "I27K7", "대경선"),
        ("korail_donghae_20260901.csv", "I26K6", "동해선"),
    )
    source = "코레일 정규 운행표"
    for filename, line, name in native_runs:
        runs = defaultdict(list)
        for row in _rows(data / "subway" / filename):
            key = metro.get((line, row["station_id"])) or crosswalk.get((line, _station_name(row["station_name_raw"])))
            identity = (row["day_type"], row["direction"], row["train_no"], row["effective_from"],
                        row["source_sheet"], row["source_column"])
            arrival, departure = _seconds(row["arrival_service_time"]), _seconds(row["departure_service_time"])
            if row["clock_status"] == "ordered":
                _add_stop(runs[identity], key, arrival, departure)
        for (day_type, _, number, effective, _, _), stops in runs.items():
            days = _metro_days(day_type)
            if len(stops) > 1 and days and _trip_ordered(stops):
                yield _Trip(LOCAL_MODE, name, number, source, None, tuple(stops), days, _dated(effective))

    # The operator table follows the same train across both Line 8 operators.
    line8 = tuple(_ordered_metro_table(data / "subway/seoul_metro_line8_stop_times_20260901.csv.gz", metro,
                                      "서울교통공사 정규 운행표"))
    yield from line8
    line8_from = min((trip.effective_from for trip in line8 if trip.effective_from), default=None)
    replaced = {"S1108": line8_from, "S4108": line8_from}
    active_native = {line for filename, line, _ in native_runs if (data / "subway" / filename).is_file()}
    for filename in ("kric_stop_times_20260630.csv.gz", "kric_packed_stop_times_20260630.csv.gz"):
        yield from _ordered_metro_table(data / "subway" / filename, metro, "국가철도공단 도시·광역철도 운행표",
                                       active_native=active_native, replacement_dates=replaced)


def _ordered_metro_table(path, metro, source, *, active_native=(), replacement_dates=None):
    replacement_dates = replacement_dates or {}
    for _, rows in groupby(_rows(path), key=itemgetter("run_id")):
        representative, stops = None, []
        for row in rows:
            if row["line_id"] in active_native or row.get("timing_status", "ordered_clock_times") != "ordered_clock_times":
                continue
            representative = representative or row
            key = metro.get((row["line_id"], row["station_id"]))
            _add_stop(stops, key, _seconds(row["arrival_service_time"]), _seconds(row["departure_service_time"]))
        if representative and len(stops) > 1 and _trip_ordered(stops):
            days = _metro_days(representative["day_type"])
            if days:
                yield _Trip(LOCAL_MODE, representative["line_name"], representative["train_no"], source,
                            None, tuple(stops), days, _dated(representative["basis_date"]),
                            effective_until=replacement_dates.get(representative["line_id"]))


def _load_rail_trips(data, rail_by_name, rail_by_id):
    for filename in ("korail_ktx_stop_times_20261101.csv", "korail_passenger_stop_times_20261101.csv"):
        runs = defaultdict(list)
        for row in _rows(data / "rail" / filename):
            if row["time_order_status"] != "ordered":
                continue
            key = rail_by_name.get(_station_name(row["station_name"]))
            identity = (row["source_sheet"], row["train_number"], row["train_type"], row["direction"],
                        row["source_table_row"], row["source_table_column"], row["schedule_note"], row["runs_from_date"])
            clock = _seconds(row["scheduled_time"])
            _add_stop(runs[identity], key, clock, clock)
        for (line, number, train_type, _, _, _, note, effective), stops in runs.items():
            days = _rail_days(note)
            if len(stops) > 1 and days and _trip_ordered(stops):
                provider = "srt" if "SRT" in train_type.upper() else "korail"
                yield _Trip(TRAIN_MODE, line, f"{train_type} {number}", "코레일 정규 운행표", provider, tuple(stops),
                            days, _dated(effective), notes=note)

    for row in _rows(data / "rail/tago_rail_chuncheon_cheongnyangni_sample_20261124.csv"):
        start, end = rail_by_id.get(row["departure_station_id"]), rail_by_id.get(row["arrival_station_id"])
        service_date = _dated(row["service_date"])
        if not (start and end and service_date):
            continue
        service_midnight = datetime.combine(service_date, time.min, KST)
        depart = int((datetime.fromisoformat(row["planned_departure_at"]) - service_midnight).total_seconds())
        arrive = int((datetime.fromisoformat(row["planned_arrival_at"]) - service_midnight).total_seconds())
        if not 0 <= depart < arrive <= 2 * DAY:
            continue
        fare = _number(row["adult_charge_krw"])
        train_type = row["train_type"]
        provider = "srt" if "SRT" in train_type.upper() else "korail"
        yield _Trip(TRAIN_MODE, train_type, f"{train_type} {row['train_number']}", "TAGO 날짜 지정 열차 시간표",
                    provider, (_StopTime(start, None, depart), _StopTime(end, arrive, None)), EVERY_DAY,
                    service_date, service_date, int(fare) if fare is not None and fare >= 0 else None)


def _transfer_links(stations, by_name):
    links = defaultdict(list)
    for station_keys in by_name.values():
        for index, first_key in enumerate(station_keys):
            first = stations[first_key]
            for second_key in station_keys[index + 1:]:
                second = stations[second_key]
                separation = _station_distance(first, second)
                intercity_change = "rail" in (first.line, second.line)
                declared = (first.line in second.links or second.line in first.links
                            or first.line == second.line or intercity_change)
                if separation > 480 or not (declared or separation <= 120):
                    continue
                # The station cross-reference establishes a transfer, but
                # indoor traversal time is typically unpublished. Use an
                # explicit planning allowance, distinguishable in the UI.
                allowance = (8 if intercity_change else 5) * 60
                links[first_key].append((second_key, allowance))
                links[second_key].append((first_key, allowance))
    return dict(links)


class _Timetable:
    def __init__(self, data):
        self.stations, metro, rail_by_name, rail_by_id, by_name, crosswalk = _load_stations(data)
        self.rail_by_id = rail_by_id
        self.transfers = _transfer_links(self.stations, by_name)
        self.trips = tuple(_load_metro_trips(data, metro, crosswalk)) + tuple(
            _load_rail_trips(data, rail_by_name, rail_by_id)
        )
        self.holidays = frozenset(_dated(row["date"]) for row in _rows(
            data / "geography/kr_public_holidays_2026_2027.csv"
        ))
        connections = []
        routed = set()
        for index, trip in enumerate(self.trips):
            for position, (start, end) in enumerate(zip(trip.stops, trip.stops[1:])):
                departure = start.departure if start.departure is not None else start.arrival
                arrival = end.arrival if end.arrival is not None else end.departure
                if departure is not None and arrival is not None and departure < arrival and start.station != end.station:
                    connections.append((departure, index, position, arrival))
                    routed.update((start.station, end.station))
        self.connections = tuple(sorted(connections))
        self.routed = frozenset(routed)
        self.chuncheon_gateways = {}
        for station_id in CHUNCHEON_TRAIN_IDS:
            if not (key := rail_by_id.get(station_id)):
                continue
            hub = self.stations[key]
            keys = tuple(candidate for candidate in by_name[_station_name(hub.name)]
                         if candidate in self.routed and self.stations[candidate].place() == hub.place())
            if keys:
                self.chuncheon_gateways[hub.name] = (hub, keys)

    @lru_cache(maxsize=12)
    def _valid_trips(self, service_date):
        weekday = service_date.weekday()
        metro_category = 7 if service_date in self.holidays else weekday
        return tuple((weekday if trip.mode == TRAIN_MODE else metro_category) in trip.days
                     and (not trip.effective_from or trip.effective_from <= service_date)
                     and (not trip.effective_until or service_date < trip.effective_until)
                     and (not trip.only_on or trip.only_on == service_date) for trip in self.trips)

    def _day_connections(self, service_date, shift, start, end):
        valid = self._valid_trips(service_date)
        index = bisect_left(self.connections, (start - shift,))
        while index < len(self.connections):
            departure, trip_id, position, arrival = self.connections[index]
            if departure + shift > end:
                break
            if valid[trip_id]:
                yield departure + shift, trip_id, position, arrival + shift, shift
            index += 1

    def connections_from(self, service_date, start, end):
        first_day = max(-1, start // DAY - 1)
        last_day = end // DAY
        yield from heapq.merge(*(self._day_connections(service_date + timedelta(days=day), day * DAY, start, end)
                                 for day in range(first_day, last_day + 1)))


class _TimetableWithITX:
    """Merge confirmed departures for the queried service days into the rail graph."""

    def __init__(self, timetable, trips):
        self.base = timetable
        self.stations = timetable.stations
        self.transfers = timetable.transfers
        self.chuncheon_gateways = timetable.chuncheon_gateways
        self.routed = timetable.routed | {stop.station for trip in trips for stop in trip.stops}
        self.trips = (*timetable.trips, *trips)
        self.itx = tuple(enumerate(trips, start=len(timetable.trips)))

    def connections_from(self, service_date, start, end):
        dated = []
        for index, trip in self.itx:
            shift = (trip.only_on - service_date).days * DAY
            departure = trip.stops[0].departure + shift
            if start <= departure <= end:
                dated.append((departure, index, 0, trip.stops[1].arrival + shift, shift))
        yield from heapq.merge(self.base.connections_from(service_date, start, end), sorted(dated))


def _walking(walking_time, start, end):
    if (start["lat"], start["lon"]) == (end["lat"], end["lon"]):
        return _Walk(start, end, 0, 0)
    result = walking_time(start["lat"], start["lon"], start["name"], end["lat"], end["lon"], end["name"])
    if not isinstance(result, (tuple, list)) or len(result) != 2:
        return None
    minutes, meters = (_number(value) for value in result)
    if minutes is None or meters is None or minutes <= 0 or meters < 0:
        return None
    return _Walk(start, end, ceil(minutes * 60), ceil(meters))


def _access(timetable, place):
    nearest = []
    for key in timetable.routed:
        station = timetable.stations[key]
        meters = _distance(place["lat"], place["lon"], station.lat, station.lon)
        if meters <= MAX_ACCESS_METERS:
            nearest.append((meters, key))
    rail_count, metro_count = 0, 0
    by_location = {}
    for _, key in sorted(nearest):
        station = timetable.stations[key]
        location = (station.lat, station.lon)
        if location not in by_location:
            if station.line == "rail":
                if rail_count >= 3:
                    continue
                rail_count += 1
            else:
                if metro_count >= 4:
                    continue
                metro_count += 1
            by_location[location] = []
        by_location[location].append(key)
    return by_location.values()


def _walk_access(timetable, walking_time, origin, destination):
    initial_stations = _access(timetable, origin)
    final_stations = _access(timetable, destination) if destination is not None else ()
    if destination is not None and not final_stations:
        return {}, {}
    tasks = []
    for place, outbound, groups in ((origin, True, initial_stations), (destination, False, final_stations)):
        for keys in groups:
            station = timetable.stations[keys[0]].place()
            start, end = (place, station) if outbound else (station, place)
            tasks.append((keys, outbound, start, end))

    def lookup(task):
        keys, outbound, start, end = task
        return keys, outbound, _walking(walking_time, start, end)

    seeds, exits = {}, {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        records = list(executor.map(lookup, tasks))
    for keys, outbound, walk in records:
        if walk is None:
            continue
        for key in keys:
            stop = timetable.stations[key].place()
            if outbound:
                seeds[key] = _Walk(origin, stop, walk.seconds, walk.meters)
            else:
                exits[key] = _Walk(stop, destination, walk.seconds, walk.meters)
    return seeds, exits


def _offer(timetable, labels, first):
    pending = deque([first])
    while pending:
        new = pending.popleft()
        station_labels = labels[new.station]
        if any(old.time <= new.time and old.preference <= new.preference for old in station_labels):
            continue
        # A later passenger with fewer boardings or less walking may catch the same service.
        station_labels[:] = [old for old in station_labels if old.time < new.time or old.preference < new.preference]
        station_labels.append(new)
        source = timetable.stations[new.station]
        for key, allowance in timetable.transfers.get(new.station, ()):
            target = timetable.stations[key]
            walk = _Walk(source.place(), target.place(), allowance, None, True)
            pending.append(_Label(new.time + allowance, key, new.rides, walk, new, new.walk_seconds + allowance))


def _search(timetable, service_date, starting_at, seeds, exits, *, early_exit=True, latest_arrival=None):
    labels = defaultdict(list)
    for seed in seeds:
        _offer(timetable, labels, seed)
    boarded = {}
    best_exit = None
    search_until = starting_at + MAX_SEARCH
    if latest_arrival is not None:
        search_until = min(search_until, latest_arrival)
    for departure, trip_id, position, arrival, shift in timetable.connections_from(
            service_date, starting_at, search_until):
        if best_exit is not None and departure > best_exit + 45 * 60:
            break
        trip = timetable.trips[trip_id]
        start, end = trip.stops[position:position + 2]
        instance = (trip_id, shift)
        riding = boarded.get(instance)
        if riding is not None and riding[2] != position:
            riding = None
        if riding is None or riding[0].preference != (0, 0):
            boarding_deadline = departure - BOARDING_SECONDS[trip.mode]
            here = min((label for label in labels.get(start.station, ())
                        if label.rides <= MAX_TRANSFERS and label.time <= boarding_deadline
                        and (riding is None or label.preference < riding[0].preference)),
                       key=lambda label: (label.preference, label.time), default=None)
            if here is not None:
                riding = (here, position, position)
        if riding is None:
            continue
        previous, boarding_position, _ = riding
        boarded[instance] = (previous, boarding_position, position + 1)
        label = _Label(arrival, end.station, previous.rides + 1,
                       _Ride(trip_id, boarding_position, position + 1, shift), previous, previous.walk_seconds)
        _offer(timetable, labels, label)
        if early_exit and end.station in exits:
            end_at = arrival + exits[end.station].seconds
            best_exit = end_at if best_exit is None else min(best_exit, end_at)
    arrivals = ((label.time + exits[key].seconds, key, label, exits[key])
                for key, station_labels in labels.items() if key in exits
                for label in station_labels if label.rides
                and (latest_arrival is None or label.time + exits[key].seconds <= latest_arrival))
    return sorted(arrivals, key=lambda option: (option[0], option[2].rides, option[2].walk_seconds + option[3].seconds, option[1]))


def _iso(midnight, seconds):
    return (midnight + timedelta(seconds=seconds)).isoformat()


def _walk_segment(midnight, end_time, walk):
    return {
        "mode": "walk",
        "route_name": "역내 환승 도보 여유" if walk.transfer else "도보",
        "from_place": walk.start, "to_place": walk.end,
        "departure_at": _iso(midnight, end_time - walk.seconds),
        "arrival_at": _iso(midnight, end_time),
        "duration_min": ceil(walk.seconds / 60), "fare_won": 0,
        "distance_m": walk.meters, "estimated": walk.transfer,
        "source": "역내 환승 계획 여유시간" if walk.transfer else "TMAP 보행 경로",
    }


def _render(timetable, initial, end_time, label, egress):
    segments = []
    signatures = []
    warnings = []
    if egress.seconds:
        segments.append(_walk_segment(initial, end_time, egress))
    while label is not None:
        movement = label.move
        if isinstance(movement, _Walk):
            if movement.seconds:
                segments.append(_walk_segment(initial, label.time, movement))
        elif isinstance(movement, _Access):
            segments.extend(reversed(movement.segments))
            warnings.extend(movement.warnings)
        else:
            trip = timetable.trips[movement.trip_index]
            start, end = trip.stops[movement.start], trip.stops[movement.end]
            board = movement.shift + (start.departure if start.departure is not None else start.arrival)
            signature = (movement.trip_index, movement.shift, start.station, end.station)
            signatures.append(signature)
            segments.append({
                "mode": trip.mode, "route_name": trip.route_name, "service_name": trip.service_name,
                "from_place": timetable.stations[start.station].place(),
                "to_place": timetable.stations[end.station].place(),
                "departure_at": _iso(initial, board), "arrival_at": _iso(initial, label.time),
                "duration_min": ceil((label.time - board) / 60), "fare_won": trip.fare,
                "wait_min": ceil((board - label.previous.time) / 60),
                "source": trip.source, "booking_provider": trip.provider, "schedule_note": trip.notes,
                "service_date": (initial + timedelta(seconds=movement.shift)).date().isoformat(),
            })
        label = label.previous
    return tuple(reversed(signatures)), list(reversed(segments)), warnings


def _seoul_time(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.astimezone(KST) if value.tzinfo else value.replace(tzinfo=KST)


def _routing_place(place):
    point = _coords(place.get("lat"), place.get("lon"))
    return {**place, "lat": point[0], "lon": point[1]} if point else None


def _rail_seeds(timetable, walks_in, access_routes, departure_at, midnight, started):
    seeds = [_Label(started + walk.seconds, station, 0, walk, walk_seconds=walk.seconds)
             for station, walk in walks_in.items()]
    for route in access_routes:
        gateway, arrival = route["gateway"], route["arrival_at"]
        match = timetable.chuncheon_gateways.get(gateway["name"])
        if match is None or arrival < departure_at:
            continue
        hub, stations = match
        if hub.place() != gateway:
            continue
        arrived = ceil((arrival - midnight).total_seconds())
        access = _Access(tuple(route["segments"]), tuple(route.get("warnings", ())))
        boardings = sum(segment["mode"] in {"bus", "shuttle"} for segment in access.segments)
        walking = sum((_number(segment.get("duration_min")) or 0) * 60 for segment in access.segments if segment["mode"] == "walk")
        seeds.extend(_Label(arrived, station, boardings, access, walk_seconds=ceil(walking)) for station in stations)
    return seeds


def _journey(segments, warnings, departure_at, midnight, final_time):
    return {"segments": segments, "departure_at": departure_at.isoformat(), "warnings": warnings,
            "arrival_at": _iso(midnight, final_time), "source": "공식 철도·전철 운행표",
            "estimated": any(segment.get("estimated") for segment in segments)}


class NationalTransitRepository:
    """Connect published rail service to walking and time-checked station access."""

    def __init__(self, data_dir, *, itx_schedule=None):
        self._normalized_path = Path(data_dir) / "normalized"
        self._lock = RLock()
        self._cached = None
        self._itx_schedule = itx_schedule

    def _timetable(self):
        with self._lock:
            if self._cached is None:
                self._cached = _Timetable(self._normalized_path)
            return self._cached

    def chuncheon_gateways(self):
        timetable = self._timetable()
        return [hub.place() for hub, _ in timetable.chuncheon_gateways.values()]

    def transfer_stations(self, destination):
        """Consider the nearest stop on each line before adding nearby alternatives."""
        point = _coords(destination.get("lat"), destination.get("lon"))
        if point is None:
            return []
        timetable = self._timetable()
        groups = {}
        for key in sorted(timetable.routed):
            station = timetable.stations[key]
            metres = _distance(*point, station.lat, station.lon)
            if metres <= MAX_BUS_CONNECTION_METERS:
                place = station.place()
                group = groups.setdefault(_station_place_key(place), (metres, place, set()))
                group[2].add(station.line)

        first_on_line, remaining = [], []
        covered = set()
        for _, place, lines in sorted(groups.values(), key=lambda item: (item[0], _station_place_key(item[1]))):
            if lines - covered:
                first_on_line.append(place)
                covered.update(lines)
            else:
                remaining.append(place)
        return (first_on_line + remaining)[:MAX_BUS_CONNECTION_STATIONS]

    def _with_itx(self, timetable, origin, destination, departure_at):
        if self._itx_schedule is None:
            return timetable

        def near_chuncheon(place):
            return any(_distance(place["lat"], place["lon"], station.lat, station.lon) < 14000
                       for station, _ in timetable.chuncheon_gateways.values())

        start_local, end_local = near_chuncheon(origin), near_chuncheon(destination)
        if start_local == end_local:
            return timetable
        starts, ends = (CHUNCHEON_TRAIN_IDS, ITX_SEOUL_TRAIN_IDS) if start_local else (ITX_SEOUL_TRAIN_IDS, CHUNCHEON_TRAIN_IDS)
        dates = [departure_at.date()]
        if departure_at.hour >= 21:
            dates.append(dates[0] + timedelta(days=1))
        queries = [(start, end, day) for day in dates for start in starts for end in ends
                   if start in timetable.rail_by_id and end in timetable.rail_by_id]

        def rides(query):
            start, end, day = query
            midnight = datetime.combine(day, time.min, KST)
            result = []
            for row in self._itx_schedule(start, end, day):
                board = int((row["departure_at"] - midnight).total_seconds())
                alight = int((row["arrival_at"] - midnight).total_seconds())
                stops = (_StopTime(timetable.rail_by_id[start], None, board),
                         _StopTime(timetable.rail_by_id[end], alight, None))
                result.append(_Trip(TRAIN_MODE, "ITX-청춘", f"ITX-청춘 {row['train_number']}",
                                    "TAGO 날짜 지정 열차 시간표", "korail", stops, EVERY_DAY, day, day, row["fare_won"]))
            return result

        with ThreadPoolExecutor(max_workers=4) as executor:
            trips = [trip for itineraries in executor.map(rides, queries) for trip in itineraries]
        return _TimetableWithITX(timetable, trips) if trips else timetable

    def plan(self, origin, destination, departure_at, walking_time, *, access_routes=()):
        departure_at = _seoul_time(departure_at)
        origin, destination = _routing_place(origin), _routing_place(destination)
        if origin is None or destination is None:
            return []
        timetable = self._with_itx(self._timetable(), origin, destination, departure_at)
        walks_in, exits = _walk_access(timetable, walking_time, origin, destination)
        if not exits:
            return []

        midnight = datetime.combine(departure_at.date(), time.min, KST)
        started = ceil((departure_at - midnight).total_seconds())
        seeds = _rail_seeds(timetable, walks_in, access_routes, departure_at, midnight, started)
        if not seeds:
            return []

        connections = _search(timetable, departure_at.date(), started, seeds, exits)
        results, shortest_exit = [], {}
        for final_time, _, label, walk in connections:
            signature, segments, warnings = _render(timetable, midnight, final_time, label, walk)
            if not signature:
                continue
            # Later arrivals from the same train sequence only help when they save walking.
            boarding_sequence = (*signature[:-1], signature[-1][:-1])
            if boarding_sequence in shortest_exit and walk.seconds >= shortest_exit[boarding_sequence]:
                continue
            shortest_exit[boarding_sequence] = walk.seconds
            results.append(_journey(segments, warnings, departure_at, midnight, final_time))
            if len(results) == 3:
                break
        return results

    def plan_to_stations(self, origin, destination, departure_at, walking_time, stations, *, access_routes=(),
                         latest_arrival=None):
        """Return timed rail arrivals at all requested transfer points in one scan."""
        departure_at = _seoul_time(departure_at)
        limit = _seoul_time(latest_arrival) if latest_arrival is not None else None
        origin, destination = _routing_place(origin), _routing_place(destination)
        if origin is None or destination is None or (limit is not None and limit <= departure_at):
            return []
        places = {_station_place_key(place): place for place in stations}
        if not places:
            return []
        timetable = self._with_itx(self._timetable(), origin, destination, departure_at)
        targets = {}
        for station_key in timetable.routed:
            place = timetable.stations[station_key].place()
            if target := places.get(_station_place_key(place)):
                targets[station_key] = target
        if not targets:
            return []
        exits = {key: _Walk(place, place, 0, 0) for key, place in targets.items()}
        walks_in, _ = _walk_access(timetable, walking_time, origin, None)
        midnight = datetime.combine(departure_at.date(), time.min, KST)
        started = ceil((departure_at - midnight).total_seconds())
        seeds = _rail_seeds(timetable, walks_in, access_routes, departure_at, midnight, started)
        if not seeds:
            return []
        deadline = int((limit - midnight).total_seconds()) if limit is not None else None
        connections = _search(timetable, departure_at.date(), started, seeds, exits, early_exit=False,
                              latest_arrival=deadline)
        arrivals = defaultdict(list)
        results = []
        for final_time, key, label, walk in connections:
            target = targets[key]
            previous = arrivals[_station_place_key(target)]
            if len(previous) >= MAX_BUS_CONNECTION_ARRIVALS or (previous and previous[-1] <= label.preference):
                continue
            signature, segments, warnings = _render(timetable, midnight, final_time, label, walk)
            if signature:
                previous.append(label.preference)
                results.append((target, _journey(segments, warnings, departure_at, midnight, final_time)))
        return results
