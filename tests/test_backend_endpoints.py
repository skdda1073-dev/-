"""실제 Flask·CSV·서비스를 연결하되 모든 외부 응답은 가짜로 주입한다."""

import unittest
from unittest.mock import Mock, patch

import app as application
from services.place_search_service import PlaceSearchService
from services.public_transit_client import PublicDataError, PublicTransitClient
from services.settings import Settings
from services.transit_service import TransitService
from services.walking_service import WalkingService


class BackendEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = application.app.test_client()
        self.settings = Settings(tmap_app_key="fake-private-tmap", public_data_key="fake-private-public")

    def test_health_has_only_configuration_booleans_and_never_calls_api(self):
        with patch.object(application, "settings", self.settings), \
                patch.object(application.transit_client, "arrivals", side_effect=AssertionError("no network")), \
                patch.object(application.walking_service, "get_time", side_effect=AssertionError("no network")):
            response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")
        self.assertFalse(response.json["external_apis_verified"])
        self.assertTrue(all(isinstance(value, bool) for value in response.json["configured"].values()))
        self.assertNotIn("fake-private", response.get_data(as_text=True))
        self.assertNotIn("/Users/", response.get_data(as_text=True))

    def test_live_arrival_auth_error_is_visible_without_breaking_csv_candidates(self):
        client = Mock(spec=PublicTransitClient)
        client.arrivals.side_effect = PublicDataError("403", "fake-private-raw-error")
        service = TransitService(client, lambda *args: (2, 100), application.transit_repository)
        stops = application.transit_repository.route_stops("CCB250001202")
        point = lambda stop: {"name": stop["nodenm"], "lat": stop["gpslati"], "lon": stop["gpslong"]}
        result = service.plan(point(stops[11]), point(stops[18]))
        self.assertTrue(result["journeys"])
        self.assertEqual(result["arrival_status"]["code"], "403")
        self.assertEqual(result["arrival_status"]["status"], "unavailable")
        self.assertNotIn("fake-private", str(result))

    def test_empty_arrivals_mean_connected_but_no_current_bus_values(self):
        client = Mock(spec=PublicTransitClient)
        client.arrivals.return_value = []
        service = TransitService(client, lambda *args: (2, 100), application.transit_repository)
        stops = application.transit_repository.route_stops("CCB250001202")
        point = lambda stop: {"name": stop["nodenm"], "lat": stop["gpslati"], "lon": stop["gpslong"]}
        result = service.plan(point(stops[11]), point(stops[18]))
        self.assertEqual(result["arrival_status"]["status"], "empty")
        self.assertIsNone(result["arrival_status"]["code"])

    def test_health_without_tmap_is_not_ready(self):
        with patch.object(application, "settings", Settings()):
            response = self.client.get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["status"], "degraded")

    def test_health_csv_failure_does_not_expose_error_path(self):
        with patch.object(application, "settings", self.settings), \
                patch.object(application.transit_repository, "metadata", side_effect=PublicDataError("CSV_DATA", "/private/path")):
            response = self.client.get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["checks"]["csv"]["code"], "CSV_DATA")
        self.assertNotIn("/private/path", response.get_data(as_text=True))

    def test_empty_search_does_not_call_remote(self):
        remote = Mock(side_effect=AssertionError("no network"))
        with patch.object(application, "place_search_service", PlaceSearchService("fake", {}, remote)):
            response = self.client.get("/search_poi?q=")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, [])
        remote.assert_not_called()

    def test_search_missing_key_is_configuration_error_not_empty_success(self):
        with patch.object(application, "place_search_service", PlaceSearchService("", {})):
            response = self.client.get("/search_poi?q=춘천역")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["code"], "TMAP_KEY_MISSING")

    def test_campus_matches_survive_external_failure_with_visible_partial_flag(self):
        service = PlaceSearchService("", {"hallym": {"도서관": (37.88, 127.73)}, "knu": {"도서관": (37.87, 127.74)}})
        with patch.object(application, "place_search_service", service):
            response = self.client.get("/search_poi?q=도서관&campus=knu")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Search-Partial"], "true")
        self.assertEqual(len(response.json), 2)
        self.assertEqual(response.json[0]["campus"], "knu")
        self.assertTrue(response.json[0]["search_warning"])

    def test_invalid_search_is_rejected_at_http_boundary(self):
        service = PlaceSearchService("fake", {"hallym": {}})
        with patch.object(application, "place_search_service", service):
            for query in ({"q": "가" * 101}, {"q": "도서관", "campus": "invalid"}):
                with self.subTest(query=query):
                    response = self.client.get("/search_poi", query_string=query)
                    self.assertEqual(response.status_code, 400)

    def test_search_to_routing_uses_pedestrian_destination_and_actual_csv(self):
        # 가짜 응답을 주입하지만 정류장·경유 순서는 실제 프로젝트 CSV다.
        stops = application.transit_repository.route_stops("CCB250001202")
        origin, destination = stops[11], stops[18]
        poi_response = Mock(status_code=200)
        poi_response.json.return_value = {"searchPoiInfo": {"totalCount": "1", "pois": {"poi": [{
            "id": "fake-poi", "name": "춘천역", "upperAddrName": "강원특별자치도", "middleAddrName": "춘천시",
            "pnsLat": destination["gpslati"], "pnsLon": destination["gpslong"],
            "noorLat": 37.9, "noorLon": 127.8,
        }]}}}
        poi_request = Mock(return_value=poi_response)
        walking_response = Mock(status_code=200)
        walking_response.json.return_value = {"features": [{"properties": {"totalTime": 120, "totalDistance": 100}}]}
        walking_request = Mock(return_value=walking_response)
        arrival_response = Mock(status_code=200)
        arrival_response.json.return_value = {"response": {"header": {"resultCode": "00"}, "body": {
            "items": {"item": [{"routeid": "CCB250001202", "arrtime": 600}]}, "totalCount": 1,
        }}}
        arrival_request = Mock(return_value=arrival_response)
        service = TransitService(PublicTransitClient("fake-private-public", "32010", arrival_request),
                                 WalkingService("fake-private-tmap", walking_request).get_time,
                                 application.transit_repository)
        with patch.object(application, "place_search_service", PlaceSearchService("fake-private-tmap", {}, poi_request)), \
                patch.object(application, "transit_service", service):
            places = self.client.get("/search_poi?q=춘천역")
            self.assertEqual(places.status_code, 200)
            selected = places.json[0]
            self.assertEqual(selected["coordinate_source"], "pns")
            self.assertEqual(selected["lat"], destination["gpslati"])
            response = self.client.post("/get_route_info", json={
                "lat": origin["gpslati"] + 0.0001, "lon": origin["gpslong"], "location": "검증 출발",
                "dest_name": selected["name"], "dest_lat": selected["lat"], "dest_lon": selected["lon"],
            })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["journeys"])
        self.assertEqual(response.json["destination"]["lat"], selected["lat"])
        self.assertEqual(response.json["source"]["source"], "csv")
        self.assertTrue(walking_request.called)
        self.assertTrue(arrival_request.called)
        self.assertTrue(all("ArvlInfoInqireService" in call.args[0] for call in arrival_request.call_args_list))
        self.assertTrue(all(item["total_min"] is None for item in response.json["journeys"]))
        self.assertNotIn("fake-private", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
