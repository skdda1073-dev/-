"""셔틀 시간표·GPS 연결의 경계를 검증한다. 좌표·운행일 fixture는 합성 자료다."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock

from services.shuttle_service import SEOUL, ShuttleService
from services.walking_service import WalkingService


POINTS = {
    "A": {"lat": 37.88, "lon": 127.72, "evidence": "합성 fixture A의 검증 좌표"},
    "B": {"lat": 37.88, "lon": 127.73, "evidence": "합성 fixture B의 검증 좌표"},
    "C": {"lat": 37.88, "lon": 127.74, "evidence": "합성 fixture C의 검증 좌표"},
}
CALENDAR = {"weekdays": [0, 1, 2, 3, 4], "excluded_dates": [], "evidence": "합성 fixture의 명시된 평일 운행 조건"}


def at(value):
    return datetime.fromisoformat(value).replace(tzinfo=SEOUL)


def event(stop_id, minutes, name=None):
    return {"stop_id": stop_id, "name": name or stop_id, "minutes": minutes,
            "time": f"{minutes // 60:02d}:{minutes % 60:02d}", "line": 2}


def trip(identifier, events, direction="등교", route="합성노선"):
    return {"id": identifier, "route_name": route, "direction": direction,
            "routing_usable": True, "sequence_basis": "csv_row_order", "events": events}


def snapshot(semantics="departure_arrival", trips=None):
    return {
        "source": {"filename": "synthetic_fixture.csv", "encoding": "utf-8-sig", "sha256": "fixture",
                   "row_count": 8, "route_count": 1, "stop_count": 3, "time_semantics": semantics},
        "conditions": {"weekdays": None, "term": None, "excluded_dates": None, "eligibility": None, "fare": None},
        "diagnostics": [],
        "trips": trips if trips is not None else [
            trip("morning-1", [event("A", 480), event("B", 490), event("C", 500)]),
            trip("morning-2", [event("A", 510), event("B", 520), event("C", 530)]),
            trip("return", [event("C", 540), event("B", 550), event("A", 560)], direction="하교"),
        ],
    }


def position(stop_id, offset=0):
    point = POINTS[stop_id]
    return {"name": stop_id, "lat": point["lat"] + offset, "lon": point["lon"]}


class ShuttleServiceTests(unittest.TestCase):
    def service(self, data=None, walking=None, mappings=None, calendar=CALENDAR, **kwargs):
        repository = Mock()
        repository.snapshot.return_value = deepcopy(data if data is not None else snapshot())
        walking = walking if walking is not None else (lambda *args: (0, 0))
        return ShuttleService(repository, walking, POINTS if mappings is None else mappings,
                              service_calendar=calendar, **kwargs)

    def plan(self, service, query="2026-10-19T07:50:00", origin=None, destination=None):
        return service.plan(origin or position("A"), destination or position("C"), query_at=at(query))

    def test_access_margin_wait_and_total_have_no_double_count(self):
        service = self.service(walking=lambda *args: (5, 350))
        result = self.plan(service, origin=position("A", 0.00001), destination=position("C", 0.00001))
        journey = result["journeys"][0]
        self.assertEqual(journey["ready_at"], "2026-10-19T07:56:00+09:00")
        self.assertEqual(journey["wait_min"], 4)
        self.assertEqual(journey["ride_min"], 20)
        self.assertEqual(journey["total_min"], 35)  # 5접근 + 1여유 + 4대기 + 20승차 + 5하차보행
        self.assertEqual(journey["walk_min"], 10)
        self.assertFalse(journey["has_live_departure"])
        self.assertIsNone(journey["legs"][0]["arrival_min"])
        self.assertFalse(journey["realtime"])

    def test_walk_can_make_first_scheduled_trip_unusable(self):
        result = self.plan(self.service(walking=lambda *args: (5, 350)), query="2026-10-19T07:55:00")
        self.assertEqual(result["journeys"][0]["trip_id"], "morning-2")
        self.assertTrue(all(item["wait_min"] >= 0 for item in result["journeys"]))

    def test_ready_time_boundary_is_inclusive_at_the_scheduled_stop(self):
        service = self.service()
        cases = [
            ("2026-10-19T07:58:59.999999", "morning-1"),
            ("2026-10-19T07:59:00", "morning-1"),
            ("2026-10-19T07:59:00.000001", "morning-2"),
            ("2026-10-19T08:00:00", "morning-2"),
            ("2026-10-19T08:00:00.000001", "morning-2"),
        ]
        for query, expected in cases:
            with self.subTest(query=query):
                self.assertEqual(self.plan(service, query)["journeys"][0]["trip_id"], expected)

    def test_zero_configured_margin_allows_exact_time_and_excludes_after(self):
        service = self.service(boarding_margin_min=0)
        exact = self.plan(service, "2026-10-19T08:00:00")["journeys"][0]
        self.assertEqual(exact["trip_id"], "morning-1")
        self.assertEqual(exact["wait_min"], 0)
        self.assertEqual(self.plan(service, "2026-10-19T08:00:00.000001")["journeys"][0]["trip_id"], "morning-2")

    def test_last_trip_and_unknown_calendar_do_not_roll_forward(self):
        result = self.plan(self.service(calendar=None), "2026-10-19T23:59:59")
        self.assertEqual(result["status"], "no_departures_remaining")
        self.assertEqual(result["calendar_status"], "unknown")
        self.assertEqual(result["journeys"], [])
        self.assertNotIn("next_service_date", result)

    def test_unknown_calendar_is_reference_only_and_preserves_unknown_conditions(self):
        result = self.plan(self.service(calendar=None))
        self.assertEqual(result["status"], "unknown_calendar")
        journey = result["journeys"][0]
        self.assertFalse(journey["service_date_confirmed"])
        self.assertFalse(journey["availability_confirmed"])
        self.assertIsNone(journey["total_min"])
        self.assertEqual(journey["total_unavailable_reason"], "service_calendar_unknown")
        for key in ("weekdays", "term", "excluded_dates", "eligibility", "fare"):
            self.assertIsNone(result["conditions"][key])

    def test_unspecified_time_is_never_promoted_to_departure_arrival_or_ride(self):
        result = self.plan(self.service(snapshot(semantics="unspecified")))
        journey = result["journeys"][0]
        self.assertEqual(journey["scheduled_stop_at"], "2026-10-19T08:00:00+09:00")
        self.assertEqual(journey["scheduled_segment_min"], 20)
        self.assertEqual(journey["wait_min"], 9)
        self.assertFalse(journey["known_min_complete"])
        for item in (journey, journey["legs"][0]):
            for key in ("scheduled_departure_at", "scheduled_arrival_at", "ride_min"):
                self.assertIsNone(item[key])
        self.assertIsNone(journey["total_min"])
        self.assertIsNone(journey["legs"][0]["arrival_min"])
        self.assertIn("time_semantics_unknown", journey["total_unavailable_reason"])

    def test_known_calendar_applies_weekday_and_explicit_holiday(self):
        walking = Mock(return_value=(0, 0))
        holiday = {**CALENDAR, "excluded_dates": ["2026-10-19"]}
        for service, query in ((self.service(walking=walking), "2026-10-18T07:00:00"),
                               (self.service(walking=walking, calendar=holiday), "2026-10-19T07:00:00")):
            with self.subTest(query=query):
                result = self.plan(service, query)
                self.assertEqual(result["status"], "not_operating")
                self.assertEqual(result["journeys"], [])
        walking.assert_not_called()

    def test_calendar_applies_semester_bounds_without_extrapolating_another_year(self):
        calendar = {**CALENDAR, "coverage_start": "2026-01-01", "coverage_end": "2026-12-31",
                    "term_ranges": [{"start": "2026-09-01", "end": "2026-12-21"}]}
        service = self.service(calendar=calendar)
        self.assertEqual(self.plan(service, "2026-09-01T07:50:00")["status"], "ok")
        for query in ("2026-08-31T07:50:00", "2026-12-22T07:50:00"):
            with self.subTest(query=query):
                self.assertEqual(self.plan(service, query)["status"], "not_operating")
        outside = self.plan(service, "2027-10-19T07:50:00")
        self.assertEqual(outside["status"], "unknown_calendar")
        self.assertEqual(outside["calendar_status"], "unknown")
        self.assertFalse(outside["journeys"][0]["service_date_confirmed"])
        self.assertIsNone(outside["journeys"][0]["total_min"])

    def test_reference_point_metadata_keeps_zero_walk_distinct_from_verified_stop(self):
        mappings = {key: {**value, "is_approximate": True, "stop_location_verified": False,
                          "location_basis": "place_representative", "reference_name": key,
                          "official_stop_name": key + " 앞"} for key, value in POINTS.items()}
        journey = self.plan(self.service(mappings=mappings))["journeys"][0]
        self.assertEqual(journey["access_walk"]["minutes"], 0)
        self.assertTrue(journey["access_walk"]["is_approximate"])
        self.assertFalse(journey["access_walk"]["stop_location_verified"])
        self.assertEqual(journey["egress_walk"]["reference_name"], "C")
        self.assertTrue(journey["location_is_approximate"])
        self.assertEqual(journey["path_basis"], "reference_points")
        self.assertIsNone(journey["total_min"])
        self.assertEqual(journey["total_unavailable_reason"], "reference_location_approximate")

    def test_explicit_extended_hour_preserves_service_date_across_midnight(self):
        data = snapshot(trips=[trip("night", [event("A", 1450), event("C", 1470)])])
        service = self.service(data, calendar={**CALENDAR, "weekdays": [0]})
        result = self.plan(service, "2026-10-20T00:00:00")
        journey = result["journeys"][0]
        self.assertEqual(journey["service_date"], "2026-10-19")
        self.assertEqual(journey["scheduled_stop_at"], "2026-10-20T00:10:00+09:00")
        self.assertTrue(journey["service_date_confirmed"])
        self.assertEqual(journey["total_min"], 30)

    def test_decreasing_time_is_not_assumed_to_cross_midnight(self):
        malformed = trip("invalid", [event("A", 1435), event("C", 5)])
        data = snapshot(trips=[malformed])
        result = self.plan(self.service(data), "2026-10-19T23:50:00")
        self.assertEqual(result["status"], "no_connection")
        self.assertEqual(result["journeys"], [])

    def test_same_trip_forward_order_is_required(self):
        data = snapshot(trips=[trip("one-way", [event("A", 480), event("B", 490), event("C", 500)])])
        service = self.service(data)
        self.assertTrue(self.plan(service)["journeys"])
        reverse = self.plan(service, origin=position("C"), destination=position("A"))
        self.assertEqual(reverse["status"], "no_connection")

    def test_stop_pairs_are_never_connected_across_trips(self):
        data = snapshot(trips=[trip("first", [event("A", 480), event("B", 490)]),
                               trip("second", [event("B", 510), event("C", 520)])])
        self.assertEqual(self.plan(self.service(data))["status"], "no_connection")

    def test_return_trip_is_kept_in_its_declared_direction(self):
        result = self.plan(self.service(), origin=position("C"), destination=position("A"))
        self.assertTrue(result["journeys"])
        self.assertTrue(all(item["direction"] == "하교" for item in result["journeys"]))
        self.assertTrue(all(item["trip_id"] == "return" for item in result["journeys"]))

    def test_unverified_mapping_and_same_name_mapping_are_not_accepted(self):
        mappings = {"A": {"lat": 37.88, "lon": 127.72}, "C": POINTS["C"], "A와 비슷한 이름": POINTS["A"]}
        result = self.plan(self.service(mappings=mappings))
        self.assertEqual(result["status"], "unmatched")
        self.assertIn("A", result["unmatched_stop_ids"])
        self.assertTrue(any(item["code"] == "invalid_stop_mapping" for item in result["diagnostics"]))

    def test_missing_intermediate_coordinates_does_not_create_fake_geometry(self):
        result = self.plan(self.service(mappings={"A": POINTS["A"], "C": POINTS["C"]}))
        journey = result["journeys"][0]
        self.assertEqual(journey["legs"][0]["path"], [])
        self.assertEqual(journey["legs"][0]["stop_count"], 2)
        self.assertTrue(any("중간 정류장" in warning for warning in journey["warnings"]))

    def test_walking_failures_are_excluded_and_never_zero_filled(self):
        for response in ((-1, -1), (float("nan"), 10), (1, float("inf"))):
            with self.subTest(response=response):
                result = self.plan(self.service(walking=lambda *args: response))
                self.assertEqual(result["status"], "walking_unavailable")
                self.assertEqual(result["journeys"], [])
        result = self.plan(self.service(walking=Mock(side_effect=RuntimeError("private raw response"))))
        self.assertEqual(result["status"], "walking_unavailable")
        self.assertNotIn("private raw response", str(result))

    def test_exact_stop_walks_reuse_existing_service_without_api_keys(self):
        requester = Mock(side_effect=AssertionError("외부 API 호출 금지"))
        result = self.plan(self.service(walking=WalkingService("", requester).get_time))
        self.assertTrue(result["journeys"])
        self.assertEqual(result["journeys"][0]["walk_min"], 0)
        requester.assert_not_called()

    def test_clock_utc_and_naive_inputs_are_explicitly_seoul_based(self):
        utc = datetime(2026, 10, 18, 22, 50, tzinfo=timezone.utc)
        service = self.service(clock=lambda: utc)
        from_clock = service.plan(position("A"), position("C"))
        self.assertEqual(from_clock["query_at"], "2026-10-19T07:50:00+09:00")
        naive = service.plan(position("A"), position("C"), query_at=datetime(2026, 10, 19, 7, 50))
        self.assertEqual(naive["journeys"], from_clock["journeys"])

    def test_current_query_rechecks_clock_after_slow_walking_response(self):
        clock = {"now": at("2026-10-19T07:58:00")}

        def slow_walk(*args):
            clock["now"] += timedelta(minutes=2)
            return 0, 0

        service = self.service(walking=slow_walk, clock=lambda: clock["now"])
        journey = service.plan(position("A"), position("C"))["journeys"][0]
        self.assertEqual(journey["trip_id"], "morning-2")
        self.assertEqual(journey["query_at"], "2026-10-19T08:02:00+09:00")
        self.assertEqual(journey["ready_at"], "2026-10-19T08:03:00+09:00")

    def test_explicit_query_time_remains_fixed_even_when_walking_is_slow(self):
        clock = {"now": at("2026-10-19T07:58:00")}

        def slow_walk(*args):
            clock["now"] += timedelta(minutes=2)
            return 0, 0

        service = self.service(walking=slow_walk, clock=lambda: clock["now"])
        journey = service.plan(position("A"), position("C"), query_at=at("2026-10-19T07:58:00"))["journeys"][0]
        self.assertEqual(journey["trip_id"], "morning-1")
        self.assertEqual(journey["query_at"], "2026-10-19T07:58:00+09:00")

    def test_later_candidate_walking_delay_rechecks_previously_selected_departures(self):
        clock = {"now": at("2026-10-19T07:59:00"), "calls": 0}
        mappings = {**POINTS, "B": {**POINTS["B"], "lat": 37.8802, "lon": 127.72}}
        data = snapshot(trips=[trip("early", [event("A", 485), event("C", 505)]),
                               trip("later", [event("B", 510), event("C", 530)])])

        def slow_third_walk(*args):
            clock["calls"] += 1
            if clock["calls"] == 3:
                clock["now"] += timedelta(minutes=5)
            return 2, 100

        service = self.service(data, mappings=mappings, walking=slow_third_walk, clock=lambda: clock["now"])
        journeys = service.plan(position("A"), position("C"))["journeys"]
        self.assertEqual([row["trip_id"] for row in journeys], ["later"])
        self.assertEqual(journeys[0]["query_at"], "2026-10-19T08:04:00+09:00")
        self.assertEqual(journeys[0]["ready_at"], "2026-10-19T08:07:00+09:00")
        self.assertEqual(journeys[0]["wait_min"], 23)

    def test_timetable_supports_exact_name_or_id_with_no_walk_assumption(self):
        walking = Mock(side_effect=AssertionError("시간표 조회에 도보를 호출하지 않습니다."))
        service = self.service(walking=walking)
        result = service.timetable(route="합성노선", direction="등교", board="A", alight="C", query_at=at("2026-10-19T08:00:00"))
        self.assertEqual(len(result["timetable_candidates"]), 2)
        self.assertEqual(result["timetable_candidates"][0]["trip_id"], "morning-1")
        self.assertFalse(result["timetable_candidates"][0]["availability_confirmed"])
        self.assertEqual(result["timetable_candidates"][0]["selection_basis"], "timetable_only_without_access_walk")
        walking.assert_not_called()
        self.assertEqual(len(result["trips"]), 2)
        self.assertEqual({item["id"] for item in result["stops"]}, {"A", "B", "C"})

    def test_ambiguous_stop_name_requires_route_or_exact_id(self):
        data = snapshot(trips=[trip("first", [event("A", 480, "공통"), event("C", 500)], route="첫노선"),
                               trip("second", [event("X", 480, "공통"), event("Y", 500)], route="둘째노선")])
        service = self.service(data)
        kwargs = {"board": "공통", "alight": "C", "query_at": at("2026-10-19T07:00:00")}
        self.assertEqual(service.timetable(**kwargs)["status"], "ambiguous_stop")
        self.assertEqual(len(service.timetable(route="첫노선", **kwargs)["timetable_candidates"]), 1)
        kwargs["board"] = "A"
        self.assertEqual(len(service.timetable(**kwargs)["timetable_candidates"]), 1)

    def test_timetable_rejects_backwards_unmatched_and_future_day_guess(self):
        service = self.service(calendar=None)
        query = at("2026-10-19T07:00:00")
        self.assertEqual(service.timetable(direction="등교", board="C", alight="A", query_at=query)["status"], "no_connection")
        self.assertEqual(service.timetable(board="미등록", alight="C", query_at=query)["status"], "unmatched")
        ended = service.timetable(direction="등교", board="A", alight="C", query_at=query + timedelta(hours=16, minutes=59))
        self.assertEqual(ended["status"], "no_departures_remaining")
        self.assertEqual(ended["timetable_candidates"], [])

    def test_data_diagnostics_are_preserved_without_losing_good_trip(self):
        data = snapshot()
        data["diagnostics"] = [{"line": 9, "code": "invalid_time", "message": "시간 오류"}]
        result = self.plan(self.service(data))
        self.assertTrue(result["journeys"])
        self.assertEqual(result["diagnostics"], data["diagnostics"])

    def test_calendar_without_evidence_and_negative_margin_are_rejected(self):
        for kwargs in ({"calendar": {"weekdays": [0]}}, {"boarding_margin_min": -1}, {"boarding_margin_min": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.service(**kwargs)


class ActualShuttleServiceTests(unittest.TestCase):
    def test_actual_file_can_be_queried_without_coordinates_or_external_calls(self):
        from services.shuttle_repository import ShuttleRepository
        path = Path(__file__).resolve().parents[1] / "한림대_셔틀버스.csv"
        repository = ShuttleRepository(path)
        walking = Mock(side_effect=AssertionError("원본 좌표 미매칭에서는 외부 API 호출 금지"))
        service = ShuttleService(repository, walking)
        result = service.timetable(
            route="제2노선", board="온의금호1차 버스정류장", alight="양우A 건너 버스정류장",
            query_at=at("2026-10-19T07:50:00"),
        )
        self.assertEqual(result["status"], "unknown_calendar")
        self.assertTrue(result["timetable_candidates"])
        candidate = result["timetable_candidates"][0]
        self.assertIsNone(candidate["scheduled_departure_at"])
        self.assertIsNone(candidate["scheduled_arrival_at"])
        self.assertFalse(candidate["service_date_confirmed"])
        gps = service.plan(position("A"), position("C"), query_at=at("2026-10-19T07:50:00"))
        self.assertEqual(gps["status"], "unmatched")
        self.assertEqual(gps["journeys"], [])
        walking.assert_not_called()


if __name__ == "__main__":
    unittest.main()
