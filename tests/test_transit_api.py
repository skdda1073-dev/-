"""Flask 경계 검증·새 경로 계약·기존 주변 정류장 응답 유지 검증. 네트워크 없음."""

import unittest
from unittest.mock import Mock, patch

from app import app
from services.public_transit_client import PublicDataError
from services.transit_service import TransitService


class TransitApiTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.origin = {"lat": 37.8863, "lon": 127.7381, "location": "테스트 출발"}

    def test_non_object_and_invalid_coordinates_return_400(self):
        for payload in ([], None, {"lat": True, "lon": 127.7}, {"lat": "NaN", "lon": 127.7}, {"lat": 10 ** 400, "lon": 127.7}, {"lat": 91, "lon": 127.7}):
            with self.subTest(payload=payload):
                response = self.client.post("/get_route_info", json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.json)

    def test_partial_destination_is_not_treated_as_nearby_mode(self):
        response = self.client.post("/get_route_info", json={**self.origin, "dest_lat": 37.8})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["code"], "INVALID_DESTINATION")

    def test_nearby_mode_keeps_array_response(self):
        with patch("app.transit_service.nearby", return_value=[{"station_name": "테스트"}]):
            response = self.client.post("/get_route_info", json=self.origin)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json, list)

    def test_destination_mode_uses_planner(self):
        with patch("app.transit_service.plan", return_value={"mode": "journeys", "journeys": []}) as planner:
            response = self.client.post("/get_route_info", json={**self.origin, "dest_lat": 37.86, "dest_lon": 127.72, "dest_name": "도착"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["mode"], "journeys")
        self.assertEqual(planner.call_args.args[1]["name"], "도착")

    def test_permission_error_is_visible_in_destination_mode(self):
        with patch("app.transit_service.plan", side_effect=PublicDataError("ROUTE_PERMISSION", "활용신청 확인 필요")):
            response = self.client.post("/get_route_info", json={**self.origin, "dest_lat": 37.86, "dest_lon": 127.72})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json["journeys"], [])
        self.assertEqual(response.json["code"], "ROUTE_PERMISSION")
        self.assertEqual(response.json["origin"]["lat"], self.origin["lat"])

    def test_service_stops_after_first_denied_route_call(self):
        data = Mock()
        data.nearby_stops.return_value = [{"nodeid": "A", "nodenm": "정류장", "gpslati": 37.88, "gpslong": 127.73}]
        data.station_routes.return_value = [{"routeid": "r1", "routeno": "10"}, {"routeid": "r2", "routeno": "20"}]
        data.route_stops.side_effect = PublicDataError("403", "Forbidden")
        walk = Mock()
        service = TransitService(data, walk)
        with self.assertRaises(PublicDataError) as error:
            service.plan({"lat": 37.88, "lon": 127.73, "name": "출발"}, {"lat": 37.86, "lon": 127.72, "name": "도착"})
        self.assertEqual(error.exception.code, "ROUTE_PERMISSION")
        self.assertEqual(data.route_stops.call_count, 1)
        walk.assert_not_called()

    def test_real_service_wires_sequence_walks_and_arrivals_into_planner(self):
        def stop(node, order, lat):
            return {"nodeid": node, "nodenm": node, "nodeord": order, "updowncd": 0, "gpslati": lat, "gpslong": 127.73}
        board, middle, alight = stop("A", 1, 37.88), stop("B", 2, 37.87), stop("C", 3, 37.86)
        data = Mock()
        data.nearby_stops.side_effect = lambda lat, lon: [board] if lat > 37.87 else [alight]
        data.station_routes.return_value = [{"routeid": "r1", "routeno": "10"}]
        data.route_stops.return_value = [board, middle, alight]
        data.arrivals.return_value = [{"routeid": "r1", "arrtime": 600}]
        service = TransitService(data, lambda *args: (3, 200))
        result = service.plan({"lat": 37.881, "lon": 127.73, "name": "출발"}, {"lat": 37.859, "lon": 127.73, "name": "도착"})
        self.assertEqual(result["mode"], "journeys")
        self.assertEqual(len(result["journeys"]), 1)
        journey = result["journeys"][0]
        self.assertEqual(journey["legs"][0]["stop_count"], 2)
        self.assertEqual(journey["egress_walk"]["minutes"], 3)
        self.assertTrue(journey["has_live_departure"])
        self.assertIsNotNone(journey["total_min"])

    def test_nearby_walking_failure_is_reported_instead_of_zero(self):
        data = Mock()
        data.nearby_stops.return_value = [{"nodeid": "A", "gpslati": 37.88, "gpslong": 127.73}]
        service = TransitService(data, lambda *args: (-1, -1))
        with self.assertRaises(PublicDataError) as error:
            service.nearby({"lat": 37.881, "lon": 127.73, "name": "출발"})
        self.assertEqual(error.exception.code, "WALK_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
