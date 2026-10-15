"""공공데이터 연동의 페이지 누락·실패 은폐·도보 0분 회귀를 검증한다."""

import unittest

import requests

from services.public_transit_client import PublicDataError, PublicTransitClient
from services.walking_service import WalkingService


class Response:
    def __init__(self, rows=None, total=None, status=200, code="00", payload=None, text=""):
        self.status_code = status
        self.text = text
        self.payload = payload if payload is not None else {
            "response": {"header": {"resultCode": code}, "body": {
                "items": {"item": rows or []}, "totalCount": total,
            }},
        }

    def json(self):
        if self.text:
            raise ValueError("not JSON")
        return self.payload


class TransitClientTests(unittest.TestCase):
    def test_all_route_pages_and_occurrences_are_preserved(self):
        calls = []
        rows = [{"nodeid": "A", "nodeord": 1}, {"nodeid": "B", "nodeord": 2}, {"nodeid": "A", "nodeord": 3}]
        def requester(url, params, timeout):
            calls.append((url, params.copy(), timeout))
            start = (params["pageNo"] - 1) * 2
            return Response(rows[start:start + 2], total=3)
        client = PublicTransitClient("test-key", "32010", requester)
        client.PAGE_SIZE = 2
        self.assertEqual(client.route_stops("route-1"), rows)
        self.assertEqual([call[1]["pageNo"] for call in calls], [1, 2])
        self.assertEqual(calls[0][1]["routeId"], "route-1")
        self.assertTrue(calls[0][0].startswith("https://"))
        client.route_stops("route-1")
        self.assertEqual(len(calls), 2)

    def test_repeated_page_is_not_accepted_as_complete(self):
        client = PublicTransitClient("test-key", "32010", lambda *args, **kwargs: Response([{"nodeord": 1}], total=2))
        with self.assertRaises(PublicDataError) as error:
            client.route_stops("r")
        self.assertEqual(error.exception.code, "INCOMPLETE")

    def test_missing_page_fails_closed(self):
        responses = iter([Response([{"nodeord": 1}], total=2), Response([], total=2)])
        client = PublicTransitClient("test-key", "32010", lambda *args, **kwargs: next(responses))
        with self.assertRaises(PublicDataError):
            client.route_stops("r")

    def test_http_permission_denied_is_actionable(self):
        client = PublicTransitClient("test-key", "32010", lambda *args, **kwargs: Response(status=403))
        with self.assertRaises(PublicDataError) as error:
            client.route_stops("r")
        self.assertTrue(error.exception.authorization_failed)

    def test_xml_permission_denied_is_recognized(self):
        xml = "<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
        client = PublicTransitClient("test-key", "32010", lambda *args, **kwargs: Response(text=xml))
        with self.assertRaises(PublicDataError) as error:
            client.route_stops("r")
        self.assertTrue(error.exception.authorization_failed)

    def test_network_exception_does_not_expose_key(self):
        def requester(*args, **kwargs):
            raise requests.ConnectionError("secret-key-in-request-url")
        client = PublicTransitClient("test-key", "32010", requester)
        with self.assertRaises(PublicDataError) as error:
            client.route_stops("r")
        self.assertNotIn("secret-key", str(error.exception))

    def test_nearby_filters_other_cities_and_normalizes_single_item(self):
        client = PublicTransitClient("test-key", "32010", lambda *args, **kwargs: Response({"nodeid": "A", "citycode": 32010}, total=1))
        self.assertEqual(len(client.nearby_stops(37.8, 127.7)), 1)
        client = PublicTransitClient("test-key", "32010", lambda *args, **kwargs: Response([{"citycode": 32010}, {"citycode": 11}], total=2))
        self.assertEqual(client.nearby_stops(37.8, 127.7), [{"citycode": 32010}])

    def test_arrival_data_is_not_served_from_old_cache(self):
        calls = []
        def requester(url, params, timeout):
            calls.append(params)
            return Response([{"arrtime": 600 - len(calls)}], total=1)
        client = PublicTransitClient("test-key", "32010", requester)
        self.assertNotEqual(client.arrivals("A"), client.arrivals("A"))
        self.assertEqual(calls[0]["nodeId"], "A")

    def test_request_uses_expected_parameter_case(self):
        calls = []
        def requester(url, params, timeout):
            calls.append(params)
            return Response([], total=0)
        client = PublicTransitClient("test-key", "32010", requester)
        client.station_routes("A")
        self.assertEqual(calls[0]["nodeid"], "A")
        self.assertNotIn("nodeId", calls[0])

    def test_malformed_header_is_a_sanitized_provider_error(self):
        client = PublicTransitClient("test-key", "32010", lambda *args, **kwargs: Response(payload={"response": {"header": None}}))
        with self.assertRaises(PublicDataError) as error:
            client.route_stops("r")
        self.assertEqual(error.exception.code, "INVALID_RESPONSE")


class WalkingServiceTests(unittest.TestCase):
    def test_walking_failure_never_becomes_zero(self):
        service = WalkingService("test-key", lambda *args, **kwargs: Response(status=500))
        self.assertEqual(service.get_time(37.8, 127.7, "A", 37.81, 127.71, "B"), (-1, -1))

    def test_walking_time_rounds_up_and_caches(self):
        calls = []
        def requester(*args, **kwargs):
            calls.append(kwargs)
            return Response(payload={"features": [{"properties": {"totalTime": 61, "totalDistance": 101}}]})
        service = WalkingService("test-key", requester)
        args = (37.8, 127.7, "A", 37.81, 127.71, "B")
        self.assertEqual(service.get_time(*args), (2, 101))
        self.assertEqual(service.get_time(*args), (2, 101))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["json"]["reqCoordType"], "WGS84GEO")
        self.assertIn("timeout", calls[0])

    def test_only_identical_points_can_return_zero_without_request(self):
        service = WalkingService("test-key", lambda *args, **kwargs: self.fail("must not request"))
        self.assertEqual(service.get_time(37.8, 127.7, "A", 37.8, 127.7, "A"), (0, 0))

    def test_oversized_integer_coordinate_is_not_an_uncaught_exception(self):
        service = WalkingService("test-key", lambda *args, **kwargs: self.fail("must not request"))
        self.assertEqual(service.get_time(10 ** 400, 127.7, "A", 37.8, 127.7, "B"), (-1, -1))


if __name__ == "__main__":
    unittest.main()
