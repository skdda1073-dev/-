"""Adapt MOTIS v6 transit itineraries to the national journey service."""

from datetime import datetime, timedelta
from urllib.parse import urlsplit

import requests


MODES = {
    "WALK": "walk",
    "BUS": "bus",
    "COACH": "intercity_bus",
    "SUBWAY": "subway",
    "SUBURBAN": "subway",
    "METRO": "subway",
    "TRAM": "tram",
    "RAIL": "train",
    "HIGHSPEED_RAIL": "train",
    "LONG_DISTANCE": "train",
    "NIGHT_RAIL": "train",
    "REGIONAL_FAST_RAIL": "train",
    "REGIONAL_RAIL": "train",
    "FERRY": "ferry",
    "AIRPLANE": "flight",
}
MODE_NAMES = {
    "walk": "도보", "bus": "시내버스", "intercity_bus": "시외버스", "subway": "전철",
    "tram": "트램", "train": "철도", "ferry": "여객선", "flight": "항공",
}
KORAIL_SERVICES = {"KTX", "KTX-산천", "KTX-이음", "KTX-청룡", "ITX-청춘", "ITX-새마을",
                   "ITX-마음", "무궁화", "무궁화호", "새마을", "새마을호", "누리로"}
MAX_STATION_WALK_SECONDS = 3600


class MotisProviderError(Exception):
    """MOTIS could not return a usable planning response."""


def _string(value):
    return value.strip() if isinstance(value, str) else ""


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _place(value):
    if not isinstance(value, dict):
        return None
    name, lat, lon = _string(value.get("name")), value.get("lat"), value.get("lon")
    if not name or type(lat) not in (int, float) or type(lon) not in (int, float):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return {"name": name, "lat": lat, "lon": lon}


def _matches_endpoint(place, requested):
    # MOTIS echoes coordinate endpoints. Allow for its coordinate precision.
    return all(abs(place[key] - requested[key]) <= 0.00005 for key in ("lat", "lon"))


def _booking_provider(mode, names):
    if mode != "train":
        return None
    services = {name.split(maxsplit=1)[0].upper() for name in names if name}
    if "SRT" in services:
        return "srt"
    if services & KORAIL_SERVICES:
        return "korail"
    return None


def _segment(leg, earliest):
    if not isinstance(leg, dict) or leg.get("cancelled") is True:
        return None
    mode = MODES.get(_string(leg.get("mode")))
    if mode == "tram" and leg.get("routeType") == 3:
        # GTFS uses route_type=3 for bus. Never infer a bus from its route name.
        mode = "bus"
    start, end = _time(leg.get("startTime")), _time(leg.get("endTime"))
    origin, destination = _place(leg.get("from")), _place(leg.get("to"))
    if not (mode and start and end and origin and destination and earliest <= start <= end):
        return None

    names = [_string(leg.get(field)) for field in ("displayName", "routeShortName", "routeLongName")]
    route_name = next((name for name in names if name), MODE_NAMES[mode])
    booking = _booking_provider(mode, names)
    result = {
        "mode": mode, "route_name": route_name, "from_place": origin, "to_place": destination,
        "departure_at": start, "arrival_at": end, "duration_min": round((end - start).total_seconds() / 60, 2),
        "fare_won": 0 if mode == "walk" else None, "source": "MOTIS",
        "realtime": mode != "walk" and leg.get("realTime") is True,
        "booking_provider": booking, "bookable": booking is not None,
    }
    trip_number = _string(leg.get("tripShortName"))
    if booking and trip_number:
        result["service_name"] = f"{route_name} {trip_number}"
        for field, source in (("scheduled_departure_at", "scheduledStartTime"), ("scheduled_arrival_at", "scheduledEndTime")):
            scheduled = _time(leg.get(source))
            if scheduled is not None:
                result[field] = scheduled.isoformat()
    return result


def _journey(itinerary, origin, destination, query_at):
    if not isinstance(itinerary, dict) or not isinstance(itinerary.get("legs"), list):
        return None
    start, end = _time(itinerary.get("startTime")), _time(itinerary.get("endTime"))
    if not (start and end and query_at <= start <= end):
        return None

    segments = []
    earliest = start
    for leg in itinerary["legs"]:
        segment = _segment(leg, earliest)
        if segment is None:
            return None
        segments.append(segment)
        earliest = segment["arrival_at"]
    if not any(segment["mode"] != "walk" for segment in segments) or earliest != end:
        return None
    first, last = segments[0], segments[-1]
    if not (_matches_endpoint(first["from_place"], origin) and _matches_endpoint(last["to_place"], destination)):
        return None
    first["from_place"], last["to_place"] = origin, destination
    return {"segments": segments, "source": "MOTIS", "complete": True}


class MotisJourneyProvider:
    def __init__(self, base_url, *, user_agent, requester=None):
        try:
            url = urlsplit(base_url)
            if (url.scheme not in ("https", "http") or not url.hostname or url.username is not None
                    or url.password is not None or url.query or url.fragment):
                raise ValueError
        except ValueError:
            raise ValueError("Invalid MOTIS base URL") from None
        self.url = url.geturl().rstrip("/") + "/api/v6/plan"
        self.user_agent = user_agent
        self.requester = requester if requester is not None else requests.get

    def plan(self, origin, destination, query_at):
        # MOTIS truncates search times to whole minutes. Round upwards to avoid offering a departed trip.
        routing_at = query_at.replace(second=0, microsecond=0)
        if routing_at < query_at:
            routing_at += timedelta(minutes=1)
        params = {
            "fromPlace": f"{origin['lat']},{origin['lon']}",
            "toPlace": f"{destination['lat']},{destination['lon']}",
            "time": routing_at.isoformat(), "arriveBy": "false", "timetableView": "false",
            "realtimeMode": "REALTIME", "preTransitModes": "WALK", "postTransitModes": "WALK",
            "maxPreTransitTime": MAX_STATION_WALK_SECONDS, "maxPostTransitTime": MAX_STATION_WALK_SECONDS,
            "minTransferTime": 5, "useRoutedTransfers": "true",
            "detailedLegs": "false", "language": "ko",
        }
        try:
            response = self.requester(self.url, params=params, timeout=(1.5, 4.0), allow_redirects=False,
                                      headers={"Accept": "application/json", "User-Agent": self.user_agent})
            if response.status_code != 200:
                raise MotisProviderError("MOTIS planning request failed")
            data = response.json()
        except (requests.RequestException, ValueError):
            raise MotisProviderError("MOTIS planning request failed") from None
        if not isinstance(data, dict) or not isinstance(data.get("itineraries"), list):
            raise MotisProviderError("MOTIS returned an invalid planning response")
        journeys = (_journey(itinerary, origin, destination, query_at) for itinerary in data["itineraries"])
        return [journey for journey in journeys if journey is not None]
