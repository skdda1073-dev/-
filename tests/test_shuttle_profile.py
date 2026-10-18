"""공식 보강정보·실제 대표좌표·공급자 오류 격리를 외부 호출 없이 검증한다."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from services.csv_transit_repository import CsvTransitRepository
from services.public_transit_client import PublicDataError
from services.shuttle_profile import ShuttleProfile
from services.shuttle_repository import ShuttleDataError, ShuttleRepository


ROOT = Path(__file__).resolve().parents[1]
RAW_PATH = ROOT / "한림대_셔틀버스.csv"
PROFILE_PATH = ROOT / "data" / "hallym_shuttle_profile.json"
CAMPUS_PATH = ROOT / "한림대학교 건물별 좌표.csv"
CITY_PATH = ROOT / "data" / "chuncheon_bus_routes_20260326.csv"
RAW_SHA = "7755f5982aa671785e59ea5b528b12048bcb9210b715ba3e4dc13d0f18d9f03d"


def profile(repository=None, profile_path=PROFILE_PATH, campus_path=CAMPUS_PATH, city=None):
    return ShuttleProfile(repository or ShuttleRepository(RAW_PATH), profile_path, campus_path,
                          city or CsvTransitRepository(CITY_PATH))


def stop_id(route, direction, name):
    return ShuttleRepository._id("stop", route, direction, name)


class ActualShuttleProfileTests(unittest.TestCase):
    def test_original_counts_hash_and_rows_survive_endpoint_enrichment(self):
        repository = ShuttleRepository(RAW_PATH)
        original = repository.snapshot()
        enriched = profile(repository).snapshot()
        self.assertEqual(enriched["source"]["row_count"], 196)
        self.assertEqual(enriched["source"]["sha256"], RAW_SHA)
        self.assertEqual(sha256(RAW_PATH.read_bytes()).hexdigest(), RAW_SHA)
        self.assertEqual(enriched["source"]["time_semantics"], "unspecified")
        self.assertEqual(len(enriched["trips"]), 30)
        self.assertEqual(sum(len(trip["events"]) for trip in enriched["trips"]), 212)
        self.assertEqual(enriched["source"]["endpoint_enrichment"]["generated_event_count"], 16)
        self.assertEqual(repository.snapshot(), original)
        self.assertEqual(sum(len(trip["events"]) for trip in original["trips"]), 196)
        self.assertTrue(all("time_role" not in event for trip in original["trips"] for event in trip["events"]))

    def test_first_route_has_official_endpoints_but_no_invented_arrival(self):
        snapshot = profile().snapshot()
        trips = [trip for trip in snapshot["trips"] if trip["route_name"] == "제1노선"]
        self.assertEqual(len(trips), 16)
        for trip in trips:
            first, last = trip["events"]
            inbound = trip["direction"] == "춘천역 방면 (등교)"
            self.assertEqual((first["name"], last["name"]), ("춘천역", "대학교") if inbound else ("대학교", "춘천역"))
            self.assertEqual(first["time_role"], "departure")
            self.assertEqual(last["time_role"], "arrival_unknown")
            self.assertTrue(last["generated_endpoint"])
            self.assertTrue(trip["routing_usable"])
            self.assertEqual(trip["sequence_basis"], "official_route_endpoints")
            self.assertEqual(last["stop_id"], stop_id(trip["route_name"], trip["direction"], last["name"]))
            for key in ("minutes", "time", "line"):
                self.assertIsNone(last[key])
            self.assertTrue(trip["source_endpoint_evidence"].startswith("https://www.hallym.ac.kr/"))

    def test_friday_three_trips_are_explicitly_excluded(self):
        trips = [trip for trip in profile().snapshot()["trips"] if trip["route_name"] == "제1노선"]
        excluded = {trip["events"][0]["time"] for trip in trips if trip["operating_weekdays"] == [0, 1, 2, 3]}
        self.assertEqual(excluded, {"09:15", "10:00", "10:15"})
        self.assertTrue(all(trip["operating_weekdays"] == [0, 1, 2, 3, 4] for trip in trips if trip["events"][0]["time"] not in excluded))

    def test_loops_keep_unknown_time_roles_and_duplicate_visits(self):
        trips = [trip for trip in profile().snapshot()["trips"] if trip["route_name"] == "제4노선"]
        self.assertEqual(trips[1]["events"][-1]["line"], 135)
        self.assertEqual(trips[2]["events"][0]["line"], 136)
        self.assertTrue(all("time_role" not in event for trip in trips for event in trip["events"]))

    def test_2026_calendar_has_term_ranges_election_and_updated_holidays(self):
        actual = profile()
        calendar = actual.calendar()
        self.assertEqual(calendar["weekdays"], [0, 1, 2, 3, 4])
        self.assertEqual((calendar["coverage_start"], calendar["coverage_end"]), ("2026-01-01", "2026-12-31"))
        self.assertEqual(calendar["term_ranges"], [{"start": "2026-03-03", "end": "2026-06-22"}, {"start": "2026-09-01", "end": "2026-12-21"}])
        self.assertEqual(len(calendar["excluded_dates"]), 22)
        for day in ("2026-05-01", "2026-05-25", "2026-06-03", "2026-07-17", "2026-09-24", "2026-09-25", "2026-09-26", "2026-10-05", "2026-10-09"):
            self.assertIn(day, calendar["excluded_dates"])
        self.assertNotIn("2026-09-28", calendar["excluded_dates"])
        snapshot = actual.snapshot()
        self.assertEqual(snapshot["conditions"]["excluded_dates"], calendar["excluded_dates"])
        self.assertIsNone(snapshot["conditions"]["eligibility"])
        self.assertIsNone(snapshot["conditions"]["fare"])
        sources = {source["id"] for source in snapshot["source"]["official_sources"]}
        self.assertTrue({"hallym_shuttle", "hallym_academic_calendar_2026", "kasi_calendar_2026", "mpm_2026_holiday_update", "nec_local_election_2026"} <= sources)

    def test_representatives_use_existing_coordinates_and_keep_location_limitations(self):
        actual = profile()
        snapshot, mappings = actual.snapshot(), actual.stop_mappings()
        mapped_names = {event["name"] for trip in snapshot["trips"] for event in trip["events"] if event["stop_id"] in mappings}
        self.assertEqual(len(mapped_names), 22)
        self.assertEqual(len(mappings), 28)
        city = CsvTransitRepository(CITY_PATH)
        for mapping in mappings.values():
            self.assertTrue(mapping["is_approximate"])
            self.assertFalse(mapping["stop_location_verified"])
            self.assertTrue(mapping["evidence"])
            if "source_stop_id" in mapping:
                raw = city.stop(mapping["source_stop_id"])
                self.assertEqual((mapping["lat"], mapping["lon"]), (float(raw["gpslati"]), float(raw["gpslong"])))
                self.assertEqual(mapping["location_basis"], "place_representative")
            else:
                self.assertEqual((mapping["lat"], mapping["lon"]), (37.88632, 127.73812))
                self.assertEqual(mapping["location_basis"], "building_reference")
        station = mappings[stop_id("제1노선", "춘천역 방면 (등교)", "춘천역")]
        self.assertEqual((station["lat"], station["lon"]), (37.88529, 127.71803))
        self.assertEqual(station["source_stop_id"], "CCB250001357")
        self.assertEqual(station["alternative_source_stop_ids"], ["CCB250026552"])

    def test_university_labels_distinguish_official_stop_and_approved_reference(self):
        mappings = profile().stop_mappings()
        first = mappings[stop_id("제1노선", "춘천역 방면 (등교)", "대학교")]
        loop = mappings[stop_id("제2노선", "온의동/퇴계동/석사동", "대학교")]
        self.assertEqual(first["official_stop_name"], "CLC(희망터) 앞")
        self.assertEqual(loop["official_stop_name"], "대학교(교내 대표점: CLC)")
        self.assertIn("동일 승강장", loop["evidence"])

    def test_nine_unresolved_names_are_not_assigned_similar_places(self):
        actual = profile()
        snapshot, mappings = actual.snapshot(), actual.stop_mappings()
        excluded = {"한국교직원공제회", "CGV춘천 횡단보도 앞", "칠전동(영화루 맞은편)", "교원공제 회관", "퇴계 주공 5단지 505동 앞", "세경4차A 정문", "이마트24(우석초교 건너)", "석사진흥A 후문", "써브웨이 춘천후평점"}
        names = {event["name"] for trip in snapshot["trips"] for event in trip["events"]}
        self.assertTrue(excluded <= names)
        self.assertTrue(all(event["stop_id"] not in mappings for trip in snapshot["trips"] for event in trip["events"] if event["name"] in excluded))


class ShuttleProfileValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "private-profile.json"
        self.original = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))

    def write(self, value):
        self.path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return profile(profile_path=self.path)

    def assert_safe_error(self, actual):
        with self.assertRaises(ShuttleDataError) as raised:
            actual.snapshot()
        self.assertEqual(raised.exception.code, "SHUTTLE_DATA")
        self.assertNotIn(str(self.path), str(raised.exception))
        self.assertNotIn("private", str(raised.exception))

    def test_load_is_lazy_and_missing_or_invalid_profile_is_safe(self):
        actual = profile(profile_path=self.path)
        self.assert_safe_error(actual)
        self.path.write_text("private invalid JSON", encoding="utf-8")
        self.assert_safe_error(profile(profile_path=self.path))
        for value in ([], {}, {"schema_version": True}, {**self.original, "checked_on": "bad"}):
            with self.subTest(value_type=type(value).__name__):
                self.assert_safe_error(self.write(value))

    def test_missing_original_or_changed_source_hash_cannot_apply_old_rules(self):
        self.assert_safe_error(profile(repository=ShuttleRepository(Path(self.directory.name) / "missing.csv")))
        for key, value in (("sha256", "0" * 64), ("row_count", 195), ("filename", "different.csv")):
            with self.subTest(key=key):
                candidate = deepcopy(self.original)
                candidate["raw_source"][key] = value
                self.assert_safe_error(self.write(candidate))

    def test_bad_dates_ranges_weekdays_and_references_are_safe(self):
        cases = []
        for key, value in (("weekdays", [True]), ("excluded_dates", ["2026-02-30"]), ("excluded_dates", ["2027-01-01"]), ("term_ranges", [{"start": "2026-06-22", "end": "2026-03-03"}])):
            candidate = deepcopy(self.original)
            candidate["calendar"][key] = value
            cases.append(candidate)
        candidate = deepcopy(self.original)
        candidate["stop_references"]["춘천역"] = "unknown-reference"
        cases.append(candidate)
        candidate = deepcopy(self.original)
        candidate["representatives"]["chuncheon_station"]["source_stop_id"] = "different-prefix"
        cases.append(candidate)
        for candidate in cases:
            self.assert_safe_error(self.write(candidate))

    def test_city_failure_does_not_block_enriched_timetable_or_campus_mapping(self):
        city = Mock()
        city.stop.side_effect = PublicDataError("CSV_DATA", "private-city-path")
        actual = profile(city=city)
        before = actual.snapshot()
        calendar = actual.calendar()
        city.stop.assert_not_called()
        mappings = actual.stop_mappings()
        self.assertEqual(len(mappings), 5)
        self.assertTrue(all(mapping["location_basis"] == "building_reference" for mapping in mappings.values()))
        self.assertEqual(city.stop.call_count, 1)
        after = actual.snapshot()
        self.assertEqual(after["source"], before["source"])
        self.assertEqual(calendar, actual.calendar())
        self.assertTrue(any(row["code"] == "CITY_COORDINATES_UNAVAILABLE" for row in after["diagnostics"]))
        self.assertNotIn("private-city-path", str(after))
        actual.stop_mappings()
        self.assertEqual(city.stop.call_count, 1)

    def test_mismatched_name_or_missing_id_is_excluded_without_alternate_guess(self):
        city = CsvTransitRepository(CITY_PATH)
        for altered in ({}, {**city.stop("CCB250001357"), "nodenm": "다른 역"}, {**city.stop("CCB250001357"), "gpslati": float("nan")}):
            with self.subTest(altered=bool(altered)):
                fake = Mock()
                fake.stop.side_effect = lambda value: altered if value == "CCB250001357" else city.stop(value)
                actual = profile(city=fake)
                mappings = actual.stop_mappings()
                self.assertNotIn(stop_id("제1노선", "춘천역 방면 (등교)", "춘천역"), mappings)
                self.assertIn(stop_id("제1노선", "춘천역 방면 (등교)", "대학교"), mappings)
                self.assertTrue(any(row["code"] == "SOURCE_STOP_UNMATCHED" for row in actual.snapshot()["diagnostics"]))
                self.assertNotIn("CCB250026552", [call.args[0] for call in fake.stop.call_args_list])

    def test_missing_or_ambiguous_campus_does_not_invent_clc_coordinate(self):
        missing = Path(self.directory.name) / "missing-campus.csv"
        actual = profile(campus_path=missing)
        mappings = actual.stop_mappings()
        self.assertTrue(mappings)
        self.assertTrue(all(mapping["location_basis"] == "place_representative" for mapping in mappings.values()))
        self.assertTrue(any(row["code"] == "CAMPUS_COORDINATES_UNAVAILABLE" for row in actual.snapshot()["diagnostics"]))
        missing.write_text("학교,건물명,위도(lat),경도(lon)\n한림대학교,Campus Life Center,37.8,127.7\n한림대학교,Campus Life Center,37.9,127.8\n", encoding="utf-8")
        actual = profile(campus_path=missing)
        self.assertTrue(all(mapping["location_basis"] == "place_representative" for mapping in actual.stop_mappings().values()))

    def test_cache_and_returned_data_cannot_mutate_source_or_profile(self):
        actual = self.write(self.original)
        snapshot, calendar, mappings = actual.snapshot(), actual.calendar(), actual.stop_mappings()
        snapshot["trips"][0]["events"].clear()
        calendar["weekdays"].clear()
        first_key = next(iter(mappings))
        mappings[first_key]["lat"] = -1
        self.path.unlink()
        self.assertEqual(len(actual.snapshot()["trips"][0]["events"]), 2)
        self.assertEqual(actual.calendar()["weekdays"], [0, 1, 2, 3, 4])
        self.assertNotEqual(actual.stop_mappings()[first_key]["lat"], -1)
        self.assertEqual(sha256(RAW_PATH.read_bytes()).hexdigest(), RAW_SHA)


if __name__ == "__main__":
    unittest.main()
