"""National timeline API and provider joins. Outbound requests use test doubles."""

from datetime import datetime, timedelta
import unittest
from unittest.mock import Mock, patch

from app import CAMPUS_BUILDINGS, app
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
        self.local.plan.assert_called_once_with(ORIGIN, self.gateway)
        self.assertEqual(result["status"], "ok")
        self.assertEqual([part["mode"] for part in result["journeys"][0]["segments"]],
                         ["walk", "wait", "bus", "walk", "wait", "subway"])

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
        segments = provider._ground(terminal, destination, AT, "목적지 연결", regional=True)
        self.assertEqual([part["mode"] for part in segments], ["subway"])
        regional.plan.assert_called_once_with(terminal, destination, AT, walking)

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
