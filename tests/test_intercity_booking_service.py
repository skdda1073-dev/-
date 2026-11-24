"""선택 가능한 목적지와 같은 제공처 시간표의 연결을 합성 자료로 검증한다."""

from copy import deepcopy
from datetime import datetime, timedelta
import unittest
from unittest.mock import Mock

from flask import Flask

from services.intercity_booking_service import BookingIntercityService
from services.intercity_client import IntercityError, KST
from services.intercity_routes import create_intercity_blueprint


NOW = datetime(2030, 10, 21, 12, tzinfo=KST)
PROFILE = {
    "origin": {"source_id": "1000", "name": "테스트 출발", "city": "강원"},
    "terminals": [
        {"source_id": "0001", "source_name": "동명", "city": "서울", "display_name": "동명(직통)", "kind": "terminal"},
        {"source_id": "0002", "source_name": "동명", "city": "서울", "display_name": "동명(경유)", "kind": "terminal"},
        {"source_id": "0003", "source_name": "테스트공항", "city": "인천", "display_name": "테스트공항 T1", "kind": "airport"},
        {"source_id": "0099", "source_name": "연결 없는 시설", "city": "강원", "display_name": "연결 없는 시설", "kind": "terminal"},
    ],
}
ORIGINS = [
    {"source_id": "1000", "name": "테스트 출발", "city": "강원"},
    {"source_id": "0001", "name": "동명", "city": "서울"},
]
DESTINATIONS = [
    {"source_id": "0001", "name": "동명", "city": "서울", "booking_mode": "on_site"},
    {"source_id": "0002", "name": "동명", "city": "서울", "booking_mode": "one_way"},
    {"source_id": "0003", "name": "테스트공항", "city": "인천", "booking_mode": "unknown"},
    {"source_id": "0004", "name": "합성 정류장", "city": "강원", "booking_mode": "on_site"},
    {"source_id": "0005", "name": "이름만터미널", "city": "강원", "booking_mode": "one_way"},
    ORIGINS[0],
]


def trip(**changes):
    row = {"sterCode": "1000", "eterCode": "0001", "DEP_DATE": "20301021", "DEP_TIME": "0900",
           "TOT_SEAT_CNT": "30", "DIST_TIME": 70, "FARE0": 9000, "BUS_TYPE_NM": "일반",
           "BUS_ROUTE_ID": "SYNTHETIC", "TRANSP_BIZR_ABBR_NM": "테스트 운수"}
    row.update(changes)
    return row


def service_with(destinations=None, rows=None, now=None, profile=None):
    destinations = deepcopy(DESTINATIONS if destinations is None else destinations)
    source = Mock()
    source.terminals.return_value = {"terminals": deepcopy(ORIGINS), "fetched_at": NOW.isoformat(), "from_cache": False}
    source.destinations.return_value = {"destinations": destinations, "fetched_at": NOW.isoformat(), "from_cache": False}
    timetable = Mock()
    timetable.read.return_value = {"rows": deepcopy([trip()] if rows is None else rows),
                                   "fetched_at": NOW.isoformat(), "from_cache": False}
    service = BookingIntercityService(source, timetable, now=now or (lambda: NOW),
                                      profile=PROFILE if profile is None else profile)
    return service, source, timetable


def client_for(service):
    app = Flask(__name__)
    app.register_blueprint(create_intercity_blueprint(service))
    return app.test_client()


class BookingIntercityServiceTests(unittest.TestCase):
    def test_bad_profile_kind_is_a_controlled_error(self):
        for kind in ([], {}, None, "stop"):
            profile = deepcopy(PROFILE)
            profile["terminals"][0]["kind"] = kind
            service, _, _ = service_with(profile=profile)
            response = client_for(service).get("/api/intercity/destinations?departure_id=BUSTAGO_1000")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json["code"], "INTERCITY_TERMINAL_PROFILE")

    def test_same_provider_ids_need_no_key_or_name_crosswalk(self):
        service, source, schedule = service_with()
        self.assertTrue(service.configuration()["configured"])
        origins = service.terminals("테스트")["terminals"]
        self.assertEqual([row["id"] for row in origins], ["BUSTAGO_1000"])
        result = service.destinations("BUSTAGO_1000")
        self.assertEqual(result["status"], "ok")
        self.assertEqual({row["id"] for row in result["destinations"]}, {"BUSTAGO_0001", "BUSTAGO_0002", "BUSTAGO_0003"})
        source.destinations.assert_called_once_with("1000")
        schedule.read.assert_not_called()
        for row in result["destinations"] + result["additional_stops"]:
            self.assertEqual(set(row), {"id", "name", "city"})
            self.assertTrue(row["id"])

    def test_reviewed_facilities_intersect_live_links_and_other_stops_are_separate(self):
        service, _, _ = service_with()
        result = service.destinations("BUSTAGO_1000")
        self.assertEqual({row["id"] for row in result["additional_stops"]}, {"BUSTAGO_0004", "BUSTAGO_0005"})
        self.assertEqual(result["display_scope"], "terminals")
        self.assertEqual(result["total_count"], 3)
        self.assertEqual(result["additional_count"], 2)
        all_ids = {row["id"] for row in result["destinations"] + result["additional_stops"]}
        self.assertNotIn("BUSTAGO_0099", all_ids)
        self.assertNotIn("BUSTAGO_1000", all_ids)
        # No suffix test, booking-mode filter, ambiguous merging or disabled rows.
        self.assertIn("동명(직통)", [row["name"] for row in result["destinations"]])
        self.assertIn("동명(경유)", [row["name"] for row in result["destinations"]])

    def test_renamed_or_moved_id_cannot_inherit_old_facility_label(self):
        for changes in ({"name": "새로운 장소"}, {"city": "부산"}):
            rows = deepcopy(DESTINATIONS)
            rows[0].update(changes)
            service, _, _ = service_with(destinations=rows)
            result = service.destinations("BUSTAGO_1000")
            self.assertNotIn("BUSTAGO_0001", {row["id"] for row in result["destinations"]})
            self.assertIn("BUSTAGO_0001", {row["id"] for row in result["additional_stops"]})

    def test_changed_origin_identity_does_not_show_its_old_profile_as_verified(self):
        service, source, _ = service_with()
        source.terminals.return_value["terminals"][0]["name"] = "다른 출발지"
        result = service.destinations("BUSTAGO_1000")
        self.assertEqual(result["display_scope"], "destinations")
        self.assertEqual(result["additional_stops"], [])
        self.assertNotIn("동명(직통)", [row["name"] for row in result["destinations"]])

    def test_other_origins_keep_their_actual_connected_destinations(self):
        service, source, _ = service_with(destinations=[ORIGINS[0], DESTINATIONS[3]])
        result = service.destinations("BUSTAGO_0001")
        self.assertEqual(result["display_scope"], "destinations")
        self.assertEqual({row["id"] for row in result["destinations"]}, {"BUSTAGO_1000", "BUSTAGO_0004"})
        self.assertEqual(result["additional_stops"], [])
        source.destinations.assert_called_once_with("0001")

    def test_every_offered_destination_queries_its_exact_original_pair(self):
        service, _, schedule = service_with()
        offered = service.destinations("BUSTAGO_1000")
        for row in offered["destinations"] + offered["additional_stops"]:
            with self.subTest(id=row["id"]):
                source_id = row["id"].removeprefix("BUSTAGO_")
                schedule.read.return_value["rows"] = [trip(eterCode=source_id)]
                result = service.timetable("BUSTAGO_1000", row["id"], "2030-10-21")
                schedule.read.assert_called_with("1000", source_id, "20301021")
                self.assertEqual(result["arrival"], row)
                self.assertEqual(result["status"], "ok")

    def test_unrelated_or_invalid_pair_is_rejected_before_schedule_request(self):
        for origin, arrival in (("BUSTAGO_1000", "BUSTAGO_0099"), ("BUSTAGO_8888", "BUSTAGO_0001"),
                                ("BUSTAGO_1000", "BUSTAGO_1000"), ("NAI123", "BUSTAGO_0001"),
                                ("BUSTAGO_1000", "BUSTAGO_bad/id"), (None, "BUSTAGO_0001")):
            service, _, schedule = service_with()
            with self.subTest(pair=(origin, arrival)):
                with self.assertRaises(IntercityError) as raised:
                    service.timetable(origin, arrival)
                self.assertEqual(raised.exception.status_code, 400)
                schedule.read.assert_not_called()

    def test_estimated_arrival_fare_sorting_and_duplicate_handling(self):
        service, _, _ = service_with(rows=[trip(DEP_TIME="2300", DIST_TIME=90), trip(), trip()])
        result = service.timetable("BUSTAGO_1000", "BUSTAGO_0001")
        self.assertEqual(len(result["trips"]), 2)
        self.assertEqual(result["trips"][0]["departure_at"], "2030-10-21T09:00:00+09:00")
        self.assertEqual(result["trips"][1]["arrival_at"], "2030-10-22T00:30:00+09:00")
        self.assertTrue(result["trips"][1]["arrival_estimated"])
        self.assertEqual(result["trips"][0]["fare_krw"], 9000)
        self.assertTrue(any("예상" in value for value in result["warnings"]))

    def test_unknown_duration_and_fare_do_not_become_zero_or_free(self):
        for value in (0, -1, True, "NaN", "", {}, None):
            service, _, _ = service_with(rows=[trip(DIST_TIME=value, FARE0=value, BUS_TYPE_NM="")])
            with self.subTest(value=value):
                result = service.timetable("BUSTAGO_1000", "BUSTAGO_0001")
                self.assertEqual(result["status"], "partial")
                item = result["trips"][0]
                self.assertIsNone(item["fare_krw"])
                self.assertIsNone(item["duration_minutes"])
                self.assertIsNone(item["arrival_at"])
                self.assertFalse(item["arrival_estimated"])

    def test_unspecified_time_sentinels_are_not_fake_late_night_buses(self):
        rows = [trip(DEP_TIME=value, TOT_SEAT_CNT="00") for value in ("2358", "2359")]
        service, _, _ = service_with(rows=[*rows, trip(DEP_TIME="2359", TOT_SEAT_CNT="30")])
        result = service.timetable("BUSTAGO_1000", "BUSTAGO_0001")
        self.assertEqual(len(result["trips"]), 1)
        self.assertTrue(result["trips"][0]["departure_at"].endswith("23:59:00+09:00"))
        self.assertEqual(result["status"], "partial")
        service, _, _ = service_with(rows=rows)
        with self.assertRaises(IntercityError) as raised:
            service.timetable("BUSTAGO_1000", "BUSTAGO_0001")
        self.assertEqual(raised.exception.code, "INTERCITY_SCHEDULE_RESPONSE")

    def test_empty_results_and_provider_errors_remain_distinct(self):
        service, _, schedule = service_with(rows=[])
        self.assertEqual(service.timetable("BUSTAGO_1000", "BUSTAGO_0001")["status"], "empty")
        schedule.read.side_effect = IntercityError("INTERCITY_BOOKING_TIMEOUT", "시간표 응답이 늦습니다.", 504)
        response = client_for(service).get("/api/intercity/timetable?departure_id=BUSTAGO_1000&arrival_id=BUSTAGO_0001")
        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json["status"], "error")

    def test_only_today_and_midnight_rollover(self):
        for bad in ("2030-10-20", "2030-10-22", "2030-02-30", "20301021", [], True):
            service, _, schedule = service_with()
            with self.subTest(date=bad):
                with self.assertRaises(IntercityError) as raised:
                    service.timetable("BUSTAGO_1000", "BUSTAGO_0001", bad)
                self.assertEqual(raised.exception.status_code, 400)
                schedule.read.assert_not_called()
        clock = [NOW]
        service, _, schedule = service_with(now=lambda: clock[0])
        def crossing(*args):
            clock[0] += timedelta(days=1)
            return {"rows": [trip()], "fetched_at": NOW.isoformat(), "from_cache": False}
        schedule.read.side_effect = crossing
        with self.assertRaises(IntercityError) as raised:
            service.timetable("BUSTAGO_1000", "BUSTAGO_0001", "2030-10-21")
        self.assertEqual(raised.exception.code, "INTERCITY_TODAY_ONLY")

    def test_http_workflow_uses_all_listed_ids_and_rejects_ambiguous_arguments(self):
        service, _, _ = service_with()
        client = client_for(service)
        config = client.get("/api/intercity/config")
        self.assertTrue(config.json["configured"])
        listing = client.get("/api/intercity/destinations?departure_id=BUSTAGO_1000")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.headers["Cache-Control"], "no-store")
        item = listing.json["destinations"][0]
        schedule = client.get("/api/intercity/timetable", query_string={
            "departure_id": "BUSTAGO_1000", "arrival_id": item["id"], "date": "2030-10-21",
        })
        self.assertEqual(schedule.status_code, 200)
        self.assertEqual(schedule.json["arrival"]["id"], item["id"])
        self.assertEqual(client.get("/api/intercity/destinations?departure_id=BUSTAGO_1000&departure_id=BUSTAGO_0001").status_code, 400)


if __name__ == "__main__":
    unittest.main()
