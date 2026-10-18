"""CSV 조회의 식별자·순번·거리·변경 이력 처리와 실패 경로를 검증한다."""

import csv
from pathlib import Path
import tempfile
import unittest

from services.csv_transit_repository import CsvTransitRepository
from services.public_transit_client import PublicDataError


COLUMNS = ["노선번호", "노선", "정류장순서", "정류장", "정류장명", "경도", "위도", "데이터기준일"]


def row(order=1, node="101", route="900", label="7-1", name=None, lat=37.88, lon=127.73, day="2026-03-26"):
    return {
        "노선번호": label, "노선": route, "정류장순서": str(order), "정류장": node,
        "정류장명": name or f"정류장 {node}", "경도": str(lon), "위도": str(lat), "데이터기준일": day,
    }


class CsvTransitRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "private-file.csv"

    def repository(self, rows=None, encoding="utf-8-sig", columns=COLUMNS, radius_m=500):
        with self.path.open("w", encoding=encoding, newline="") as output:
            writer = csv.DictWriter(output, fieldnames=columns)
            writer.writeheader()
            writer.writerows([row()] if rows is None else rows)
        return CsvTransitRepository(self.path, radius_m)

    def assert_csv_error(self, repository):
        with self.assertRaises(PublicDataError) as raised:
            repository.metadata()
        self.assertEqual(raised.exception.code, "CSV_DATA")
        self.assertNotIn(str(self.path), str(raised.exception))
        self.assertNotIn(self.path.name, str(raised.exception))

    def test_utf8_bom_and_cp949_are_both_supported(self):
        for encoding in ("utf-8-sig", "utf-8", "cp949"):
            with self.subTest(encoding=encoding):
                repository = self.repository(encoding=encoding)
                self.assertEqual(repository.route_stops("900")[0]["nodenm"], "정류장 101")

    def test_canonical_ids_and_no_invented_direction(self):
        repository = self.repository()
        stop = repository.route_stops("CCB900")[0]
        self.assertEqual(stop["nodeid"], "CCB101")
        self.assertEqual(stop["routeid"], "CCB900")
        self.assertIsNone(stop["updowncd"])
        self.assertEqual(stop["sequence_basis"], "csv_order")
        self.assertFalse(stop["direction_verified"])
        self.assertEqual(repository.station_routes(101)[0]["routeid"], "CCB900")

    def test_unrelated_identifier_prefixes_do_not_match(self):
        repository = self.repository()
        for identifier in ("OTHER900", "route900", "900.0", None):
            self.assertEqual(repository.route_stops(identifier), [])
        self.assertEqual(repository.station_routes("OTHER101"), [])

    def test_invalid_source_identifier_is_rejected(self):
        self.assert_csv_error(self.repository([row(route="wrong900")]))

    def test_same_stop_revisited_is_preserved_and_sorted(self):
        repository = self.repository([row(3, "101"), row(1, "101"), row(2, "102", lat=37.881)])
        stops = repository.route_stops("900")
        self.assertEqual([stop["nodeid"] for stop in stops], ["CCB101", "CCB102", "CCB101"])
        self.assertEqual([stop["nodeord"] for stop in stops], [1, 2, 3])
        self.assertEqual(len(repository.station_routes("101")), 1)

    def test_gap_duplicate_and_zero_order_are_rejected(self):
        for orders in ((1, 3), (1, 1), (0, 1), (2, 3)):
            with self.subTest(orders=orders):
                self.assert_csv_error(self.repository([row(order, str(100 + index)) for index, order in enumerate(orders)]))

    def test_bad_coordinates_fail_closed(self):
        for lat, lon in (("NaN", 127.7), (37.8, "inf"), (91, 127.7), (37.8, 181), ("text", 127.7)):
            with self.subTest(lat=lat, lon=lon):
                self.assert_csv_error(self.repository([row(lat=lat, lon=lon)]))

    def test_invalid_date_is_rejected(self):
        self.assert_csv_error(self.repository([row(day="2026-02-30")]))

    def test_conflicting_route_labels_are_unverified_without_majority_guess(self):
        repository = self.repository([row(), row(2, "102", label="8")])
        info = repository.route_info("900")
        self.assertFalse(info["route_number_verified"])
        self.assertEqual(info["raw_route_nos"], ["7-1", "8"])
        self.assertEqual(info["routeno"], "번호 미확인(ID 900)")
        self.assertEqual(len(repository.route_stops("900")), 2)

    def test_missing_empty_and_unexpected_columns_are_sanitized(self):
        self.assert_csv_error(CsvTransitRepository(self.path))
        self.assert_csv_error(self.repository([]))
        self.assert_csv_error(self.repository([], columns=COLUMNS[:-1]))
        self.assert_csv_error(self.repository([], columns=COLUMNS + ["비밀경로"]))

    def test_blank_required_cell_is_rejected(self):
        missing = row()
        missing["정류장명"] = " "
        self.assert_csv_error(self.repository([missing]))

    def test_date_like_route_numbers_remain_unverified(self):
        repository = self.repository([row(label="07월 01일")])
        info = repository.station_routes("101")[0]
        self.assertEqual(info["raw_route_no"], "07월 01일")
        self.assertEqual(info["routeno"], "번호 미확인(ID 900)")
        self.assertFalse(info["route_number_verified"])
        self.assertEqual(repository.metadata()["unverified_route_count"], 1)
        self.assertTrue(any("07월 01일" in warning for warning in repository.metadata()["warnings"]))

    def test_normal_hyphenated_route_number_is_not_guessed_as_date(self):
        info = self.repository().route_info("900")
        self.assertEqual(info["routeno"], "7-1")
        self.assertTrue(info["route_number_verified"])

    def test_nearby_filters_actual_radius_and_sorts_stably(self):
        repository = self.repository([
            row(1, "103", lon=127.75), row(2, "102"), row(3, "101"),
        ])
        stops = repository.nearby_stops(37.88, 127.73)
        self.assertEqual([stop["nodeid"] for stop in stops], ["CCB101", "CCB102"])
        self.assertEqual(stops[0]["distance"], 0)

    def test_nearby_invalid_query_is_a_provider_error(self):
        repository = self.repository()
        with self.assertRaises(PublicDataError) as raised:
            repository.nearby_stops("NaN", 127.7)
        self.assertEqual(raised.exception.code, "CSV_DATA")

    def test_same_date_conflicting_identity_is_excluded_and_marked(self):
        repository = self.repository([
            row(1, "101"), row(2, "102"), row(3, "101", name="다른 이름"),
        ])
        self.assertEqual(repository.station_routes("101"), [])
        self.assertNotIn("CCB101", [stop["nodeid"] for stop in repository.nearby_stops(37.88, 127.73)])
        self.assertTrue(repository.route_stops("900")[0]["topology_break"])
        self.assertTrue(repository.route_stops("900")[2]["stop_conflict"])
        self.assertEqual(repository.metadata()["conflict_stop_ids"], ["CCB101"])

    def test_old_and_new_close_identity_uses_latest_for_all_occurrences(self):
        repository = self.repository([
            row(1, "101", name="예전 명칭", day="2025-02-12"),
            row(2, "102"), row(3, "101", name="최신 명칭", lat=37.88001, lon=127.73001),
        ])
        stops = repository.route_stops("900")
        for stop in (stops[0], stops[2]):
            self.assertEqual(stop["nodenm"], "최신 명칭")
            self.assertEqual(stop["gpslati"], 37.88001)
            self.assertEqual(stop["gpslong"], 127.73001)
            self.assertNotIn("topology_break", stop)
            self.assertTrue(stop["stop_metadata_resolved"])
        self.assertEqual(repository.metadata()["resolved_stop_ids"], ["CCB101"])
        self.assertEqual(len(repository.station_routes("101")), 1)

    def test_far_old_new_identity_is_not_resolved(self):
        repository = self.repository([
            row(1, "101", day="2025-02-12"), row(2, "101", lat=37.89),
        ])
        self.assertEqual(repository.station_routes("101"), [])
        self.assertEqual(repository.metadata()["resolved_stop_ids"], [])
        self.assertTrue(repository.route_stops("900")[0]["topology_break"])

    def test_latest_identity_must_be_unique(self):
        repository = self.repository([
            row(1, "101", day="2025-02-12"),
            row(2, "101", name="새 이름 1"), row(3, "101", name="새 이름 2"),
        ])
        self.assertEqual(repository.metadata()["conflict_stop_ids"], ["CCB101"])

    def test_metadata_reports_mixed_dates_and_all_vs_usable_counts(self):
        repository = self.repository([
            row(1, "101", day="2025-02-12"), row(2, "102"), row(3, "102", name="충돌"),
        ])
        metadata = repository.metadata()
        self.assertEqual(metadata["source"], "csv")
        self.assertEqual(metadata["row_count"], 3)
        self.assertEqual(metadata["route_count"], 1)
        self.assertEqual(metadata["stop_count_all"], 2)
        self.assertEqual(metadata["usable_stop_count"], 1)
        self.assertEqual(metadata["data_dates"], ["2025-02-12", "2026-03-26"])
        self.assertEqual(metadata["oldest_date"], "2025-02-12")
        self.assertTrue(any("혼재" in warning for warning in metadata["warnings"]))

    def test_responses_do_not_mutate_repository_cache(self):
        repository = self.repository()
        repository.route_stops("900")[0]["nodenm"] = "mutated"
        repository.station_routes("101")[0]["routeno"] = "mutated"
        repository.metadata()["warnings"].clear()
        repository.nearby_stops(37.88, 127.73)[0]["nodenm"] = "mutated"
        self.assertEqual(repository.route_stops("900")[0]["nodenm"], "정류장 101")
        self.assertEqual(repository.station_routes("101")[0]["routeno"], "7-1")
        self.assertTrue(repository.metadata()["warnings"])
        self.assertEqual(repository.nearby_stops(37.88, 127.73)[0]["nodenm"], "정류장 101")

    def test_successful_load_is_cached_once_without_network(self):
        repository = self.repository()
        repository.metadata()
        self.path.unlink()
        self.assertEqual(len(repository.route_stops("900")), 1)


if __name__ == "__main__":
    unittest.main()
