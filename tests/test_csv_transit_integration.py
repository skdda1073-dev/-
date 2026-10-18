"""실제 춘천 CSV와 경로 서비스 연결을 외부 통신 없이 검증한다."""

import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from services.csv_transit_repository import CsvTransitRepository
from services.public_transit_client import PublicDataError, PublicTransitClient
from services.route_planner import plan_journeys
from services.transit_service import TransitService
from services.walking_service import WalkingService


DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "chuncheon_bus_routes_20260326.csv"


def position(stop, name=None):
    return {"name": name or stop["nodenm"], "lat": stop["gpslati"], "lon": stop["gpslong"]}


def csv_route(repository, route_id):
    info = repository.route_info(route_id)
    return {
        "route_no": info["routeno"], "end_node": info["endnodenm"],
        "sequence_basis": info["sequence_basis"], "direction_verified": info["direction_verified"],
        "route_number_verified": info["route_number_verified"], "stops": repository.route_stops(route_id),
    }


class ActualCsvRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repository = CsvTransitRepository(DATA_PATH)

    def plan_route(self, route_id, first_index, last_index, with_live_arrival=False):
        route = csv_route(self.repository, route_id)
        stops = route["stops"]
        board, alight = stops[first_index], stops[last_index]
        arrivals = {board["nodeid"]: [{"routeid": route_id, "arrtime": 600}]} if with_live_arrival else {}
        return plan_journeys(
            position(board), position(alight), [board], [alight],
            {route_id: route}, arrivals, lambda *args: (1, 70), max_transfers=0,
        )

    def test_actual_csv_counts_and_source_dates_are_exposed(self):
        metadata = self.repository.metadata()
        self.assertEqual(metadata["row_count"], 16168)
        self.assertEqual(metadata["route_count"], 223)
        self.assertEqual(metadata["stop_count_all"], 1853)
        self.assertEqual(metadata["data_dates"], ["2025-02-12", "2026-03-26"])
        self.assertEqual(metadata["unverified_route_count"], 10)

    def test_real_12s_keeps_full_forward_sequence(self):
        journeys = self.plan_route("CCB250001202", 0, -1)
        self.assertEqual(len(journeys), 1)
        leg = journeys[0]["legs"][0]
        self.assertEqual(leg["route_no"], "12-S")
        self.assertEqual(leg["stop_count"], 47)
        self.assertEqual(len(leg["path"]), 48)
        self.assertEqual(leg["board"]["name"], "상공회의소")
        self.assertEqual(leg["alight"]["name"], "소양강댐정상")

    def test_actual_campus_commute_stop_ids_and_counts(self):
        cases = [
            ("CCB250001202", 11, 18, "CCB250002127", "CCB250026552", 7),
            ("CCB250040000", 6, 17, "CCB250001275", "CCB250026903", 11),
            ("CCB250040000", 39, 49, "CCB250026904", "CCB250002127", 10),
        ]
        for route_id, start, end, board_id, alight_id, count in cases:
            with self.subTest(route_id=route_id, board=board_id):
                leg = self.plan_route(route_id, start, end)[0]["legs"][0]
                self.assertEqual((leg["board"]["id"], leg["alight"]["id"]), (board_id, alight_id))
                self.assertEqual(leg["stop_count"], count)
                self.assertEqual(len(leg["path"]), count + 1)

    def test_actual_csv_flask_endpoint_works_without_route_or_arrival_keys(self):
        import app as application
        network = Mock(side_effect=AssertionError("오프라인 검증에서 외부 호출 금지"))
        service = TransitService(PublicTransitClient("", "32010", network),
                                 WalkingService("", network).get_time, self.repository)
        stops = self.repository.route_stops("CCB250001202")
        board, alight = stops[11], stops[18]
        with patch.object(application, "transit_service", service):
            response = application.app.test_client().post("/get_route_info", json={
                "lat": board["gpslati"], "lon": board["gpslong"], "location": board["nodenm"],
                "dest_lat": alight["gpslati"], "dest_lon": alight["gpslong"], "dest_name": alight["nodenm"],
            })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["journeys"])
        self.assertEqual(response.json["source"]["source"], "csv")
        self.assertTrue(all(item["total_min"] is None for item in response.json["journeys"]))
        self.assertTrue(any("PUBLIC_DATA_KEY" in text for text in response.json["warnings"]))
        network.assert_not_called()

    def test_actual_csv_flask_missing_walk_key_is_not_no_bus_route(self):
        import app as application
        network = Mock(side_effect=AssertionError("오프라인 검증에서 외부 호출 금지"))
        service = TransitService(PublicTransitClient("", "32010", network),
                                 WalkingService("", network).get_time, self.repository)
        board, alight = self.repository.route_stops("CCB250001202")[11:19:7]
        with patch.object(application, "transit_service", service):
            response = application.app.test_client().post("/get_route_info", json={
                "lat": board["gpslati"] + 0.000001, "lon": board["gpslong"],
                "dest_lat": alight["gpslati"], "dest_lon": alight["gpslong"], "dest_name": alight["nodenm"],
            })
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json["code"], "WALK_UNAVAILABLE")
        self.assertIn("TMAP_APP_KEY", response.json["error"])
        network.assert_not_called()

    def test_real_400_does_not_invent_a_midpoint_direction_boundary(self):
        journeys = self.plan_route("CCB250040000", 0, -1)
        self.assertEqual(len(journeys), 1)
        leg = journeys[0]["legs"][0]
        self.assertEqual(leg["route_no"], "400")
        self.assertEqual(leg["stop_count"], 56)
        self.assertEqual(len(leg["path"]), 57)
        self.assertEqual(leg["board"]["id"], "CCB250001357")
        self.assertEqual(leg["alight"]["id"], "CCB250026552")

    def test_real_route_sequences_cannot_be_travelled_backwards(self):
        for route_id in ("CCB250001202", "CCB250040000"):
            with self.subTest(route_id=route_id):
                self.assertEqual(self.plan_route(route_id, -1, 0), [])

    def test_real_400_terminal_does_not_wrap_to_first_stop(self):
        self.assertEqual(self.plan_route("CCB250040000", -1, 1), [])

    def test_csv_live_arrival_reference_cannot_become_a_total_eta(self):
        journey = self.plan_route("CCB250001202", 0, -1, with_live_arrival=True)[0]
        self.assertIsNone(journey["total_min"])
        self.assertIsNone(journey["wait_min"])
        self.assertFalse(journey["has_live_departure"])
        self.assertTrue(journey["legs"][0]["arrival_reference_only"])

    def test_unidentified_api_direction_is_not_promoted_to_csv_order(self):
        route = csv_route(self.repository, "CCB250001202")
        route.pop("sequence_basis")
        route.pop("direction_verified")
        for stop in route["stops"]:
            stop.pop("sequence_basis", None)
            stop.pop("direction_verified", None)
        board, alight = route["stops"][0], route["stops"][-1]
        result = plan_journeys(position(board), position(alight), [board], [alight],
                              {"CCB250001202": route}, {}, lambda *args: (1, 70))
        self.assertEqual(result, [])

    def test_conflicting_stop_breaks_topology_instead_of_becoming_a_shortcut(self):
        route = csv_route(self.repository, "CCB250001202")
        route["stops"][20]["topology_break"] = True
        route["stops"][20]["stop_conflict"] = True
        board, alight = route["stops"][0], route["stops"][-1]
        result = plan_journeys(position(board), position(alight), [board], [alight],
                              {"CCB250001202": route}, {}, lambda *args: (1, 70), max_transfers=0)
        self.assertEqual(result, [])


class CsvServiceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name) / "fixture.csv"
        fields = ["노선번호", "노선", "정류장순서", "정류장", "정류장명", "경도", "위도", "데이터기준일"]
        self.stops = [
            {"nodeid": "CCB101", "nodenm": "승차", "gpslati": 37.88, "gpslong": 127.73},
            {"nodeid": "CCB102", "nodenm": "중간", "gpslati": 37.87, "gpslong": 127.73},
            {"nodeid": "CCB103", "nodenm": "하차", "gpslati": 37.86, "gpslong": 127.73},
        ]
        with path.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            for index, stop in enumerate(self.stops, 1):
                writer.writerow({
                    "노선번호": "10", "노선": "900", "정류장순서": index,
                    "정류장": stop["nodeid"][3:], "정류장명": stop["nodenm"],
                    "위도": stop["gpslati"], "경도": stop["gpslong"], "데이터기준일": "2026-03-26",
                })
        self.repository = CsvTransitRepository(path)
        self.client = Mock(spec=PublicTransitClient)
        for operation in ("nearby_stops", "station_routes", "route_stops"):
            getattr(self.client, operation).side_effect = AssertionError("CSV 모드에서 정적 노선 API를 호출했습니다.")
        self.client.arrivals.side_effect = PublicDataError("KEY_MISSING", "실시간 도착 키가 없습니다.")
        self.walking = Mock(return_value=(2, 100))
        self.origin = {**position(self.stops[0]), "lat": 37.8801}
        self.destination = {**position(self.stops[-1]), "lat": 37.8599}

    def service(self, walking=None):
        return TransitService(self.client, self.walking if walking is None else walking,
                              topology_repository=self.repository)

    def assert_static_api_not_called(self):
        self.client.nearby_stops.assert_not_called()
        self.client.station_routes.assert_not_called()
        self.client.route_stops.assert_not_called()

    def test_missing_arrival_key_still_allows_csv_route_search(self):
        response = self.service().plan(self.origin, self.destination)
        self.assertEqual(response["mode"], "journeys")
        self.assertEqual(response["source"]["source"], "csv")
        self.assertEqual(response["source"]["route_count"], 1)
        self.assertEqual(len(response["journeys"]), 1)
        journey = response["journeys"][0]
        self.assertEqual(journey["legs"][0]["stop_count"], 2)
        self.assertEqual(journey["egress_walk"]["minutes"], 2)
        self.assertIsNone(journey["total_min"])
        self.assertFalse(journey["has_live_departure"])
        self.assertTrue(response["warnings"])
        self.assert_static_api_not_called()

    def test_live_arrival_remains_reference_only_for_unverified_csv_direction(self):
        self.client.arrivals.side_effect = None
        self.client.arrivals.return_value = [{"routeid": "CCB900", "arrtime": 600}]
        response = self.service().plan(self.origin, self.destination)
        journey = response["journeys"][0]
        self.assertIsNone(journey["total_min"])
        self.assertIsNone(journey["wait_min"])
        self.assertFalse(journey["has_live_departure"])
        self.assertEqual(journey["total_unavailable_reason"], "direction_unverified")
        self.assertTrue(journey["legs"][0]["arrival_reference_only"])
        self.assert_static_api_not_called()

    def test_failed_walking_is_actionable_and_never_zero(self):
        with self.assertRaises(PublicDataError) as raised:
            self.service(walking=lambda *args: (-1, -1)).plan(self.origin, self.destination)
        self.assertEqual(raised.exception.code, "WALK_UNAVAILABLE")
        self.assert_static_api_not_called()

    def test_nearby_also_uses_csv_when_arrivals_unavailable(self):
        response = self.service().nearby(self.origin)
        self.assertEqual(len(response), 1)
        self.assertEqual(response[0]["station_name"], "승차")
        self.assertEqual(response[0]["walk_min"], 2)
        self.assertEqual(response[0]["buses"], [])
        self.assert_static_api_not_called()

    def test_only_exact_coincident_walks_succeed_without_a_walk_key(self):
        requester = Mock(side_effect=AssertionError("동일 좌표 도보는 외부 호출이 필요하지 않습니다."))
        walking_service = WalkingService("", requester=requester)
        response = self.service(walking_service.get_time).plan(position(self.stops[0]), position(self.stops[-1]))
        self.assertTrue(response["journeys"])
        self.assertEqual(response["journeys"][0]["walk_min"], 0)
        requester.assert_not_called()
        self.assert_static_api_not_called()

    def test_production_application_wires_the_checked_in_csv_repository(self):
        import app as application
        self.assertIsInstance(application.transit_repository, CsvTransitRepository)
        self.assertEqual(application.transit_repository.path.resolve(), DATA_PATH.resolve())


if __name__ == "__main__":
    unittest.main()
