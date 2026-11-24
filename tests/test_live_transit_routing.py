"""공식 노선 ID·번호·정류장 순서를 연결하는 회귀 검증. 외부 호출 없음."""

from copy import deepcopy
import unittest
from unittest.mock import Mock

from services.public_transit_client import PublicDataError, PublicTransitClient
from services.transit_service import TransitService


class LiveTransitRoutingTests(unittest.TestCase):
    def setUp(self):
        # ID 끝자리로 표시 번호를 추정하면 400이 되지만, 공식 번호는 300이다.
        self.route_id = "CCB250040000"
        self.stops = [
            self.stop("A", "앞 정류장", 1, 37.8840, 127.7365),
            self.stop("B", "한림대학교", 2, 37.8831, 127.73737),
            self.stop("C", "승차 직후 정류장", 3, 37.8821, 127.7395),
            self.stop("D", "포스코후문", 4, 37.88154, 127.74694),
            self.stop("E", "하차 다음 정류장", 5, 37.8815, 127.7486),
        ]
        self.client = Mock(spec=PublicTransitClient)
        self.client.station_routes.return_value = [
            {"routeid": self.route_id, "routeno": "400"},
        ]
        self.client.route_info.return_value = {
            "routeid": self.route_id, "routeno": "300",
            "startnodenm": "시점", "endnodenm": "종점",
        }
        self.client.route_stops.return_value = deepcopy(self.stops)
        self.client.arrivals.return_value = [
            {"routeid": self.route_id, "routeno": "400", "arrtime": 1800},
        ]
        self.walk = Mock(return_value=(3, 200))
        self.service = TransitService(self.client, self.walk)

    @staticmethod
    def stop(node_id, name, order, lat, lon):
        return {
            "nodeid": node_id, "nodenm": name, "nodeord": order,
            "gpslati": lat, "gpslong": lon,
        }

    @staticmethod
    def point(stop):
        return {
            "name": stop["nodenm"], "lat": stop["gpslati"],
            "lon": stop["gpslong"],
        }

    @staticmethod
    def public_stop(stop):
        return {
            "id": stop["nodeid"], "name": stop["nodenm"],
            "lat": stop["gpslati"], "lon": stop["gpslong"],
        }

    def plan(self, board_index=1, alight_index=3):
        board, alight = self.stops[board_index], self.stops[alight_index]
        origin, destination = self.point(board), self.point(alight)
        self.client.nearby_stops.side_effect = lambda lat, lon: (
            [deepcopy(board)] if (lat, lon) == (origin["lat"], origin["lon"])
            else [deepcopy(alight)]
        )
        return self.service.plan(origin, destination)

    def test_official_number_overrides_station_label_and_route_id_suffix(self):
        result = self.plan()
        self.assertIs(self.service.topology, self.client)
        self.assertEqual(result["source"]["source"], "api")
        self.assertEqual(len(result["journeys"]), 1)
        leg = result["journeys"][0]["legs"][0]
        self.assertEqual((leg["route_id"], leg["route_no"]), (self.route_id, "300"))
        self.client.route_info.assert_called_once_with(self.route_id)
        self.client.route_stops.assert_called_once_with(self.route_id)

    def test_next_stop_is_the_visit_after_boarding_in_the_middle_of_route(self):
        leg = self.plan()["journeys"][0]["legs"][0]
        self.assertEqual(leg["board"]["id"], "B")
        self.assertEqual(leg["alight"]["id"], "D")
        self.assertEqual(leg["next_stop"], self.public_stop(self.stops[2]))
        self.assertNotEqual(leg["next_stop"]["id"], self.stops[4]["nodeid"])

    def test_direct_walking_is_queried_before_collecting_live_arrivals(self):
        events = []
        self.walk.side_effect = lambda *args: events.append(("walk", args)) or (3, 200)
        self.client.arrivals.side_effect = lambda node_id: events.append(("arrivals", node_id)) or []
        result = self.plan()
        origin, destination = self.point(self.stops[1]), self.point(self.stops[3])
        self.assertEqual(events[0], ("walk", (
            origin["lat"], origin["lon"], origin["name"],
            destination["lat"], destination["lon"], destination["name"],
        )))
        self.assertTrue(any(event[0] == "arrivals" for event in events[1:]))
        self.assertEqual(result["direct_walk"], {
            "status": "ok", "minutes": 3, "distance_m": 200, "source": "tmap",
        })

    def test_naver_reference_hallym_to_hupyeong_dt_uses_300_toward_seongsim(self):
        # 2026-11-21 네이버 지도 UI에서 관측한 번호·정류장 순서를 모사한다.
        # 중간 정류소 ID와 좌표는 합성값이며 공식 API 실호출 검증으로 간주하지 않는다.
        names = ["한림대학교", "성심경로당", "봉의중학교", "크로바A", "포스코후문", "포스코더샵A"]
        self.route_id = "CCB250030000"
        self.stops = [
            self.stop(f"REFERENCE_{index}", name, index + 1, 37.8831 - index * .0002, 127.73737 + index * .001)
            for index, name in enumerate(names)
        ]
        self.client.station_routes.return_value = [{"routeid": self.route_id, "routeno": 300}]
        self.client.route_info.return_value = {"routeid": self.route_id, "routeno": 300}
        self.client.route_stops.return_value = deepcopy(self.stops)
        self.client.arrivals.return_value = []
        leg = self.plan(0, 5)["journeys"][0]["legs"][0]
        self.assertEqual(leg["route_no"], "300")
        self.assertEqual(leg["board"]["name"], "한림대학교")
        self.assertEqual(leg["alight"]["name"], "포스코더샵A")
        self.assertEqual(leg["next_stop"]["name"], "성심경로당")
        self.assertEqual(leg["direction"], "다음 정류장 성심경로당 방면")
        self.assertEqual(leg["stop_count"], 5)

    def test_api_order_without_direction_remains_an_unconfirmed_candidate(self):
        journey = self.plan()["journeys"][0]
        leg = journey["legs"][0]
        self.assertEqual(leg["sequence_basis"], "api_order")
        self.assertFalse(leg["direction_verified"])
        self.assertTrue(leg["arrival_reference_only"])
        self.assertIsNone(journey["total_min"])
        self.assertIsNone(journey["wait_min"])
        self.assertFalse(journey["has_live_departure"])

    def test_api_order_does_not_connect_backwards_or_wrap_at_the_terminal(self):
        for board, alight in ((3, 1), (4, 0)):
            with self.subTest(board=board, alight=alight):
                self.assertEqual(self.plan(board, alight)["journeys"], [])

    def test_known_direction_boundary_does_not_join_opposite_directions(self):
        directed = deepcopy(self.stops)
        for index, stop in enumerate(directed):
            stop["updowncd"] = 0 if index < 2 else 1
        self.client.route_stops.return_value = directed
        self.assertEqual(self.plan()["journeys"], [])

    def test_route_permission_failure_does_not_fall_back_to_station_number(self):
        self.client.route_info.side_effect = PublicDataError("403", "조회 권한 없음")
        with self.assertRaises(PublicDataError) as error:
            self.plan()
        self.assertEqual(error.exception.code, "ROUTE_PERMISSION")

    def test_nearby_and_journeys_use_the_same_official_number(self):
        self.client.nearby_stops.return_value = [deepcopy(self.stops[1])]
        self.client.arrivals.return_value = [{"routeid": self.route_id, "routeno": 300, "arrtime": 1800}]
        nearby = self.service.nearby(self.point(self.stops[1]))
        self.assertEqual(nearby[0]["buses"][0]["route"], "300")
        self.client.route_info.assert_called_once_with(self.route_id)

    def test_nearby_does_not_show_conflicting_arrival_number(self):
        self.client.nearby_stops.return_value = [deepcopy(self.stops[1])]
        nearby = self.service.nearby(self.point(self.stops[1]))
        self.assertEqual(nearby[0]["buses"], [])
        self.assertTrue(nearby[0]["arrival_error"])

    def test_missing_or_other_route_metadata_cannot_supply_a_fallback_number(self):
        invalid_records = (
            {},
            {"routeid": self.route_id, "routeno": ""},
            {"routeid": "CCB250030000", "routeno": "300"},
        )
        for record in invalid_records:
            with self.subTest(record=record):
                self.client.route_info.return_value = record
                try:
                    result = self.plan()
                except PublicDataError as error:
                    self.assertEqual(error.code, "ROUTE_DATA")
                else:
                    self.assertEqual(result["journeys"], [])


class DirectWalkingTests(unittest.TestCase):
    def setUp(self):
        self.origin = {"name": "출발", "lat": 37.88, "lon": 127.73}
        self.destination = {"name": "도착", "lat": 37.89, "lon": 127.74}
        self.unavailable = {"status": "unavailable", "minutes": None, "distance_m": None, "source": "tmap"}

    def test_direct_walk_preserves_provider_time_and_distance_without_straight_line_estimates(self):
        walking = Mock(return_value=(27.25, 1800.5))
        service = TransitService(Mock(), walking)
        service._plan_city = Mock(return_value={"journeys": []})
        response = service.plan(self.origin, self.destination)
        self.assertEqual(response["direct_walk"], {
            "status": "ok", "minutes": 27.25, "distance_m": 1800.5, "source": "tmap",
        })
        self.assertEqual(response["recommendation"]["walk"], {"minutes": 27.25, "distance_m": 1800.5})
        self.assertEqual(response["recommendation"]["preferred_mode"], "undetermined")
        walking.assert_called_once_with(37.88, 127.73, "출발", 37.89, 127.74, "도착")

    def test_exactly_coincident_valid_coordinates_are_zero_without_external_walking(self):
        walking = Mock(side_effect=AssertionError("동일 좌표는 외부 조회 불필요"))
        service = TransitService(Mock(), walking)
        destination = {"name": "같은 지점", "lat": "37.88", "lon": "127.73"}
        self.assertEqual(service._direct_walk(self.origin, destination), {
            "status": "ok", "minutes": 0, "distance_m": 0, "source": "tmap",
        })
        walking.assert_not_called()

    def test_invalid_origin_or_destination_never_calls_walking_provider(self):
        invalid = (
            None, [], {}, {"lat": True, "lon": 127.73}, {"lat": 37.88, "lon": False},
            {"lat": float("nan"), "lon": 127.73}, {"lat": 37.88, "lon": float("inf")},
            {"lat": 91, "lon": 127.73}, {"lat": 37.88, "lon": -181},
            {"lat": "invalid", "lon": 127.73}, {"lat": 10 ** 400, "lon": 127.73},
        )
        walking = Mock(side_effect=AssertionError("유효하지 않은 좌표로 호출 금지"))
        service = TransitService(Mock(), walking)
        for position in invalid:
            for origin, destination in ((position, self.destination), (self.origin, position)):
                with self.subTest(origin=origin, destination=destination):
                    self.assertEqual(service._direct_walk(origin, destination), self.unavailable)
        walking.assert_not_called()

    def test_invalid_walking_values_are_unavailable_instead_of_zero_or_estimates(self):
        invalid = (
            None, {}, "12", (3,), (3, 100, 200), ("3", 100),
            (-1, 100), (3, -1), (0, 100), (3, 0), (0, 0),
            (float("nan"), 100), (3, float("nan")), (float("inf"), 100), (3, float("inf")),
            (True, 100), (3, False), (10 ** 400, 100),
        )
        for value in invalid:
            with self.subTest(value=value):
                service = TransitService(Mock(), Mock(return_value=value))
                self.assertEqual(service._direct_walk(self.origin, self.destination), self.unavailable)

    def test_walking_failure_preserves_successful_bus_candidates_and_does_not_expose_errors(self):
        walking = Mock(side_effect=RuntimeError("fake-private-provider-error"))
        service = TransitService(Mock(), walking)
        candidates = [{"legs": [{"route_no": "300"}]}]
        service._plan_city = Mock(return_value={"journeys": candidates})
        response = service.plan(self.origin, self.destination)
        self.assertEqual(response["journeys"], candidates)
        self.assertEqual(response["direct_walk"], self.unavailable)
        self.assertEqual(response["recommendation"]["status"], "unavailable")
        self.assertEqual(response["recommendation"]["reason"], "walk_unavailable")
        self.assertNotIn("fake-private", str(response))

    def test_direct_walk_survives_city_and_shuttle_failures_in_production_response(self):
        shuttle = Mock()
        shuttle.plan.side_effect = RuntimeError("fake-private-shuttle-error")
        service = TransitService(Mock(), Mock(return_value=(25, 1700)), shuttle_service=shuttle)
        service._plan_city = Mock(side_effect=PublicDataError("ROUTE_PERMISSION", "fake-private-city-error"))
        response = service.plan(self.origin, self.destination)
        self.assertTrue(response["error"])
        self.assertEqual(response["providers"]["city_bus"]["code"], "ROUTE_PERMISSION")
        self.assertEqual(response["providers"]["shuttle"]["code"], "SHUTTLE_UNAVAILABLE")
        self.assertEqual(response["direct_walk"], {
            "status": "ok", "minutes": 25, "distance_m": 1700, "source": "tmap",
        })
        self.assertEqual(response["recommendation"]["status"], "ok")
        self.assertEqual(response["recommendation"]["walk"], {"minutes": 25, "distance_m": 1700})
        self.assertEqual(response["recommendation"]["scooter"]["status"], "conditional")
        self.assertNotIn("fake-private", str(response))

    def test_city_failure_does_not_discard_direct_walk_or_successful_shuttle(self):
        shuttle = Mock()
        shuttle.plan.return_value = {"journeys": [{"total_min": 20, "legs": []}], "status": "ok", "warnings": []}
        service = TransitService(Mock(), Mock(return_value=(25, 1700)), shuttle_service=shuttle)
        service._plan_city = Mock(side_effect=PublicDataError("NETWORK", "조회 실패"))
        response = service.plan(self.origin, self.destination)
        self.assertNotIn("error", response)
        self.assertEqual(len(response["journeys"]), 1)
        self.assertEqual(response["direct_walk"]["status"], "ok")

    def test_legacy_city_exception_without_shuttle_is_preserved(self):
        walking = Mock(return_value=(25, 1700))
        service = TransitService(Mock(), walking)
        failure = PublicDataError("ROUTE_PERMISSION", "조회 실패")
        service._plan_city = Mock(side_effect=failure)
        with self.assertRaises(PublicDataError) as error:
            service.plan(self.origin, self.destination)
        self.assertIs(error.exception, failure)
        walking.assert_called_once()


if __name__ == "__main__":
    unittest.main()
