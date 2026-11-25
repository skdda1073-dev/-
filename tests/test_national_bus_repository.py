import csv
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from services.national_bus_repository import KST, NationalBusRepository


class NationalBusRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.write(
            "bus/chuncheon_tago_route_stop_sequences_20261124.csv",
            ("source_route_id", "route_number", "source_stop_id", "stop_name", "stop_sequence",
             "source_direction_code", "latitude", "longitude"),
            [("route-2", "2", "board", "차고지", 1, 0, 37.870, 127.720),
             ("route-2", "2", "alight", "터미널", 2, 0, 37.880, 127.730)],
        )
        self.write(
            "bus/chuncheon_published_departures_20260930.csv",
            ("route_label", "day_type", "departure_role", "departure_stop_label",
             "effective_from", "local_departure_time"),
            [("2", "weekday", "origin", "차고지", "2026-09-30", "07:02"),
             ("2", "weekday", "origin", "차고지", "2026-09-30", "07:08"),
             ("2", "source_unspecified", "origin", "차고지", "2026-09-30", "07:04")],
        )
        self.write("geography/kr_public_holidays_2026_2027.csv", ("date",), [("2026-12-25",)])
        self.repository = NationalBusRepository(self.root)
        self.start = {"name": "교내 건물", "lat": 37.869, "lon": 127.720}
        self.end = {"name": "목적지", "lat": 37.881, "lon": 127.730}
        self.walk = lambda *args: (3, 270)

    def write(self, name, header, rows):
        path = self.root / "normalized" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(header)
            writer.writerows(rows)

    def test_does_not_invent_a_ride_time_and_uses_next_published_departure(self):
        departure_at = datetime(2026, 11, 24, 7, 0, tzinfo=KST)
        journey, = self.repository.plan(self.start, self.end, departure_at, self.walk)
        walking, bus, egress = journey["segments"]
        self.assertEqual(walking["from_place"], self.start)
        self.assertEqual(walking["duration_min"], 3)
        self.assertTrue(bus["departure_at"].endswith("07:08:00+09:00"))
        self.assertIsNone(bus["arrival_at"])
        self.assertIsNone(journey["total_min"])
        self.assertEqual(egress["to_place"], self.end)
        self.assertEqual(journey["status"], "partial")

    def test_holiday_and_reverse_direction_do_not_inherit_weekday_departure(self):
        departure_at = datetime(2026, 12, 25, 7, 0, tzinfo=KST)
        journey, = self.repository.plan(self.start, self.end, departure_at, self.walk)
        self.assertIsNone(journey["segments"][1]["departure_at"])
        self.assertEqual(self.repository.plan(self.end, self.start, departure_at, self.walk), [])


class PublishedChuncheonBusJourneyTests(unittest.TestCase):
    def setUp(self):
        self.repository = NationalBusRepository(Path(__file__).resolve().parents[1] / "data")
        self.start = {"name": "출발", "lat": 37.88313, "lon": 127.73734}
        self.end = {"name": "도착", "lat": 37.88152, "lon": 127.74856}
        self.walk = lambda *args: (3, 270)

    def _plan(self, departure, *, use_timing=True):
        return self.repository.plan(self.start, self.end, departure, self.walk, use_published_timing=use_timing)

    def test_published_ride_includes_walking_wait_and_only_boardable_runs(self):
        departure = datetime(2026, 11, 28, 7, 12, tzinfo=KST)
        first = self._plan(departure)[0]
        self.assertEqual(first["status"], "ok")
        walk, bus, egress = first["segments"]
        self.assertEqual(walk["arrival_at"], "2026-11-28T07:15:00+09:00")
        self.assertEqual(bus["departure_at"], "2026-11-28T07:16:00+09:00")
        self.assertEqual(bus["arrival_at"], "2026-11-28T07:21:00+09:00")
        self.assertEqual(bus["duration_min"], 5)
        self.assertTrue(bus["estimated"])
        self.assertFalse(bus["realtime"])
        self.assertEqual(egress["departure_at"], bus["arrival_at"])
        self.assertEqual(first["arrival_at"], "2026-11-28T07:24:00+09:00")
        self.assertEqual(first["total_min"], 12)

        later = self._plan(departure.replace(second=1))[0]
        self.assertEqual(later["segments"][1]["departure_at"], "2026-11-28T07:46:00+09:00")
        self.assertEqual(later["total_min"], 41.98)

    def test_no_projected_departure_after_last_run_outside_date_or_in_reverse(self):
        departure = datetime(2026, 11, 28, 22, 12, tzinfo=KST)
        self.assertEqual(self._plan(departure)[0]["segments"][1]["departure_at"], "2026-11-28T22:16:00+09:00")
        missed_last = self._plan(departure.replace(second=1))
        for reference in missed_last:
            ride = reference["segments"][1]
            self.assertNotEqual((ride["route_name"], ride["from_place"]["lat"], ride["to_place"]["lat"]),
                                ("300", 37.88310, 37.88150))
        for results in (
                missed_last,
                self._plan(departure.replace(month=12, day=1, hour=7)),
                self._plan(departure, use_timing=False),
                self.repository.plan(self.end, self.start, departure, self.walk, use_published_timing=True)):
            self.assertTrue(all(item["status"] == "partial" for item in results))


class SeoulBusTopologyTests(unittest.TestCase):
    def test_nearby_route_preserves_stop_id_and_order_for_live_api_matching(self):
        repository = NationalBusRepository(Path(__file__).resolve().parents[1] / "data")
        origin = {"lat": 37.513305, "lon": 127.100133}
        destination = {"lat": 37.5339323, "lon": 127.09476136}

        starts = repository.nearby_seoul_routes(origin, 100)
        ends = repository.nearby_seoul_routes(destination, 650)
        route, board = next((route, stops) for route, stops in starts if route.identifier == "100100052")
        _, alight = next((route, stops) for route, stops in ends if route.identifier == "100100052")
        self.assertTrue(all(route.city == "seoul" for route, _ in starts + ends))
        self.assertTrue(all(distance <= 100 for _, distance in board))
        self.assertTrue(all(distance <= 650 for _, distance in alight))
        self.assertIn(("123000579", 40), [(route.stops[index].identifier, route.stops[index].sequence)
                                         for index, _ in board])
        self.assertIn(("104000302", 46), [(route.stops[index].identifier, route.stops[index].sequence)
                                         for index, _ in alight])
