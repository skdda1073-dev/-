"""실제 셔틀 원본과 손상 행·회차 경계를 외부 통신 없이 검증한다."""

import csv
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from services.shuttle_repository import ShuttleDataError, ShuttleRepository


DATA_PATH = Path(__file__).resolve().parents[1] / "한림대_셔틀버스.csv"
COLUMNS = ["노선명", "방면", "정류장", "시간"]


def event(name, time, route="제2노선", direction="원문 방면"):
    return [route, direction, name, time]


def loop(start="08:00", middle="08:10", end="08:20"):
    return [event("대학교", start), event("실제 정류장", middle), event("대학교", end)]


class ActualShuttleRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = ShuttleRepository(DATA_PATH).snapshot()

    def test_actual_source_counts_and_hash_match_untouched_file(self):
        source = self.snapshot["source"]
        self.assertEqual(source["filename"], "한림대_셔틀버스.csv")
        self.assertEqual(source["encoding"], "utf-8")
        self.assertEqual((source["row_count"], source["route_count"], source["stop_count"]), (196, 4, 31))
        self.assertEqual(source["sha256"], sha256(DATA_PATH.read_bytes()).hexdigest())
        self.assertEqual(self.snapshot["diagnostics"], [])

    def test_actual_trip_counts_and_single_stop_limit(self):
        groups = {route: [trip for trip in self.snapshot["trips"] if trip["route_name"] == route]
                  for route in ("제1노선", "제2노선", "제3노선", "제4노선")}
        self.assertEqual([len(trips) for trips in groups.values()], [16, 6, 2, 6])
        self.assertTrue(all(not trip["routing_usable"] and len(trip["events"]) == 1 for trip in groups["제1노선"]))
        self.assertTrue(all(trip["routing_usable"] for route in ("제2노선", "제3노선", "제4노선") for trip in groups[route]))

    def test_actual_loop_sequence_and_times_are_preserved(self):
        trip = next(trip for trip in self.snapshot["trips"] if trip["route_name"] == "제2노선")
        self.assertEqual(trip["direction"], "온의동/퇴계동/석사동")
        self.assertEqual(trip["sequence_basis"], "csv_row_order")
        self.assertEqual(len(trip["events"]), 12)
        self.assertEqual((trip["events"][0]["name"], trip["events"][0]["time"], trip["events"][0]["line"]), ("대학교", "07:40", 18))
        self.assertEqual((trip["events"][1]["name"], trip["events"][1]["time"]), ("온의금호1차 버스정류장", "07:58"))
        self.assertEqual((trip["events"][-1]["name"], trip["events"][-1]["time"], trip["events"][-1]["line"]), ("대학교", "08:40", 29))
        self.assertEqual(trip["events"][0]["stop_id"], trip["events"][-1]["stop_id"])
        self.assertEqual(trip["events"][-1]["minutes"] - trip["events"][1]["minutes"], 42)

    def test_duplicate_0940_is_two_visits_in_distinct_trips(self):
        trips = [trip for trip in self.snapshot["trips"] if trip["route_name"] == "제4노선"]
        self.assertEqual(trips[1]["events"][-1]["time"], "09:40")
        self.assertEqual(trips[2]["events"][0]["time"], "09:40")
        self.assertEqual(trips[1]["events"][-1]["line"], 135)
        self.assertEqual(trips[2]["events"][0]["line"], 136)
        self.assertNotEqual(trips[1]["id"], trips[2]["id"])
        self.assertEqual(sum(len(trip["events"]) for trip in self.snapshot["trips"]), 196)

    def test_route_direction_and_trip_identifiers_have_distinct_responsibilities(self):
        trips = self.snapshot["trips"]
        route2 = [trip for trip in trips if trip["route_name"] == "제2노선"]
        self.assertEqual(len({trip["route_id"] for trip in route2}), 1)
        self.assertEqual(len({trip["id"] for trip in route2}), 6)
        route1 = [trip for trip in trips if trip["route_name"] == "제1노선"]
        self.assertEqual(len({trip["route_id"] for trip in route1}), 2)

    def test_only_observed_afternoon_route4_trips_contain_chuncheon_station(self):
        trips = [trip for trip in self.snapshot["trips"] if trip["route_name"] == "제4노선"]
        self.assertEqual([trip["events"][0]["time"] for trip in trips if any(row["name"] == "춘천역" for row in trip["events"])], ["17:10", "18:20"])

    def test_no_unknown_operation_or_time_semantics_are_invented(self):
        self.assertEqual(self.snapshot["source"]["time_semantics"], "unspecified")
        self.assertTrue(all(value is None for value in self.snapshot["conditions"].values()))
        self.assertTrue(all("lat" not in row and "lon" not in row for trip in self.snapshot["trips"] for row in trip["events"]))


class ShuttleRepositoryValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "shuttle.csv"

    def write_rows(self, rows, encoding="utf-8", columns=COLUMNS):
        with self.path.open("w", encoding=encoding, newline="") as output:
            writer = csv.writer(output)
            writer.writerow(columns)
            writer.writerows(rows)
        return ShuttleRepository(self.path)

    def assert_error(self, repository):
        with self.assertRaises(ShuttleDataError) as raised:
            repository.snapshot()
        self.assertEqual(raised.exception.code, "SHUTTLE_DATA")
        self.assertNotIn(str(self.path), str(raised.exception))

    def test_supported_encodings_preserve_names_and_report_encoding(self):
        for encoding in ("utf-8", "utf-8-sig", "cp949"):
            with self.subTest(encoding=encoding):
                snapshot = self.write_rows(loop(), encoding).snapshot()
                self.assertEqual(snapshot["source"]["encoding"], encoding)
                self.assertEqual(snapshot["trips"][0]["events"][1]["name"], "실제 정류장")
                self.assertTrue(snapshot["trips"][0]["routing_usable"])

    def test_missing_empty_header_only_and_wrong_columns_are_errors(self):
        self.assert_error(ShuttleRepository(self.path))
        self.path.write_text("", encoding="utf-8")
        self.assert_error(ShuttleRepository(self.path))
        self.assert_error(self.write_rows([]))
        self.assert_error(self.write_rows([], columns=COLUMNS[:-1]))
        self.assert_error(self.write_rows([], columns=COLUMNS + ["운행조건"]))

    def test_bad_middle_row_disables_whole_trip_without_losing_next_trip(self):
        for bad in (event("잘못된 행", "bad"), event("", "08:10"), event("잘못된 행", "08:10") + ["추가 셀"], []):
            with self.subTest(bad=bad):
                rows = [event("대학교", "08:00"), bad, event("대학교", "08:20")] + loop("09:00", "09:10", "09:20")
                snapshot = self.write_rows(rows).snapshot()
                self.assertEqual(len(snapshot["trips"]), 2)
                self.assertFalse(snapshot["trips"][0]["routing_usable"])
                self.assertTrue(snapshot["trips"][1]["routing_usable"])
                self.assertTrue(any(row["line"] == 3 for row in snapshot["diagnostics"]))

    def test_bad_start_or_end_time_preserves_next_normal_loop(self):
        for rows in ([event("대학교", "bad"), event("A", "08:10"), event("대학교", "08:20")],
                     [event("대학교", "08:00"), event("A", "08:10"), event("대학교", "bad")]):
            with self.subTest(rows=rows):
                snapshot = self.write_rows(rows + loop("09:00", "09:10", "09:20")).snapshot()
                self.assertEqual([trip["routing_usable"] for trip in snapshot["trips"]], [False, True])

    def test_partial_terminal_identity_closes_only_the_damaged_trip(self):
        for terminal in (["", "원문 방면", "대학교", "08:20"], ["제2노선", "", "대학교", "08:20"]):
            with self.subTest(terminal=terminal):
                rows = [event("대학교", "08:00"), event("A", "08:10"), terminal]
                snapshot = self.write_rows(rows + loop("09:00", "09:10", "09:20")).snapshot()
                self.assertEqual([trip["routing_usable"] for trip in snapshot["trips"]], [False, True])

    def test_malformed_quoted_row_does_not_consume_next_normal_loop(self):
        self.write_rows(loop() + loop("09:00", "09:10", "09:20"))
        text = self.path.read_text(encoding="utf-8").splitlines()
        text[2] = '제2노선,원문 방면,"닫히지 않은 정류장,08:10'
        self.path.write_text("\n".join(text) + "\n", encoding="utf-8")
        snapshot = ShuttleRepository(self.path).snapshot()
        self.assertEqual([trip["routing_usable"] for trip in snapshot["trips"]], [False, True])
        self.assertTrue(any(row["code"] == "INVALID_CSV_ROW" and row["line"] == 3 for row in snapshot["diagnostics"]))

    def test_decreasing_clock_is_not_interpreted_as_midnight(self):
        snapshot = self.write_rows(loop("23:50", "00:05", "00:20") + loop("08:00", "08:10", "08:20")).snapshot()
        self.assertEqual([trip["routing_usable"] for trip in snapshot["trips"]], [False, True])
        self.assertTrue(any(row["code"] == "DECREASING_TIME" for row in snapshot["diagnostics"]))
        self.assertEqual(snapshot["trips"][0]["events"][1]["minutes"], 5)

    def test_explicit_24_hour_time_keeps_service_day_offset(self):
        trip = self.write_rows(loop("23:50", "24:05", "24:20")).snapshot()["trips"][0]
        self.assertTrue(trip["routing_usable"])
        self.assertEqual([row["minutes"] for row in trip["events"]], [1430, 1445, 1460])
        self.assertEqual(trip["events"][1]["time"], "24:05")

    def test_invalid_time_formats_disable_only_affected_trip(self):
        for value in ("8:10", "08:60", "-1:00", "48:00", "NaN"):
            with self.subTest(value=value):
                snapshot = self.write_rows(loop(middle=value) + loop("09:00", "09:10", "09:20")).snapshot()
                self.assertEqual([trip["routing_usable"] for trip in snapshot["trips"]], [False, True])
                self.assertTrue(any(row["code"] == "INVALID_TIME" for row in snapshot["diagnostics"]))

    def test_route_and_direction_change_breaks_incomplete_trip(self):
        rows = [event("대학교", "08:00"), event("A", "08:10")]
        rows += [event("대학교", "09:00", direction="반대 방면"), event("A", "09:10", direction="반대 방면"), event("대학교", "09:20", direction="반대 방면")]
        snapshot = self.write_rows(rows).snapshot()
        self.assertEqual([trip["routing_usable"] for trip in snapshot["trips"]], [False, True])
        self.assertNotEqual(snapshot["trips"][0]["events"][0]["stop_id"], snapshot["trips"][1]["events"][0]["stop_id"])

    def test_orphan_and_truncated_sequences_are_not_usable(self):
        snapshot = self.write_rows([event("A", "07:50"), event("대학교", "08:00")] + loop() + [event("대학교", "09:00"), event("A", "09:10")]).snapshot()
        self.assertEqual([trip["routing_usable"] for trip in snapshot["trips"]], [False, True, False])

    def test_stop_ids_are_stable_and_source_names_are_not_merged(self):
        rows = [event("대학교", "08:00"), event("교대", "08:10"), event("교육대학교", "08:15"), event("대학교", "08:20")]
        repository = self.write_rows(rows)
        first = repository.snapshot()["trips"][0]
        second = ShuttleRepository(self.path).snapshot()["trips"][0]
        self.assertEqual(first, second)
        self.assertNotEqual(first["events"][1]["stop_id"], first["events"][2]["stop_id"])

    def test_cache_returns_deep_copies(self):
        repository = self.write_rows(loop())
        snapshot = repository.snapshot()
        snapshot["source"]["row_count"] = -1
        snapshot["conditions"]["fare"] = "무료"
        snapshot["trips"][0]["events"][0]["name"] = "수정"
        snapshot["diagnostics"].append({"line": 1})
        self.path.unlink()
        fresh = repository.snapshot()
        self.assertEqual(fresh["source"]["row_count"], 3)
        self.assertIsNone(fresh["conditions"]["fare"])
        self.assertEqual(fresh["trips"][0]["events"][0]["name"], "대학교")
        self.assertEqual(fresh["diagnostics"], [])


if __name__ == "__main__":
    unittest.main()
