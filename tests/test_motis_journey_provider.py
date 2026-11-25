"""Exercise the MOTIS v6 adapter without contacting a routing server."""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock

import requests

from services.motis_journey_provider import MotisJourneyProvider, MotisProviderError
from services.national_journey_service import KST, _finalize


ORIGIN = {"name": "한림대학교 의학관", "lat": 37.885861, "lon": 127.738729, "campus": "hallym"}
DESTINATION = {"name": "다산역", "lat": 37.624339, "lon": 127.151721}
CHUNCHEON = {"name": "춘천", "lat": 37.885189, "lon": 127.717066}
BYEOLLAE = {"name": "별내", "lat": 37.642391, "lon": 127.127975}
DASAN = {"name": "다산", "lat": 37.624330, "lon": 127.151730}
AT = datetime(2026, 11, 24, 22, 0, tzinfo=KST)


def motis_time(minutes):
    return (AT + timedelta(minutes=minutes)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def leg(mode, origin, destination, start, end, **extra):
    return {"mode": mode, "from": origin, "to": destination, "realTime": False,
            "startTime": motis_time(start), "endTime": motis_time(end), **extra}


def itinerary():
    return {
        "startTime": motis_time(1), "endTime": motis_time(130), "legs": [
            leg("WALK", {**ORIGIN, "name": "START"}, CHUNCHEON, 1, 23),
            leg("SUBURBAN", CHUNCHEON, BYEOLLAE, 31, 117, displayName="경춘선", realTime=True),
            leg("SUBWAY", BYEOLLAE, DASAN, 123, 128, routeShortName="8호선"),
            leg("WALK", DASAN, {**DESTINATION, "name": "END", "lat": 37.62438}, 128, 130),
        ],
    }


class MotisJourneyProviderTests(unittest.TestCase):
    def setUp(self):
        self.reply = Mock(status_code=200)
        self.request = Mock(return_value=self.reply)
        self.provider = MotisJourneyProvider("https://motis.example/", user_agent="HallymCommute/1.0",
                                             requester=self.request)

    def test_walking_fallback_requires_a_matching_pedestrian_route_and_keeps_precise_seconds(self):
        wrong_stop = leg("WALK", CHUNCHEON, DESTINATION, 0, 3, duration=180, distance=190)
        wrong_mode = leg("CAR", ORIGIN, CHUNCHEON, 0, 3, duration=180, distance=190)
        verified = leg("WALK", ORIGIN, CHUNCHEON, 0, 3, duration=185, distance=187.3)
        args = (ORIGIN["lat"], ORIGIN["lon"], ORIGIN["name"], CHUNCHEON["lat"], CHUNCHEON["lon"], CHUNCHEON["name"])
        self.reply.json.return_value = {"direct": [{"legs": [wrong_stop]}, {"legs": [wrong_mode]}]}
        self.assertEqual(self.provider.walking_time(*args), (-1, -1))
        self.reply.json.return_value["direct"].append({"legs": [verified]})
        self.assertEqual(self.provider.walking_time(*args), (185 / 60, 188))
        parameters = self.request.call_args.kwargs["params"]
        self.assertEqual(parameters["directModes"], "WALK")
        self.assertEqual(parameters["transitModes"], "")
        self.assertEqual(parameters["maxDirectTime"], 3600)
        self.assertNotIn("time", parameters)

        self.assertEqual(self.provider.walking_time(*args, reference=True), (185 / 60, 188))
        self.assertEqual(self.request.call_args.kwargs["params"]["time"], "2025-03-19T12:00:00+09:00")

    def test_requests_earliest_arrival_and_preserves_leg_times_and_source(self):
        self.reply.json.return_value = {"itineraries": [itinerary()]}
        requested_at = AT + timedelta(seconds=20)
        [journey] = self.provider.plan(ORIGIN, DESTINATION, requested_at)

        self.request.assert_called_once_with(
            "https://motis.example/api/v6/plan",
            params={"fromPlace": "37.885861,127.738729", "toPlace": "37.624339,127.151721",
                    "time": "2026-11-24T22:01:00+09:00", "arriveBy": "false", "timetableView": "false",
                    "maxPreTransitTime": 3600, "maxPostTransitTime": 3600,
                    "minTransferTime": 5, "useRoutedTransfers": "true",
                    "realtimeMode": "REALTIME", "preTransitModes": "WALK", "postTransitModes": "WALK",
                    "detailedLegs": "false", "language": "ko"},
            timeout=(1.5, 4.0), allow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "HallymCommute/1.0"},
        )

        self.assertEqual(journey["source"], "MOTIS")
        self.assertTrue(journey["complete"])
        walk, commuter, subway, egress = journey["segments"]
        self.assertEqual(walk["from_place"], ORIGIN)
        self.assertEqual((walk["mode"], walk["duration_min"], walk["fare_won"]), ("walk", 22, 0))
        self.assertEqual((commuter["mode"], commuter["route_name"], commuter["realtime"]),
                         ("subway", "경춘선", True))
        self.assertEqual((subway["mode"], subway["route_name"], subway["realtime"]), ("subway", "8호선", False))
        self.assertIsNone(commuter["fare_won"])
        self.assertEqual(egress["to_place"], DESTINATION)

        finalized = _finalize(journey, requested_at)
        self.assertEqual(finalized["arrival_at"], "2026-11-25T00:10:00+09:00")
        waits = [segment["duration_min"] for segment in finalized["segments"] if segment["mode"] == "wait"]
        self.assertEqual(waits, [0.67, 8, 6])
        self.assertEqual(finalized["total_min"], 129.67)

    def test_keeps_mode_labels_and_only_books_identified_rail_services(self):
        rides = [leg("LONG_DISTANCE", ORIGIN, DESTINATION, 5, 105, displayName="ITX-청춘 S2036"),
                 leg("HIGHSPEED_RAIL", ORIGIN, DESTINATION, 5, 105, routeShortName="SRT"),
                 leg("REGIONAL_RAIL", ORIGIN, DESTINATION, 5, 105, displayName="R7"),
                 leg("TRAM", ORIGIN, DESTINATION, 5, 105, displayName="노선 12", routeType=0),
                 leg("TRAM", ORIGIN, DESTINATION, 5, 105, displayName="노선 12", routeType=3),
                 leg("COACH", ORIGIN, DESTINATION, 5, 105, displayName="시외버스")]
        self.reply.json.return_value = {"itineraries": [
            {"startTime": motis_time(5), "endTime": motis_time(105), "legs": [ride]} for ride in rides
        ]}
        segments = [item["segments"][0] for item in self.provider.plan(ORIGIN, DESTINATION, AT)]
        self.assertEqual([item["mode"] for item in segments],
                         ["train", "train", "train", "tram", "bus", "intercity_bus"])
        self.assertEqual([item["booking_provider"] for item in segments], ["korail", "srt", None, None, None, None])
        self.assertFalse(segments[2]["bookable"])
        self.assertNotIn("booking", _finalize({"segments": [segments[2]]}, AT)["segments"][-1])

    def test_reference_search_uses_seoul_clock_and_drops_times_trip_identity_and_booking(self):
        ride = leg("LONG_DISTANCE", ORIGIN, DESTINATION, 6, 105, displayName="ITX-청춘",
                   tripShortName="S2036", realTime=True, scheduledStartTime="2025-03-19T13:06:00Z")
        for field in ("startTime", "endTime"):
            ride[field] = ride[field].replace("2026-11-24", "2025-03-19")
        self.reply.json.return_value = {"itineraries": [
            {"startTime": ride["startTime"], "endTime": ride["endTime"], "legs": [ride]}
        ]}
        [journey] = self.provider.plan_reference(ORIGIN, DESTINATION, AT.astimezone(timezone.utc))
        self.assertEqual(self.request.call_args.kwargs["params"]["time"], "2025-03-19T22:00:00+09:00")
        self.assertEqual(journey["reference_kind"], "ktdb_2025_03_weekday")
        self.assertFalse(journey["complete"])
        [segment] = _finalize(journey, AT)["segments"]
        self.assertEqual((segment["mode"], segment["route_name"]), ("train", "ITX-청춘"))
        for field in ("departure_at", "arrival_at", "duration_min", "fare_won"):
            self.assertIsNone(segment[field])
        for field in ("booking", "scheduled_departure_at", "scheduled_arrival_at", "service_name"):
            self.assertNotIn(field, segment)
        self.assertFalse(segment["realtime"])

    def test_rejects_missing_endpoint_overlap_unknown_modes_past_trips_and_walking_only(self):
        incomplete = itinerary()
        incomplete["legs"][-1]["to"] = DASAN | {"lat": 37.61}
        overlapping = itinerary()
        overlapping["legs"][2]["startTime"] = motis_time(116)
        untimed = itinerary()
        untimed["legs"][0]["endTime"] = None
        wrong_arrival = itinerary() | {"endTime": motis_time(131)}
        unknown = itinerary()
        unknown["legs"][0]["mode"] = "CAR"
        cancelled = itinerary()
        cancelled["legs"][1]["cancelled"] = True
        old = itinerary() | {"startTime": motis_time(-1)}
        foot = {"startTime": motis_time(1), "endTime": motis_time(130),
                "legs": [leg("WALK", ORIGIN, DESTINATION, 1, 130)]}
        self.reply.json.return_value = {"itineraries": [incomplete, overlapping, untimed, wrong_arrival, unknown,
                                                       cancelled, old, foot, itinerary()]}
        self.assertEqual(len(self.provider.plan(ORIGIN, DESTINATION, AT)), 1)

    def test_empty_and_invalid_responses_remain_distinct(self):
        self.reply.json.return_value = {"itineraries": []}
        self.assertEqual(self.provider.plan(ORIGIN, DESTINATION, AT), [])
        self.reply.json.return_value = {"itineraries": None}
        with self.assertRaises(MotisProviderError):
            self.provider.plan(ORIGIN, DESTINATION, AT)
        self.reply.json.side_effect = ValueError("invalid JSON")
        with self.assertRaises(MotisProviderError):
            self.provider.plan(ORIGIN, DESTINATION, AT)

    def test_redirects_and_timeouts_are_reported_without_request_details(self):
        self.reply.status_code = 302
        with self.assertRaises(MotisProviderError):
            self.provider.plan(ORIGIN, DESTINATION, AT)
        self.reply.json.assert_not_called()

        self.request.side_effect = requests.Timeout("upstream URL includes coordinates")
        with self.assertRaisesRegex(MotisProviderError, "^MOTIS planning request failed$"):
            self.provider.plan(ORIGIN, DESTINATION, AT)
