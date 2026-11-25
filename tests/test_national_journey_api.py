"""National timeline API and provider joins. Outbound requests use test doubles."""

from datetime import datetime, timedelta
import unittest
from unittest.mock import Mock, patch

from app import CAMPUS_BUILDINGS, app
from services.motis_journey_provider import MotisProviderError
from services.national_journey_service import IntercityJourneyProvider, KST, NationalJourneyService


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
        result = NationalJourneyService(repository, lambda *_: (21, 1600)).plan(origin, destination, at)
        self.assertEqual([j["total_min"] for j in result["journeys"]], [16, 21])
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
        self.local.plan.return_value = self.local_result()

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
        self.local.plan.assert_called_once_with(ORIGIN, self.gateway, max_transfers=0, include_direct_walk=False, departure_at=AT)
        self.assertEqual(result["status"], "ok")
        self.assertEqual([part["mode"] for part in result["journeys"][0]["segments"]],
                         ["walk", "wait", "bus", "walk", "wait", "subway"])

    def test_verified_bus_is_joined_to_motis_after_the_station_walk(self):
        self.local.plan.return_value = self.local_result()
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
                self.local.plan.return_value = self.local_result(eta, verified)
                self.service.plan(ORIGIN, DESTINATION, AT)
                self.repository.plan.assert_called_once_with(ORIGIN, DESTINATION, AT, self.walking)

    def test_shuttle_feeder_requires_confirmed_service_and_arrival(self):
        result = self.local_result()
        leg = result["journeys"][0]["legs"][0]
        leg.update(transport_type="shuttle", service_date_confirmed=True,
                   scheduled_departure_at=(AT + timedelta(minutes=9)).isoformat(),
                   scheduled_arrival_at=(AT + timedelta(minutes=17)).isoformat())
        self.local.plan.return_value = result
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
        self.local.plan.assert_not_called()
        self.repository.chuncheon_gateways.assert_not_called()
        self.repository.plan.assert_called_once_with(ORIGIN, DESTINATION, future, self.walking)

    def test_feeder_provider_failure_preserves_rail_search(self):
        self.local.plan.side_effect = OSError
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
        self.assertEqual([route["total_min"] for route in routes], [87, 145])
        self.assertEqual([leg["mode"] for leg in routes[0]["segments"] if leg["mode"] in ("bus", "train")],
                         ["bus", "train"])
        self.local.plan.assert_not_called()

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
        mixed, direct = service.plan(DESTINATION, ORIGIN, departure)["journeys"]
        self.assertEqual((mixed["total_min"], direct["total_min"]), (93, 111))
        self.assertEqual(next(leg["departure_at"] for leg in mixed["segments"] if leg["mode"] == "bus"), at(82).isoformat())


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
