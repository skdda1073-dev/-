"""Find city buses from rail stations to the final destination."""

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
MAX_DESTINATION_STOPS = 12
MAX_DISCOVERY_ROUTES = 16
MAX_LINKS_PER_STATION = 8
LIVE_PREDICTION_WINDOW = timedelta(hours=2)
BOARDING_MARGIN = timedelta(minutes=BOARDING_MARGIN_MIN)


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

    def __init__(self, data_dir, service_key, walking_time, *, now=None, gyeonggi_arrivals=None):
        self.data = Path(data_dir) / "normalized"
        self.service_key = service_key
        self.walking_time = walking_time
        self.now = now or (lambda: datetime.now(KST))
        self.gyeonggi_arrivals = gyeonggi_arrivals

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

    def _destination_stops(self, destination):
        target = _coordinates(destination)
        y, x = _cell(target)
        distances = {}
        for dy in (-1, 0, 1):
            for dx in (-2, -1, 0, 1, 2):
                for city, node, coordinates in self._stop_grid.get((y + dy, x + dx), ()):
                    metres = _distance(target, coordinates)
                    if metres <= DESTINATION_RADIUS_M:
                        distances[city, node] = min(metres, distances.get((city, node), metres))
        for (city, _), rows in self._official_sequences.items():
            for row in rows:
                metres = _distance(target, (float(row["gpslati"]), float(row["gpslong"])))
                if metres <= DESTINATION_RADIUS_M:
                    distances[city, row["nodeid"]] = metres
        return sorted((metres, *stop) for stop, metres in distances.items())[:MAX_DESTINATION_STOPS]

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
            if not rows or any(str(row.get("updowncd")) not in {"0", "1"} for row in rows):
                return None
            number = next(iter(numbers)) if len(numbers) == 1 else str(client.route_info(route)["routeno"])
            return {"route_no": number, "stops": rows}
        except PublicDataError:
            return None

    def _live_requested(self, requested):
        return bool(self.service_key.strip()) and abs(requested - self.now()) <= LIVE_PREDICTION_WINDOW

    def links(self, stations, destination, requested):
        """Start at stops near the destination and work backwards along bus routes."""
        live_requested = self._live_requested(requested)
        if not stations or (not live_requested and not self._official_sequences):
            return {}
        nearby = self._destination_stops(destination)
        if not nearby:
            return {}
        goals = {node for _, _, node in nearby}
        reviewed = [key for key, rows in self._official_sequences.items() if any(row["nodeid"] in goals for row in rows)]
        routes = {key: set() for key in reviewed}
        if live_requested:
            serving = _parallel([(city, node) for _, city, node in nearby], self._serving)
            for _, city, node in nearby:
                for row in serving[(city, node)]:
                    if route := row.get("routeid"):
                        numbers = routes.setdefault((city, str(route)), set())
                        number = row.get("routeno")
                        if isinstance(number, (str, int)) and not isinstance(number, bool) and str(number).strip():
                            numbers.add(str(number).strip())
        keys = list(routes)[:MAX_DISCOVERY_ROUTES]
        patterns = _parallel(keys, lambda key: self._pattern(key, routes[key]))
        options = defaultdict(list)
        for (city, route), info in patterns.items():
            if info is None:
                continue
            for route_id, route_no, stops, counts, metadata in _segments({route: info}):
                end_indexes = [index for index, stop in enumerate(stops) if stop.id in goals]
                if not end_indexes:
                    continue
                for start, board in enumerate(stops[:max(end_indexes)]):
                    board_options = [(station, _distance(_coordinates(station), (board.lat, board.lon)))
                                     for station in stations]
                    board_options = [(station, metres) for station, metres in board_options if metres <= BOARD_RADIUS_M]
                    if not board_options:
                        continue
                    prefix = _leg(route_id, route_no, stops, counts, metadata, 0, start, None).ride_min if start else 0
                    for end in end_indexes:
                        if not start < end <= start + 60 or board.id == stops[end].id:
                            continue
                        leg = _leg(route_id, route_no, stops, counts, metadata, start, end, None)
                        last_walk = _distance(_coordinates(destination), (leg.alight.lat, leg.alight.lon))
                        for station, metres in board_options:
                            key = _coordinates(station)
                            options[key].append((leg.ride_min + (metres + last_walk) / 72, _Link(city, leg, prefix)))
        selected = {}
        for station, candidates in options.items():
            by_route = defaultdict(int)
            chosen = []
            for _, link in sorted(candidates, key=lambda item: item[0]):
                route_key = (link.city_code, link.leg.route_id, link.leg.board.direction)
                if by_route[route_key] >= 2:
                    continue
                chosen.append(link)
                by_route[route_key] += 1
                if len(chosen) == MAX_LINKS_PER_STATION:
                    break
            selected[station] = chosen
        return selected

    def _frequency_departure(self, link, ready):
        day = ready.date()
        category = "sunday_or_holiday" if day in self._holidays or day.weekday() == 6 else (
            "saturday" if day.weekday() == 5 else "weekday")
        key = link.city_code, link.leg.route_id, link.leg.board.direction, category
        schedule = self._frequencies.get(key)
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
                rides.extend((station, route, arrival, link) for link in links.get(_coordinates(station), ()))
        if not rides:
            return []
        now = self.now()
        live_requested = self._live_requested(requested)
        live_stops = dict.fromkeys((link.city_code, link.leg.board.id) for _, _, arrival, link in rides
                                   if live_requested and arrival <= now + LIVE_PREDICTION_WINDOW)
        gyeonggi_stops = {}
        if self.gyeonggi_arrivals is not None and abs(now - requested) <= LIVE_PREDICTION_WINDOW:
            gyeonggi_stops = dict.fromkeys((link.leg.board.id, link.leg.route_id, link.leg.board.order)
                                          for _, _, arrival, link in rides
                                          if arrival <= now + LIVE_PREDICTION_WINDOW
                                          and (link.city_code, link.leg.route_id) in self._official_sequences)
        boarding_stops = [("tago", stop) for stop in live_stops] + [("gbis", stop) for stop in gyeonggi_stops]
        readers = {"tago": self._snapshot, "gbis": self._gyeonggi_snapshot}
        snapshots = _parallel(boarding_stops, lambda key: readers[key[0]](key[1]))

        def live_departure(link, ready):
            leg = link.leg
            eta = min((at for at in snapshots.get(("gbis", (leg.board.id, leg.route_id, leg.board.order)), ())
                       if at >= ready + BOARDING_MARGIN), default=None)
            if eta is not None:
                return eta, "경기버스정보"
            return self._live_departure(link, ready, snapshots.get(("tago", (link.city_code, leg.board.id)))), "TAGO"

        walks = {}
        feasible = []
        for station, route, arrival, link in rides:
            if (live_departure(link, arrival)[0] is None
                    and self._frequency_departure(link, arrival + BOARDING_MARGIN) is None):
                continue
            board, alight = link.leg.board.public(), link.leg.alight.public()
            walks[(_coordinates(station), _coordinates(board))] = (station, board)
            walks[(_coordinates(alight), _coordinates(destination))] = (alight, destination)
            feasible.append((station, route, arrival, link))
        walked = _parallel(list(walks), lambda key: _measured_walk(self.walking_time, *walks[key]))
        best_connections = {}
        for station, route, arrival, link in feasible:
            leg = link.leg
            board, alight = leg.board.public(), leg.alight.public()
            walk_in = walked.get((_coordinates(station), _coordinates(board)))
            walk_out = walked.get((_coordinates(alight), _coordinates(destination)))
            if walk_in is None or walk_out is None or walk_in[1] > 1100 or walk_out[1] > 1400:
                continue
            at_stop = arrival + timedelta(minutes=walk_in[0])
            departure, source = live_departure(link, at_stop)
            realtime = departure is not None
            if departure is None:
                departure = self._frequency_departure(link, at_stop + BOARDING_MARGIN)
                source = "경기버스정보 공개 배차 간격"
            if departure is None or departure < at_stop + BOARDING_MARGIN:
                continue
            alighted = departure + timedelta(minutes=leg.ride_min)
            reached = alighted + timedelta(minutes=walk_out[0])
            basis = leg.ride_time_basis if realtime else "published_headway_estimate"
            warning = ("시내버스 승차에는 실시간 도착 예측을 사용했고 하차 시각은 구간 거리로 추정했어요." if realtime else
                       "시내버스 대기는 공개 배차 간격의 평균 절반으로, 탑승 시간은 구간 거리로 추정했어요.")
            segments = [_segment("walk", "버스 정류장까지", station, board, arrival, at_stop, walk_in[0],
                                 distance_m=walk_in[1])]
            if not realtime:
                segments.append(_segment("wait", "예상 승차 대기", board, board, at_stop, departure,
                                         (departure - at_stop).total_seconds() / 60, estimated=True, ride_time_basis=basis))
            segments.extend([
                _segment("bus", leg.route_no, board, alight, departure, alighted, leg.ride_min,
                         source=source, estimated=True, realtime=realtime, ride_time_basis=basis,
                         path=[stop.public() for stop in leg.stops]),
                _segment("walk", "하차 후 도보", alight, destination, alighted, reached, walk_out[0], distance_m=walk_out[1]),
            ])
            key = (id(route), link.city_code, leg.route_id, leg.board.direction)
            rank = (reached, walk_in[0] + walk_out[0])
            if key not in best_connections or rank < best_connections[key][0]:
                best_connections[key] = (rank, route, {"estimated": True, "warnings": [warning], "segments": segments})
        return [(route, onward) for _, route, onward in best_connections.values()]
