"""임시 Flask에 택시 Blueprint만 연결하며 실제 키·CSV·네트워크를 읽지 않는다."""

from datetime import timezone
import unittest
from unittest.mock import Mock
from uuid import uuid4

from flask import Flask

from services.mobility_validation import (
    MobilityInputError, aware_datetime, clean_text, named_point, uuid_text,
)
from services.taxi_routes import create_taxi_blueprint
from services.taxi_service import TaxiError


class TaxiApiTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.estimate.return_value = {"status": "ok", "fare": {"amount_krw": 10001}}
        self.app = Flask(__name__)
        self.app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
        self.app.register_blueprint(create_taxi_blueprint(self.service, {
            "hallym": {"도서관": (37.88, 127.73)}, "knu": {"도서관": (37.87, 127.74)},
        }))
        self.client = self.app.test_client()
        self.payload = {"lat": 37.88, "lon": 127.73, "location": "가상 출발지",
                        "dest_lat": 37.86, "dest_lon": 127.72, "dest_name": "가상 목적지"}

    def post(self, payload):
        return self.client.post("/api/taxi/estimate", json=payload)

    def test_gps_request_has_independent_origin_destination_and_split(self):
        result = self.post(dict(self.payload, split_count=3))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["Cache-Control"], "no-store")
        self.service.estimate.assert_called_once_with(
            {"name": "가상 출발지", "lat": 37.88, "lon": 127.73},
            {"name": "가상 목적지", "lat": 37.86, "lon": 127.72}, split_count=3,
        )

    def test_building_origin_is_resolved_from_requested_campus_without_bus_response(self):
        payload = {key: value for key, value in self.payload.items() if key not in ("lat", "lon")}
        payload.update(location="도서관", campus="knu")
        result = self.post(payload)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.service.estimate.call_args.args[0], {"name": "도서관", "lat": 37.87, "lon": 127.74})

    def test_numeric_strings_and_optional_names_match_existing_valid_requests(self):
        payload = {"lat": "37.88", "lon": "127.73", "dest_lat": "37.86", "dest_lon": "127.72"}
        result = self.post(payload)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.service.estimate.call_args.args[0]["name"], "내 실시간 위치")
        self.assertEqual(self.service.estimate.call_args.args[1]["name"], "선택한 목적지")
        self.assertEqual(self.service.estimate.call_args.kwargs, {"split_count": 1})

    def test_non_objects_malformed_json_and_invalid_names_are_400(self):
        for payload in (None, [], "x", 3, dict(self.payload, location=[]), dict(self.payload, location=123),
                        dict(self.payload, dest_name={}), dict(self.payload, dest_name=" "),
                        dict(self.payload, location="가" * 101), dict(self.payload, dest_name="장소\n다음")):
            with self.subTest(payload=payload):
                result = self.post(payload)
                self.assertEqual(result.status_code, 400)
                self.assertEqual(set(result.json), {"code", "error"})
                self.assertEqual(result.headers["Cache-Control"], "no-store")
        result = self.client.post("/api/taxi/estimate", data="{bad", content_type="application/json")
        self.assertEqual(result.status_code, 400)
        self.service.estimate.assert_not_called()

    def test_partial_missing_invalid_and_out_of_range_coordinates_are_400(self):
        cases = [{key: value for key, value in self.payload.items() if key != missing}
                 for missing in ("lat", "lon", "dest_lat", "dest_lon")]
        cases.extend(dict(self.payload, **{field: value}) for field, value in (
            ("lat", True), ("lat", "NaN"), ("lat", float("inf")), ("lat", 10 ** 400),
            ("lat", 91), ("lon", -181), ("dest_lat", -91), ("dest_lon", "Infinity"), ("lon", []),
        ))
        cases.append({"location": "없는 건물", "dest_lat": 37.86, "dest_lon": 127.72})
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertEqual(self.post(payload).status_code, 400)
        self.service.estimate.assert_not_called()

    def test_campus_and_split_count_are_strictly_validated(self):
        for field, values in (("campus", (None, "unknown", [], 2)),
                              ("split_count", (None, True, False, "2", 2.0, 0, 5))):
            for value in values:
                with self.subTest(field=field, value=value):
                    result = self.post(dict(self.payload, **{field: value}))
                    self.assertEqual(result.status_code, 400)
        self.service.estimate.assert_not_called()

    def test_provider_error_contract_is_safe_and_not_user_login_error(self):
        self.service.estimate.side_effect = TaxiError("TAXI_PROVIDER_AUTH", "자동차 경로 상품 권한을 확인해 주세요.", 502)
        result = self.post(self.payload)
        self.assertEqual(result.status_code, 502)
        self.assertEqual(result.json["code"], "TAXI_PROVIDER_AUTH")
        self.assertEqual(result.headers["Cache-Control"], "no-store")

    def test_partial_quote_still_returns_success(self):
        self.service.estimate.return_value = {"status": "partial", "ride_duration_seconds": 600,
                                              "fare": {"amount_krw": None}}
        result = self.post(self.payload)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["ride_duration_seconds"], 600)
        self.assertIsNone(result.json["fare"]["amount_krw"])

    def test_body_limit_returns_json_413_without_provider_request(self):
        result = self.client.post("/api/taxi/estimate", data=" " * (16 * 1024 + 1), content_type="application/json")
        self.assertEqual(result.status_code, 413)
        self.assertEqual(result.json["code"], "REQUEST_TOO_LARGE")
        self.assertEqual(result.headers["Cache-Control"], "no-store")
        self.service.estimate.assert_not_called()


class MobilityValidationTests(unittest.TestCase):
    def test_text_normalization_and_named_point_keep_safe_scalar_types(self):
        self.assertEqual(clean_text("  가  나  ", "title", 60), "가  나")
        self.assertEqual(named_point({"name": " 장소 ", "lat": "37.88", "lon": "127.73"}, "meeting"),
                         {"name": "장소", "lat": 37.88, "lon": 127.73})
        for value in ({}, [], None, {"name": "장소", "lat": True, "lon": 127.73}):
            with self.subTest(value=value):
                with self.assertRaises(MobilityInputError):
                    named_point(value, "meeting")

    def test_timezone_is_required_and_normalized_to_utc(self):
        result = aware_datetime("2026-10-20T12:30:00+09:00", "departure_at")
        self.assertEqual((result.hour, result.minute, result.tzinfo), (3, 30, timezone.utc))
        for value in ("2026-10-20T12:30:00", "bad", None, True, "2026-99-99T12:00:00+09:00"):
            with self.subTest(value=value):
                with self.assertRaises(MobilityInputError) as raised:
                    aware_datetime(value, "departure_at")
                self.assertEqual(raised.exception.code, "INVALID_DEPARTURE_AT")
                self.assertEqual(raised.exception.status_code, 400)

    def test_uuid_canonicalization_and_invalid_identifier_rejection(self):
        identifier = uuid4()
        self.assertEqual(uuid_text(str(identifier).upper(), "client_room_id"), str(identifier))
        for value in (None, [], "not-a-uuid", "x" * 100):
            with self.subTest(value=value):
                with self.assertRaises(MobilityInputError):
                    uuid_text(value, "client_room_id")


if __name__ == "__main__":
    unittest.main()
