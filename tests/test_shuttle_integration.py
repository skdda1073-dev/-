"""실제 셔틀 자료·Flask 계약·시내/셔틀 실패 격리를 네트워크 없이 검증한다."""

from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import app as application
from services.public_transit_client import PublicDataError, PublicTransitClient
from services.route_planner import plan_journeys
from services.shuttle_repository import ShuttleDataError, ShuttleRepository
from services.shuttle_service import ShuttleService
from services.transit_service import TransitService
from services.walking_service import WalkingService


DATA_PATH = Path(__file__).resolve().parents[1] / "한림대_셔틀버스.csv"
NOW = datetime.fromisoformat("2026-10-19T07:50:00+09:00")
ORIGIN = {"name": "출발", "lat": 37.88, "lon": 127.73}
DESTINATION = {"name": "도착", "lat": 37.89, "lon": 127.74}


class ShuttleApiTests(unittest.TestCase):
    def setUp(self):
        self.client = application.app.test_client()
        self.walk = Mock(side_effect=AssertionError("시간표 조회에서 외부 도보 호출 금지"))
        self.shuttle = ShuttleService(ShuttleRepository(DATA_PATH), self.walk, clock=lambda: NOW)
        self.service_patch = patch.object(application, "shuttle_service", self.shuttle)
        self.service_patch.start()
        self.addCleanup(self.service_patch.stop)

    def query(self, **updates):
        return self.client.get("/shuttle_timetable", query_string={
            "route": "제2노선", "board": "온의금호1차 버스정류장",
            "alight": "양우A 건너 버스정류장", "at": NOW.isoformat(), **updates,
        })

    def test_actual_file_api_returns_forward_timetable_without_external_calls(self):
        response = self.query()
        self.assertEqual(response.status_code, 200)
        body = response.json
        self.assertEqual(body["mode"], "shuttle_timetable")
        self.assertEqual(body["source"]["row_count"], 196)
        self.assertEqual(body["source"]["sha256"], "7755f5982aa671785e59ea5b528b12048bcb9210b715ba3e4dc13d0f18d9f03d")
        self.assertTrue(body["timetable_candidates"])
        candidate = body["timetable_candidates"][0]
        self.assertEqual(candidate["scheduled_stop_at"], "2026-10-19T07:58:00+09:00")
        self.assertEqual(candidate["scheduled_segment_min"], 27)
        self.assertFalse(candidate["service_date_confirmed"])
        self.assertIsNone(candidate["scheduled_departure_at"])
        self.assertIsNone(candidate["total_min"])
        self.walk.assert_not_called()

    def test_reverse_and_unlisted_stops_do_not_create_connections(self):
        reverse = self.query(board="양우A 건너 버스정류장", alight="온의금호1차 버스정류장")
        self.assertEqual(reverse.status_code, 200)
        self.assertEqual(reverse.json["timetable_candidates"], [])
        missing = self.query(board="존재하지 않는 셔틀 정류장")
        self.assertEqual(missing.status_code, 200)
        self.assertEqual(missing.json["timetable_candidates"], [])

    def test_first_route_does_not_invent_unlisted_destination(self):
        response = self.query(route="제1노선", direction="춘천역 방면 (등교)", board="춘천역", alight="대학교")
        self.assertEqual(response.json["timetable_candidates"], [])

    def test_same_instant_in_utc_and_seoul_selects_same_departure(self):
        seoul = self.query().json["timetable_candidates"]
        utc = self.query(at="2026-10-18T22:50:00+00:00").json["timetable_candidates"]
        self.assertEqual(seoul, utc)

    def test_api_rejects_naive_or_invalid_dates(self):
        for value in ("2026-10-19T07:50:00", "not-a-time", "2026-02-30T12:00:00+09:00",
                      "9999-12-31T23:59:00-01:00", ""):
            with self.subTest(value=value):
                response = self.query(at=value)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json["code"], "INVALID_SHUTTLE_QUERY")

    def test_local_data_failure_is_safe_and_city_api_is_independent(self):
        with patch.object(self.shuttle, "timetable", side_effect=ShuttleDataError("내부 파일 경로")):
            response = self.query()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json["code"], "SHUTTLE_DATA")
        self.assertNotIn("내부 파일 경로", response.get_data(as_text=True))

    def test_real_file_has_no_verified_gps_route_without_mapping(self):
        result = self.shuttle.plan(ORIGIN, DESTINATION)
        self.assertEqual(result["journeys"], [])
        self.assertEqual(result["status"], "unmatched")
        self.walk.assert_not_called()

    def test_actual_csv_flask_candidate_with_explicit_test_mapping_survives_city_failure(self):
        # 좌표는 이 테스트에만 주입하는 합성 위치다. 실제 승강장 자료가 아니다.
        trip = next(row for row in self.shuttle.repository.snapshot()["trips"] if row["route_name"] == "제2노선")
        board, alight = trip["events"][1], trip["events"][-2]
        mappings = {
            board["stop_id"]: {"lat": ORIGIN["lat"], "lon": ORIGIN["lon"], "evidence": "테스트 전용 합성 연결 A"},
            alight["stop_id"]: {"lat": DESTINATION["lat"], "lon": DESTINATION["lon"], "evidence": "테스트 전용 합성 연결 B"},
        }
        shuttle = ShuttleService(self.shuttle.repository, lambda *args: (2, 100),
                                 mappings, clock=lambda: NOW)
        city = Mock()
        city.nearby_stops.side_effect = PublicDataError("NETWORK", "private external error")
        service = TransitService(city, Mock(), shuttle_service=shuttle)
        with patch.object(application, "transit_service", service):
            response = self.client.post("/get_route_info", json={
                "lat": ORIGIN["lat"], "lon": ORIGIN["lon"],
                "dest_lat": DESTINATION["lat"], "dest_lon": DESTINATION["lon"],
            })
        self.assertEqual(response.status_code, 200)
        journey = response.json["journeys"][0]
        self.assertEqual(journey["transport_type"], "shuttle")
        self.assertEqual(journey["legs"][0]["board"]["name"], "온의금호1차 버스정류장")
        self.assertEqual(journey["legs"][0]["alight"]["name"], "양우A 건너 버스정류장")
        self.assertEqual(journey["scheduled_stop_at"], "2026-10-19T07:58:00+09:00")
        self.assertEqual(journey["ready_at"], "2026-10-19T07:53:00+09:00")
        self.assertEqual(journey["wait_min"], 5)
        self.assertEqual(journey["scheduled_segment_min"], 27)
        self.assertIsNone(journey["ride_min"])
        self.assertIsNone(journey["total_min"])
        self.assertEqual(response.json["providers"]["city_bus"]["code"], "NETWORK")
        self.assertNotIn("private external error", str(response.json))
        city.arrivals.assert_not_called()


class CombinedProviderTests(unittest.TestCase):
    def city_journey(self, index=0):
        return {"total_min": None, "known_min": 10 + index, "legs": [], "transfer_count": 0}

    def shuttle_result(self, **updates):
        return {"status": "unknown_calendar", "warnings": [], "journeys": [{
            "transport_type": "shuttle", "total_min": None, "known_min": 1,
            "legs": [], "transfer_count": 0,
        }], **updates}

    def city_response(self, journeys):
        return {"mode": "journeys", "origin": ORIGIN, "destination": DESTINATION,
                "journeys": journeys, "warnings": [], "source": {"source": "csv"}, "search": {}}

    def service(self):
        shuttle = Mock(spec=ShuttleService)
        shuttle.plan.return_value = self.shuttle_result()
        return TransitService(Mock(), Mock(), shuttle_service=shuttle)

    def test_city_data_failure_does_not_suppress_shuttle_candidate_or_expose_raw_error(self):
        service = self.service()
        with patch.object(service, "_plan_city", side_effect=PublicDataError("CSV_DATA", "private-provider-response")):
            result = service.plan(ORIGIN, DESTINATION)
        self.assertEqual(result["journeys"][0]["transport_type"], "shuttle")
        self.assertEqual(result["providers"]["city_bus"], {"status": "error", "code": "CSV_DATA"})
        self.assertNotIn("error", result)
        self.assertNotIn("private-provider-response", str(result))

    def test_shuttle_failure_does_not_suppress_city_candidates(self):
        service = self.service()
        service.shuttle_service.plan.side_effect = ShuttleDataError("private-shuttle-response")
        with patch.object(service, "_plan_city", return_value=self.city_response([self.city_journey()])):
            result = service.plan(ORIGIN, DESTINATION)
        self.assertEqual(result["journeys"][0]["transport_type"], "city_bus")
        self.assertEqual(result["providers"]["shuttle"]["code"], "SHUTTLE_DATA")
        self.assertNotIn("error", result)
        self.assertNotIn("private-shuttle-response", str(result))

    def test_unexpected_provider_error_is_also_isolated(self):
        service = self.service()
        service.shuttle_service.plan.side_effect = RuntimeError("internal detail")
        with patch.object(service, "_plan_city", return_value=self.city_response([self.city_journey()])):
            result = service.plan(ORIGIN, DESTINATION)
        self.assertEqual(len(result["journeys"]), 1)
        self.assertEqual(result["providers"]["shuttle"]["code"], "SHUTTLE_UNAVAILABLE")
        self.assertNotIn("internal detail", str(result))

    def test_shuttle_is_merged_before_final_limit_and_partial_times_do_not_win(self):
        service = self.service()
        with patch.object(service, "_plan_city", return_value=self.city_response([
            self.city_journey(i) for i in range(5)
        ])) as city:
            result = service.plan(ORIGIN, DESTINATION)
        self.assertIsNone(city.call_args.kwargs["journey_limit"])
        self.assertEqual(len(result["journeys"]), 3)
        self.assertEqual([j["transport_type"] for j in result["journeys"]], ["city_bus", "shuttle", "city_bus"])
        self.assertEqual(result["journeys"][0]["known_min"], 10)

    def test_city_planner_default_three_and_unlimited_integration_option(self):
        board = {"nodeid": "A", "nodenm": "A", "nodeord": 1, "updowncd": 0,
                 "gpslati": ORIGIN["lat"], "gpslong": ORIGIN["lon"]}
        alight = {"nodeid": "B", "nodenm": "B", "nodeord": 2, "updowncd": 0,
                  "gpslati": DESTINATION["lat"], "gpslong": DESTINATION["lon"]}
        routes = {f"r{i}": {"route_no": str(i), "stops": [board, alight]} for i in range(4)}
        args = (ORIGIN, DESTINATION, [board], [alight], routes, {}, lambda *args: (0, 0))
        self.assertEqual(len(plan_journeys(*args, max_transfers=0)), 3)
        self.assertEqual(len(plan_journeys(*args, max_transfers=0, limit=None)), 4)

    def test_city_early_empty_return_does_not_skip_shuttle(self):
        service = self.service()
        service.topology.nearby_stops.return_value = []
        result = service.plan(ORIGIN, DESTINATION)
        service.shuttle_service.plan.assert_called_once_with(ORIGIN, DESTINATION)
        self.assertEqual(len(result["journeys"]), 1)

    def test_failed_city_without_replacement_keeps_502_and_shuttle_diagnostics(self):
        service = self.service()
        service.shuttle_service.plan.return_value = self.shuttle_result(journeys=[], status="unmatched")
        with patch.object(service, "_plan_city", side_effect=PublicDataError("WALK_UNAVAILABLE", "private-detail")), \
                patch.object(application, "transit_service", service):
            response = application.app.test_client().post("/get_route_info", json={
                "lat": ORIGIN["lat"], "lon": ORIGIN["lon"],
                "dest_lat": DESTINATION["lat"], "dest_lon": DESTINATION["lon"],
            })
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json["code"], "WALK_UNAVAILABLE")
        self.assertEqual(response.json["shuttle"]["status"], "unmatched")
        self.assertEqual(response.json["journeys"], [])
        self.assertNotIn("private-detail", str(response.json))

    def test_actual_city_csv_still_works_with_broken_shuttle_and_no_arrival_key(self):
        network = Mock(side_effect=AssertionError("외부 API 호출 금지"))
        client = PublicTransitClient("", "32010", network)
        shuttle = Mock(spec=ShuttleService)
        shuttle.plan.side_effect = ShuttleDataError("unreadable")
        repository = application.transit_repository
        stops = repository.route_stops("CCB250001202")
        point = lambda stop: {"name": stop["nodenm"], "lat": stop["gpslati"], "lon": stop["gpslong"]}
        service = TransitService(client, lambda *args: (2, 100), repository, shuttle_service=shuttle)
        result = service.plan(point(stops[11]), point(stops[18]))
        self.assertTrue(result["journeys"])
        self.assertTrue(all(j["transport_type"] == "city_bus" for j in result["journeys"]))
        self.assertEqual(result["arrival_status"]["code"], "ARRIVAL_KEY_MISSING")
        network.assert_not_called()

    def test_flask_partial_result_keeps_existing_envelope_and_no_top_level_error(self):
        service = self.service()
        with patch.object(service, "_plan_city", side_effect=PublicDataError("WALK_UNAVAILABLE", "unavailable")), \
                patch.object(application, "transit_service", service):
            response = application.app.test_client().post("/get_route_info", json={
                "lat": ORIGIN["lat"], "lon": ORIGIN["lon"],
                "dest_lat": DESTINATION["lat"], "dest_lon": DESTINATION["lon"],
            })
        self.assertEqual(response.status_code, 200)
        for key in ("mode", "origin", "destination", "journeys", "warnings", "source", "search"):
            self.assertIn(key, response.json)
        self.assertNotIn("error", response.json)
        self.assertEqual(response.json["providers"]["city_bus"]["code"], "WALK_UNAVAILABLE")


class OfficialShuttleProfileApiTests(unittest.TestCase):
    """원본 CSV와 실제 공식 보완 프로필을 사용하고 도보·외부 통신만 대체한다."""

    STATION = {"name": "춘천역 대표지점", "lat": 37.88529, "lon": 127.71803}
    CAMPUS = {"name": "CLC 대표지점", "lat": 37.88632, "lon": 127.73812}

    def setUp(self):
        self.client = application.app.test_client()
        self.network_blocks = []
        for target in ("socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex", "socket.getaddrinfo"):
            blocker = patch(target, side_effect=AssertionError("공식 프로필 오프라인 검증에서 외부 통신 금지"))
            self.network_blocks.append(blocker.start())
            self.addCleanup(blocker.stop)
        self.walking = Mock(return_value=(2, 100))
        self.shuttle = ShuttleService(application.shuttle_repository, self.walking,
                                      profile=application.shuttle_profile, clock=lambda: NOW)

    def tearDown(self):
        for blocker in self.network_blocks:
            blocker.assert_not_called()

    def transit(self, shuttle=None, actual_city=True):
        client = PublicTransitClient("", "32010", Mock(side_effect=AssertionError("외부 도착 API 호출 금지")))
        topology = application.transit_repository
        if not actual_city:
            topology = Mock()
            topology.metadata.return_value = {"source": "csv", "route_count": 0, "warnings": []}
            topology.nearby_stops.return_value = []
        return TransitService(client, self.walking, topology, shuttle_service=shuttle or self.shuttle)

    def route(self, origin=None, destination=None, service=None):
        origin, destination = origin or self.STATION, destination or self.CAMPUS
        with patch.object(application, "transit_service", service or self.transit()):
            return self.client.post("/get_route_info", json={
                "location": origin["name"], "lat": origin["lat"], "lon": origin["lon"],
                "dest_name": destination["name"], "dest_lat": destination["lat"], "dest_lon": destination["lon"],
            })

    def timetable(self, at=NOW, **filters):
        with patch.object(application, "shuttle_service", self.shuttle):
            return self.client.get("/shuttle_timetable", query_string={
                "route": "제1노선", "direction": "춘천역 방면 (등교)",
                "board": "춘천역", "alight": "대학교", "at": at.isoformat(), **filters,
            })

    def assert_reference_metadata(self, item, campus=False):
        self.assertEqual(item["location_basis"], "building_reference" if campus else "place_representative")
        self.assertTrue(item["is_approximate"])
        self.assertFalse(item["stop_location_verified"])
        self.assertTrue(item["reference_name"])
        self.assertTrue(item["official_stop_name"])

    def test_production_application_wires_real_profile_lazily_and_keeps_raw_repository(self):
        from services.shuttle_profile import ShuttleProfile
        self.assertIsInstance(application.shuttle_profile, ShuttleProfile)
        self.assertIsInstance(application.shuttle_repository, ShuttleRepository)
        self.assertEqual(application.shuttle_repository.path.resolve(), DATA_PATH.resolve())
        self.assertIs(application.shuttle_service.profile, application.shuttle_profile)
        raw = application.shuttle_repository.snapshot()
        self.assertEqual(raw["source"]["row_count"], 196)
        self.assertEqual(raw["source"]["sha256"], "7755f5982aa671785e59ea5b528b12048bcb9210b715ba3e4dc13d0f18d9f03d")
        self.assertEqual(raw["source"]["time_semantics"], "unspecified")

    def test_actual_profile_flask_returns_first_route_without_fabricated_arrival(self):
        response = self.route()
        self.assertEqual(response.status_code, 200)
        body = response.json
        self.assertNotIn("error", body)
        journeys = [item for item in body["journeys"] if item["transport_type"] == "shuttle"]
        self.assertTrue(journeys)
        journey = journeys[0]
        self.assertEqual(journey["route_name"], "제1노선")
        self.assertEqual(journey["scheduled_departure_at"], "2026-10-19T08:05:00+09:00")
        self.assertEqual(journey["ready_at"], "2026-10-19T07:53:00+09:00")
        self.assertEqual(journey["wait_min"], 12)
        self.assertEqual(journey["legs"][0]["board"]["name"], "춘천역")
        self.assertEqual(journey["legs"][0]["alight"]["name"], "대학교")
        for item in (journey, journey["legs"][0]):
            self.assertIsNone(item["scheduled_arrival_at"])
            self.assertIsNone(item["ride_min"])
        self.assertIsNone(journey["total_min"])
        self.assertFalse(journey["availability_confirmed"])
        self.assertFalse(journey["realtime"])
        self.assertIsNone(journey["legs"][0]["arrival_min"])
        self.assertFalse(journey["has_live_departure"])

    def test_representative_board_alight_and_walk_metadata_survive_api_serialization(self):
        response = self.route(service=self.transit(actual_city=False))
        self.assertEqual(response.status_code, 200)
        journey = response.json["journeys"][0]
        board, alight = journey["legs"][0]["board"], journey["legs"][0]["alight"]
        self.assertEqual((board["lat"], board["lon"]), (self.STATION["lat"], self.STATION["lon"]))
        self.assertEqual((alight["lat"], alight["lon"]), (self.CAMPUS["lat"], self.CAMPUS["lon"]))
        self.assert_reference_metadata(board)
        self.assert_reference_metadata(alight, campus=True)
        self.assert_reference_metadata(journey["access_walk"])
        self.assert_reference_metadata(journey["egress_walk"], campus=True)
        self.assertEqual(journey["access_walk"]["minutes"], 2)
        self.assertEqual(journey["egress_walk"]["minutes"], 2)
        self.assertTrue(journey["location_is_approximate"])
        self.assertEqual(journey["path_basis"], "reference_points")
        self.assertTrue(any("대표" in warning for warning in journey["warnings"]))

    def test_first_route_return_direction_uses_official_endpoints_and_departure_only(self):
        self.shuttle.clock = lambda: datetime.fromisoformat("2026-10-19T17:00:00+09:00")
        response = self.route(self.CAMPUS, self.STATION, self.transit(actual_city=False))
        self.assertEqual(response.status_code, 200)
        first = next(item for item in response.json["journeys"] if item["route_name"] == "제1노선")
        self.assertEqual(first["route_name"], "제1노선")
        self.assertEqual(first["direction"], "춘천역 방면 (하교)")
        self.assertEqual(first["scheduled_departure_at"], "2026-10-19T17:30:00+09:00")
        self.assertEqual(first["legs"][0]["board"]["name"], "대학교")
        self.assertEqual(first["legs"][0]["alight"]["name"], "춘천역")
        self.assertIsNone(first["scheduled_arrival_at"])
        self.assertIsNone(first["total_min"])

    def test_friday_zero_vehicle_trips_are_excluded_from_timetable_and_walk_plan(self):
        friday = datetime.fromisoformat("2026-10-23T09:14:00+09:00")
        self.shuttle.clock = lambda: friday
        self.walking.return_value = (0, 0)
        timetable = self.timetable(at=friday)
        self.assertEqual(timetable.status_code, 200)
        times = [row["scheduled_departure_at"][11:16] for row in timetable.json["timetable_candidates"]]
        self.assertTrue(times)
        self.assertEqual(times[0], "09:20")
        for excluded in ("09:15", "10:00", "10:15"):
            self.assertNotIn(excluded, times)
        response = self.route(service=self.transit(actual_city=False))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["journeys"][0]["scheduled_departure_at"], "2026-10-23T09:20:00+09:00")

    def test_verified_weekend_vacation_and_public_holidays_have_no_candidates(self):
        for value in ("2026-10-17", "2026-10-18", "2026-07-01", "2026-12-23", "2026-10-09", "2026-10-05"):
            with self.subTest(day=value):
                response = self.timetable(at=datetime.fromisoformat(value + "T07:50:00+09:00"))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json["status"], "not_operating")
                self.assertEqual(response.json["timetable_candidates"], [])

    def test_profile_coverage_does_not_claim_2027_operation(self):
        response = self.timetable(at=datetime.fromisoformat("2027-01-04T07:50:00+09:00"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["calendar_status"], "unknown")
        for candidate in response.json["timetable_candidates"]:
            self.assertFalse(candidate["service_date_confirmed"])
            self.assertFalse(candidate["availability_confirmed"])
            self.assertEqual(candidate["service_date"], "2027-01-04")

    def test_real_second_third_and_fourth_route_segments_return_api_candidates(self):
        cases = [
            ("제2노선", "2026-10-19T07:50:00+09:00",
             {"name": "온의금호 대표점", "lat": 37.86888, "lon": 127.71620},
             {"name": "양우A 대표점", "lat": 37.86833, "lon": 127.73050}),
            ("제3노선", "2026-10-19T09:10:00+09:00",
             {"name": "KT&G 대표점", "lat": 37.86183, "lon": 127.72745}, self.CAMPUS),
            ("제4노선", "2026-10-19T16:50:00+09:00", self.STATION, self.CAMPUS),
        ]
        for route, value, origin, destination in cases:
            with self.subTest(route=route):
                self.shuttle.clock = lambda value=value: datetime.fromisoformat(value)
                response = self.route(origin, destination, self.transit(actual_city=False))
                self.assertEqual(response.status_code, 200)
                journeys = response.json["journeys"]
                self.assertTrue(any(item["route_name"] == route for item in journeys))
                target = next(item for item in journeys if item["route_name"] == route)
                self.assertTrue(target["location_is_approximate"])
                self.assertIsNone(target["total_min"])
                self.assertIsNone(target["ride_min"])

    def test_actual_profile_shuttle_survives_city_failure_without_leaking_error(self):
        service = self.transit()
        with patch.object(service, "_plan_city", side_effect=PublicDataError("NETWORK", "private raw URL")):
            response = self.route(service=service)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["journeys"])
        self.assertEqual(response.json["journeys"][0]["transport_type"], "shuttle")
        self.assertEqual(response.json["providers"]["city_bus"]["code"], "NETWORK")
        self.assertNotIn("private raw URL", str(response.json))

    def test_malformed_profile_leaves_actual_city_routes_available(self):
        from services.shuttle_profile import ShuttleProfile
        with tempfile.TemporaryDirectory() as directory:
            invalid_path = Path(directory) / "broken-profile.json"
            invalid_path.write_text('{"incomplete":', encoding="utf-8")
            profile = ShuttleProfile(application.shuttle_repository, invalid_path,
                                     DATA_PATH.parent / "한림대학교 건물별 좌표.csv", application.transit_repository)
            shuttle = ShuttleService(application.shuttle_repository, self.walking, profile=profile, clock=lambda: NOW)
            stops = application.transit_repository.route_stops("CCB250001202")
            point = lambda stop: {"name": stop["nodenm"], "lat": stop["gpslati"], "lon": stop["gpslong"]}
            response = self.route(point(stops[11]), point(stops[18]), self.transit(shuttle=shuttle))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["journeys"])
        self.assertTrue(all(item["transport_type"] == "city_bus" for item in response.json["journeys"]))
        self.assertEqual(response.json["providers"]["shuttle"]["code"], "SHUTTLE_DATA")
        self.assertNotIn("error", response.json)
        self.assertNotIn(str(invalid_path), str(response.json))

    def test_malformed_profile_keeps_raw_second_route_timetable_available(self):
        from services.shuttle_profile import ShuttleProfile
        with tempfile.TemporaryDirectory() as directory:
            invalid_path = Path(directory) / "broken-profile.json"
            invalid_path.write_text('{"incomplete":', encoding="utf-8")
            profile = ShuttleProfile(application.shuttle_repository, invalid_path,
                                     DATA_PATH.parent / "한림대학교 건물별 좌표.csv", application.transit_repository)
            self.shuttle = ShuttleService(application.shuttle_repository, self.walking, profile=profile, clock=lambda: NOW)
            response = self.timetable(route="제2노선", direction="온의동/퇴계동/석사동",
                                      board="온의금호1차 버스정류장", alight="양우A 건너 버스정류장")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["timetable_candidates"])
        self.assertEqual(response.json["profile_status"], {"status": "error", "code": "SHUTTLE_DATA"})
        self.assertEqual(response.json["source"]["row_count"], 196)
        self.assertIsNone(response.json["timetable_candidates"][0]["scheduled_departure_at"])
        self.assertIsNone(response.json["timetable_candidates"][0]["total_min"])
        self.assertNotIn(str(invalid_path), str(response.json))
        self.walking.assert_not_called()

    def test_missing_raw_file_still_returns_502_instead_of_empty_fallback_success(self):
        from services.shuttle_profile import ShuttleProfile
        with tempfile.TemporaryDirectory() as directory:
            missing_path = Path(directory) / "missing-shuttle.csv"
            missing_repository = ShuttleRepository(missing_path)
            profile = ShuttleProfile(missing_repository, DATA_PATH.parent / "data" / "hallym_shuttle_profile.json",
                                     DATA_PATH.parent / "한림대학교 건물별 좌표.csv", application.transit_repository)
            self.shuttle = ShuttleService(missing_repository, self.walking, profile=profile, clock=lambda: NOW)
            response = self.timetable(route="제2노선", direction="온의동/퇴계동/석사동",
                                      board="온의금호1차 버스정류장", alight="양우A 건너 버스정류장")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json["code"], "SHUTTLE_DATA")
        self.assertNotIn(str(missing_path), str(response.json))
        self.walking.assert_not_called()


if __name__ == "__main__":
    unittest.main()
