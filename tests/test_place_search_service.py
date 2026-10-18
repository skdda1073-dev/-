"""TMAP은 모두 가짜 응답을 사용하며 실제 키·외부 호출이 필요 없다."""

import unittest
from unittest.mock import Mock

import requests

from services.place_search_service import PlaceSearchError, PlaceSearchService


BUILDINGS = {
    "hallym": {"도서관": (37.885, 127.739), "공학관": (37.886, 127.740)},
    "knu": {"도서관": (37.868, 127.744), "공학관": (37.867, 127.745)},
}


def poi(name="춘천역", poi_id="100", **updates):
    item = {"id": poi_id, "name": name, "upperAddrName": "강원특별자치도",
            "middleAddrName": "춘천시", "lowerAddrName": "근화동",
            "noorLat": "37.884", "noorLon": "127.716"}
    item.update(updates)
    return item


def response(items=None, status=200, payload=None):
    result = Mock(status_code=status)
    rows = [] if items is None else items
    result.json.return_value = payload if payload is not None else {
        "searchPoiInfo": {"totalCount": str(len(rows)), "pois": {"poi": rows}},
    }
    return result


class PlaceSearchServiceTests(unittest.TestCase):
    def service(self, result=None, key="test-key"):
        requester = Mock(return_value=result or response())
        return PlaceSearchService(key, BUILDINGS, requester), requester

    def test_empty_query_does_not_request(self):
        service, requester = self.service()
        self.assertEqual(service.search("  \t "), [])
        requester.assert_not_called()

    def test_invalid_query_and_campus_are_400(self):
        service, requester = self.service()
        for query, campus in ((None, None), ("가" * 101, None), ("춘천\x00역", None),
                              ("춘천역", "unknown"), ("춘천역", [])):
            with self.subTest(query=query, campus=campus):
                with self.assertRaises(PlaceSearchError) as raised:
                    service.search(query, campus)
                self.assertEqual(raised.exception.status_code, 400)
        requester.assert_not_called()

    def test_same_named_campus_buildings_are_not_overwritten(self):
        service, _ = self.service()
        rows = service.search("도서관", "hallym")
        self.assertEqual([row["campus"] for row in rows], ["hallym", "knu"])
        self.assertNotEqual(rows[0]["lat"], rows[1]["lat"])
        self.assertNotEqual(rows[0]["id"], rows[1]["id"])

    def test_selected_campus_only_prioritizes_not_filters_destination(self):
        service, _ = self.service()
        rows = service.search("도서관", "knu")
        self.assertEqual([row["campus"] for row in rows], ["knu", "hallym"])

    def test_school_alias_query_scopes_to_the_requested_campus(self):
        service, _ = self.service()
        for query in ("강원대 도서관", "강원대학교도서관", "춘천 강원대 도서관"):
            with self.subTest(query=query):
                rows = service.search(query, "hallym")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["campus"], "knu")

    def test_missing_key_is_explicit_when_no_local_results(self):
        service, requester = self.service(key="")
        with self.assertRaises(PlaceSearchError) as raised:
            service.search("춘천역")
        self.assertEqual(raised.exception.code, "TMAP_KEY_MISSING")
        self.assertEqual(raised.exception.status_code, 503)
        requester.assert_not_called()

    def test_missing_key_preserves_local_results_with_warning(self):
        service, requester = self.service(key="")
        rows = service.search("도서관")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["search_warning"]["code"], "TMAP_KEY_MISSING")
        requester.assert_not_called()

    def test_external_failure_preserves_local_results_without_catching_input_errors(self):
        service, requester = self.service()
        requester.side_effect = requests.Timeout("test-key and raw provider URL")
        rows = service.search("도서관")
        self.assertTrue(all(row["search_warning"]["code"] == "TMAP_TIMEOUT" for row in rows))
        self.assertNotIn("test-key", str(rows))

    def test_search_contract_uses_header_key_wgs84_timeout_and_no_redirects(self):
        service, requester = self.service(response([poi()]))
        rows = service.search("춘천역")
        args, kwargs = requester.call_args
        self.assertEqual(args[0], "https://apis.openapi.sk.com/tmap/pois")
        self.assertEqual(kwargs["headers"]["appKey"], "test-key")
        self.assertNotIn("appKey", kwargs["params"])
        self.assertEqual(kwargs["params"]["searchKeyword"], "춘천역")
        self.assertEqual(kwargs["params"]["reqCoordType"], "WGS84GEO")
        self.assertEqual(kwargs["params"]["resCoordType"], "WGS84GEO")
        self.assertEqual(kwargs["params"]["searchType"], "all")
        self.assertEqual(kwargs["timeout"], (3, 6))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(rows[0]["source"], "tmap")

    def test_adds_chuncheon_context_once_and_collapses_whitespace(self):
        service, requester = self.service(response([poi()]))
        service.search("  시외버스   터미널 ")
        self.assertEqual(requester.call_args.kwargs["params"]["searchKeyword"], "춘천 시외버스 터미널")
        service.search("춘천 시외버스 터미널")
        self.assertEqual(requester.call_args.kwargs["params"]["searchKeyword"], "춘천 시외버스 터미널")

    def test_prefers_pedestrian_entrance_then_regular_entrance_then_center(self):
        values = [poi(pnsLat="37.881", pnsLon="127.711", frontLat="37.882", frontLon="127.712"),
                  poi(poi_id="101", pnsLat="0", pnsLon="0", frontLat="37.882", frontLon="127.712"),
                  poi(poi_id="102", pnsLat="nan", pnsLon="127.711", frontLat="", frontLon="")]
        service, _ = self.service(response(values))
        rows = service.search("춘천역")
        self.assertEqual([row["coordinate_source"] for row in rows], ["pns", "front", "noor"])
        self.assertEqual([row["lat"] for row in rows], [37.881, 37.882, 37.884])

    def test_invalid_coordinate_pairs_are_never_mixed_or_zero_filled(self):
        values = [poi(pnsLat="37.881", pnsLon="", frontLat="", frontLon="127.712"),
                  poi(poi_id="101", noorLat="0", noorLon="0"),
                  poi(poi_id="102", noorLat="nan"), poi(poi_id="103", noorLon="inf"),
                  poi(poi_id="104", noorLat=True), poi(poi_id="105", noorLat="2000000"),
                  poi(poi_id="106", noorLat="1" + "0" * 500)]
        service, _ = self.service(response(values))
        rows = service.search("춘천역")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["lat"], rows[0]["lon"]), (37.884, 127.716))

    def test_non_chuncheon_results_are_filtered(self):
        values = [poi(name="서울역", middleAddrName="용산구", upperAddrName="서울", noorLat="37.55", noorLon="126.97"),
                  poi(name="홍천역", middleAddrName="홍천군"), poi()]
        service, _ = self.service(response(values))
        self.assertEqual([row["name"] for row in service.search("역")], ["춘천역"])

    def test_full_road_address_can_verify_chuncheon(self):
        value = poi(upperAddrName="", middleAddrName="", lowerAddrName="", newAddressList={
            "newAddress": [{"fullAddressRoad": "강원특별자치도 춘천시 평화로 1"}],
        })
        service, _ = self.service(response([value]))
        self.assertEqual(service.search("춘천역")[0]["sub"], "강원특별자치도 춘천시 평화로 1")

    def test_same_named_different_locations_preserved_and_exact_duplicates_removed(self):
        values = [poi("편의점", "1"), poi("편의점", "2", noorLat="37.885"),
                  poi("편의점", "1"), poi("편의점", "1", noorLat="37.886")]
        service, _ = self.service(response(values))
        rows = service.search("편의점")
        self.assertEqual(len(rows), 3)
        self.assertEqual([row["lat"] for row in rows], [37.884, 37.885, 37.886])

    def test_missing_poi_id_deduplicates_by_name_and_coordinates(self):
        values = [poi("편의점", ""), poi("편의점", ""), poi("편의점", "", noorLat="37.885")]
        service, _ = self.service(response(values))
        self.assertEqual(len(service.search("편의점")), 2)

    def test_limits_to_eight_results(self):
        service, _ = self.service(response([poi(poi_id=str(n)) for n in range(20)]))
        self.assertEqual(len(service.search("춘천역")), 8)

    def test_zero_count_is_successful_empty_result(self):
        service, _ = self.service(response(payload={"searchPoiInfo": {"totalCount": "0"}}))
        self.assertEqual(service.search("없는장소"), [])

    def test_single_poi_object_is_normalized(self):
        service, _ = self.service(response(payload={
            "searchPoiInfo": {"totalCount": "1", "pois": {"poi": poi()}},
        }))
        self.assertEqual(len(service.search("춘천역")), 1)

    def test_auth_quota_http_errors_are_sanitized(self):
        for status, code, expected_http in ((401, "TMAP_AUTH", 502), (403, "TMAP_AUTH", 502),
                                             (429, "TMAP_QUOTA", 503), (500, "TMAP_HTTP", 502),
                                             (302, "TMAP_HTTP", 502)):
            with self.subTest(status=status):
                service, _ = self.service(response(status=status))
                with self.assertRaises(PlaceSearchError) as raised:
                    service.search("춘천역")
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(raised.exception.status_code, expected_http)
                self.assertNotIn("test-key", str(raised.exception))

    def test_network_and_timeout_errors_are_sanitized(self):
        for exception, code, expected_http in (
            (requests.ConnectionError("URL?appKey=test-key"), "TMAP_NETWORK", 502),
            (requests.Timeout("URL?appKey=test-key"), "TMAP_TIMEOUT", 504),
        ):
            with self.subTest(code=code):
                service, requester = self.service()
                requester.side_effect = exception
                with self.assertRaises(PlaceSearchError) as raised:
                    service.search("춘천역")
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(raised.exception.status_code, expected_http)
                self.assertNotIn("test-key", str(raised.exception))

    def test_malformed_json_is_not_a_fake_empty_result(self):
        service, requester = self.service()
        requester.return_value.json.side_effect = ValueError("test-key raw JSON")
        with self.assertRaises(PlaceSearchError) as raised:
            service.search("춘천역")
        self.assertEqual(raised.exception.code, "TMAP_RESPONSE")
        self.assertNotIn("test-key", str(raised.exception))

    def test_malformed_response_shapes_are_explicit(self):
        for payload in ([], {}, {"error": {"message": "test-key"}},
                        {"searchPoiInfo": []}, {"searchPoiInfo": {"totalCount": "bad"}},
                        {"searchPoiInfo": {"totalCount": True}},
                        {"searchPoiInfo": {"totalCount": "1", "pois": {"poi": []}}},
                        {"searchPoiInfo": {"totalCount": "1", "pois": {"poi": [None]}}},
                        {"searchPoiInfo": {"totalCount": "1", "pois": {"poi": [poi(noorLat="0")]}}}):
            with self.subTest(payload=payload):
                service, _ = self.service(response(payload=payload))
                with self.assertRaises(PlaceSearchError) as raised:
                    service.search("춘천역")
                self.assertEqual(raised.exception.code, "TMAP_RESPONSE")
                self.assertNotIn("test-key", str(raised.exception))

    def test_partial_warning_does_not_persist_to_later_success(self):
        service, requester = self.service()
        requester.side_effect = [requests.Timeout(), response()]
        self.assertIn("search_warning", service.search("도서관")[0])
        self.assertNotIn("search_warning", service.search("도서관")[0])


if __name__ == "__main__":
    unittest.main()
