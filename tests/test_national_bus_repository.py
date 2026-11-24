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
