from datetime import date, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from services.chuncheon_bus_timing import ChuncheonBusTiming
from services.national_bus_repository import NationalBusRepository


DATA = Path(__file__).resolve().parents[1] / "data/normalized/bus/chuncheon_ride_time_estimates.json"
STOPS = ("CCB250001275", "CCB250026527", "CCB250002228", "CCB250001337", "CCB250001294", "CCB250001295")
KEY = ("CCB250030000", "0", STOPS)
SOURCE = "춘천시대중교통정보 공개 시각표 기반 추정"
STATION_APPROACH = ("CCB250002127", "CCB250001262", "CCB250001358", "CCB250001746",
                    "CCB250001885", "CCB265000722", "CCB250026781")


class ChuncheonBusTimingTests(unittest.TestCase):
    def test_keeps_exact_order_direction_and_verified_date_range(self):
        timing = ChuncheonBusTiming(DATA)
        for day in (20, 25, 28, 30):
            schedules = timing.published_for_date(date(2026, 11, day))
            self.assertEqual(timing.for_date(date(2026, 11, day))[KEY], (5.0, SOURCE))
            self.assertEqual({(key[1], key[2][0], key[2][-1], value.board_offset, value.alight_offset)
                              for key, value in schedules.items() if key[0] == KEY[0]}, {
                ("0", "CCB250001275", "CCB250001295", 6, 11),
                ("0", "CCB250001357", "CCB250001275", 0, 6),
                ("0", "CCB250001275", "CCB250026559", 6, 24),
                ("1", "CCB250002081", "CCB250002127", 25, 49),
                ("1", "CCB250002127", "CCB250026781", 49, 55),
            })
        self.assertEqual(timing.for_date(date(2026, 11, 19)), {})
        self.assertEqual(timing.for_date(date(2026, 12, 1)), {})

    def test_12_published_timing_only_applies_to_verified_regular_route_and_dates(self):
        timing = ChuncheonBusTiming(DATA)
        stops = (*STATION_APPROACH, "CCB250026552")
        regular = ("CCB250001200", "0", stops)
        variant = ("CCB250001201", "0", stops)
        self.assertNotIn(regular, timing.for_date(date(2026, 11, 24)))
        self.assertNotIn(regular, timing.published_for_date(date(2026, 12, 1)))
        for day in (25, 26, 28, 30):
            service_date = date(2026, 11, day)
            self.assertEqual(timing.for_date(service_date)[regular], (7.0, SOURCE))
            self.assertNotIn(variant, timing.for_date(service_date))
            schedules = timing.published_for_date(service_date)
            self.assertNotIn(variant, schedules)
            schedule = schedules[regular]
            self.assertEqual((schedule.board_offset, schedule.alight_offset), (13, 20))
            self.assertEqual(len(schedule.departure_times), 18)
            self.assertEqual(schedule.departure_times[8].isoformat(timespec="minutes"), "12:20")

    def test_12_can_reach_the_station_before_the_noon_train(self):
        repo = NationalBusRepository(DATA.parents[2])
        origin = {"name": "한림대학교 Campus Life Center", "lat": 37.88632, "lon": 127.73812}
        station = {"name": "춘천역", "lat": 37.884391, "lon": 127.716603}
        requested = datetime.fromisoformat("2026-11-26T12:13:00+09:00")
        trip, *_ = repo.plan(origin, station, requested, lambda *_: (5, 350),
                           use_published_timing=True, timed_only=True)
        access, bus, egress = trip["segments"]
        self.assertEqual(access["to_place"]["name"], "한림대학교")
        self.assertEqual(bus["route_name"], "12")
        self.assertEqual(bus["departure_at"], "2026-11-26T12:33:00+09:00")
        self.assertEqual(bus["arrival_at"], "2026-11-26T12:40:00+09:00")
        self.assertEqual(egress["to_place"], station)
        self.assertEqual(trip["arrival_at"], "2026-11-26T12:45:00+09:00")

    def test_300_uses_published_stop_before_station_and_walks_the_last_segment(self):
        repo = NationalBusRepository(DATA.parents[2])
        origin = {"name": "한림대학교", "lat": 37.88355, "lon": 127.73840}
        destination = {"name": "춘천역", "lat": 37.884391, "lon": 127.716603}
        requested = datetime.fromisoformat("2026-11-25T07:20:00+09:00")
        trips = repo.plan(origin, destination, requested, lambda *_: (5, 350),
                          use_published_timing=True, timed_only=True)
        trip, = (trip for trip in trips if trip["segments"][1]["route_name"] == "300")
        _, bus, walk = trip["segments"]
        self.assertEqual((bus["route_name"], bus["to_place"]["name"]), ("300", "꿈자람어린이공원"))
        self.assertEqual((bus["departure_at"], bus["arrival_at"]),
                         ("2026-11-25T07:29:00+09:00", "2026-11-25T07:35:00+09:00"))
        self.assertEqual(walk["to_place"], destination)
        self.assertEqual(trip["arrival_at"], "2026-11-25T07:40:00+09:00")

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
            self.assertFalse(any(key[0] == KEY[0] for key in loader.published_for_date(query_date)))
            self.assertEqual(loader.for_date(query_date)[KEY], (5.0, SOURCE))

    def test_inline_projection_requires_verified_clocks(self):
        query_date = date(2026, 11, 26)
        data = json.loads(DATA.read_text(encoding="utf-8"))
        entry = next(row for row in data["estimates"] if row["route_number"] == "12")
        entry["published_departures"]["source_departure_times"][8] = "12:25"
        key = ("CCB250001200", "0", (*STATION_APPROACH, "CCB250026552"))
        with TemporaryDirectory() as directory:
            path = Path(directory) / DATA.name
            path.write_text(json.dumps({"estimates": [entry]}), encoding="utf-8")
            loader = ChuncheonBusTiming(path)
            self.assertEqual(loader.published_for_date(query_date), {})
            self.assertEqual(loader.for_date(query_date)[key], (7.0, SOURCE))
