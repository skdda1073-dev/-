import csv
from datetime import date
from io import TextIOWrapper
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from scripts.export_motis_gtfs import MAX_DAYS, export_gtfs
from scripts.prepare_ktdb_gtfs import KTDB_REFERENCE_DATE, prepare_ktdb_gtfs
from services.national_transit_repository import LOCAL_MODE, TRAIN_MODE, _Station, _StopTime, _Timetable, _Trip


START = date(2026, 12, 24)
HOLIDAY = date(2026, 12, 25)


def station(name, line, links=()):
    return _Station(name, 37.85, 127.73, "", line, frozenset(links))


def trip(mode, route, name, stops, weekdays, *, only_on=None):
    return _Trip(mode, route, name, "공식 정기 시간표", None, stops, frozenset(weekdays),
                 START, only_on=only_on)


def timetable(stations, trips, transfers=None):
    feed = _Timetable.__new__(_Timetable)
    feed.stations = stations
    feed.trips = trips
    feed.transfers = transfers or {}
    feed.holidays = frozenset({HOLIDAY})
    return feed


def read_table(archive, table):
    with TextIOWrapper(archive.open(table + ".txt"), encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


class MotisGtfsExportTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "gtfs.zip"

    def complete_feed(self):
        stations = {
            "metro:a": station("출발역", "L1", ("L8",)),
            "metro:b": station("도착역", "L8"),
            "rail:c": station("고속철도역", "rail"),
        }
        metro_stops = (_StopTime("metro:a", None, 88200), _StopTime("metro:b", 90015, None))
        rail_stops = (_StopTime("metro:b", None, 39000), _StopTime("rail:c", 49800, None))
        trips = (
            trip(LOCAL_MODE, "8호선", "801", metro_stops, {3, 4}),
            trip(LOCAL_MODE, "8호선", "801", metro_stops, {5}),
            trip(LOCAL_MODE, "8호선", "802", metro_stops, {7}),
            trip(TRAIN_MODE, "경부선", "KTX 123", rail_stops, {4}, only_on=HOLIDAY),
        )
        transfers = {"metro:a": [("metro:b", 300), ("rail:c", 480)], "metro:b": [("metro:a", 300)]}
        return timetable(stations, trips, transfers)

    def test_preserves_overnight_times_calendar_and_declared_transfers(self):
        self.assertEqual(export_gtfs(self.complete_feed(), self.path, START, 3), (3, 2, 3))
        with ZipFile(self.path) as archive:
            self.assertIsNone(archive.testzip())
            self.assertCountEqual(archive.namelist(), [name + ".txt" for name in (
                "agency", "stops", "routes", "trips", "stop_times", "calendar_dates", "transfers", "feed_info")])
            trips = {row["trip_short_name"]: row for row in read_table(archive, "trips")}
            calendar = read_table(archive, "calendar_dates")
            dates = lambda name: [row["date"] for row in calendar if row["service_id"] == trips[name]["service_id"]]
            self.assertEqual(dates("801"), ["20261224", "20261226"])
            self.assertEqual(dates("802"), ["20261225"])
            self.assertEqual(dates("KTX 123"), ["20261225"])
            times = [row for row in read_table(archive, "stop_times") if row["trip_id"] == trips["801"]["trip_id"]]
            self.assertEqual([(row["arrival_time"], row["departure_time"]) for row in times],
                             [("24:30:00", "24:30:00"), ("25:00:15", "25:00:15")])
            self.assertEqual([(row["pickup_type"], row["drop_off_type"]) for row in times], [("0", "1"), ("1", "0")])
            self.assertEqual({(row["from_stop_id"], row["to_stop_id"], row["transfer_type"])
                              for row in read_table(archive, "transfers")},
                             {("metro:a", "metro:b", "0"), ("metro:b", "metro:a", "0")})
            self.assertEqual({row["route_type"] for row in read_table(archive, "routes")}, {"1", "2"})
            self.assertEqual(read_table(archive, "agency")[0]["agency_timezone"], "Asia/Seoul")

    def test_metro_only_can_be_used_with_another_general_rail_feed(self):
        self.assertEqual(export_gtfs(self.complete_feed(), self.path, START, 3, include_intercity_rail=False), (2, 1, 2))
        with ZipFile(self.path) as archive:
            self.assertEqual({row["route_type"] for row in read_table(archive, "routes")}, {"1"})
            self.assertNotIn("rail:c", {row["stop_id"] for row in read_table(archive, "stops")})

    def test_does_not_interpolate_or_bridge_unknown_clocks(self):
        stations = {key: station(key, "L1") for key in "abcde"}
        stops = tuple(_StopTime(key, clock, clock) for key, clock in zip("abcde", (36000, 36600, None, 37800, 38400)))
        feed = timetable(stations, (trip(LOCAL_MODE, "1호선", "101", stops, {3}),))
        self.assertEqual(export_gtfs(feed, self.path, START, 1), (2, 1, 4))
        with ZipFile(self.path) as archive:
            trips = read_table(archive, "trips")
            times = read_table(archive, "stop_times")
            segments = [[row["stop_id"] for row in times if row["trip_id"] == run["trip_id"]] for run in trips]
            self.assertEqual(segments, [["a", "b"], ["d", "e"]])

    def test_invalid_window_and_empty_feed_preserve_previous_output(self):
        export_gtfs(self.complete_feed(), self.path, START, 3)
        previous = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "days"):
            export_gtfs(self.complete_feed(), self.path, START, MAX_DAYS + 1)
        with self.assertRaisesRegex(ValueError, "No rail or metro service"):
            export_gtfs(timetable({}, ()), self.path, START, 3)
        self.assertEqual(self.path.read_bytes(), previous)


class KtdbGtfsPreparationTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.source = Path(directory.name) / "raw"
        self.source.mkdir()
        self.target = self.source.parent / "reference.zip"
        self.tables = {
            "agency": "agency_id,agency_name,agency_url,agency_timezone\nA1,KTDB,https://ktdb.go.kr,Asia/Seoul\n",
            "stops": "stop_id,stop_name,stop_lat,stop_lon\nA,첫 정류장,37.8,127.7\nB,다음 정류장,37.81,127.71\n",
            "routes": "route_id,agency_id,route_short_name,route_type\nBR_TAGO_1,A1,12,0\nBR_ACC3_1,A1,시외,3\n"
                      "BR_TAGO_2,A1,공항,5\nRR_ACC1_1,A1,경춘선,1\nRR_ACC4_1,A1,일반철도,4\n"
                      "RR_ACC6_1,A1,KTX,6\nSR_1,A1,여객선,2\nAR_ACC7_1,A1,국내선,7\n",
            "trips": "route_id,service_id,trip_id\nBR_TAGO_1,B1,trip1\n",
            "stop_times": "trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type,drop_off_type,timepoint\n"
                          "trip1,23:50:00,23:50:00,A,1,0,1,1\ntrip1,24:10:00,24:10:00,B,2,1,0,1\n",
            "transfers": "from_stop_id,to_stop_id,transfer_type,min_transfer_time\nA,B,2,120\n",
            "calendar": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                        "B1,1,1,1,1,1,1,1,20170101,20301231\n",
        }
        for name, table in self.tables.items():
            (self.source / f"{name}.txt").write_text(table, encoding="utf-8-sig")

    def test_uses_one_reference_weekday_and_corrects_mode_codes(self):
        prepare_ktdb_gtfs(self.source, self.target)
        with ZipFile(self.target) as archive:
            self.assertNotIn("calendar.txt", archive.namelist())
            self.assertEqual(read_table(archive, "calendar_dates"), [
                {"service_id": "B1", "date": KTDB_REFERENCE_DATE.strftime("%Y%m%d"), "exception_type": "1"}
            ])
            self.assertEqual({row["route_id"]: row["route_type"] for row in read_table(archive, "routes")}, {
                "BR_TAGO_1": "3", "BR_ACC3_1": "200", "BR_TAGO_2": "3", "RR_ACC1_1": "1",
                "RR_ACC4_1": "2", "RR_ACC6_1": "101",
                "SR_1": "4", "AR_ACC7_1": "1100",
            })
            for name in ("agency", "stops", "trips", "stop_times", "transfers"):
                self.assertEqual(archive.read(f"{name}.txt").decode("utf-8"), self.tables[name])

    def test_malformed_source_preserves_previous_zip(self):
        prepare_ktdb_gtfs(self.source, self.target)
        previous = self.target.read_bytes()
        (self.source / "routes.txt").write_text("route_id,route_type\nBR_TAGO_1,8\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "KTDB 교통수단"):
            prepare_ktdb_gtfs(self.source, self.target)
        self.assertEqual(self.target.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
