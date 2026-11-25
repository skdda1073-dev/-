"""National timeline API and provider joins. Outbound requests use test doubles."""

from datetime import datetime, timedelta
import unittest
from unittest.mock import Mock, patch

from app import CAMPUS_BUILDINGS, app, shuttle_profile, shuttle_repository
from services.motis_journey_provider import MotisProviderError
from services.national_journey_service import IntercityJourneyProvider, KST, NationalJourneyService, RegionalBusJourneyProvider
from services.shuttle_service import ShuttleService


ORIGIN = {"name": "한림대학교 의학관", "lat": 37.886, "lon": 127.737}
DESTINATION = {"name": "서울역", "lat": 37.555, "lon": 126.97}
AT = datetime(2026, 11, 24, 22, 0, tzinfo=KST)


def segment(mode, start, end, departure, arrival, fare=None, **extra):
    return {"mode": mode, "route_name": mode, "from_place": start, "to_place": end, "departure_at": departure,
            "arrival_at": arrival, "duration_min": None if arrival is None else (arrival - departure).total_seconds() / 60,
            "fare_won": fare, **extra}


class NationalEndpointTests(unittest.TestCase):
    def test_selected_building_uses_authoritative_coordinates_and_korea_time(self):
        building, coords = next(iter(CAMPUS_BUILDINGS["hallym"].items()))
        with patch("app.national_journey_service.plan", return_value={"status": "empty"}) as plan:
            response = app.test_client().post("/api/journeys", json={
                "origin": {"campus": "hallym", "building": building, "lat": -1, "lon": -1},
                "destination": DESTINATION,
                "departure_at": "2026-11-24T22:00",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual((plan.call_args.args[0]["lat"], plan.call_args.args[0]["lon"]), coords)
        self.assertEqual(plan.call_args.args[2], AT)

    def test_invalid_route_arguments_do_not_query_providers(self):
        with patch("app.national_journey_service.plan") as plan:
            client = app.test_client()
            for bad in ({"departure_at": "2026-11-24"},
                        {"origin": {"name": "Invalid", "lat": True, "lon": 127}},
                        {"destination": {"campus": "knu", "building": "존재하지 않는 건물"}}):
                with self.subTest(payload=bad):
                    response = client.post("/api/journeys", json={"origin": ORIGIN, "destination": DESTINATION, **bad})
                    self.assertEqual(response.status_code, 400, response.json)
                    self.assertIn("code", response.json)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(client.get("/api/journeys").status_code, 405)
            plan.assert_not_called()

    def test_national_place_scope_is_used_without_changing_local_default(self):
        with patch("app.place_search_service.search", return_value=[]) as search:
            client = app.test_client()
            self.assertEqual(client.get("/search_poi?q=서울역&scope=national").status_code, 200)
            self.assertEqual(search.call_args.kwargs["scope"], "national")
            self.assertEqual(client.get("/search_poi?q=서울역").status_code, 200)
            self.assertEqual(search.call_args.kwargs["scope"], "local")


class NationalTimelineTests(unittest.TestCase):
    def test_long_walking_connector_is_a_fallback_when_a_timed_subway_connects(self):
        jamsil = {"name": "잠실", "lat": 37.513305, "lon": 127.100133}
        gangbyeon = {"name": "강변", "lat": 37.535, "lon": 127.095}
        terminal = {"name": "동서울종합터미널", "lat": 37.5339323, "lon": 127.09476136}
        arrive_jamsil = AT + timedelta(minutes=28)
        first_train = segment("subway", ORIGIN, jamsil, AT, arrive_jamsil, route_name="8호선")
        long_walk = segment("walk", jamsil, terminal, arrive_jamsil, arrive_jamsil + timedelta(minutes=40))
        rail_board = arrive_jamsil + timedelta(minutes=8)
        rail_alight = rail_board + timedelta(minutes=4)
        rail_tail = [segment("subway", jamsil, gangbyeon, rail_board, rail_alight, route_name="2호선"),
                     segment("walk", gangbyeon, terminal, rail_alight, rail_alight + timedelta(minutes=3))]
        repository = Mock()
        service = NationalJourneyService(repository, Mock())
        walking_route = {"segments": [first_train, long_walk]}
        subway_route = {"segments": [first_train, *rail_tail]}
        repository.plan.return_value = [walking_route, subway_route]
        best, = service.plan(ORIGIN, terminal, AT)["journeys"]
        self.assertEqual(best["total_min"], 43)
        self.assertEqual(best["segments"][-2]["route_name"], "2호선")

        repository.plan.return_value = [walking_route, {**subway_route, "complete": False}]
        best, reference = service.plan(ORIGIN, terminal, AT)["journeys"]
        self.assertEqual(best["segments"][-1]["duration_min"], 40)
        self.assertIsNone(reference["arrival_at"])

        late_board = AT + timedelta(minutes=70)
        late_alight = late_board + timedelta(minutes=4)
        repository.plan.return_value = [walking_route, {"segments": [
            first_train, segment("subway", jamsil, gangbyeon, late_board, late_alight),
            segment("walk", gangbyeon, terminal, late_alight, late_alight + timedelta(minutes=3)),
        ]}]
        self.assertEqual([route["total_min"] for route in service.plan(ORIGIN, terminal, AT)["journeys"]], [68, 77])

    def test_measured_walking_fallback_is_used_when_primary_is_unavailable(self):
        origin = {"name": "강변역", "lat": 37.535, "lon": 127.095}
        destination = {"name": "동서울종합터미널", "lat": 37.5339323, "lon": 127.09476136}
        repository = Mock(**{"plan.return_value": []})
        fallback = Mock(return_value=(158 / 60, 166))
        result = NationalJourneyService(repository, lambda *_: (-1, -1), walking_fallback=fallback).plan(
            origin, destination, AT,
        )
        best, = result["journeys"]
        self.assertEqual(best["arrival_at"], (AT + timedelta(seconds=158)).isoformat())
        self.assertEqual(best["segments"][0]["distance_m"], 166)
        fallback.assert_called_once_with(origin["lat"], origin["lon"], origin["name"],
                                         destination["lat"], destination["lon"], destination["name"])

    def test_regional_bus_feeder_can_start_a_journey_to_chuncheon(self):
        station = {"name": "금곡역", "lat": 37.637414, "lon": 127.207931}
        arrival = AT + timedelta(minutes=20)
        feeder = {"gateway": station, "arrival_at": arrival, "segments": [
            segment("bus", DESTINATION, station, AT + timedelta(minutes=5), arrival),
        ]}
        rail = segment("subway", station, ORIGIN, arrival + timedelta(minutes=3), arrival + timedelta(minutes=67))
        repository = Mock(**{"transfer_stations.return_value": [station],
                             "plan.return_value": [{"segments": [*feeder["segments"], rail]}]})
        transfers = Mock(**{"feeders.return_value": [feeder]})
        service = NationalJourneyService(repository, Mock(), rail_bus_transfers=transfers)
        best, = service.plan(DESTINATION, ORIGIN, AT)["journeys"]
        transfers.feeders.assert_called_once_with(DESTINATION, [station], AT)
        self.assertEqual(repository.plan.call_args.kwargs["access_routes"], [feeder])
        self.assertEqual([leg["mode"] for leg in best["segments"]], ["wait", "bus", "wait", "subway"])
        self.assertEqual(best["total_min"], 87)

    def test_motis_and_existing_candidates_are_sorted_by_arrival_not_provider_order(self):
        early = AT + timedelta(minutes=70)
        later = AT + timedelta(minutes=90)
        motis = Mock()
        motis.plan.return_value = [
            {"source": "MOTIS", "segments": [segment("train", ORIGIN, DESTINATION, AT, later)]},
            {"source": "MOTIS", "segments": [segment("train", ORIGIN, DESTINATION, AT, early, realtime=True)]},
        ]
        repository = Mock()
        repository.plan.return_value = [{"segments": [segment("train", ORIGIN, DESTINATION, AT, early + timedelta(minutes=5))]}]
        result = NationalJourneyService(repository, Mock(), motis=motis).plan(ORIGIN, DESTINATION, AT)
        self.assertEqual([journey["total_min"] for journey in result["journeys"]], [70, 75, 90])
        self.assertEqual(result["journeys"][0]["source"], "MOTIS")
        self.assertTrue(result["journeys"][0]["segments"][0]["realtime"])
        motis.plan.assert_called_once_with(ORIGIN, DESTINATION, AT)

    def test_motis_outage_preserves_an_available_route(self):
        repository = Mock()
        repository.plan.return_value = [{"segments": [segment("train", ORIGIN, DESTINATION, AT, AT + timedelta(minutes=100))]}]
        motis = Mock()
        motis.plan.side_effect = MotisProviderError("provider unavailable")
        result = NationalJourneyService(repository, Mock(), motis=motis).plan(ORIGIN, DESTINATION, AT)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["journeys"][0]["total_min"], 100)
        self.assertTrue(any("MOTIS" in warning for warning in result["warnings"]))

    def test_reference_fills_an_empty_search_but_is_skipped_when_a_timed_trip_resolves(self):
        repository = Mock(**{"plan.return_value": []})
        reference_provider = Mock(**{"plan_reference.return_value": [{
            "reference_kind": "ktdb_2025_03_weekday", "complete": False,
            "segments": [segment("bus", ORIGIN, DESTINATION, None, None)],
        }]})
        service = NationalJourneyService(repository, Mock(), ktdb_reference=reference_provider)
        result = service.plan(ORIGIN, DESTINATION, AT)
        self.assertEqual(result["status"], "partial")
        [reference] = result["journeys"]
        self.assertEqual(reference["reference_kind"], "ktdb_2025_03_weekday")
        self.assertIsNone(reference["total_min"])
        self.assertNotIn("확인 가능한 운행표 기준입니다.", " ".join(result["warnings"]))
        reference_provider.plan_reference.assert_called_once_with(ORIGIN, DESTINATION, AT)

        reference_provider.reset_mock()
        repository.plan.return_value = [{"segments": [segment("train", ORIGIN, DESTINATION,
                                                             AT, AT + timedelta(minutes=100))]}]
        result = service.plan(ORIGIN, DESTINATION, AT)
        self.assertEqual(result["status"], "ok")
        [current] = result["journeys"]
        self.assertEqual(current["total_min"], 100)
        reference_provider.plan_reference.assert_not_called()

    def test_reference_is_shown_after_a_measured_walk_and_its_outage_leaves_walk_available(self):
        destination = {"name": "춘천역", "lat": 37.8844, "lon": 127.7166}
        repository = Mock(**{"plan.return_value": []})
        reference_provider = Mock(**{"plan_reference.return_value": [{
            "reference_kind": "ktdb_2025_03_weekday", "complete": False,
            "segments": [segment("bus", ORIGIN, destination, None, None)],
        }]})
        service = NationalJourneyService(repository, lambda *_: (30, 2000), ktdb_reference=reference_provider)
        result = service.plan(ORIGIN, destination, AT)
        walk, route = result["journeys"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(walk["total_min"], 30)
        self.assertEqual(route["reference_kind"], "ktdb_2025_03_weekday")

        reference_provider.plan_reference.side_effect = MotisProviderError("provider unavailable")
        self.assertEqual(service.plan(ORIGIN, destination, AT)["journeys"], [walk])

    def test_reference_bus_is_available_when_the_timed_rail_trip_has_a_long_walk(self):
        station = {"name": "전철역", "lat": 37.885, "lon": 127.719}
        rail = segment("subway", station, DESTINATION, AT + timedelta(minutes=40), AT + timedelta(minutes=100))
        repository = Mock(**{"plan.return_value": [{"segments": [
            segment("walk", ORIGIN, station, AT, AT + timedelta(minutes=35)), rail,
        ]}]})
        provider = Mock(**{"plan_reference.return_value": [{
            "reference_kind": "ktdb_2025_03_weekday", "complete": False,
            "segments": [segment("bus", ORIGIN, station, None, None),
                         segment("subway", station, DESTINATION, None, None)],
        }]})
        service = NationalJourneyService(repository, Mock(), ktdb_reference=provider)
        recommended, reference = service.plan(ORIGIN, DESTINATION, AT)["journeys"]
        self.assertTrue(recommended["complete"])
        self.assertFalse(reference["complete"])
        self.assertIsNone(reference["arrival_at"])

    def test_rail_variants_do_not_fill_every_slot_ahead_of_a_bus_connection(self):
        stop = {"name": "환승역", "lat": 37.56, "lon": 126.98}
        variants = [{"segments": [segment("subway", ORIGIN, DESTINATION, AT + timedelta(minutes=i),
                                          AT + timedelta(minutes=100 + i))]} for i in range(5)]
        variants.append({"segments": [segment("subway", ORIGIN, stop, AT, AT + timedelta(minutes=95)),
                                      segment("bus", stop, DESTINATION, AT + timedelta(minutes=100),
                                              AT + timedelta(minutes=115))]})
        repository = Mock(**{"plan.return_value": variants})
        results = NationalJourneyService(repository, Mock()).plan(ORIGIN, DESTINATION, AT)["journeys"]
        self.assertEqual([route["total_min"] for route in results], [100, 101, 102, 103, 115])
        self.assertEqual(results[-1]["segments"][-1]["mode"], "bus")

    def test_same_arrival_avoids_a_long_walk_even_if_another_long_walk_remains(self):
        board = {"name": "출발역", "lat": 37.885, "lon": 127.719}
        alight = {"name": "하차역", "lat": 37.56, "lon": 126.98}
        def at(minutes):
            return AT + timedelta(minutes=minutes)

        onward = [segment("subway", board, alight, at(40), at(70)),
                  segment("walk", alight, DESTINATION, at(70), at(95))]
        routes = [{"segments": [segment("walk", ORIGIN, board, AT, at(34)), *onward]},
                  {"segments": [segment("bus", ORIGIN, board, at(10), at(24)), *onward]}]
        repository = Mock(**{"plan.return_value": routes})
        best = NationalJourneyService(repository, Mock()).plan(ORIGIN, DESTINATION, AT)["journeys"][0]
        self.assertEqual(best["arrival_at"], at(95).isoformat())
        self.assertTrue(any(leg["mode"] == "bus" for leg in best["segments"]))

    def test_duplicate_mixed_route_from_two_sources_occupies_one_result(self):
        station = {"name": "춘천역", "lat": 37.8844, "lon": 127.7166}
        rail = segment("subway", DESTINATION, station, AT + timedelta(minutes=3), AT + timedelta(minutes=61))
        bus = segment("bus", station, ORIGIN, AT + timedelta(minutes=67), AT + timedelta(minutes=75))
        motis = Mock(**{"plan.return_value": [{"segments": [rail, bus]}]})
        repository = Mock(**{"plan.return_value": [{"segments": [{**rail, "service_name": "K8021"}, bus]}]})
        service = NationalJourneyService(repository, Mock(), motis=motis)
        self.assertEqual(len(service.plan(DESTINATION, ORIGIN, AT)["journeys"]), 1)

    def test_mixed_itx_and_bus_deduplicates_train_number_and_station_aliases(self):
        station = {"name": "춘천역", "lat": 37.8844, "lon": 127.7166}
        rail = segment("train", DESTINATION, station, AT + timedelta(minutes=5), AT + timedelta(minutes=65),
                       route_name="ITX-청춘", service_name="ITX-청춘 02005", booking_provider="korail")
        alternate = {**rail, "service_name": "ITX-청춘 S2005",
                     "from_place": {**DESTINATION, "name": "서울"}, "to_place": {**station, "name": "춘천"}}
        bus = segment("bus", station, ORIGIN, AT + timedelta(minutes=70), AT + timedelta(minutes=80))
        motis = Mock(**{"plan.return_value": [{"segments": [alternate, bus]}]})
        repository = Mock(**{"plan.return_value": [{"segments": [rail, bus]}]})
        service = NationalJourneyService(repository, Mock(), motis=motis)
        best, = service.plan(DESTINATION, ORIGIN, AT)["journeys"]
        self.assertEqual(best["arrival_at"], (AT + timedelta(minutes=80)).isoformat())

    def test_delayed_train_supersedes_only_its_matching_original_schedule(self):
        station = {**ORIGIN, "name": "동대구역"}
        arrival = AT + timedelta(minutes=60)
        scheduled = segment("train", station, DESTINATION, AT + timedelta(seconds=30), arrival,
                            service_name="KTX 033", booking_provider="korail")
        updated = segment("train", {**station, "name": "동대구"}, {**DESTINATION, "name": "서울"},
                          AT + timedelta(minutes=4), arrival + timedelta(minutes=4), service_name="KTX 33",
                          booking_provider="korail", source="MOTIS", realtime=True,
                          scheduled_departure_at=AT.isoformat(), scheduled_arrival_at=arrival.isoformat())
        repository = Mock(**{"plan.return_value": [{"segments": [scheduled]},
                          {"segments": [{**scheduled, "service_name": "KTX 034"}]}]})
        motis = Mock(**{"plan.return_value": [{"source": "MOTIS", "segments": [updated]}]})
        result = NationalJourneyService(repository, Mock(), motis=motis).plan(station, DESTINATION, AT)
        self.assertEqual([journey["segments"][-1]["service_name"] for journey in result["journeys"]], ["KTX 034", "KTX 33"])

    def test_timeline_ranks_next_day_with_walk_wait_and_booking_link(self):
        station = {"name": "춘천역", "lat": 37.884, "lon": 127.716}
        stop = AT + timedelta(minutes=27)
        board = AT + timedelta(minutes=40)
        arrival = AT + timedelta(minutes=143)
        repository = Mock()
        repository.plan.return_value = [
            {"segments": [segment("bus", ORIGIN, DESTINATION, None, None)], "complete": False},
            {"segments": [segment("walk", ORIGIN, station, AT, stop),
                          segment("train", station, DESTINATION, board, arrival, 8600,
                                  booking_provider="srt", booking={"url": "https://example.invalid"})]},
        ]
        result = NationalJourneyService(repository, Mock()).plan(ORIGIN, DESTINATION, AT)
        best, partial = result["journeys"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual((best["total_min"], best["fare_won"]), (143, 8600))
        self.assertEqual(best["arrival_at"], "2026-11-25T00:23:00+09:00")
        self.assertEqual(best["segments"][0]["duration_min"], 27)
        self.assertEqual((best["segments"][1]["mode"], best["segments"][1]["duration_min"]), ("wait", 13))
        self.assertEqual(best["segments"][2]["booking"]["url"], "https://www.korail.com/ticket")
        self.assertIsNone(partial["total_min"])

    def test_time_reversal_and_unknown_walks_are_excluded(self):
        near = {"name": "후문", "lat": 37.885, "lon": 127.738}
        invalid = segment("train", ORIGIN, near, AT, AT - timedelta(minutes=5))
        result = NationalJourneyService(Mock(**{"plan.return_value": [{"segments": [invalid]}]}),
                                        lambda *_: (0, 0)).plan(ORIGIN, near, AT)
        self.assertEqual(result["journeys"], [])

    def test_clock_order_cannot_replace_missing_spatial_connection(self):
        origin = {"name": "한림대 의학관", "lat": 37.88695, "lon": 127.73585}
        destination = {"name": "춘천역", "lat": 37.884391, "lon": 127.716603}
        stop = {"name": "한림대학교 정류장", "lat": 37.8865, "lon": 127.737}
        remote = {"name": "남춘천역", "lat": 37.864, "lon": 127.722}
        at = datetime(2026, 11, 25, 9, 0, tzinfo=KST)
        walk = segment("walk", origin, stop, at, at + timedelta(minutes=3))
        impossible = segment("bus", remote, destination, at + timedelta(minutes=3), at + timedelta(minutes=14))
        available = segment("bus", stop, destination, at + timedelta(minutes=5), at + timedelta(minutes=16))
        available["duration_min"] = 90  # The provider's decorative total must not replace the leg clocks.
        repository = Mock(**{"plan.return_value": [{"segments": [walk, impossible]}, {"segments": [walk, available]}]})
        result = NationalJourneyService(repository, lambda *_: (19, 1600)).plan(origin, destination, at)
        self.assertEqual([j["total_min"] for j in result["journeys"]], [16, 19])
        self.assertEqual([(s["mode"], s["duration_min"]) for s in result["journeys"][0]["segments"]],
                         [("walk", 3), ("wait", 2), ("bus", 11)])

    def test_equal_arrival_prefers_fewer_transfers_then_less_walking(self):
        stop = {**ORIGIN, "name": "정류장", "lat": 37.889}
        wait_until = AT + timedelta(minutes=8)
        arrival = AT + timedelta(minutes=60)
        transit = segment("bus", stop, DESTINATION, wait_until, arrival)
        short_walk = segment("walk", ORIGIN, stop, AT, AT + timedelta(minutes=2))
        long_walk = segment("walk", ORIGIN, stop, AT, AT + timedelta(minutes=6))
        two_rides = [segment("bus", ORIGIN, stop, AT, AT + timedelta(minutes=5)), transit]
        repository = Mock(**{"plan.return_value": [
            {"name": "longer walk", "segments": [long_walk, {**transit, "realtime": True}]},
            {"name": "transfer", "segments": two_rides},
            {"name": "shorter walk", "segments": [short_walk, transit]},
        ]})
        result = NationalJourneyService(repository, Mock()).plan(ORIGIN, DESTINATION, AT)
        self.assertEqual([j["name"] for j in result["journeys"]], ["shorter walk", "longer walk", "transfer"])

    def test_missed_departure_and_subminute_transfer_are_not_candidates(self):
        stop = {**ORIGIN, "name": "환승 정류장", "lat": 37.889}
        arrival = AT + timedelta(minutes=8)
        bus = segment("bus", ORIGIN, stop, AT, arrival)
        too_soon = segment("bus", stop, DESTINATION, arrival + timedelta(seconds=59), AT + timedelta(minutes=60))
        catchable = segment("bus", stop, DESTINATION, arrival + timedelta(minutes=1), AT + timedelta(minutes=61))
        departed = segment("bus", ORIGIN, DESTINATION, AT - timedelta(seconds=1), AT + timedelta(minutes=45))
        repository = Mock(**{"plan.return_value": [{"segments": [departed]},
            {"segments": [bus, too_soon]}, {"segments": [bus, catchable]}]})
        result = NationalJourneyService(repository, Mock()).plan(ORIGIN, DESTINATION, AT)
        self.assertEqual([j["total_min"] for j in result["journeys"]], [61])


class ChuncheonRailAccessTests(unittest.TestCase):
    def setUp(self):
        self.gateway = {"name": "춘천역", "lat": 37.884391, "lon": 127.716603}
        self.board = {"name": "한림대학교", "lat": 37.88355, "lon": 127.7384}
        self.alight = {"name": "춘천역 정류장", "lat": 37.88529, "lon": 127.71803}
        self.repository = Mock()
        self.repository.chuncheon_gateways.return_value = [self.gateway]
        self.repository.plan.return_value = []
        self.local = Mock()
        self.walking = Mock()
        self.service = NationalJourneyService(self.repository, self.walking, local_transit=self.local, now=lambda: AT)

    def local_result(self, eta=9, direction_verified=True):
        return {"journeys": [{
            "access_walk": {"minutes": 6}, "egress_walk": {"minutes": 4}, "legs": [{
                "route_no": "12", "board": self.board, "alight": self.alight, "arrival_min": eta,
                "ride_min": 8, "direction_verified": direction_verified, "route_number_verified": True,
            }],
        }]}

    def test_live_campus_bus_supplies_timed_rail_access(self):
        self.local.plan_city.return_value = self.local_result()

        def rail_plan(origin, destination, query_at, walking, *, access_routes):
            self.assertEqual((origin, destination, query_at, walking), (ORIGIN, DESTINATION, AT, self.walking))
            access, = access_routes
            self.assertEqual(access["gateway"], self.gateway)
            self.assertEqual(access["arrival_at"], AT + timedelta(minutes=21))
            self.assertEqual(access["segments"][2]["route_name"], "12")
            self.assertTrue(access["estimated"])
            return [{"segments": [*access["segments"], segment(
                "subway", self.gateway, destination, AT + timedelta(minutes=24), AT + timedelta(minutes=100))]}]

        self.repository.plan.side_effect = rail_plan
        result = self.service.plan(ORIGIN, DESTINATION, AT)
        self.local.plan_city.assert_called_once_with(ORIGIN, self.gateway, max_transfers=0, departure_at=AT)
        self.assertEqual(result["status"], "ok")
        self.assertEqual([part["mode"] for part in result["journeys"][0]["segments"]],
                         ["walk", "wait", "bus", "walk", "wait", "subway"])

    def test_verified_bus_is_joined_to_motis_after_the_station_walk(self):
        self.local.plan_city.return_value = self.local_result()
        self.repository.plan.return_value = []
        motis = Mock()
        self.service.motis = motis

        def provider(origin, destination, departure):
            if origin == ORIGIN:
                return []
            self.assertEqual((origin, destination), (self.gateway, DESTINATION))
            self.assertGreater(departure, AT + timedelta(minutes=21))
            return [{"source": "MOTIS", "segments": [segment("train", origin, destination, departure,
                        AT + timedelta(minutes=80), realtime=True)]}]

        motis.plan.side_effect = provider
        best, = self.service.plan(ORIGIN, DESTINATION, AT)["journeys"]
        self.assertEqual(best["source"], "MOTIS")
        self.assertEqual([part["mode"] for part in best["segments"]], ["walk", "wait", "bus", "walk", "wait", "train"])
        self.assertTrue(best["segments"][-1]["realtime"])

    def test_missed_or_unconfirmed_bus_falls_back_to_existing_rail_search(self):
        for eta, verified in ((6, True), (9, False), (None, True)):
            with self.subTest(eta=eta, verified=verified):
                self.repository.plan.reset_mock()
                self.local.plan_city.return_value = self.local_result(eta, verified)
                self.service.plan(ORIGIN, DESTINATION, AT)
                self.repository.plan.assert_called_once_with(ORIGIN, DESTINATION, AT, self.walking)

    def test_current_departure_compares_published_and_live_routes(self):
        def at(minutes):
            return AT + timedelta(minutes=minutes)

        def published_plan(origin, destination, when, walking, *, use_published_timing=False, timed_only=False):
            if not timed_only:
                return []
            self.assertTrue(use_published_timing)
            self.assertEqual((origin, destination, when, walking), (ORIGIN, self.gateway, AT, self.walking))
            return [{"segments": [
                segment("walk", ORIGIN, self.board, at(0), at(5)),
                segment("bus", self.board, self.alight, at(8), at(15), route_name="300"),
                segment("walk", self.alight, self.gateway, at(15), at(19)),
            ]}]

        def rail_plan(origin, destination, when, walking, *, access_routes=()):
            routes = [{"segments": [segment("walk", origin, self.gateway, at(0), at(32)),
                                    segment("subway", self.gateway, destination, at(40), at(115))]}]
            routes.extend({"segments": [*feeder["segments"],
                          segment("subway", self.gateway, destination, at(25), at(100))]}
                          for feeder in access_routes if feeder["arrival_at"] <= at(24))
            return routes

        bus = Mock()
        bus.plan.side_effect = published_plan
        self.repository.plan.side_effect = rail_plan
        service = NationalJourneyService(self.repository, self.walking, local_transit=self.local,
                                         bus_repository=bus, now=lambda: AT)
        same_route = self.local_result()
        same_route["journeys"][0]["legs"][0]["route_no"] = "300"
        for live, routes, live_route in (({"journeys": []}, {"300"}, None),
                                         (self.local_result(direction_verified=False), {"300"}, None),
                                         (self.local_result(), {"12", "300"}, "12"),
                                         (same_route, {"300"}, "300")):
            with self.subTest(routes=routes, live=live):
                bus.plan.reset_mock()
                self.local.plan_city.return_value = live
                journeys = service.plan(ORIGIN, DESTINATION, AT)["journeys"]
                self.assertEqual(journeys[0]["arrival_at"], at(100).isoformat())
                bus_legs = [leg for journey in journeys for leg in journey["segments"] if leg["mode"] == "bus"]
                self.assertEqual(len(bus_legs), len(routes))
                self.assertEqual({leg["route_name"] for leg in bus_legs}, routes)
                self.assertEqual([leg["route_name"] for leg in bus_legs if leg.get("realtime")],
                                 [live_route] if live_route else [])
                published_calls = [call for call in bus.plan.call_args_list if call.kwargs.get("timed_only")]
                self.assertEqual(len(published_calls), 1)

    def test_published_bus_connections_use_the_walking_fallback(self):
        fallback = Mock(return_value=(6, 300))
        primary = Mock(return_value=(-1, -1))
        repository = Mock()
        provider = RegionalBusJourneyProvider(repository, primary, walking_fallback=fallback)

        def planned(origin, destination, when, walk, **kwargs):
            arguments = (origin["lat"], origin["lon"], origin["name"],
                         destination["lat"], destination["lon"], destination["name"])
            self.assertEqual(walk(*arguments), (6, 300))
            fallback.assert_called_once_with(*arguments)
            return []

        repository.plan.side_effect = planned
        self.assertEqual(provider.connections(ORIGIN, self.gateway, AT), [])

    def test_shuttle_feeder_requires_confirmed_service_and_arrival(self):
        result = self.local_result()
        leg = result["journeys"][0]["legs"][0]
        leg.update(transport_type="shuttle", service_date_confirmed=True,
                   scheduled_departure_at=(AT + timedelta(minutes=9)).isoformat(),
                   scheduled_arrival_at=(AT + timedelta(minutes=17)).isoformat())
        self.local.plan_city.return_value = result
        self.service.plan(ORIGIN, DESTINATION, AT)
        access, = self.repository.plan.call_args.kwargs["access_routes"]
        self.assertEqual(access["segments"][2]["mode"], "shuttle")

        for missing in ("service_date_confirmed", "scheduled_arrival_at"):
            with self.subTest(missing=missing):
                value = leg.pop(missing)
                self.repository.plan.reset_mock()
                self.service.plan(ORIGIN, DESTINATION, AT)
                self.repository.plan.assert_called_once_with(ORIGIN, DESTINATION, AT, self.walking)
                leg[missing] = value

    def test_future_departure_does_not_use_the_current_live_bus(self):
        future = AT + timedelta(minutes=30)
        self.service.plan(ORIGIN, DESTINATION, future)
        self.local.plan_city.assert_not_called()
        self.repository.chuncheon_gateways.assert_not_called()
        self.repository.plan.assert_called_once_with(ORIGIN, DESTINATION, future, self.walking)

    def test_feeder_provider_failure_preserves_rail_search(self):
        self.local.plan_city.side_effect = OSError
        self.service.plan(ORIGIN, DESTINATION, AT)
        self.repository.plan.assert_called_once_with(ORIGIN, DESTINATION, AT, self.walking)

    def test_future_bus_to_station_catches_earlier_train(self):
        departure = datetime(2026, 11, 26, 8, 0, tzinfo=KST)
        at = lambda minutes: departure + timedelta(minutes=minutes)
        bus = Mock()
        bus.has_published_connection.side_effect = lambda start, end, date: (start, end) == (ORIGIN, self.gateway)
        bus.plan.side_effect = lambda start, end, when, walk, **flags: [{"estimated": True, "segments": [
            segment("walk", ORIGIN, self.board, at(0), at(5)),
            segment("bus", self.board, self.alight, at(8), at(13)),
            segment("walk", self.alight, self.gateway, at(13), at(16)),
        ]}] if flags.get("timed_only") else []

        def rail_plan(start, end, when, walk, *, access_routes=()):
            result = [{"segments": [segment("walk", start, self.gateway, at(0), at(29)),
                                    segment("train", self.gateway, end, at(80), at(145))]}]
            if access_routes:
                feeder, = access_routes
                self.assertEqual(feeder["arrival_at"], at(16))
                result.append({**feeder, "segments": [*feeder["segments"],
                               segment("train", self.gateway, end, at(23), at(87))]})
            return result

        self.repository.plan.side_effect = rail_plan
        service = NationalJourneyService(self.repository, self.walking, local_transit=self.local,
                                         bus_repository=bus, now=lambda: AT)
        routes = service.plan(ORIGIN, DESTINATION, departure)["journeys"]
        self.assertEqual([route["total_min"] for route in routes], [87])
        self.assertEqual([leg["mode"] for leg in routes[0]["segments"] if leg["mode"] in ("bus", "train")],
                         ["bus", "train"])
        self.local.plan_city.assert_not_called()

    def test_rail_arrival_queries_bus_and_rejects_boarding_without_margin(self):
        departure = datetime(2026, 11, 26, 8, 0, tzinfo=KST)
        at = lambda minutes: departure + timedelta(minutes=minutes)
        bus = Mock()
        bus.has_published_connection.side_effect = lambda start, end, date: (start, end) == (self.gateway, ORIGIN)

        def bus_plan(start, end, when, walk, *, use_published_timing, timed_only=False):
            if not timed_only:
                return []
            self.assertEqual((start, end, when), (self.gateway, ORIGIN, at(73)))
            return [{"segments": [segment("walk", self.gateway, self.alight, at(73), at(78)),
                                  segment("bus", self.alight, self.board, at(board), at(arrive - 3)),
                                  segment("walk", self.board, ORIGIN, at(arrive - 3), at(arrive))]}
                    for board, arrive in [(78, 89), (82, 93)]]

        def rail_plan(start, end, when, walk):
            segments = [segment("train", start, self.gateway, at(8), at(73))]
            if end != self.gateway:
                segments.append(segment("walk", self.gateway, end, at(73), at(111)))
            return [{"segments": segments}]

        bus.plan.side_effect = bus_plan
        self.repository.plan.side_effect = rail_plan
        service = NationalJourneyService(self.repository, self.walking, bus_repository=bus, now=lambda: AT)
        mixed, = service.plan(DESTINATION, ORIGIN, departure)["journeys"]
        self.assertEqual(mixed["total_min"], 93)
        self.assertEqual(next(leg["departure_at"] for leg in mixed["segments"] if leg["mode"] == "bus"), at(82).isoformat())


class PublishedShuttleJourneyTests(unittest.TestCase):
    CLC = {"name": "한림대학교 Campus Life Center", "lat": 37.88632, "lon": 127.73812}
    GATEWAY = {"name": "춘천역", "lat": 37.884391, "lon": 127.716603}
    HUMAN_TOWN = {"name": "휴먼타운", "lat": 37.85433, "lon": 127.74069}
    SOYANG = {"name": "소양중 정문", "lat": 37.90604, "lon": 127.72475}
    YONGSAN = {"name": "용산역", "lat": 37.5299, "lon": 126.9648}
    TERMINAL = {"name": "춘천시외버스터미널", "lat": 37.864, "lon": 127.717, "provider_id": "BUSTAGO_2037"}

    def setUp(self):
        self.rail = Mock()
        self.rail.chuncheon_gateways.return_value = [self.GATEWAY]
        self.rail.plan.return_value = []
        shuttle = ShuttleService(shuttle_repository, self.walking, profile=shuttle_profile)
        self.regional = RegionalBusJourneyProvider(None, self.walking, now=lambda: AT, shuttle_service=shuttle)
        self.service = NationalJourneyService(self.rail, self.walking, regional_bus=self.regional, now=lambda: AT)

    @staticmethod
    def at(hour, minute, second=0, *, day=25):
        return datetime(2026, 11, day, hour, minute, second, tzinfo=KST)

    def walking(self, from_lat, from_lon, from_name, to_lat, to_lon, to_name):
        start, end = (from_lat, from_lon), (to_lat, to_lon)
        if start == end:
            return 0, 0
        if start == (self.CLC["lat"], self.CLC["lon"]) and end in (
                (self.HUMAN_TOWN["lat"], self.HUMAN_TOWN["lon"]), (self.TERMINAL["lat"], self.TERMINAL["lon"])):
            return 55, 3400
        if end == (self.TERMINAL["lat"], self.TERMINAL["lon"]):
            return 8, 700
        return 4, 220

    @staticmethod
    def ride_modes(journey):
        return [item["mode"] for item in journey["segments"] if item["mode"] not in {"walk", "wait"}]

    def test_city_arrivals_and_catchable_shuttle_are_ranked_on_the_same_timeline(self):
        when = self.at(17, 4)
        self.regional.now = lambda: when
        self.regional.local_transit = city = Mock()
        self.service.walking_time = lambda *_: (45, 2800)

        for eta_300, eta_12, fastest in ((6, 15, "300"), (15, 6, "12"), (15, 17, "제4노선")):
            with self.subTest(fastest=fastest):
                city.plan_city.return_value = {"journeys": [
                    {"access_walk": {"minutes": 4}, "egress_walk": {"minutes": 3}, "legs": [
                        {"route_no": route, "board": self.CLC, "alight": self.GATEWAY,
                         "arrival_min": eta, "ride_min": 8, "direction_verified": True},
                    ]} for route, eta in (("300", eta_300), ("12", eta_12))
                ]}
                city.plan_city.reset_mock()
                response = self.service.plan(self.CLC, self.GATEWAY, when)
                ranked = [next(leg["route_name"] for leg in item["segments"] if leg["mode"] in {"bus", "shuttle"})
                          for item in response["journeys"] if self.ride_modes(item)]
                self.assertEqual(ranked[0], fastest)
                self.assertTrue({"300", "12", "제4노선"}.issubset(ranked))
                self.assertEqual(response["journeys"][0]["total_min"], 20 if fastest == "제4노선" else 17)
                city.plan_city.assert_called_once_with(self.CLC, self.GATEWAY, max_transfers=1, departure_at=when)
                city.plan.assert_not_called()

    def test_planned_local_search_ranks_shuttle_and_checks_boarding_time_and_operating_day(self):
        def search(at):
            with patch("app.national_journey_service", self.service):
                return app.test_client().post("/api/journeys", json={
                    "origin": {"campus": "hallym", "building": "Campus Life Center"},
                    "destination": self.HUMAN_TOWN, "departure_at": at.isoformat(),
                })

        response = search(self.at(17, 10))
        self.assertEqual(response.status_code, 200)
        best = response.json["journeys"][0]
        self.assertEqual(self.ride_modes(best), ["shuttle"])
        self.assertTrue(best["estimated"])
        self.assertEqual(best["arrival_at"], self.at(17, 41).isoformat())
        self.assertEqual(best["total_min"], 31)
        ride = next(leg for leg in best["segments"] if leg["mode"] == "shuttle")
        self.assertEqual((ride["route_name"], ride["departure_at"]), ("제2노선", self.at(17, 20).isoformat()))
        self.assertIsNot(ride.get("realtime"), True)

        late = search(self.at(17, 19, 31)).json["journeys"]
        late_departures = [leg["departure_at"] for route in late for leg in route["segments"] if leg["mode"] == "shuttle"]
        self.assertNotIn(self.at(17, 20).isoformat(), late_departures)
        saturday = search(self.at(17, 10, day=28)).json["journeys"]
        self.assertTrue(saturday)
        self.assertTrue(all("shuttle" not in self.ride_modes(journey) for journey in saturday))

    def test_unpublished_route_one_arrival_remains_a_reference(self):
        result = self.service.plan(self.CLC, self.GATEWAY, self.at(18, 45))
        reference = next(journey for journey in result["journeys"] if self.ride_modes(journey) == ["shuttle"])
        ride = next(leg for leg in reference["segments"] if leg["mode"] == "shuttle")
        self.assertEqual((ride["route_name"], ride["departure_at"]), ("제1노선", self.at(19, 0).isoformat()))
        self.assertIsNone(ride["arrival_at"])
        self.assertFalse(reference["complete"])
        self.assertIsNone(reference["total_min"])
        self.assertNotEqual(self.ride_modes(result["journeys"][0]), ["shuttle"])

    def test_future_shuttle_feeds_rail_without_live_or_published_city_bus(self):
        def rail_plan(start, end, when, walking, *, access_routes=()):
            self.assertEqual((start, end, when), (self.CLC, self.YONGSAN, self.at(17, 0)))
            self.assertTrue(access_routes)
            catchable = [route for route in access_routes if route["arrival_at"] < self.at(18, 15)]
            feeder = min(catchable, key=lambda route: route["arrival_at"])
            self.assertEqual(feeder["gateway"], self.GATEWAY)
            self.assertEqual(feeder["arrival_at"], self.at(17, 24))
            return [{"segments": [*feeder["segments"], segment(
                "train", self.GATEWAY, end, self.at(18, 15), self.at(19, 35), booking_provider="korail")]}]

        self.rail.plan.side_effect = rail_plan
        result = self.service.plan(self.CLC, self.YONGSAN, self.at(17, 0))
        best = result["journeys"][0]
        self.assertEqual(self.ride_modes(best), ["shuttle", "train"])
        self.assertEqual(best["transfer_count"], 1)
        self.assertEqual(best["arrival_at"], self.at(19, 35).isoformat())

    def test_rail_egress_uses_shuttle_time_after_reaching_the_representative_stop(self):
        def rail_plan(start, end, when, walking):
            if end != self.GATEWAY:
                return []
            return [{"segments": [segment("train", start, end, self.at(17, 1), self.at(18, 17))]}]

        self.rail.plan.side_effect = rail_plan
        result = self.service.plan(self.YONGSAN, self.SOYANG, self.at(16, 57))
        mixed = result["journeys"][0]
        self.assertEqual(self.ride_modes(mixed), ["train", "shuttle"])
        ride = next(leg for leg in mixed["segments"] if leg["mode"] == "shuttle")
        self.assertEqual((ride["route_name"], ride["departure_at"]), ("제4노선", self.at(18, 30).isoformat()))
        self.assertEqual(mixed["arrival_at"], self.at(18, 40).isoformat())

    def test_shuttle_walk_to_intercity_terminal_checks_the_coach_boarding_deadline(self):
        remote = {"name": "동서울종합터미널", "lat": 37.534, "lon": 127.094, "provider_id": "BUSTAGO_0001"}
        timetable = Mock()
        timetable.timetable.return_value = {"trips": [
            {"departure_at": self.at(17, 44), "arrival_at": self.at(19, 4)},
            {"departure_at": self.at(17, 50), "arrival_at": self.at(19, 10)},
        ]}
        intercity = IntercityJourneyProvider(timetable, Mock(), "/unused", self.walking,
                                            regional_bus=self.regional.connections)
        results = intercity._route(self.CLC, remote, self.TERMINAL, remote, self.at(17, 10))
        self.assertTrue(results)
        best = results[0]
        self.assertEqual(self.ride_modes(best), ["shuttle", "intercity_bus"])
        coach = next(leg for leg in best["segments"] if leg["mode"] == "intercity_bus")
        self.assertEqual(coach["departure_at"], self.at(17, 50).isoformat())


class IntercityJoinTests(unittest.TestCase):
    def test_departure_uses_terminal_walk_and_returns_actual_same_day_fare(self):
        local = {"name": "춘천시외버스터미널", "lat": 37.864, "lon": 127.717, "provider_id": "BUSTAGO_2037"}
        remote = {"name": "동서울종합터미널", "lat": 37.534, "lon": 127.094, "provider_id": "BUSTAGO_0001"}
        destination = {"name": "강변역", "lat": 37.535, "lon": 127.095}
        new_at = AT - timedelta(hours=8)
        dep = new_at + timedelta(minutes=32)
        arr = dep + timedelta(minutes=70)
        provider = Mock()
        provider.today.return_value = "2026-11-24"
        provider.timetable.return_value = {"trips": [
            {"departure_at": (new_at + timedelta(minutes=20)).isoformat(), "arrival_at": arr.isoformat()},
            {"departure_at": dep.isoformat(), "arrival_at": arr.isoformat(), "duration_minutes": 70,
             "fare_krw": 9400, "grade": "우등", "arrival_estimated": True},
        ]}
        walks = Mock(side_effect=[(23, 1800), (3, 180)])
        intercity = IntercityJourneyProvider(provider, Mock(), "/unused", walks)
        with (
            patch.object(intercity, "_chuncheon_terminal", return_value=local),
            patch.object(intercity, "_venues", return_value=[remote]),
        ):
            result, missing = intercity.plan(ORIGIN, destination, new_at)
        self.assertFalse(missing)
        provider.timetable.assert_called_once_with("BUSTAGO_2037", "BUSTAGO_0001", "2026-11-24")
        self.assertEqual([leg["duration_min"] for leg in result[0]["segments"]], [23, 9, 70, 3])
        self.assertTrue(result[0]["estimated"])
        self.assertEqual(result[0]["segments"][2]["fare_won"], 9400)

    def test_confirmed_regional_rail_feeder_can_replace_a_slow_walk(self):
        terminal = {"name": "동서울종합터미널", "lat": 37.534, "lon": 127.094}
        destination = {"name": "잠실역", "lat": 37.513, "lon": 127.101}
        arrival = AT + timedelta(minutes=13)
        regional = Mock()
        regional.plan.return_value = [
            {"arrival_at": arrival, "segments": [segment("subway", terminal, destination, AT, arrival)]},
        ]
        walking = Mock(return_value=(36, 2400))
        provider = IntercityJourneyProvider(Mock(), Mock(), "/unused", walking, regional_transit=regional)
        best = provider._ground(terminal, destination, AT, "목적지 연결")[0]
        self.assertEqual([part["mode"] for part in best["segments"]], ["subway"])
        regional.plan.assert_called_once_with(terminal, destination, AT, walking)

    def test_outbound_city_bus_then_intercity_then_subway_checks_terminal_boarding(self):
        local = {"name": "춘천시외버스터미널", "lat": 37.864, "lon": 127.717, "provider_id": "BUSTAGO_2037"}
        remote = {"name": "동서울종합터미널", "lat": 37.534, "lon": 127.094, "provider_id": "BUSTAGO_0001"}
        destination = {"name": "잠실역", "lat": 37.513, "lon": 127.101}
        at = AT - timedelta(hours=14)
        bus_departure, bus_arrival = at + timedelta(minutes=20), at + timedelta(minutes=90)
        timetable = Mock()
        timetable.today.return_value = "2026-11-24"
        timetable.timetable.return_value = {"trips": [
            {"departure_at": at + timedelta(minutes=16), "arrival_at": at + timedelta(minutes=82)},
            {"departure_at": bus_departure, "arrival_at": bus_arrival},
        ]}

        def city_bus(start, end, when):
            if (start, end) == (ORIGIN, local):
                return [{"estimated": True, "warnings": ["시내버스 예정 시각"],
                         "segments": [segment("city_bus", start, end, when + timedelta(minutes=4), when + timedelta(minutes=12))]}]
            return []

        def subway(start, end, when):
            if (start, end) == (remote, destination):
                return [{"segments": [segment("subway", start, end, when + timedelta(minutes=3), when + timedelta(minutes=11))]}]
            return []

        regional_bus = Mock(side_effect=city_bus)
        motis = Mock()
        motis.plan.side_effect = subway
        provider = IntercityJourneyProvider(timetable, Mock(), "/unused", lambda *_: (33, 2500),
                                           regional_bus=regional_bus, motis=motis)
        with (
            patch.object(provider, "_chuncheon_terminal", return_value=local),
            patch.object(provider, "_venues", return_value=[remote]),
        ):
            (best,), missing = provider.plan(ORIGIN, destination, at)
        self.assertFalse(missing)
        modes = [(leg["mode"], leg["departure_at"]) for leg in best["segments"] if leg["mode"] != "wait"]
        self.assertEqual(modes, [("bus", (at + timedelta(minutes=4)).isoformat()),
                                 ("intercity_bus", bus_departure.isoformat()),
                                 ("subway", (bus_arrival + timedelta(minutes=3)).isoformat())])
        self.assertEqual((best["total_min"], best["transfer_count"]), (101, 2))
        self.assertTrue(best["estimated"])
        self.assertIn("시내버스 예정 시각", best["warnings"])
        regional_bus.assert_any_call(ORIGIN, local, at)
        motis.plan.assert_any_call(remote, destination, bus_arrival)

    def test_inbound_uses_rail_access_and_waits_for_a_catchable_local_bus(self):
        local = {"name": "춘천시외버스터미널", "lat": 37.864, "lon": 127.717, "provider_id": "BUSTAGO_2037"}
        remote = {"name": "동서울종합터미널", "lat": 37.534, "lon": 127.094, "provider_id": "BUSTAGO_0001"}
        start = {"name": "잠실역", "lat": 37.513, "lon": 127.101}
        stop = {"name": "시외버스터미널 정류장", "lat": 37.8648, "lon": 127.718}
        at = AT - timedelta(hours=14)
        departure, arrival = at + timedelta(minutes=20), at + timedelta(minutes=90)
        timetable = Mock()
        timetable.timetable.return_value = {"trips": [{"departure_at": departure, "arrival_at": arrival}]}
        rail = Mock()
        rail.plan.side_effect = lambda a, b, t, walking: ([{"segments": [segment(
            "train", a, b, t + timedelta(minutes=2), t + timedelta(minutes=7))]}] if (a, b) == (start, remote) else [])

        def city_bus(a, b, t):
            if (a, b) != (local, ORIGIN):
                return []
            alight = t + timedelta(minutes=18)
            return [
                {"segments": [segment("city_bus", a, b, t + timedelta(seconds=30), t + timedelta(minutes=5))]},
                {"segments": [segment("walk", a, stop, t, t + timedelta(minutes=4)),
                              segment("city_bus", stop, b, t + timedelta(minutes=3), t + timedelta(minutes=10))]},
                {"segments": [segment("walk", a, stop, t, t + timedelta(minutes=4)),
                              segment("city_bus", stop, b, t + timedelta(minutes=8), alight)]},
            ]

        regional_bus = Mock(side_effect=city_bus)
        provider = IntercityJourneyProvider(timetable, Mock(), "/unused", lambda *_: (32, 2300),
                                           rail, regional_bus=regional_bus)
        best, = provider._route(start, ORIGIN, remote, local, at)
        self.assertEqual([leg["mode"] for leg in best["segments"] if leg["mode"] != "wait"],
                         ["train", "intercity_bus", "walk", "bus"])
        self.assertEqual(best["segments"][-1]["departure_at"], (arrival + timedelta(minutes=8)).isoformat())
        self.assertEqual(best["total_min"], 108)
        regional_bus.assert_any_call(local, ORIGIN, arrival)

    def test_slower_walk_to_the_same_intercity_bus_requires_fewer_transfers(self):
        local = {"name": "춘천시외버스터미널", "lat": 37.864, "lon": 127.717, "provider_id": "BUSTAGO_2037"}
        remote = {"name": "동서울종합터미널", "lat": 37.534, "lon": 127.094, "provider_id": "BUSTAGO_0001"}
        departure, arrival = AT + timedelta(minutes=15), AT + timedelta(minutes=75)
        timetable = Mock()
        timetable.timetable.return_value = {"trips": [{"departure_at": departure, "arrival_at": arrival}]}
        city_bus = Mock(return_value=[{"segments": [segment("city_bus", ORIGIN, local,
                                                            AT + timedelta(minutes=2), AT + timedelta(minutes=5))]}])
        provider = IntercityJourneyProvider(timetable, Mock(), "/unused", lambda *_: (8, 600), regional_bus=city_bus)
        best, = provider._route(ORIGIN, remote, local, remote, AT)
        self.assertEqual(best["segments"][0]["mode"], "walk")
        self.assertEqual((best["total_min"], best["transfer_count"]), (75, 0))

    def test_unknown_terminal_access_does_not_make_a_complete_journey(self):
        local = {"name": "춘천시외버스터미널", "lat": 37.864, "lon": 127.717, "provider_id": "BUSTAGO_2037"}
        regional = {"name": "동서울종합터미널", "lat": 37.534, "lon": 127.094, "provider_id": "BUSTAGO_0001"}
        timetable = Mock()
        timetable.timetable.return_value = {"trips": [{
            "departure_at": (AT + timedelta(minutes=60)).isoformat(),
            "arrival_at": (AT + timedelta(minutes=130)).isoformat(),
            "duration_minutes": 70, "fare_krw": 9400,
        }]}
        provider = IntercityJourneyProvider(timetable, Mock(), "/unused", lambda *_: (-1, -1))
        repo = Mock()
        repo.plan.return_value = provider._route(ORIGIN, DESTINATION, local, regional, AT)
        result = NationalJourneyService(repo, Mock()).plan(ORIGIN, DESTINATION, AT)
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["journeys"][0]["arrival_at"])
        self.assertEqual([part["mode"] for part in result["journeys"][0]["segments"]],
                         ["connection", "intercity_bus", "connection"])


if __name__ == "__main__":
    unittest.main()
