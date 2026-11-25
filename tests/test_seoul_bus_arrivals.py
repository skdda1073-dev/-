from datetime import datetime, timedelta
import unittest
from unittest.mock import Mock, patch
from xml.etree import ElementTree

import requests

from services.seoul_bus_arrivals import ARRIVAL_URL, CACHE_TTL_SECONDS, KST, SeoulBusArrivals


ROUTE = "100100052"
BOARD = "123000579"
ALIGHT = "104000302"
VEHICLE_A = "117000021"
VEHICLE_B = "117000022"
NOW = datetime(2026, 11, 25, 16, 35, 14, tzinfo=KST)


def stop(identifier, ordinal, first=("", ""), second=("", ""), **overrides):
    return {
        "busRouteId": ROUTE, "stId": identifier, "staOrd": str(ordinal),
        "mkTm": "2026-11-25 16:35:14.0", "deTourAt": "00",
        "vehId1": first[0], "exps1": str(first[1]), "full1": "0",
        "vehId2": second[0], "exps2": str(second[1]), "full2": "0",
        **overrides,
    }


def response(*rows, header_code="0", status_code=200):
    result = ElementTree.Element("ServiceResult")
    message = ElementTree.SubElement(result, "msgHeader")
    ElementTree.SubElement(message, "headerCd").text = header_code
    body = ElementTree.SubElement(result, "msgBody")
    for fields in rows:
        item = ElementTree.SubElement(body, "itemList")
        for field, value in fields.items():
            ElementTree.SubElement(item, field).text = value
    return Mock(status_code=status_code, content=ElementTree.tostring(result))


class SeoulBusArrivalsTests(unittest.TestCase):
    def setUp(self):
        self.request = Mock()
        self.arrivals = SeoulBusArrivals("test%2Bvalue%3D", requester=self.request, now=lambda: NOW)

    def pair(self, board, alight, *, ready=60):
        self.request.return_value = response(board, alight)
        snapshot = self.arrivals.snapshot(ROUTE)
        return self.arrivals.trip_times(snapshot, BOARD, 40, ALIGHT, 46, NOW + timedelta(seconds=ready))

    def test_matches_vehicle_across_slots_and_keeps_provider_time(self):
        result = self.pair(
            stop(BOARD, 40, (VEHICLE_A, 200)),
            stop(ALIGHT, 46, (VEHICLE_B, 420), (VEHICLE_A, 490), mkTm="2026-11-25 16:35:04.0"),
        )
        self.assertEqual(result, (NOW + timedelta(seconds=200), NOW + timedelta(seconds=480)))
        self.request.assert_called_once_with(
            ARRIVAL_URL, params={"serviceKey": "test+value=", "busRouteId": ROUTE},
            timeout=(2, 4), allow_redirects=False,
        )

    def test_picks_earliest_alighting_after_boarding_readiness(self):
        result = self.pair(stop(BOARD, 40, (VEHICLE_A, 125), (VEHICLE_B, 142)),
                           stop(ALIGHT, 46, (VEHICLE_B, 395), (VEHICLE_A, 401)))
        self.assertEqual(result, (NOW + timedelta(seconds=142), NOW + timedelta(seconds=395)))

    def test_missed_or_full_vehicle_is_skipped_but_alight_full_flag_does_not_block(self):
        self.request.return_value = response(
            stop(BOARD, 40, (VEHICLE_A, 40), (VEHICLE_B, 157), full1="1"),
            stop(ALIGHT, 46, (VEHICLE_A, 310), (VEHICLE_B, 450), full2="1"),
        )
        snapshot = self.arrivals.snapshot(ROUTE)
        for ready in (30, 90):
            with self.subTest(ready=ready):
                self.assertEqual(self.arrivals.trip_times(snapshot, BOARD, 40, ALIGHT, 46, NOW + timedelta(seconds=ready)),
                                 (NOW + timedelta(seconds=157), NOW + timedelta(seconds=450)))
        self.assertIsNone(self.arrivals.trip_times(snapshot, BOARD, 40, ALIGHT, 46, NOW + timedelta(seconds=158)))

    def test_match_requires_exact_stop_ids_order_and_a_later_arrival(self):
        self.request.return_value = response(stop(BOARD, 40, (VEHICLE_A, 200)), stop(BOARD, 80, (VEHICLE_B, 300)),
                                             stop(ALIGHT, 46, (VEHICLE_A, 190)), stop(ALIGHT, 55, (VEHICLE_A, 400)))
        snapshot = self.arrivals.snapshot(ROUTE)
        for board, order, alight, end in ((BOARD, 40, ALIGHT, 46), (BOARD, 80, ALIGHT, 55),
                                           (BOARD, 40, "123000001", 55), ("24017", 40, ALIGHT, 55)):
            with self.subTest(board=board, order=order, alight=alight, end=end):
                self.assertIsNone(self.arrivals.trip_times(snapshot, board, order, alight, end, NOW))
        self.assertEqual(self.arrivals.trip_times(snapshot, BOARD, 40, ALIGHT, 55, NOW),
                         (NOW + timedelta(seconds=200), NOW + timedelta(seconds=400)))

    def test_drops_detours_bad_ids_stale_rows_and_invalid_predictions(self):
        self.request.return_value = response(
            stop(BOARD, 40, ("0", 140), (VEHICLE_B, 0)),
            stop(BOARD, 41, (VEHICLE_B, 140), deTourAt="11"),
            stop(BOARD, 42, (VEHICLE_B, 145), mkTm="2026-11-25 16:30:00.0"),
            stop(BOARD, 43, (VEHICLE_B, 150), mkTm="2026-11-25 17:30:00.0"),
            stop(BOARD, 44, (VEHICLE_B, -20), (VEHICLE_A, 9000)),
            stop(BOARD, 45, (VEHICLE_B, 150), busRouteId="100100118"),
            stop("05272", 45, (VEHICLE_B, 150)),
            stop(ALIGHT, 46, (VEHICLE_A, 350), (VEHICLE_B, 470)),
        )
        snapshot = self.arrivals.snapshot(ROUTE)
        self.assertEqual({key for key, predictions in snapshot.items() if predictions}, {(ALIGHT, 46)})

    def test_bad_http_provider_error_and_malformed_xml_fail_closed(self):
        failures = [response(status_code=401), response(header_code="7"),
                    Mock(status_code=200, content=b"<not valid"),
                    requests.Timeout("not for logs")]
        for failure in failures:
            with self.subTest(failure=type(failure)):
                request = Mock(side_effect=[failure])
                arrivals = SeoulBusArrivals("test", requester=request, now=lambda: NOW)
                self.assertEqual(arrivals.snapshot(ROUTE), {})

    def test_missing_key_or_invalid_route_never_calls_the_api(self):
        self.assertEqual(SeoulBusArrivals("", requester=self.request).snapshot(ROUTE), {})
        for invalid in ("302", 100100052, "100100052?x=1", None):
            self.assertEqual(self.arrivals.snapshot(invalid), {})
        self.request.assert_not_called()

    def test_cache_expires_without_shifting_observation_time(self):
        self.request.return_value = response(stop(BOARD, 40, (VEHICLE_A, 140)), stop(ALIGHT, 46, (VEHICLE_A, 390)))
        with patch("services.seoul_bus_arrivals.monotonic", return_value=100) as clock:
            first = self.arrivals.snapshot(ROUTE)
            clock.return_value += CACHE_TTL_SECONDS - 1
            self.assertIs(self.arrivals.snapshot(ROUTE), first)
            self.assertEqual(self.request.call_count, 1)
            clock.return_value += 1
            self.assertEqual(self.arrivals.snapshot(ROUTE), first)
            self.assertEqual(self.request.call_count, 2)
