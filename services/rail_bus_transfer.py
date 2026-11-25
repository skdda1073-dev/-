"""Find timed city buses for rail transfers and local journeys."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import cached_property, lru_cache
from math import floor
from pathlib import Path
from zoneinfo import ZoneInfo

from services.national_journey_service import _measured_walk, _segment, _time
from services.public_transit_client import PublicDataError, PublicTransitClient
from services.route_planner import BOARDING_MARGIN_MIN, _Leg, _arrival_for, _distance, _leg, _segments


KST = ZoneInfo("Asia/Seoul")
BOARD_RADIUS_M = 750
DESTINATION_RADIUS_M = 950
MAX_NEARBY_STOPS = 12
MAX_DISCOVERY_ROUTES = 16
MAX_LINKS_PER_STATION = 8
MAX_SEOUL_LINKS_PER_ROUTE = 8
LIVE_PREDICTION_WINDOW = timedelta(hours=2)
BOARDING_MARGIN = timedelta(minutes=BOARDING_MARGIN_MIN)
SEOUL_CITY_CODE = "11"
SEOUL_SOURCE = "서울특별시 버스 도착정보"


def _coordinates(place):
    return place["lat"], place["lon"]


def _cell(coordinates):
    return tuple(floor(value * 100) for value in coordinates)


def _rows(path):
    if path.is_file():
        with path.open(encoding="utf-8-sig", newline="") as stream:
            yield from csv.DictReader(stream)


def _parallel(items, function, workers=6):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(items, pool.map(function, items)))


def _gyeonggi_directions(rows, schedule):
    def stop_name(value):
        return "".join(str(value or "").split())

    if not rows or schedule is None or not schedule.start or not schedule.end:
        return []
    try:
        ordered = sorted(rows, key=lambda row: int(row["nodeord"]))
        orders = [int(row["nodeord"]) for row in ordered]
    except (KeyError, TypeError, ValueError):
        return []
    if orders != list(range(1, len(orders) + 1)) or stop_name(ordered[0].get("nodenm")) != stop_name(schedule.start):
        return []
    turns = [index for index, row in enumerate(ordered) if stop_name(row.get("nodenm")) == stop_name(schedule.end)]
    if len(turns) != 1 or turns[0] == 0:
        return []
    turn = turns[0]
    if any(str(row.get("updowncd")) in {"0", "1"} and str(row["updowncd"]) != str(int(index > turn))
           for index, row in enumerate(ordered) if index != turn):
        return []
    return ([{**row, "updowncd": "0"} for row in ordered[:turn + 1]]
            + ([{**row, "updowncd": "1"} for row in ordered[turn:]] if turn < len(ordered) - 1 else []))


@dataclass(frozen=True)
class _Frequency:
    first: time
    last: time
    minimum: int
    maximum: int
    observed: date


@dataclass(frozen=True)
class _Link:
    city_code: str
    leg: _Leg
    minutes_from_terminal: float


class RailBusTransfers:
    """Reuse TAGO stop and arrival data, with separately verified frequency estimates."""

    def __init__(self, data_dir, service_key, walking_time, *, now=None, gyeonggi_arrivals=None, walking_fallback=None,
                 seoul_arrivals=None, seoul_routes=None, gyeonggi_schedules=None):
        self.data = Path(data_dir) / "normalized"
        self.service_key = service_key
        self.walking_time = walking_time
        self.walking_fallback = walking_fallback
        self.now = now or (lambda: datetime.now(KST))
        self.gyeonggi_arrivals = gyeonggi_arrivals
        self.gyeonggi_schedules = gyeonggi_schedules
        self.seoul_arrivals = seoul_arrivals
        self.seoul_routes = seoul_routes

    @lru_cache(maxsize=16)
    def _client(self, city_code):
        return PublicTransitClient(self.service_key, city_code, timeout=(2, 4))

    @cached_property
    def _stop_grid(self):
        grid = defaultdict(list)
        for row in _rows(self.data / "bus/tago_stops_20261109.csv"):
            try:
                location = float(row["latitude"]), float(row["longitude"])
            except (KeyError, ValueError):
                continue
            if row["source_city_code"] and row["source_stop_id"] and 33 <= location[0] <= 39 and 124 <= location[1] <= 132:
                grid[_cell(location)].append((row["source_city_code"], row["source_stop_id"], location))
        return grid

    @cached_property
    def _official_sequences(self):
        routes = defaultdict(list)
        for row in _rows(self.data / "bus/gyeonggi_bus_transfer_sequences_20261125.csv"):
            key = row["source_city_code"], row["source_route_id"]
            routes[key].append({"nodeid": row["source_stop_id"], "nodenm": row["stop_name"],
                                "nodeord": row["stop_sequence"], "gpslati": row["latitude"], "gpslong": row["longitude"],
                                "updowncd": row["source_direction_code"], "routeno": row["route_label"]})
        return dict(routes)

    @cached_property
    def _frequencies(self):
        result = {}
        for row in _rows(self.data / "bus/gyeonggi_bus_transfer_frequencies_20261125.csv"):
            try:
                first, last = time.fromisoformat(row["first_departure"]), time.fromisoformat(row["last_departure"])
                minimum, maximum = int(row["headway_min"]), int(row["headway_max"])
                observed = date.fromisoformat(row["observed_on"])
            except (KeyError, ValueError):
                continue
            if 0 < minimum <= maximum <= 180 and first < last:
                key = row["source_city_code"], row["source_route_id"], row["direction"], row["day_type"]
                result[key] = _Frequency(first, last, minimum, maximum, observed)
        return result

    @cached_property
    def _holidays(self):
        return {date.fromisoformat(row["date"]) for row in _rows(self.data / "geography/kr_public_holidays_2026_2027.csv")}

    def _nearby_stops(self, place, radius, *, skip_seoul=False):
        target = _coordinates(place)
        y, x = _cell(target)
        distances = {}
        for dy in (-1, 0, 1):
            for dx in (-2, -1, 0, 1, 2):
                for city, node, coordinates in self._stop_grid.get((y + dy, x + dx), ()):
                    if skip_seoul and city == SEOUL_CITY_CODE:
                        continue
                    metres = _distance(target, coordinates)
                    if metres <= radius:
                        distances[city, node] = min(metres, distances.get((city, node), metres))
        for (city, _), rows in self._official_sequences.items():
            for row in rows:
                metres = _distance(target, (float(row["gpslati"]), float(row["gpslong"])))
                if metres <= radius:
                    distances[city, row["nodeid"]] = metres
        return sorted((metres, *stop) for stop, metres in distances.items())[:MAX_NEARBY_STOPS]

    def _serving(self, stop):
        city, node = stop
        try:
            return self._client(city).station_routes(node)
        except PublicDataError:
            return []

    def _pattern(self, key, numbers):
        if key in self._official_sequences:
            rows = self._official_sequences[key]
            return {"route_no": rows[0]["routeno"], "stops": rows}
        city, route = key
        try:
            client = self._client(city)
            rows = client.route_stops(route)
            if not rows:
                return None
            if any(str(row.get("updowncd")) not in {"0", "1"} for row in rows):
                schedule = self.gyeonggi_schedules.route(city, route) if self.gyeonggi_schedules is not None else None
                rows = _gyeonggi_directions(rows, schedule)
                if not rows or (numbers and schedule.name not in numbers):
                    return None
                return {"route_no": schedule.name, "stops": rows}
            number = next(iter(numbers)) if len(numbers) == 1 else str(client.route_info(route)["routeno"])
            return {"route_no": number, "stops": rows}
        except PublicDataError:
            return None

    def _live_requested(self, requested):
        return bool(self.service_key.strip()) and abs(requested - self.now()) <= LIVE_PREDICTION_WINDOW

    def _seoul_requested(self, requested):
        return (self.seoul_arrivals is not None and self.seoul_routes is not None
                and abs(requested - self.now()) <= LIVE_PREDICTION_WINDOW)

    def links(self, stations, destination, requested):
        """Start at stops near the destination and work backwards along bus routes."""
        return self._links(stations, destination, requested)

    def feeders(self, origin, stations, requested):
        """Return time-checked bus arrival journeys to the requested rail stations."""
        links = self._links(stations, origin, requested, toward_rail=True)
        rides = [(origin, station, station, requested, link) for station in stations
                 for link in links.get(_coordinates(station), ())]
        return [{**journey, "gateway": station, "arrival_at": journey["segments"][-1]["arrival_at"]}
                for station, journey in self._timed_journeys(rides, requested)]

    def plan_direct(self, origin, destination, requested):
        """Find a direct timed bus for a local origin and destination."""
        links = self.links([origin], destination, requested)
        return [journey for _, journey in self.connections(links, [(origin, {"arrival_at": requested})], destination, requested)]

    def _links(self, stations, point, requested, *, toward_rail=False):
        live_requested = self._live_requested(requested)
        seoul_requested = self._seoul_requested(requested)
        published_requested = (self.gyeonggi_schedules is not None and bool(self.service_key.strip())
                               and requested.astimezone(KST).date() >= self.now().astimezone(KST).date())
        if not stations or (not live_requested and not seoul_requested and not published_requested and not self._official_sequences):
            return {}
        endpoint_radius = BOARD_RADIUS_M if toward_rail else DESTINATION_RADIUS_M
        nearby = self._nearby_stops(point, endpoint_radius, skip_seoul=seoul_requested)
        by_city = defaultdict(set)
        for _, city, node in nearby:
            by_city[city].add(node)
        reviewed = [key for key, rows in self._official_sequences.items()
                    if any(row["nodeid"] in by_city[key[0]] for row in rows)]
        routes = {key: set() for key in reviewed}
        discovery = [(city, node) for _, city, node in nearby
                     if live_requested or (published_requested and self.gyeonggi_schedules.supports(city))]
        if discovery:
            serving = _parallel(discovery, self._serving)
            for city, node in discovery:
                for row in serving[(city, node)]:
                    if route := row.get("routeid"):
                        numbers = routes.setdefault((city, str(route)), set())
                        number = row.get("routeno")
                        if isinstance(number, (str, int)) and not isinstance(number, bool) and str(number).strip():
                            numbers.add(str(number).strip())
        keys = list(routes)[:MAX_DISCOVERY_ROUTES]
        regional_patterns = _parallel(keys, lambda key: self._pattern(key, routes[key]))
        patterns = [(city, route, info, by_city[city], None) for (city, route), info in regional_patterns.items()
                    if info is not None]
        if seoul_requested:
            for route, nearby_visits in self.seoul_routes(point, endpoint_radius):
                info = {"route_no": route.name, "sequence_basis": "api_order", "stops": [
                    {"nodeid": stop.identifier, "nodenm": stop.name, "nodeord": stop.sequence,
                     "gpslati": stop.lat, "gpslong": stop.lon} for stop in route.stops
                ]}
                orders = {route.stops[index].sequence for index, _ in nearby_visits}
                patterns.append((SEOUL_CITY_CODE, route.identifier, info, (), orders))
        options = defaultdict(list)
        point_coordinates = _coordinates(point)
        for city, route, info, anchor_ids, anchor_orders in patterns:
            for route_id, route_no, stops, counts, metadata in _segments({route: info}):
                anchor_indexes = [index for index, stop in enumerate(stops)
                                  if (stop.id in anchor_ids if anchor_orders is None else stop.order in anchor_orders)]
                if not anchor_indexes:
                    continue
                radius = DESTINATION_RADIUS_M if toward_rail else BOARD_RADIUS_M
                neighbours = self._station_neighbours(stations, stops if toward_rail else stops[:anchor_indexes[-1]], radius)
                starts, ends = (anchor_indexes, neighbours) if toward_rail else (neighbours, anchor_indexes)
                for start in starts:
                    prefix = (_leg(route_id, route_no, stops, counts, metadata, 0, start, None).ride_min
                              if start and city != SEOUL_CITY_CODE else 0)
                    for end in ends:
                        if not start < end <= start + 60 or stops[start].id == stops[end].id:
                            continue
                        leg = _leg(route_id, route_no, stops, counts, metadata, start, end, None)
                        nearby_stop = leg.board if toward_rail else leg.alight
                        endpoint_walk = _distance(point_coordinates, (nearby_stop.lat, nearby_stop.lon))
                        for station, metres in neighbours[end if toward_rail else start]:
                            options[station].append((leg.ride_min + (metres + endpoint_walk) / 72, _Link(city, leg, prefix)))
        selected = {}
        for station, candidates in options.items():
            by_route = defaultdict(int)
            regional, seoul = [], []
            seoul_route_ids = set()
            for _, link in sorted(candidates, key=lambda item: item[0]):
                route_key = (link.city_code, link.leg.route_id, link.leg.board.direction)
                if seoul_requested and link.city_code == SEOUL_CITY_CODE:
                    route_id = link.leg.route_id
                    if route_id not in seoul_route_ids and len(seoul_route_ids) >= MAX_DISCOVERY_ROUTES:
                        continue
                    if by_route[route_key] >= MAX_SEOUL_LINKS_PER_ROUTE:
                        continue
                    seoul.append(link)
                    seoul_route_ids.add(route_id)
                else:
                    if len(regional) >= MAX_LINKS_PER_STATION or by_route[route_key] >= 2:
                        continue
                    regional.append(link)
                by_route[route_key] += 1
            selected[station] = regional + seoul
        return selected

    @staticmethod
    def _station_neighbours(stations, stops, radius):
        station_points = [_coordinates(station) for station in stations]
        neighbours = {}
        for index, stop in enumerate(stops):
            matches = [(station, metres) for station in station_points
                       if (metres := _distance(station, (stop.lat, stop.lon))) <= radius]
            if matches:
                neighbours[index] = matches
        return neighbours

    def _frequency_departure(self, link, ready):
        day = ready.date()
        category = "holiday" if day in self._holidays else {5: "saturday", 6: "sunday"}.get(day.weekday(), "weekday")
        static_category = "sunday_or_holiday" if category in {"sunday", "holiday"} else category
        key = link.city_code, link.leg.route_id, link.leg.board.direction, static_category
        schedule = self._frequencies.get(key)
        if schedule is None and self.gyeonggi_schedules is not None:
            route = self.gyeonggi_schedules.route(link.city_code, link.leg.route_id)
            if route is not None:
                schedule = route.frequencies.get((link.leg.board.direction, category))
        if schedule is None or day < schedule.observed:
            return None
        first = datetime.combine(day, schedule.first, KST) + timedelta(minutes=link.minutes_from_terminal)
        average_wait = (schedule.minimum + schedule.maximum) / 4
        projected = first if ready <= first else ready + timedelta(minutes=average_wait)
        # Stay within the published operating window even when the exact last
        # departure at an intermediate stop is unavailable.
        return projected if projected <= datetime.combine(day, schedule.last, KST) else None

    def _snapshot(self, stop):
        city, node = stop
        captured = self.now()
        try:
            rows = self._client(city).arrivals(node)
        except PublicDataError:
            return None
        return captured, rows

    @staticmethod
    def _live_departure(link, at_stop, snapshot):
        if snapshot is None:
            return None
        captured, rows = snapshot
        ready_minutes = max(0, (at_stop - captured).total_seconds() / 60)
        eta = _arrival_for(link.leg, ready_minutes, rows)
        return captured + timedelta(minutes=eta) if eta is not None else None

    def _gyeonggi_snapshot(self, stop):
        captured = self.now()
        return [captured + timedelta(seconds=eta) for eta in self.gyeonggi_arrivals.predictions(*stop)]

    def connections(self, links, railway, destination, requested):
        """Link each arrival to a boardable bus and its verified last walk."""
        rides = []
        for station, route in railway:
            arrival = _time(route.get("arrival_at"))
            if arrival is not None:
                rides.extend((station, destination, route, arrival, link) for link in links.get(_coordinates(station), ()))
        return self._timed_journeys(rides, requested)

    def _walk(self, start, end):
        walk = _measured_walk(self.walking_time, start, end)
        if walk is None and self.walking_fallback is not None:
            return _measured_walk(self.walking_fallback, start, end)
        return walk

    def _timed_journeys(self, rides, requested):
        if not rides:
            return []
        now = self.now()
        live_requested = self._live_requested(requested)
        seoul_requested = self._seoul_requested(requested)
        live_stops = dict.fromkeys((link.city_code, link.leg.board.id) for _, _, _, arrival, link in rides
                                   if live_requested and (not seoul_requested or link.city_code != SEOUL_CITY_CODE)
                                   and arrival <= now + LIVE_PREDICTION_WINDOW)
        gyeonggi_stops = {}
        if self.gyeonggi_arrivals is not None and abs(now - requested) <= LIVE_PREDICTION_WINDOW:
            gyeonggi_stops = dict.fromkeys((link.leg.board.id, link.leg.route_id, link.leg.board.order)
                                          for _, _, _, arrival, link in rides
                                          if arrival <= now + LIVE_PREDICTION_WINDOW
                                          and ((link.city_code, link.leg.route_id) in self._official_sequences
                                               or (self.gyeonggi_schedules is not None
                                                   and self.gyeonggi_schedules.supports(link.city_code))))
        seoul_routes = dict.fromkeys(link.leg.route_id for _, _, _, arrival, link in rides
                                   if seoul_requested and link.city_code == SEOUL_CITY_CODE
                                   and arrival <= now + LIVE_PREDICTION_WINDOW)
        boarding_stops = ([("tago", stop) for stop in live_stops] + [("gbis", stop) for stop in gyeonggi_stops]
                          + [("seoul", route) for route in list(seoul_routes)[:MAX_DISCOVERY_ROUTES]])
        readers = {"tago": self._snapshot, "gbis": self._gyeonggi_snapshot}
        if seoul_requested:
            readers["seoul"] = self.seoul_arrivals.snapshot
        snapshots = _parallel(boarding_stops, lambda key: readers[key[0]](key[1]))

        def live_trip(link, ready):
            leg = link.leg
            if seoul_requested and link.city_code == SEOUL_CITY_CODE:
                snapshot = snapshots.get(("seoul", leg.route_id))
                times = (self.seoul_arrivals.trip_times(snapshot, leg.board.id, leg.board.order,
                                                       leg.alight.id, leg.alight.order, max(ready, self.now()) + BOARDING_MARGIN)
                         if snapshot is not None else None)
                return (*times, SEOUL_SOURCE) if times is not None else (None, None, "")
            eta = min((at for at in snapshots.get(("gbis", (leg.board.id, leg.route_id, leg.board.order)), ())
                       if at >= ready + BOARDING_MARGIN), default=None)
            if eta is not None:
                return eta, None, "경기버스정보"
            return self._live_departure(link, ready, snapshots.get(("tago", (link.city_code, leg.board.id)))), None, "TAGO"

        walks = {}
        feasible = []
        for origin, destination, route, arrival, link in rides:
            if (live_trip(link, arrival)[0] is None
                    and self._frequency_departure(link, arrival + BOARDING_MARGIN) is None):
                continue
            board, alight = link.leg.board.public(), link.leg.alight.public()
            walks[(_coordinates(origin), _coordinates(board))] = (origin, board)
            walks[(_coordinates(alight), _coordinates(destination))] = (alight, destination)
            feasible.append((origin, destination, route, arrival, link))
        walked = _parallel(list(walks), lambda key: self._walk(*walks[key]))
        best_connections = {}
        for origin, destination, route, arrival, link in feasible:
            leg = link.leg
            board, alight = leg.board.public(), leg.alight.public()
            walk_in = walked.get((_coordinates(origin), _coordinates(board)))
            walk_out = walked.get((_coordinates(alight), _coordinates(destination)))
            if walk_in is None or walk_out is None or walk_in[1] > 1100 or walk_out[1] > 1400:
                continue
            at_stop = arrival + timedelta(minutes=walk_in[0])
            departure, predicted_alighting, source = live_trip(link, at_stop)
            realtime = departure is not None
            if departure is None:
                departure = self._frequency_departure(link, at_stop + BOARDING_MARGIN)
                source = "경기버스정보 공개 배차 간격"
            if departure is None or departure < at_stop + BOARDING_MARGIN:
                continue
            alighted = predicted_alighting or departure + timedelta(minutes=leg.ride_min)
            reached = alighted + timedelta(minutes=walk_out[0])
            if predicted_alighting is not None:
                basis = "realtime_stop_predictions"
                warning = "서울특별시 버스 도착정보에서 같은 차량의 승차·하차 정류장 도착예측을 반영했어요."
            elif realtime:
                basis = leg.ride_time_basis
                warning = "시내버스 승차에는 실시간 도착 예측을 사용했고 하차 시각은 구간 거리로 추정했어요."
            else:
                basis = "published_headway_estimate"
                warning = "시내버스 대기는 공개 배차 간격의 평균 절반으로, 탑승 시간은 구간 거리로 추정했어요."
            segments = [_segment("walk", "버스 정류장까지", origin, board, arrival, at_stop, walk_in[0],
                                 distance_m=walk_in[1])]
            if not realtime:
                segments.append(_segment("wait", "예상 승차 대기", board, board, at_stop, departure,
                                         (departure - at_stop).total_seconds() / 60, estimated=True, ride_time_basis=basis))
            segments.extend([
                _segment("bus", leg.route_no, board, alight, departure, alighted, (alighted - departure).total_seconds() / 60,
                         source=source, estimated=True, realtime=realtime, ride_time_basis=basis,
                         path=[stop.public() for stop in leg.stops]),
                _segment("walk", "하차 후 도보", alight, destination, alighted, reached, walk_out[0], distance_m=walk_out[1]),
            ])
            key = (id(route), link.city_code, leg.route_id, leg.board.direction)
            rank = (reached, walk_in[0] + walk_out[0])
            if key not in best_connections or rank < best_connections[key][0]:
                best_connections[key] = (rank, route, {"estimated": True, "warnings": [warning], "segments": segments})
        return [(route, onward) for _, route, onward in best_connections.values()]
