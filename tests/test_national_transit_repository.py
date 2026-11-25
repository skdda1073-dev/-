import csv
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from services.national_transit_repository import KST, NationalTransitRepository, _Label, _StopTime, _Walk, _search


class NationalTransitRepositoryTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        self.rail = self.data / "normalized" / "rail"
        self.rail.mkdir(parents=True)
        self.write_csv(
            "rail_station_locations.csv",
            ("source_record", "station_name", "address", "latitude", "longitude"),
            [
                ("1", "출발역", "강원도 시험로 1", 37.89, 127.72),
                ("2", "도착역", "부산 시험로 2", 35.11, 129.04),
            ],
        )
        self.campus_a = {"name": "첫 건물", "lat": 37.882, "lon": 127.725}
        self.campus_b = {"name": "둘째 건물", "lat": 37.886, "lon": 127.728}
        self.station_b = {"name": "도착역", "lat": 35.11, "lon": 129.04}

    def write_csv(self, filename, headers, rows):
        with (self.rail / filename).open("w", encoding="utf-8", newline="") as source:
            writer = csv.writer(source)
            writer.writerow(headers)
            writer.writerows(rows)

    def trains(self, source_runs):
        rows = []
        for number, direction, start, end, depart, arrive, days in source_runs:
            # Coordinates in the source worksheet differ by stop for the
            # vertical timetable, but the trip belongs to a single table.
            rows.extend([
                ("시험선", 9, 1, 38, 2, direction, number, "무궁화", 1, start,
                 depart, "ordered", days, "2026-11-24"),
                ("시험선", 9, 1, 39, 2, direction, number, "무궁화", 2, end,
                 arrive, "ordered", days, "2026-11-24"),
            ])
        self.write_csv(
            "korail_passenger_stop_times_20261101.csv",
            ("source_sheet", "source_table_row", "source_table_column", "source_row", "source_column", "direction",
             "train_number", "train_type", "stop_order", "station_name", "scheduled_time", "time_order_status",
             "schedule_note", "runs_from_date"),
            rows,
        )
        return NationalTransitRepository(self.data)

    def test_building_walk_controls_which_departure_can_be_boarded(self):
        repository = self.trains([
            ("101", "하행", "출발", "도착", "08:12", "11:15", "화"),
            ("103", "하행", "출발", "도착", "08:30", "11:41", "화"),
        ])

        def walk(_lat1, _lon1, start_name, _lat2, _lon2, _end_name):
            return (6, 400) if start_name == "첫 건물" else (12, 850)

        requested = "2026-11-24T08:00:00+09:00"
        first, = repository.plan(self.campus_a, self.station_b, requested, walk)
        second, = repository.plan(self.campus_b, self.station_b, requested, walk)
        self.assertEqual(first["segments"][0]["from_place"], self.campus_a)
        self.assertEqual(second["segments"][0]["from_place"], self.campus_b)
        self.assertEqual(first["segments"][0]["duration_min"], 6)
        self.assertEqual(second["segments"][0]["duration_min"], 12)
        self.assertEqual(first["segments"][1]["service_name"], "무궁화 101")
        self.assertEqual(second["segments"][1]["service_name"], "무궁화 103")
        self.assertEqual(first["segments"][1]["booking_provider"], "korail")
        self.assertIsNone(first["segments"][1]["fare_won"])
        self.assertEqual(first["arrival_at"], "2026-11-24T11:15:00+09:00")

    def test_after_midnight_departure_uses_previous_service_date_and_reverse_order(self):
        repository = self.trains([
            ("202", "상행", "도착", "출발", "24:15", "27:10", "화"),
        ])
        requested = "2026-11-25T00:02:00+09:00"
        result, = repository.plan(self.station_b, self.campus_a, requested, lambda *_: (6, 400))
        ride, walk = result["segments"]
        self.assertEqual(ride["departure_at"], "2026-11-25T00:15:00+09:00")
        self.assertEqual(ride["arrival_at"], "2026-11-25T03:10:00+09:00")
        self.assertEqual(result["arrival_at"], "2026-11-25T03:16:00+09:00")
        self.assertEqual(walk["to_place"], self.campus_a)
        self.assertFalse(repository.plan(self.campus_a, self.station_b, requested, lambda *_: (6, 400)))

    def test_unverified_access_walk_is_not_replaced_with_a_zero_duration(self):
        repository = self.trains([
            ("101", "하행", "출발", "도착", "08:12", "11:15", "화"),
        ])
        self.assertFalse(repository.plan(self.campus_a, self.station_b, "2026-11-24T08:00:00+09:00",
                                         lambda *_: (-1, -1)))

    def test_bus_feeder_can_board_at_a_verified_station_outside_chuncheon(self):
        repository = self.trains([
            ("202", "상행", "도착", "출발", "08:12", "11:15", "화"),
        ])
        origin = {"name": "부산 출발지", "lat": 35.09, "lon": 129.04}
        destination = {"name": "출발역", "lat": 37.89, "lon": 127.72}
        requested = datetime.fromisoformat("2026-11-24T08:00+09:00")
        arrival = datetime.fromisoformat("2026-11-24T08:06+09:00")
        gateway, = repository.transfer_stations(origin)
        bus = {"mode": "bus", "route_name": "26", "from_place": origin, "to_place": gateway,
               "departure_at": requested.isoformat(), "arrival_at": arrival.isoformat(), "duration_min": 6}
        feeder = {"gateway": gateway, "arrival_at": arrival, "segments": [bus]}

        def unavailable_walk(*_):
            return -1, -1

        journey, = repository.plan(origin, destination, requested, unavailable_walk, access_routes=[feeder])
        self.assertEqual([leg["mode"] for leg in journey["segments"]], ["bus", "train"])
        self.assertEqual(journey["segments"][1]["from_place"], gateway)
        self.assertEqual(journey["arrival_at"], "2026-11-24T11:15:00+09:00")

        wrong_location = {**feeder, "gateway": {**gateway, "lat": gateway["lat"] + 0.001}}
        missed_departure = {**feeder, "arrival_at": datetime.fromisoformat("2026-11-24T08:08+09:00")}
        self.assertFalse(repository.plan(origin, destination, requested, unavailable_walk,
                                         access_routes=[wrong_location, missed_departure]))

    def test_boarding_later_stop_of_same_trip_can_save_walk_with_same_arrival(self):
        timetable = SimpleNamespace(
            trips=[SimpleNamespace(mode="subway", stops=(_StopTime("first", None, 300),
                _StopTime("second", 480, 540), _StopTime("terminal", 900, None)))],
            connections_from=lambda *_: [(300, 0, 0, 480, 0), (540, 0, 1, 900, 0)],
            stations={key: SimpleNamespace() for key in ("first", "second", "terminal")},
            transfers={},
        )
        walk = _Walk({}, {}, 0, 0)
        seeds = [_Label(120, "first", 0, walk, walk_seconds=120), _Label(60, "second", 0, walk, walk_seconds=60)]
        best, = _search(timetable, datetime(2026, 11, 25).date(), 0, seeds, {"terminal": walk})
        self.assertEqual(best[0], 900)
        self.assertEqual(best[2].move.start, 1)
        self.assertEqual(best[2].walk_seconds, 60)

    def test_rail_to_bus_stations_keep_later_arrivals_and_use_exact_exits(self):
        self.write_csv(
            "rail_station_locations.csv",
            ("source_record", "station_name", "address", "latitude", "longitude"),
            [("1", "출발역", "강원도 시험로 1", 37.89, 127.72),
             ("2", "가까운역", "경기도 시험로 2", 37.63, 127.20),
             ("3", "다른역", "경기도 시험로 3", 37.59, 127.22)],
        )
        repository = self.trains([
            ("101", "하행", "출발", "가까운", "08:12", "09:00", "화"),
            ("103", "하행", "출발", "다른", "09:52", "10:20", "화"),
        ])
        destination = {"name": "버스 목적지", "lat": 37.61, "lon": 127.22}
        stations = repository.transfer_stations(destination)
        self.assertCountEqual([station["name"] for station in stations], ["가까운역", "다른역"])

        def walking(_lat1, _lon1, start, _lat2, _lon2, end):
            self.assertEqual((start, end), ("첫 건물", "출발역"))
            return (6, 400)

        requested = "2026-11-24T08:00+09:00"
        arrivals = repository.plan_to_stations(self.campus_a, destination, requested, walking, stations)
        self.assertEqual([(place["name"], journey["arrival_at"]) for place, journey in arrivals],
                         [("가까운역", "2026-11-24T09:00:00+09:00"), ("다른역", "2026-11-24T10:20:00+09:00")])
        for place, journey in arrivals:
            self.assertEqual([leg["mode"] for leg in journey["segments"]], ["walk", "train"])
            self.assertEqual(journey["segments"][-1]["to_place"], place)
        limited = repository.plan_to_stations(self.campus_a, destination, requested, walking, stations,
                                              latest_arrival="2026-11-24T10:19+09:00")
        self.assertEqual(limited, arrivals[:1])


class NationalLine8IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repository = NationalTransitRepository(Path(__file__).resolve().parents[1] / "data")
        cls.chuncheon = next(place for place in cls.repository.chuncheon_gateways() if place["name"] == "춘천역")
        cls.dasan = cls.repository._timetable().stations["metro:S4108:805"].place()

    @staticmethod
    def rides(journey):
        return [(leg["route_name"], leg["from_place"]["name"], leg["to_place"]["name"])
                for leg in journey["segments"] if leg["mode"] in {"subway", "train"}]

    def test_hallym_to_exact_maseok_station_does_not_require_a_destination_walk(self):
        origin = {"name": "한림대학교 공학관", "lat": 37.88695, "lon": 127.73585}
        destination, = self.repository.matching_stations("마석역")
        requested = "2026-11-25T22:34:30+09:00"

        def access_only(_lat1, _lon1, start, _lat2, _lon2, end):
            return (1879 / 60, 2300) if (start, end) == (origin["name"], "춘천역") else (-1, -1)

        poi = {**destination, "lat": destination["lat"] + 0.0006}
        self.assertFalse(self.repository.plan(origin, poi, requested, access_only))
        first, *_ = self.repository.plan(origin, destination, requested, access_only)
        self.assertEqual(self.rides(first), [("경춘선", "춘천역", "마석역")])
        self.assertEqual(first["arrival_at"], "2026-11-26T00:20:30+09:00")
        self.assertEqual(first["segments"][0]["duration_min"], 32)

    def test_dated_itx_uses_verified_gateway_and_preserves_midnight_arrival(self):
        service_date = date(2026, 11, 26)

        def itx_schedule(origin, destination, day):
            if (origin, destination, day) != ("NAT130126", "NAT140873", service_date):
                return ()
            return ({"train_number": "02035", "fare_won": 8600,
                     "departure_at": datetime(2026, 11, 26, 23, 8, tzinfo=KST),
                     "arrival_at": datetime(2026, 11, 27, 0, 4, tzinfo=KST)},)

        repository = NationalTransitRepository(self.repository._normalized_path.parent, itx_schedule=itx_schedule)
        table = repository._timetable()
        self.assertIn("NAT130126", table.rail_by_id)
        cheongnyangni = table.stations[table.rail_by_id["NAT130126"]].place()
        journeys = repository.plan(cheongnyangni, self.chuncheon, "2026-11-26T23:00+09:00", lambda *_: (-1, -1))
        ride = next(leg for route in journeys for leg in route["segments"] if leg["mode"] == "train")
        self.assertEqual((ride["service_name"], ride["arrival_at"], ride["fare_won"], ride["booking_provider"]),
                         ("ITX-청춘 02035", "2026-11-27T00:04:00+09:00", 8600, "korail"))
        next_day = repository.plan(cheongnyangni, self.chuncheon, "2026-11-27T23:00+09:00", lambda *_: (-1, -1))
        self.assertFalse(any(leg.get("service_name") == "ITX-청춘 02035" for route in next_day for leg in route["segments"]))

    def test_chuncheon_to_dasan_uses_the_byeollae_interchange(self):
        def walking(_lat1, _lon1, start, _lat2, _lon2, end):
            return (47, 3600) if start == "동구릉" and end == "다산역" else (-1, -1)

        journeys = self.repository.plan(self.chuncheon, self.dasan, "2026-11-24T09:32:00+09:00", walking)
        self.assertTrue(journeys)
        best = journeys[0]
        self.assertEqual(self.rides(best), [("경춘선", "춘천역", "별내역"), ("8호선", "별내역", "다산역")])
        self.assertEqual(best["arrival_at"], "2026-11-24T11:05:10+09:00")
        self.assertFalse(any(self.rides(journey)[-1] == ("8호선", "별내역", "동구릉") for journey in journeys))

    def test_dasan_to_chuncheon_uses_the_same_interchange(self):
        journeys = self.repository.plan(self.dasan, self.chuncheon, "2026-11-24T09:32:00+09:00", lambda *_: (-1, -1))
        self.assertTrue(journeys)
        self.assertEqual(self.rides(journeys[0]), [("8호선", "다산역", "별내역"), ("경춘선", "별내역", "춘천역")])

    def test_dongseoul_terminal_uses_line_2_after_the_jamsil_line_8_transfer(self):
        byeollae = self.repository._timetable().stations["metro:S4108:804"].place()
        terminal = {"name": "동서울종합터미널", "lat": 37.5339323, "lon": 127.09476136}

        def walking(_lat1, _lon1, start, _lat2, _lon2, end):
            return (158 / 60, 166) if (start, end) == ("강변(동서울터미널)", terminal["name"]) else (-1, -1)

        best, *_ = self.repository.plan(byeollae, terminal, "2026-11-25T17:50+09:00", walking)
        self.assertEqual(self.rides(best), [("8호선", "별내역", "잠실(송파구청)"),
                                          ("서울 도시철도 2호선", "잠실(송파구청)", "강변(동서울터미널)")])
        transfer, subway, egress = best["segments"][-3:]
        self.assertEqual(transfer["arrival_at"], "2026-11-25T18:23:50+09:00")
        self.assertEqual(subway["departure_at"], "2026-11-25T18:28:00+09:00")
        self.assertEqual(egress["duration_min"], 3)
        self.assertEqual(egress["source"], "보행 경로")
        self.assertEqual(best["arrival_at"], "2026-11-25T18:34:38+09:00")

    def test_deokso_transfer_candidates_include_geumgok_on_the_gyeongchun_line(self):
        destination = {"name": "덕소아이파크", "lat": 37.5871, "lon": 127.222}
        stations = self.repository.transfer_stations(destination)
        self.assertLessEqual(len(stations), 8)
        self.assertEqual(stations, self.repository.transfer_stations(destination))
        station, = (place for place in stations if place["name"] == "금곡역")
        arrivals = self.repository.plan_to_stations(self.chuncheon, destination, "2026-11-24T09:32+09:00",
                                                     lambda *_: (-1, -1), stations)
        journey = next(journey for place, journey in arrivals if place == station)
        self.assertEqual(self.rides(journey), [("경춘선", "춘천역", "금곡역")])
        self.assertEqual(journey["segments"][-1]["to_place"], station)

    def test_station_bus_access_can_catch_a_train_the_walk_misses(self):
        origin = {"name": "학교 건물", "lat": 37.89, "lon": 127.737}
        at = datetime.fromisoformat("2026-11-24T09:30:00+09:00")
        arrived_by_bus = datetime.fromisoformat("2026-11-24T09:40:00+09:00")
        bus = {"mode": "bus", "route_name": "300", "from_place": origin, "to_place": self.chuncheon,
               "departure_at": at, "arrival_at": arrived_by_bus, "duration_min": 10, "estimated": True}
        access = {"gateway": self.chuncheon, "segments": [bus], "arrival_at": arrived_by_bus,
                  "warnings": ["실시간 버스로 계산한 역 도착 예상"]}

        def walking(_lat1, _lon1, start, _lat2, _lon2, end):
            return (20, 1600) if (start, end) == (origin["name"], "춘천역") else (-1, -1)

        walking_only = self.repository.plan(origin, self.dasan, at, walking)[0]
        by_bus = self.repository.plan(origin, self.dasan, at, walking, access_routes=[access])[0]
        self.assertLess(by_bus["arrival_at"], walking_only["arrival_at"])
        self.assertEqual(by_bus["segments"][0], bus)
        self.assertTrue(by_bus["estimated"])
        self.assertEqual(by_bus["warnings"], access["warnings"])
        self.assertEqual(by_bus["arrival_at"], "2026-11-24T11:05:10+09:00")

        def walk_to_the_same_train(_lat1, _lon1, start, _lat2, _lon2, end):
            return (11, 900) if (start, end) == (origin["name"], "춘천역") else (-1, -1)

        fewer_transfers = self.repository.plan(origin, self.dasan, at, walk_to_the_same_train,
                                               access_routes=[access])[0]
        self.assertEqual(fewer_transfers["arrival_at"], by_bus["arrival_at"])
        self.assertEqual(fewer_transfers["segments"][0]["mode"], "walk")


if __name__ == "__main__":
    unittest.main()
