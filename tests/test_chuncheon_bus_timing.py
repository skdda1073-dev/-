from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from services.chuncheon_bus_timing import ChuncheonBusTiming


DATA = Path(__file__).resolve().parents[1] / "data/normalized/bus/chuncheon_ride_time_estimates.json"
STOPS = ("CCB250001275", "CCB250026527", "CCB250002228", "CCB250001337", "CCB250001294", "CCB250001295")
KEY = ("CCB250030000", "0", STOPS)
SOURCE = "춘천시대중교통정보 공개 시각표 기반 추정"


class ChuncheonBusTimingTests(unittest.TestCase):
    def test_keeps_exact_order_direction_and_verified_date_range(self):
        timing = ChuncheonBusTiming(DATA)
        for day in (20, 25, 28, 30):
            schedules = timing.published_for_date(date(2026, 11, day))
            self.assertEqual(timing.for_date(date(2026, 11, day))[KEY], (5.0, SOURCE))
            self.assertEqual({(key[1], key[2][0], key[2][-1], value.board_offset, value.alight_offset)
                              for key, value in schedules.items()}, {
                ("0", "CCB250001275", "CCB250001295", 6, 11),
                ("0", "CCB250001357", "CCB250001275", 0, 6),
                ("0", "CCB250001275", "CCB250026559", 6, 24),
                ("1", "CCB250002081", "CCB250002127", 25, 49),
            })
        self.assertEqual(timing.for_date(date(2026, 11, 19)), {})
        self.assertEqual(timing.for_date(date(2026, 12, 1)), {})

    def test_ignores_missing_and_invalid_data(self):
        query_date = date(2026, 11, 25)
        example = json.loads(DATA.read_text(encoding="utf-8"))["estimates"][0]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "estimates.json"
            self.assertEqual(ChuncheonBusTiming(path).for_date(query_date), {})
            path.write_text("{invalid", encoding="utf-8")
            self.assertEqual(ChuncheonBusTiming(path).for_date(query_date), {})

            malformed = [None, {**example, "direction": 0}, {**example, "minutes": True},
                         {**example, "minutes": -1}, {**example, "valid_from": "bad date"},
                         {**example, "stop_ids": [STOPS[0], STOPS[0]]}]
            path.write_text(json.dumps({"estimates": [*malformed, example]}), encoding="utf-8")
            self.assertEqual(ChuncheonBusTiming(path).for_date(query_date), {KEY: (5.0, SOURCE)})

    def test_conflicting_estimates_are_excluded(self):
        query_date = date(2026, 11, 25)
        example = json.loads(DATA.read_text(encoding="utf-8"))["estimates"][0]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "estimates.json"
            path.write_text(json.dumps({"estimates": [example, {**example, "minutes": 6}]}), encoding="utf-8")
            self.assertEqual(ChuncheonBusTiming(path).for_date(query_date), {})

    def test_published_projection_requires_exact_verified_clock_sequence(self):
        query_date = date(2026, 11, 28)
        timing = ChuncheonBusTiming(DATA).published_for_date(query_date)[KEY]
        self.assertEqual((timing.board_offset, timing.alight_offset), (6, 11))
        self.assertEqual(len(timing.departure_times), 42)
        self.assertEqual(timing.departure_times[-1].isoformat(timespec="minutes"), "22:10")

        with TemporaryDirectory() as directory:
            path = Path(directory) / DATA.name
            path.write_bytes(DATA.read_bytes())
            csv_name = "chuncheon_published_departures_20260930.csv"
            data = DATA.with_name(csv_name).read_text(encoding="utf-8")
            changed = data.replace(",42,22:10,", ",42,22:11,", 1)
            self.assertNotEqual(data, changed)
            path.with_name(csv_name).write_text(changed, encoding="utf-8")
            loader = ChuncheonBusTiming(path)
            self.assertEqual(loader.published_for_date(query_date), {})
            self.assertEqual(loader.for_date(query_date)[KEY], (5.0, SOURCE))
