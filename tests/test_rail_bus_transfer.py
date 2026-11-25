"""Use synthetic bus predictions and verified walking times to check bus connections."""

import csv
from datetime import datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from services.gyeonggi_bus_arrivals import GyeonggiBusArrivals
from services.gyeonggi_bus_schedules import GyeonggiBusSchedule, GyeonggiFrequency
from services.national_journey_service import NationalJourneyService, _segment
from services.national_bus_repository import NationalBusRepository
from services.rail_bus_transfer import RailBusTransfers
from services.seoul_bus_arrivals import SeoulBusArrivals


KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 11, 25, 14, 42, tzinfo=KST)
ORIGIN = {"name": "한림대학교", "lat": 37.886, "lon": 127.737}
STATION = {"name": "금곡역", "lat": 37.637414, "lon": 127.207931}
DESTINATION = {"name": "덕소아이파크", "lat": 37.58753949, "lon": 127.22274818}
CITY = "31130"
ROUTE = "GGB241349001"
BOARD = ("board", "금곡역 버스 정류장", 37.638, 127.208)
ALIGHT = ("alight", "덕소아이파크 버스 정류장", 37.588, 127.223)
REVERSE_BOARD = ("reverse-board", "금곡역 건너편", 37.6381, 127.2081)
REVERSE_ALIGHT = ("reverse-alight", "덕소아이파크 건너편", 37.5881, 127.2231)


def railway(origin, destination, start, end, name="경춘선"):
    minutes = (end - start).total_seconds() / 60
    return {"arrival_at": end, "segments": [_segment("subway", name, origin, destination, start, end, minutes)]}


class RailBusTransferTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.bus = self.data / "normalized/bus"
        self.bus.mkdir(parents=True)
        self.write("tago_stops_20261109.csv", ("source_city_code", "source_stop_id", "latitude", "longitude"),
                   [(CITY, stop, lat, lon) for stop, _, lat, lon in (BOARD, ALIGHT, REVERSE_BOARD, REVERSE_ALIGHT)])
        self.write(
            "gyeonggi_bus_transfer_sequences_20261125.csv",
            ("source_city_code", "source_route_id", "route_label", "source_direction_code", "stop_sequence",
             "source_stop_id", "stop_name", "latitude", "longitude"),
            [(CITY, ROUTE, "땡큐61", direction, ordinal, *stop)
             for direction, stops in (("1", (BOARD, ALIGHT)), ("0", (REVERSE_ALIGHT, REVERSE_BOARD)))
             for ordinal, stop in enumerate(stops, start=1)],
        )
        self.walk = Mock(side_effect=lambda _lat, _lon, name, *_end: (4, 330) if name == STATION["name"] else (2, 125))
        self.transfers = RailBusTransfers(self.data, "test-key", self.walk, now=lambda: NOW)
        self.client = Mock()
        self.client.station_routes.return_value = []
        self.client.arrivals.return_value = []
        patch_client = patch.object(self.transfers, "_client", return_value=self.client)
        self.client_factory = patch_client.start()
        self.addCleanup(patch_client.stop)
        self.station_key = STATION["lat"], STATION["lon"]

    def write(self, filename, headers, rows):
        with (self.bus / filename).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(headers)
            writer.writerows(rows)

    def write_frequencies(self):
        self.write(
            "gyeonggi_bus_transfer_frequencies_20261125.csv",
            ("source_city_code", "source_route_id", "direction", "day_type", "first_departure", "last_departure",
             "headway_min", "headway_max", "observed_on"),
            [(CITY, ROUTE, "1", "weekday", "06:00", "23:10", 30, 40, "2026-11-25")],
        )

    def onward(self, links, requested, train_arrival):
        train = railway(ORIGIN, STATION, requested, train_arrival)
        return self.transfers.connections(links, [(STATION, train)], DESTINATION, requested)

    def test_station_to_destination_only_uses_stops_in_forward_order(self):
        links = self.transfers.links([STATION], DESTINATION, NOW)
        self.assertEqual([(leg.leg.board.id, leg.leg.alight.id, leg.leg.board.direction)
                          for leg in links[self.station_key]], [(BOARD[0], ALIGHT[0], "1")])

    def test_future_search_considers_a_route_outside_the_local_frequency_file(self):
        route_id = "GGB222000055"
        requested = NOW + timedelta(days=1)
        schedule = GyeonggiBusSchedule("7", BOARD[1], ALIGHT[1], {
            ("0", "weekday"): GyeonggiFrequency(time(6), time(22), 10, 20, NOW.date()),
        })
        self.transfers.gyeonggi_schedules = Mock()
        self.transfers.gyeonggi_schedules.supports.side_effect = lambda city: city == CITY
        self.transfers.gyeonggi_schedules.route.side_effect = lambda city, route: schedule if (city, route) == (CITY, route_id) else None
        self.client.station_routes.return_value = [{"routeid": route_id, "routeno": "7"}]
        self.client.route_stops.return_value = [{"nodeid": node, "nodenm": name, "nodeord": index,
                                                "gpslati": lat, "gpslong": lon}
                                               for index, (node, name, lat, lon) in enumerate((BOARD, ALIGHT), start=1)]

        links = self.transfers.links([STATION], DESTINATION, requested)
        result = self.onward(links, requested, requested + timedelta(minutes=50))
        buses = [segment for _, onward in result for segment in onward["segments"] if segment["mode"] == "bus"]
        self.assertEqual(len(buses), 1)
        self.assertEqual(buses[0]["route_name"], "7")
        self.assertEqual(buses[0]["ride_time_basis"], "published_headway_estimate")
        self.assertFalse(buses[0]["realtime"])
        self.client.arrivals.assert_not_called()

    def test_bus_feeder_boards_after_walking_and_ends_at_the_exact_station(self):
        self.client.arrivals.return_value = [
            {"routeid": ROUTE, "arrtime": "120", "updowncd": "0"},
            {"routeid": ROUTE, "arrtime": "180", "updowncd": "0"},
            {"routeid": ROUTE, "arrtime": "540", "updowncd": "1"},
        ]

        [journey] = self.transfers.feeders(DESTINATION, [STATION], NOW)

        self.assertIs(journey["gateway"], STATION)
        first_walk, bus, last_walk = journey["segments"]
        self.assertEqual(first_walk["arrival_at"], NOW + timedelta(minutes=2))
        self.assertEqual(bus["departure_at"], NOW + timedelta(minutes=3))
        self.assertEqual(bus["from_place"]["id"], REVERSE_ALIGHT[0])
        self.assertEqual(bus["to_place"]["id"], REVERSE_BOARD[0])
        self.assertEqual(last_walk["arrival_at"], bus["arrival_at"] + timedelta(minutes=2))
        self.assertEqual(journey["arrival_at"], last_walk["arrival_at"])
        self.client.arrivals.assert_called_once_with(REVERSE_ALIGHT[0])

    def test_direct_bus_can_use_a_second_pedestrian_provider(self):
        self.client.arrivals.return_value = [
            {"routeid": ROUTE, "arrtime": "240", "updowncd": "0"},
            {"routeid": ROUTE, "arrtime": "720", "updowncd": "1"},
        ]
        self.walk.side_effect = None
        self.walk.return_value = -1, -1
        self.assertEqual(self.transfers.plan_direct(STATION, DESTINATION, NOW), [])

        self.transfers.walking_fallback = Mock(return_value=(3, 125))
        [journey] = self.transfers.plan_direct(STATION, DESTINATION, NOW)

        access, bus, egress = journey["segments"]
        self.assertEqual(access["departure_at"], NOW)
        self.assertEqual(bus["departure_at"], NOW + timedelta(minutes=12))
        self.assertEqual(bus["from_place"]["id"], BOARD[0])
        self.assertEqual(bus["to_place"]["id"], ALIGHT[0])
        self.assertEqual(egress["arrival_at"], bus["arrival_at"] + timedelta(minutes=3))
        self.assertEqual(self.transfers.walking_fallback.call_count, 2)

    def test_live_discovery_can_connect_a_route_outside_the_curated_sequences(self):
        other_route = "GGB-new-route"
        self.client.station_routes.side_effect = lambda stop_id: [{"routeid": other_route}] if stop_id == ALIGHT[0] else []
        self.client.route_info.return_value = {"routeid": other_route, "routeno": "신규"}
        self.client.route_stops.return_value = [
            {"nodeid": stop_id, "nodenm": name, "gpslati": lat, "gpslong": lon, "nodeord": order, "updowncd": 1}
            for order, (stop_id, name, lat, lon) in enumerate((BOARD, ALIGHT), start=1)
        ]
        self.client.arrivals.return_value = [{"routeid": other_route, "arrtime": "1500", "updowncd": "1"}]
        links = self.transfers.links([STATION], DESTINATION, NOW)
        [(_, journey)] = self.onward(links, NOW, NOW + timedelta(minutes=15))
        self.assertEqual(journey["segments"][1]["route_name"], "신규")
        self.assertTrue(journey["segments"][1]["realtime"])
        self.client.route_stops.assert_called_once_with(other_route)

    def test_stop_ids_are_only_matched_within_their_city_feed(self):
        other_city, other_route = "other-city", "other-route"
        other_client = Mock()
        other_client.station_routes.return_value = [{"routeid": other_route, "routeno": "99"}]
        other_client.route_stops.return_value = [
            {"nodeid": stop_id, "nodenm": name, "gpslati": lat, "gpslong": lon, "nodeord": order, "updowncd": 1}
            for order, (stop_id, name, lat, lon) in enumerate(
                (BOARD, (ALIGHT[0], "동일 ID의 다른 지역 정류장", 37.61, 127.22)), start=1)
        ]
        self.write("tago_stops_20261109.csv", ("source_city_code", "source_stop_id", "latitude", "longitude"),
                   [(other_city, "nearby", DESTINATION["lat"], DESTINATION["lon"])])
        self.client_factory.side_effect = lambda city: other_client if city == other_city else self.client

        links = self.transfers.links([STATION], DESTINATION, NOW)

        self.assertEqual([link.leg.route_id for link in links[self.station_key]], [ROUTE])
        other_client.route_stops.assert_called_once_with(other_route)

    def test_live_countdown_is_relative_to_observation_and_waits_for_the_rail_arrival_and_walk(self):
        requested = NOW + timedelta(minutes=8)
        ready = NOW + timedelta(minutes=19)
        links = self.transfers.links([STATION], DESTINATION, requested)
        # First same-direction bus leaves before the one-minute boarding margin.
        self.client.arrivals.return_value = [
            {"routeid": ROUTE, "arrtime": "1170", "updowncd": "1"},
            {"routeid": ROUTE, "arrtime": "1320", "updowncd": "0"},
            {"routeid": ROUTE, "arrtime": "1500", "updowncd": "1"},
        ]
        [(_, journey)] = self.onward(links, requested, NOW + timedelta(minutes=15))
        access, bus, egress = journey["segments"]
        self.assertEqual(access["arrival_at"], ready)
        self.assertEqual(bus["departure_at"], NOW + timedelta(minutes=25))
        self.assertEqual(egress["arrival_at"], bus["arrival_at"] + timedelta(minutes=2))
        self.assertTrue(bus["realtime"])
        self.assertTrue(journey["estimated"])
        self.client.arrivals.assert_called_once_with(BOARD[0])

    def test_missed_bus_does_not_become_a_connection_without_another_trip(self):
        links = self.transfers.links([STATION], DESTINATION, NOW)
        self.client.arrivals.return_value = [{"routeid": ROUTE, "arrtime": "1170", "updowncd": "1"}]
        self.assertEqual(self.onward(links, NOW, NOW + timedelta(minutes=15)), [])

    def test_gyeonggi_forecast_uses_the_station_route_and_stop_order(self):
        self.transfers.gyeonggi_arrivals = Mock()
        self.transfers.gyeonggi_arrivals.predictions.return_value = (1170, 1500)
        links = self.transfers.links([STATION], DESTINATION, NOW)
        [(_, journey)] = self.onward(links, NOW, NOW + timedelta(minutes=15))
        bus = next(leg for leg in journey["segments"] if leg["mode"] == "bus")
        self.transfers.gyeonggi_arrivals.predictions.assert_called_once_with(BOARD[0], ROUTE, 1)
        self.assertEqual(bus["source"], "경기버스정보")
        self.assertEqual(bus["departure_at"], NOW + timedelta(minutes=25))

    def test_gyeonggi_response_must_match_the_requested_direction_and_sequence(self):
        client = GyeonggiBusArrivals("fake-key")
        body = """<response><msgHeader><resultCode>0</resultCode></msgHeader><msgBody>
        <busArrivalItem><stationId>222000381</stationId><routeId>241349001</routeId><staOrder>44</staOrder>
        <predictTimeSec1>115</predictTimeSec1><predictTimeSec2>440</predictTimeSec2></busArrivalItem>
        </msgBody></response>"""
        with patch("services.gyeonggi_bus_arrivals.requests.get", return_value=Mock(status_code=200,
                                                                                    content=body.encode())) as get:
            self.assertEqual(client.predictions("GGB222000381", ROUTE, 44), (115, 440))
            self.assertEqual(get.call_args.kwargs["params"]["staOrder"], 44)
            self.assertEqual(client.predictions("GGB222000381", ROUTE, 17), ())
            get.return_value.status_code = 403
            self.assertEqual(client.predictions("GGB222000381", ROUTE, 44), ())

    def test_missing_pedestrian_route_invalidates_bus_connection(self):
        links = self.transfers.links([STATION], DESTINATION, NOW)
        self.client.arrivals.return_value = [{"routeid": ROUTE, "arrtime": "1500", "updowncd": "1"}]
        self.walk.return_value = None, None
        self.walk.side_effect = None
        self.assertEqual(self.onward(links, NOW, NOW + timedelta(minutes=15)), [])

    def test_seoul_live_times_work_for_direct_rail_and_terminal_connections(self):
        route_id = "100100052"
        official_data = Path(__file__).resolve().parents[1] / "data"
        topology = NationalBusRepository(official_data)
        origin = {"name": "잠실역", "lat": 37.513305, "lon": 127.100133}
        terminal = {"name": "동서울종합터미널", "lat": 37.5339323, "lon": 127.09476136}
        requester = Mock()
        requester.return_value.status_code = 200
        requester.return_value.content = b"""<ServiceResult><msgHeader><headerCd>0</headerCd></msgHeader><msgBody>
          <itemList><busRouteId>100100052</busRouteId><stId>123000579</stId><staOrd>40</staOrd>
            <vehId1>100000007</vehId1><exps1>420</exps1><mkTm>2026-11-25 14:42:00.0</mkTm></itemList>
          <itemList><busRouteId>100100052</busRouteId><stId>104000302</stId><staOrd>46</staOrd>
            <vehId2>100000007</vehId2><exps2>840</exps2><mkTm>2026-11-25 14:42:00.0</mkTm></itemList>
          </msgBody></ServiceResult>"""
        transfers = RailBusTransfers(
            self.data, "", self.walk, now=lambda: NOW,
            seoul_arrivals=SeoulBusArrivals("seoul-demo", requester=requester, now=lambda: NOW),
            seoul_routes=topology.nearby_seoul_routes,
        )
        [direct] = transfers.plan_direct(origin, terminal, NOW)
        bus = direct["segments"][1]
        self.assertEqual((bus["route_name"], bus["ride_time_basis"]), ("302", "realtime_stop_predictions"))
        self.assertEqual(bus["departure_at"], NOW + timedelta(minutes=7))
        self.assertEqual(bus["arrival_at"], NOW + timedelta(minutes=14))
        self.assertEqual(direct["segments"][-1]["arrival_at"], NOW + timedelta(minutes=16))
        self.assertNotIn("100000007", str(direct))
        self.assertEqual(sum(call.kwargs["params"]["busRouteId"] == route_id for call in requester.call_args_list), 1)

        [feeder] = transfers.feeders(origin, [terminal], NOW)
        self.assertEqual(feeder["gateway"], terminal)
        self.assertEqual(feeder["segments"][1], bus)
        links = transfers.links([origin], terminal, NOW)
        [(_, onward)] = transfers.connections(links, [(origin, {"arrival_at": NOW + timedelta(minutes=3)})], terminal, NOW)
        self.assertEqual(onward["segments"][1], bus)
        self.assertEqual(transfers.connections(links, [(origin, {"arrival_at": NOW + timedelta(minutes=6)})], terminal, NOW), [])
        self.assertEqual(transfers.plan_direct(origin, terminal, NOW + timedelta(days=1)), [])

    def test_future_and_after_last_departures_use_published_window_without_realtime(self):
        self.write_frequencies()
        requested = NOW + timedelta(days=1)
        train_arrival = requested + timedelta(minutes=15)
        links = self.transfers.links([STATION], DESTINATION, requested)
        [(_, journey)] = self.onward(links, requested, train_arrival)
        walk, wait, bus, _ = journey["segments"]
        self.assertEqual(wait["mode"], "wait")
        self.assertTrue(wait["estimated"])
        self.assertEqual(wait["departure_at"], walk["arrival_at"])
        self.assertEqual(wait["arrival_at"], bus["departure_at"])
        self.assertEqual(bus["departure_at"], train_arrival + timedelta(minutes=22.5))
        self.assertEqual(bus["ride_time_basis"], "published_headway_estimate")
        self.assertTrue(bus["estimated"])
        self.assertFalse(bus["realtime"])
        self.client_factory.assert_not_called()
        after_last = requested.replace(hour=23, minute=12)
        self.assertEqual(self.onward(links, requested, after_last), [])

    def test_full_route_with_station_bus_ranks_before_longer_rail_route(self):
        repository = Mock()
        repository.transfer_stations.return_value = [STATION]
        direct_arrival = NOW + timedelta(minutes=125)
        repository.plan.return_value = [railway(ORIGIN, DESTINATION, NOW, direct_arrival, "경의중앙선")]
        repository.plan_to_stations.return_value = [(STATION, railway(ORIGIN, STATION, NOW, NOW + timedelta(minutes=70)))]
        self.client.arrivals.return_value = [{"routeid": ROUTE, "arrtime": "4800", "updowncd": "1"}]

        baseline = NationalJourneyService(repository, self.walk, now=lambda: NOW).plan(ORIGIN, DESTINATION, NOW)
        result = NationalJourneyService(repository, self.walk, now=lambda: NOW, rail_bus_transfers=self.transfers).plan(
            ORIGIN, DESTINATION, NOW,
        )
        best, direct = result["journeys"]
        self.assertEqual(direct, baseline["journeys"][0])
        self.assertEqual([leg["mode"] for leg in best["segments"]], ["subway", "walk", "wait", "bus", "walk"])
        self.assertEqual(best["segments"][2]["duration_min"], 6)
        self.assertEqual(best["segments"][3]["to_place"]["id"], ALIGHT[0])
        self.assertLess(best["total_min"], direct["total_min"])
        self.assertEqual(best["total_min"], sum(leg["duration_min"] for leg in best["segments"]))


if __name__ == "__main__":
    unittest.main()
