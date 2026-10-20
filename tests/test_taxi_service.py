"""자동차 API는 mock만 사용한다. 예시 금액은 실제 춘천 운임이 아니다."""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock

import requests

from services.mobility_validation import MobilityInputError
from services.taxi_service import TaxiError, TaxiService


ORIGIN = {"name": "가상 집결 장소", "lat": 37.88, "lon": 127.73}
DESTINATION = {"name": "가상 목적지", "lat": 37.86, "lon": 127.72}


def response(properties=None, status=200, payload=None):
    result = Mock(status_code=status)
    result.text = "fake-secret-provider-raw"
    if payload is None:
        payload = {"features": [{"properties": properties if properties is not None else {
            "totalTime": 601, "totalDistance": 3210, "taxiFare": 10001, "totalFare": 800,
        }}]}
    result.json.return_value = payload
    return result


class TaxiServiceTests(unittest.TestCase):
    def setUp(self):
        self.elapsed = 100.0
        self.epoch = datetime(2026, 10, 20, tzinfo=timezone.utc)
        self.requester = Mock(return_value=response())
        self.service = TaxiService("fake-private-key", self.requester,
                                   clock=lambda: self.elapsed,
                                   utcnow=lambda: self.epoch + timedelta(seconds=self.elapsed))

    def test_request_is_form_with_taxi_options_and_correct_coordinate_axes(self):
        result = self.service.estimate(ORIGIN, DESTINATION, split_count=3)
        args, kwargs = self.requester.call_args
        self.assertEqual(args, ("https://apis.openapi.sk.com/tmap/routes",))
        self.assertEqual(kwargs["params"], {"version": 1})
        self.assertNotIn("json", kwargs)
        form = kwargs["data"]
        self.assertEqual((form["startX"], form["startY"], form["endX"], form["endY"]),
                         (127.73, 37.88, 127.72, 37.86))
        for key, value in {"searchOption": "24", "commercialVehicle": "Y", "carType": "1",
                           "totalValue": "2", "reqCoordType": "WGS84GEO", "resCoordType": "WGS84GEO"}.items():
            self.assertEqual(form[key], value)
        self.assertEqual(kwargs["headers"]["appKey"], "fake-private-key")
        self.assertEqual(kwargs["headers"]["Accept"], "application/json")
        self.assertEqual(kwargs["timeout"], (3, 8))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(result["ride_duration_seconds"], 601)
        self.assertEqual(result["distance_m"], 3210)
        self.assertEqual(result["fare"]["amount_krw"], 10001)
        self.assertEqual(result["split_amounts_krw"], [3334, 3334, 3333])
        self.assertEqual(result["split_preview"][2], {"count": 3, "amounts_krw": [3334, 3334, 3333]})
        self.assertNotIn("fake-private", str(result))
        self.assertNotIn("fake-private", repr(self.service))

    def test_summary_without_point_type_is_supported_and_separate_total_fare_is_not_added(self):
        result = self.service.estimate(ORIGIN, DESTINATION)
        self.assertEqual(result["fare"], {
            "amount_krw": 10001, "basis": "provider_estimate", "provider_total_fare_krw": 800,
            "toll_included": None, "surcharge_included": None, "call_fee_included": None,
        })
        self.assertIsNone(result["pickup_wait_seconds"])
        self.assertIsNone(result["door_to_door_seconds"])
        self.assertIsNone(result["provider_data_at"])
        self.assertEqual(result["status"], "ok")

    def test_full_geometry_uses_start_totals_and_never_first_line_segment_time(self):
        self.requester.return_value = response(payload={"features": [
            {"geometry": {"type": "LineString"}, "properties": {"time": 3, "distance": 5}},
            {"properties": {"pointType": "E", "totalTime": 99, "totalDistance": 99, "taxiFare": 99}},
            {"properties": {"pointType": "S", "totalTime": 600, "totalDistance": 3000, "taxiFare": 10000}},
        ]})
        result = self.service.estimate(ORIGIN, DESTINATION)
        self.assertEqual((result["ride_duration_seconds"], result["distance_m"]), (600, 3000))
        self.assertEqual(result["fare"]["amount_krw"], 10000)

    def test_summary_search_skips_unrelated_and_malformed_properties(self):
        self.requester.return_value = response(payload={"features": [None, {"properties": []},
            {"properties": {"totalTime": "NaN", "totalDistance": 100}},
            {"properties": {"totalTime": "601", "totalDistance": "3210", "taxiFare": "10001"}},
        ]})
        result = self.service.estimate(ORIGIN, DESTINATION)
        self.assertEqual(result["ride_duration_seconds"], 601)
        self.assertEqual(result["fare"]["amount_krw"], 10001)

    def test_missing_or_invalid_fare_keeps_valid_time_and_distance(self):
        for fare in (None, 0, -1, True, "NaN", "Infinity", 100.5, "bad", 10 ** 400):
            with self.subTest(fare=fare):
                service = TaxiService("fake", Mock(return_value=response({
                    "totalTime": 601, "totalDistance": 3210, "taxiFare": fare,
                })))
                result = service.estimate(ORIGIN, DESTINATION)
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["ride_duration_seconds"], 601)
                self.assertEqual(result["distance_m"], 3210)
                self.assertIsNone(result["fare"]["amount_krw"])
                self.assertIsNone(result["split_amounts_krw"])
                self.assertTrue(all(row["amounts_krw"] is None for row in result["split_preview"]))
                self.assertTrue(result["warnings"])

    def test_identical_points_are_not_a_free_taxi_quote_and_need_no_key(self):
        requester = Mock(side_effect=AssertionError("no external request"))
        result = TaxiService("", requester).estimate(ORIGIN, ORIGIN, 4)
        self.assertEqual(result["status"], "no_travel_required")
        self.assertEqual(result["ride_duration_seconds"], 0)
        self.assertEqual(result["distance_m"], 0)
        self.assertIsNone(result["fare"]["amount_krw"])
        self.assertIsNone(result["split_amounts_krw"])
        requester.assert_not_called()

    def test_missing_key_is_explicit_configuration_failure(self):
        requester = Mock()
        with self.assertRaises(TaxiError) as raised:
            TaxiService("", requester).estimate(ORIGIN, DESTINATION)
        self.assertEqual((raised.exception.code, raised.exception.status_code), ("TAXI_KEY_MISSING", 503))
        requester.assert_not_called()

    def test_provider_http_failures_are_safe_and_never_cached(self):
        cases = [(401, "TAXI_PROVIDER_AUTH", 502), (403, "TAXI_PROVIDER_AUTH", 502),
                 (429, "TAXI_PROVIDER_QUOTA", 503), (500, "TAXI_PROVIDER_HTTP", 502),
                 (302, "TAXI_PROVIDER_HTTP", 502)]
        for status, code, http_status in cases:
            with self.subTest(status=status):
                self.requester.return_value = response(status=status)
                with self.assertRaises(TaxiError) as raised:
                    self.service.estimate(ORIGIN, DESTINATION)
                self.assertEqual((raised.exception.code, raised.exception.status_code), (code, http_status))
                self.assertNotIn("fake-secret", str(raised.exception))
                self.assertNotIn("fake-private", str(raised.exception))
                self.assertIsNone(self.service.peek(ORIGIN, DESTINATION))

    def test_timeout_and_network_errors_do_not_expose_raw_exception(self):
        for error, code, status in ((requests.Timeout("fake-secret"), "TAXI_PROVIDER_TIMEOUT", 504),
                                    (requests.ConnectionError("fake-secret"), "TAXI_PROVIDER_NETWORK", 502)):
            with self.subTest(code=code):
                self.requester.side_effect = error
                with self.assertRaises(TaxiError) as raised:
                    self.service.estimate(ORIGIN, DESTINATION)
                self.assertEqual((raised.exception.code, raised.exception.status_code), (code, status))
                self.assertNotIn("fake-secret", str(raised.exception))

    def test_bad_json_and_invalid_totals_never_become_success(self):
        bad = response()
        bad.json.side_effect = ValueError("fake-secret-raw")
        responses = [bad]
        for payload in ([], {}, {"features": []}, {"features": [{"properties": {"time": 1}}]},
                        {"features": [{"properties": {"totalTime": True, "totalDistance": 1}}]},
                        {"features": [{"properties": {"totalTime": 10, "totalDistance": -1}}]}):
            responses.append(response(payload=payload))
        for remote in responses:
            with self.subTest(remote=remote):
                self.requester.return_value = remote
                with self.assertRaises(TaxiError) as raised:
                    self.service.estimate(ORIGIN, DESTINATION)
                self.assertEqual(raised.exception.code, "TAXI_PROVIDER_RESPONSE")
                self.assertNotIn("fake-secret", str(raised.exception))
                self.assertIsNone(self.service.peek(ORIGIN, DESTINATION))

    def test_cache_shares_route_across_split_counts_and_preserves_original_timestamp(self):
        first = self.service.estimate(ORIGIN, DESTINATION, 2)
        self.elapsed += 20
        renamed = dict(ORIGIN, name="다른 공개 장소명")
        cached = self.service.estimate(renamed, DESTINATION, 3)
        self.assertTrue(cached["from_cache"])
        self.assertEqual(cached["cache_age_seconds"], 20)
        self.assertEqual(cached["fetched_at"], first["fetched_at"])
        self.assertEqual(cached["expires_at"], first["expires_at"])
        self.assertEqual(cached["cache_ttl_seconds"], 60)
        self.assertEqual(cached["origin"]["name"], renamed["name"])
        self.assertEqual(cached["split_amounts_krw"], [3334, 3334, 3333])
        self.assertEqual(self.requester.call_count, 1)
        self.assertEqual(datetime.fromisoformat(first["expires_at"]) - datetime.fromisoformat(first["fetched_at"]),
                         timedelta(seconds=60))

    def test_peek_never_calls_provider_and_cache_responses_cannot_mutate_other_users(self):
        self.assertIsNone(self.service.peek(ORIGIN, DESTINATION))
        self.requester.assert_not_called()
        result = self.service.estimate(ORIGIN, DESTINATION)
        result["fare"]["amount_krw"] = 1
        result["warnings"].append("mutated")
        result["origin"]["name"] = "mutated"
        self.requester.side_effect = AssertionError("peek never calls provider")
        cached = self.service.peek(ORIGIN, DESTINATION)
        self.assertEqual(cached["fare"]["amount_krw"], 10001)
        self.assertNotIn("mutated", cached["warnings"])
        self.assertEqual(cached["origin"]["name"], ORIGIN["name"])
        cached["fare"]["amount_krw"] = 2
        self.assertEqual(self.service.peek(ORIGIN, DESTINATION)["fare"]["amount_krw"], 10001)

    def test_cache_expires_at_exact_boundary_and_failure_does_not_serve_stale_quote(self):
        self.service.estimate(ORIGIN, DESTINATION)
        self.elapsed += 59.999
        self.assertIsNotNone(self.service.peek(ORIGIN, DESTINATION))
        self.elapsed += .001
        self.assertIsNone(self.service.peek(ORIGIN, DESTINATION))
        self.requester.side_effect = requests.Timeout("fake-secret")
        with self.assertRaises(TaxiError):
            self.service.estimate(ORIGIN, DESTINATION)
        self.assertIsNone(self.service.peek(ORIGIN, DESTINATION))

    def test_lru_capacity_and_route_options_are_part_of_cache_key(self):
        service = TaxiService("fake", self.requester, max_cache_entries=2)
        points = [dict(DESTINATION, lat=DESTINATION["lat"] + offset) for offset in (0, .001, .002)]
        for point in points:
            service.estimate(ORIGIN, point)
        self.assertIsNone(service.peek(ORIGIN, points[0]))
        self.assertIsNotNone(service.peek(ORIGIN, points[2]))
        self.assertEqual(self.requester.call_count, 3)
        service.ROUTE_OPTIONS = dict(service.ROUTE_OPTIONS, searchOption="99")
        self.assertIsNone(service.peek(ORIGIN, points[2]))

    def test_service_boundary_rejects_bad_coordinates_and_split_count_without_network(self):
        for value in (True, "NaN", float("inf"), 91, 10 ** 400):
            with self.subTest(value=value):
                with self.assertRaises(MobilityInputError):
                    self.service.estimate(dict(ORIGIN, lat=value), DESTINATION)
        for count in (True, 0, 5, 2.0, "2"):
            with self.subTest(count=count):
                with self.assertRaises(MobilityInputError):
                    self.service.estimate(ORIGIN, DESTINATION, count)
        self.requester.assert_not_called()


if __name__ == "__main__":
    unittest.main()
