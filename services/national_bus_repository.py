"""Ordered public bus topology for regional legs with incomplete timetables."""

from collections import defaultdict
import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import cached_property
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from services.route_planner import _distance as distance_m


KST = ZoneInfo("Asia/Seoul")
BOARDING_RADIUS_M = 850
MAX_COMPARISON_DISTANCE_M = 25_000


@dataclass(frozen=True)
class BusStop:
    identifier: str
    name: str
    lat: float
    lon: float
    sequence: int

    @property
    def coordinates(self):
        return self.lat, self.lon

    def place(self):
        return {"name": self.name, "lat": self.lat, "lon": self.lon}


@dataclass(frozen=True)
class BusRoute:
    city: str
    identifier: str
    name: str
    source: str
    stops: tuple[BusStop, ...]


class NationalBusRepository:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir) / "normalized"

    def _rows(self, directory, filename):
        candidates = sorted((self.data_dir / directory).glob(filename))
        if candidates:
            with candidates[-1].open(encoding="utf-8-sig", newline="") as file:
                yield from csv.DictReader(file)

    @staticmethod
    def _cell(coordinates):
        return tuple(math.floor(value * 100) for value in coordinates)

    @cached_property
    def _topology(self):
        grouped = defaultdict(list)
        routes = []
        grid = defaultdict(list)
        files = (
            ("chuncheon", "chuncheon_tago_route_stop_sequences_*.csv", "route_number", "국토교통부 TAGO"),
            ("seoul", "seoul_route_stop_sequences_*.csv", "route_label", "서울 열린데이터광장"),
        )
        for city, filename, label_key, source in files:
            for row in self._rows("bus", filename):
                name = row[label_key].strip()
                direction = row.get("source_direction_code", "")
                identifier = row["source_route_id"]
                key = city, identifier, direction, name, source
                try:
                    stop = BusStop(row["source_stop_id"], row["stop_name"], float(row["latitude"]),
                                   float(row["longitude"]), int(row["stop_sequence"]))
                except ValueError:
                    grouped[key].append(None)
                    continue
                grouped[key].append(stop if 33 <= stop.lat <= 39 and 124 <= stop.lon <= 132 else None)
        for (city, identifier, _, name, source), rows in grouped.items():
            if not name or any(stop is None for stop in rows):
                continue
            stops = tuple(sorted(rows, key=lambda stop: stop.sequence))
            if len(stops) < 2 or any(b.sequence != a.sequence + 1 for a, b in zip(stops, stops[1:])):
                continue
            index = len(routes)
            routes.append(BusRoute(city, identifier, name, source, stops))
            for sequence, stop in enumerate(stops):
                grid[self._cell(stop.coordinates)].append((index, sequence))
        return routes, grid

    @cached_property
    def _calendar(self):
        times = defaultdict(list)
        holidays = {date.fromisoformat(row["date"])
                    for row in self._rows("geography", "kr_public_holidays_*.csv")}
        changes = {}
        for row in self._rows("bus", "chuncheon_announced_stop_changes_*.csv"):
            label, effective = row["route_label"], date.fromisoformat(row["effective_from"])
            changes[label] = min(changes.get(label, effective), effective)
        for row in self._rows("bus", "chuncheon_published_departures_*.csv"):
            if row["day_type"] not in {"weekday", "weekend"} or row["departure_role"] not in {"origin", "destination"}:
                continue
            key = row["route_label"], row["departure_stop_label"], row["day_type"]
            times[key].append((date.fromisoformat(row["effective_from"]), time.fromisoformat(row["local_departure_time"])))
        return times, holidays, changes

    def _near(self, coordinates):
        routes, grid = self._topology
        latitude, longitude = self._cell(coordinates)
        matches = defaultdict(list)
        for dy in range(-1, 2):
            for dx in range(-2, 3):
                for route_number, sequence in grid.get((latitude + dy, longitude + dx), ()):
                    distance = distance_m(coordinates, routes[route_number].stops[sequence].coordinates)
                    if distance <= BOARDING_RADIUS_M:
                        matches[route_number].append((sequence, distance))
        return matches

    def _published_departure(self, route, boarding, ready):
        schedules, holidays, _ = self._calendar
        if route.city != "chuncheon" or boarding != 0 or ready.date() in holidays:
            return None
        if ready.year not in {holiday.year for holiday in holidays}:
            return None
        key = route.name, route.stops[0].name, "weekday" if ready.weekday() < 5 else "weekend"
        times = (datetime.combine(ready.date(), clock, KST) for effective, clock in schedules.get(key, ())
                 if effective <= ready.date())
        return min((departure for departure in times if departure >= ready), default=None)

    @staticmethod
    def _walk(start, end, walking_time, start_at=None):
        minutes, _ = walking_time(start["lat"], start["lon"], start["name"], end["lat"], end["lon"], end["name"])
        if minutes < 0:
            minutes = None
        end_at = start_at + timedelta(minutes=minutes) if minutes is not None and start_at else None
        return {
            "mode": "walk", "route_name": "도보", "from_place": start, "to_place": end,
            "departure_at": start_at.isoformat() if start_at else None,
            "arrival_at": end_at.isoformat() if end_at else None,
            "duration_min": minutes, "fare_won": 0, "source": "TMAP 보행자 경로",
        }, end_at

    def plan(self, origin, destination, departure_at, walking_time):
        start = origin["lat"], origin["lon"]
        end = destination["lat"], destination["lon"]
        if distance_m(start, end) > MAX_COMPARISON_DISTANCE_M:
            return []
        now = departure_at.astimezone(KST)
        starts, ends = self._near(start), self._near(end)
        routes, _ = self._topology
        _, _, changes = self._calendar
        choices = []
        for index in starts.keys() & ends.keys():
            route = routes[index]
            if route.city == "chuncheon" and route.name in changes and now.date() >= changes[route.name]:
                continue
            options = [(access + egress + (alight - board) * 20, board, alight)
                       for board, access in starts[index] for alight, egress in ends[index]
                       if 0 < alight - board <= 60]
            if options:
                score, board, alight = min(options)
                path_stops = route.stops[board:alight + 1]
                if len({stop.identifier for stop in path_stops}) == len(path_stops):
                    choices.append((score, index, board, alight))
        journeys = []
        used = set()
        for _, index, board, alight in sorted(choices):
            route = routes[index]
            if (route.city, route.identifier) in used:
                continue
            used.add((route.city, route.identifier))
            path = [stop.place() for stop in route.stops[board:alight + 1]]
            access, ready = self._walk(origin, path[0], walking_time, now)
            egress, _ = self._walk(path[-1], destination, walking_time)
            bus_departure = self._published_departure(route, board, ready) if ready else None
            ride = {
                "mode": "city_bus", "route_name": route.name, "from_place": path[0], "to_place": path[-1],
                "departure_at": bus_departure.isoformat() if bus_departure else None,
                "arrival_at": None, "duration_min": None, "fare_won": None, "source": route.source, "path": path,
            }
            journeys.append({
                "status": "partial", "departure_at": now.isoformat(), "arrival_at": None, "total_min": None,
                "fare_won": None, "transfer_count": 0, "segments": [access, ride, egress],
                "warnings": ["공개 경유 정류장 순서로 확인한 버스 구간입니다. 탑승과 도착 시각은 운행 정보를 확인하세요."],
            })
            if len(journeys) == 2:
                break
        return journeys
