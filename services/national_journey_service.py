"""Join supported timetable providers and measured walking into a journey timeline."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import partial
import json
from math import isfinite, sqrt
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from services.intercity_client import IntercityError
from services.mobility_validation import MobilityInputError, clean_text, coordinate
from services.motis_journey_provider import MotisProviderError
from services.national_transit_repository import BOARDING_SECONDS
from services.place_search_service import PlaceSearchError
from services.public_transit_client import PublicDataError
from services.route_planner import BOARDING_MARGIN_MIN


KST = ZoneInfo("Asia/Seoul")
BOOKING_SITES = {
    "korail": ("코레일", "https://www.korail.com/ticket"),
    "srt": ("코레일", "https://www.korail.com/ticket"),
    "kobus": ("코버스", "https://www.kobus.co.kr/main.do"),
    "bustago": ("버스타고", "https://www.bustago.or.kr/newweb/kr/ticket/ticket.do"),
    "tmoney": ("티머니 시외버스", "https://intercitybus.tmoney.co.kr/"),
}
CHUNCHEON_TERMINAL = {
    "provider_id": "BUSTAGO_2037",
    "name": "춘천시외버스터미널",
    "address": "강원특별자치도 춘천시 경춘로 2341",
}
CHUNCHEON_CENTER = {"lat": 37.878, "lon": 127.737}
LIVE_DEPARTURE_WINDOW = timedelta(minutes=2)
CHUNCHEON_LOCAL_RADIUS_KM = 14
CHUNCHEON_RAIL_ACCESS_RADIUS_KM = 6
ENDPOINT_TOLERANCE_KM = 0.025
RAIL_MODES = {"train", "subway"}
PROVINCE_NAMES = {"충남": ("충남", "충청남도"), "충북": ("충북", "충청북도"),
                  "경남": ("경남", "경상남도"), "경북": ("경북", "경상북도"),
                  "전남": ("전남", "전라남도"), "전북": ("전북", "전라북도", "전북특별자치도")}
TERMINAL_ALIASES = {"BUSTAGO_9001": ("부산종합버스터미널",)}


def journey_point(value, field, campus_buildings):
    if not isinstance(value, dict):
        raise MobilityInputError(f"INVALID_{field.upper()}", "학교 건물 또는 좌표가 확인된 장소를 선택해 주세요.")
    campus = value.get("campus")
    building = value.get("building")
    if campus is not None and (not isinstance(campus, str) or campus not in campus_buildings):
        raise MobilityInputError("INVALID_CAMPUS", "학교를 확인해 주세요.")
    if building is None and campus and value.get("lat") is None and value.get("lon") is None:
        building = value.get("name")
    if building is not None:
        building = clean_text(building, field + "_building", 100)
        if not campus or building not in campus_buildings[campus]:
            raise MobilityInputError("INVALID_BUILDING", "선택한 학교의 건물을 확인해 주세요.")
        lat, lon = campus_buildings[campus][building]
        school = {"hallym": "한림대학교", "knu": "강원대학교"}.get(campus, "")
        point = {"name": f"{school} {building}".strip(), "campus": campus, "building": building}
    else:
        lat, lon = value.get("lat"), value.get("lon")
        point = {"name": clean_text(value.get("name"), field + "_name", 100)}
    point["lat"] = coordinate(lat, 32.9, 39.5, field + "_lat")
    point["lon"] = coordinate(lon, 124, 132, field + "_lon")
    return point


def journey_departure(value):
    if value is None:
        return datetime.now(KST)
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:[.][0-9]{1,6})?)?(?:Z|[+-][0-9]{2}:[0-9]{2})?", value
    ):
        raise MobilityInputError("INVALID_DEPARTURE_AT", "출발 날짜와 시간을 확인해 주세요.")
    try:
        parsed = datetime.fromisoformat(value)
        parsed = parsed.replace(tzinfo=KST) if parsed.tzinfo is None else parsed.astimezone(KST)
        if parsed.year < 1900 or parsed.year >= 9999:
            raise ValueError
        return parsed
    except (ValueError, OverflowError):
        raise MobilityInputError("INVALID_DEPARTURE_AT", "출발 날짜와 시간을 확인해 주세요.") from None


def _time(value):
    if isinstance(value, datetime):
        return value.astimezone(KST) if value.tzinfo else None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            return parsed.astimezone(KST)
    except (ValueError, OverflowError):
        pass
    return None


def _quantity(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def _distance_km(start, end):
    # Candidate screening only. Displayed walks always use the pedestrian provider.
    return sqrt(((start["lat"] - end["lat"]) * 111) ** 2 + ((start["lon"] - end["lon"]) * 88) ** 2)


def _measured_walk(walking_time, start, end):
    start_coords = start["lat"], start["lon"]
    end_coords = end["lat"], end["lon"]
    if start_coords == end_coords:
        return 0, 0
    minutes, meters = walking_time(*start_coords, start["name"], *end_coords, end["name"])
    if _quantity(minutes) and _quantity(meters):
        return minutes, meters
    return None


def _mode(segment):
    return {"city_bus": "bus", "rail": "train", "metro": "subway", "coach": "intercity_bus", "transfer_walk": "walk"}.get(
        segment.get("mode"), segment.get("mode")
    )


def _booking(segment):
    provider = segment.get("booking_provider")
    if provider is None and segment["mode"] == "train" and segment.get("bookable", True):
        provider = "korail"
    if provider is None and segment["mode"] == "express_bus":
        provider = "kobus"
    if not isinstance(provider, str) or provider not in BOOKING_SITES:
        return None
    name, url = BOOKING_SITES[provider]
    return {"name": name, "url": url}


def _segment(mode, route, start, end, departure, arrival, duration, fare=None, **extra):
    return {"mode": mode, "route_name": route, "from_place": start, "to_place": end,
            "departure_at": departure, "arrival_at": arrival, "duration_min": duration,
            "fare_won": fare, **extra}


def _train_identity(name):
    service = (name or "").upper().rsplit(maxsplit=1)
    if len(service) != 2 or not re.fullmatch(r"[A-Z]?[0-9]+", service[1]):
        return None
    kind, number = service
    if kind == "ITX-청춘":
        # The GTFS trip number uses S2005 where TAGO returns 02005.
        number = number.removeprefix("S")
    return kind, number.lstrip("0")


def _scheduled_train_key(leg, *, original=False):
    if leg["mode"] != "train" or leg.get("booking_provider") not in {"korail", "srt"}:
        return None
    service = _train_identity(leg.get("service_name"))
    if service is None:
        return None
    prefix = "scheduled_" if original else ""
    start, end = (_time(leg.get(prefix + field)) for field in ("departure_at", "arrival_at"))
    if start is None or end is None:
        return None
    stations = tuple(re.sub(r"\s+", "", leg[field]["name"]).removesuffix("역")
                     for field in ("from_place", "to_place"))
    # MOTIS exposes minute-level rail times; the published timetable can contain seconds.
    times = tuple(value.replace(second=0, microsecond=0) for value in (start, end))
    return (leg["booking_provider"], service, stations, times)


def _connected(start, end):
    # Published stop/platform coordinates can differ by survey precision. A real connection
    # between different stops must have its own walking segment.
    try:
        return _distance_km(start, end) <= ENDPOINT_TOLERANCE_KM
    except (TypeError, KeyError, OverflowError):
        return False


def _finalize(journey, query_at, *, origin=None, destination=None):
    current = query_at
    complete = True
    segments = []
    previous_place = origin
    previous_vehicle_end = None
    for given in journey.get("segments", []):
        segment = {**given, "mode": _mode(given)}
        if segments and segment["mode"] == "wait" and segment.get("route_name") == "출발까지 대기":
            segment["route_name"] = "승차 대기"
        begin_place, end_place = segment.get("from_place"), segment.get("to_place")
        if (not isinstance(begin_place, dict) or not isinstance(end_place, dict)
                or (previous_place is not None and not _connected(previous_place, begin_place))):
            return None
        start, end = _time(segment.get("departure_at")), _time(segment.get("arrival_at"))
        if (start and start < current) or (start and end and end < start) or (end and end < current):
            return None
        if segment["mode"] not in ("walk", "wait", "connection"):
            if start is not None and previous_vehicle_end is not None and start < previous_vehicle_end + timedelta(minutes=BOARDING_MARGIN_MIN):
                return None
            previous_vehicle_end = end
        if (segment["mode"] == "wait" or (start is not None and start == end)) and not _connected(begin_place, end_place):
            return None
        if complete and start is not None and start > current:
            stop = segment["from_place"]
            segments.append(_segment("wait", "승차 대기" if segments else "출발까지 대기", stop, stop, current.isoformat(),
                                     start.isoformat(), round((start - current).total_seconds() / 60, 2), 0))
        if start is None or end is None:
            complete = False
        if end is not None:
            current = end
        minutes = (round((end - start).total_seconds() / 60, 2) if start is not None and end is not None
                   else _quantity(segment.get("duration_min")))
        segment.update(departure_at=start.isoformat() if start else None, arrival_at=end.isoformat() if end else None,
                       duration_min=minutes, fare_won=_quantity(segment.get("fare_won")))
        if segment["mode"] in ("walk", "wait"):
            segment["fare_won"] = 0
        segment.pop("booking", None)
        booking = _booking(segment)
        if booking:
            segment["booking"] = booking
        segments.append(segment)
        previous_place = end_place
    if not segments or (destination is not None and not _connected(previous_place, destination)):
        return None
    complete = complete and journey.get("complete") is not False
    duration = round((current - query_at).total_seconds() / 60, 2) if complete else None
    fares = [segment["fare_won"] for segment in segments]
    return {**journey, "segments": segments, "complete": complete, "departure_at": query_at.isoformat(),
            "arrival_at": current.isoformat() if complete else None, "total_min": duration,
            "fare_won": sum(fares) if None not in fares else None,
            "transfer_count": max(0, sum(segment["mode"] not in ("walk", "wait", "connection")
                                         for segment in segments) - 1)}


def _journey_order(journey):
    walk_seconds = sum((_time(leg["arrival_at"]) - _time(leg["departure_at"])).total_seconds()
                       for leg in journey["segments"] if leg["mode"] == "walk") if journey["complete"] else 0
    return (not journey["complete"], _time(journey["arrival_at"]) or datetime.max.replace(tzinfo=KST),
            journey["transfer_count"], walk_seconds, -sum(leg.get("realtime") is True for leg in journey["segments"]))


def _join_journeys(first, onward):
    warnings = (*first.get("warnings", ()), *onward.get("warnings", ()))
    return {**onward, "segments": [*first["segments"], *onward["segments"]],
            "estimated": bool(first.get("estimated") or onward.get("estimated")),
            "warnings": list(dict.fromkeys(warnings))}


def _journey_signature(journey):
    legs = journey["segments"]
    if journey["complete"]:
        # Two providers can describe the same service with a train number or a
        # different walking connector. Keep the best complete version once.
        vehicles = [leg for leg in legs if leg["mode"] not in ("walk", "wait", "connection")]
        if len({leg["mode"] for leg in vehicles}) > 1:
            return tuple(_scheduled_train_key(leg) or (
                leg["mode"], leg.get("route_name"), leg["from_place"]["name"], leg["to_place"]["name"],
                leg["departure_at"], leg["arrival_at"], leg.get("service_name") if leg["mode"] == "train" else None,
            ) for leg in vehicles)
    return tuple((leg["mode"], leg.get("route_name"), leg.get("service_name"),
                  leg["from_place"]["lat"], leg["from_place"]["lon"], leg["to_place"]["lat"], leg["to_place"]["lon"],
                  leg["departure_at"], leg["arrival_at"]) for leg in legs)


def _terminal_key(name):
    # Same-region fingerprint from reviewed facility names. Never infer a match by substring.
    name = re.sub(r"\s+", "", name)
    return name.removesuffix("버스터미널") + "터미널" if name.endswith("버스터미널") else name


class IntercityJourneyProvider:
    """Join date-specific BusTago results using independently checked venue identity."""

    CONNECTOR_RADIUS_KM = 12
    COACH_CONNECTION_LIMIT = 3
    TERMINAL_MARGIN = timedelta(minutes=5)

    def __init__(self, intercity_service, place_search, profile_path, walking_time, regional_transit=None, *,
                 regional_bus=None, motis=None):
        self.service = intercity_service
        self.place_search = place_search
        self.profile_path = Path(profile_path)
        self.walking_time = walking_time
        self.regional_transit = regional_transit
        self.regional_bus = regional_bus
        self.motis = motis

    def _venues(self, center):
        profile = json.loads(self.profile_path.read_text(encoding="utf-8"))
        available = {}
        for row in profile["terminals"]:
            if row.get("kind") != "terminal":
                continue
            provider_id = "BUSTAGO_" + row["source_id"]
            evidence = row.get("evidence", {})
            terminal_names = (row.get("display_name", ""), row.get("source_name", ""),
                              evidence.get("pti_name", ""), *TERMINAL_ALIASES.get(provider_id, ()))
            available[provider_id] = (row["city"], {_terminal_key(name) for name in terminal_names if name},
                                      evidence.get("pti_id"))

        venues = {}
        for poi in self.place_search.nearby_terminals(center["lat"], center["lon"]):
            if _distance_km(poi, center) > 18:
                continue
            region = poi["sub"].partition(" ")[0]
            matched = [
                provider_id for provider_id, (province, names, _) in available.items()
                if region.startswith(PROVINCE_NAMES.get(province, (province,))) and _terminal_key(poi["name"]) in names
            ]
            facilities = {available[provider_id][2] for provider_id in matched}
            if len(matched) == 1 or (len(facilities) == 1 and None not in facilities):
                for provider_id in matched:
                    venues.setdefault(provider_id, {**poi, "provider_id": provider_id})
            if len(venues) >= 2:
                break
        return list(venues.values())[:2]

    def _chuncheon_terminal(self):
        matches = self.place_search.search(CHUNCHEON_TERMINAL["address"], scope="national")
        for poi in matches:
            exact_address = bool(re.search(r"경춘로\s+2341(?:\s|$)", poi.get("sub", "")))
            exact_name = _terminal_key(poi["name"]) == _terminal_key(CHUNCHEON_TERMINAL["name"])
            if poi.get("source") == "tmap" and "춘천" in poi.get("sub", "") and (exact_address or exact_name):
                if _distance_km(CHUNCHEON_CENTER, poi) <= 6:
                    return {**poi, **CHUNCHEON_TERMINAL}
        return None

    def _ground(self, start, end, when, label):
        distance = _distance_km(start, end)
        measured = _measured_walk(self.walking_time, start, end) if distance <= 8 else None
        candidates = []
        if measured is not None and measured[1] <= 9000:
            minutes, meters = measured
            arrival = when + timedelta(minutes=minutes) if when else None
            candidates.append({"segments": [_segment("walk", label, start, end, when, arrival, minutes,
                                                      distance_m=meters, source="TMAP 보행")]})
        if when is None:
            return candidates
        if 0 < distance <= self.CONNECTOR_RADIUS_KM and (not candidates or minutes > BOARDING_MARGIN_MIN):
            providers = []
            if self.regional_bus is not None:
                providers.append((self.regional_bus, (start, end, when)))
            if self.regional_transit is not None:
                providers.append((self.regional_transit.plan, (start, end, when, self.walking_time)))
            if self.motis is not None:
                providers.append((self.motis.plan, (start, end, when)))
            for provider, arguments in providers:
                try:
                    candidates.extend(provider(*arguments))
                except (OSError, ValueError, PublicDataError, MotisProviderError):
                    continue
        valid = []
        for option in candidates:
            timeline = _finalize(option, when, origin=start, destination=end)
            if timeline is not None and timeline["complete"] and _time(timeline["arrival_at"]) <= when + timedelta(hours=2):
                valid.append(timeline)
        return sorted(valid, key=_journey_order)

    @staticmethod
    def _access_order(candidate):
        # The intercity departure determines the final arrival. Among access routes that
        # catch it, each connector vehicle is an extra transfer on the whole journey.
        _, arrival, transfers, walking, realtime = _journey_order(candidate)
        has_vehicle = any(leg["mode"] not in ("walk", "wait", "connection") for leg in candidate["segments"])
        return transfers + has_vehicle, walking, arrival, realtime

    def _route(self, origin, destination, start, end, query_at):
        options = self.service.timetable(start["provider_id"], end["provider_id"], query_at.date().isoformat())
        upcoming = [(departure, ride) for ride in options["trips"]
                    if (departure := _time(ride.get("departure_at"))) is not None and departure >= query_at]
        if not upcoming:
            return []
        access = self._ground(origin, start, query_at, "승차 터미널까지")
        unknown_access = {"segments": [_segment("connection", "터미널 연결편 확인 필요", origin, start, None, None, None)]}
        unknown_egress = {"segments": [_segment("connection", "목적지 연결편 확인 필요", end, destination, None, None, None)]}
        boardable = []
        for departure, ride in upcoming:
            arrival = _time(ride.get("arrival_at"))
            if arrival is not None and departure > arrival:
                continue
            can_board = [option for option in access if _time(option["arrival_at"]) + self.TERMINAL_MARGIN <= departure]
            if access and not can_board:
                continue
            approach = min(can_board, key=self._access_order, default=unknown_access)
            boardable.append((departure, arrival, ride, approach))
        boardable.sort(key=lambda trip: (trip[1] is None, trip[1] or trip[0], trip[0]))
        candidates = []
        for departure, arrival, ride, approach in boardable[:self.COACH_CONNECTION_LIMIT]:
            trip = _segment("intercity_bus", "시외버스", start, end, departure, arrival,
                            ride.get("duration_minutes"), ride.get("fare_krw"), service_name=ride.get("grade"),
                            booking_provider="bustago", source="버스타고", estimated=ride.get("arrival_estimated") is True)
            egress = self._ground(end, destination, arrival, "터미널에서 목적지까지")
            joined = []
            for onward in (*egress, unknown_egress):
                segments = [*approach["segments"], trip, *onward["segments"]]
                warnings = [*approach.get("warnings", ()), *onward.get("warnings", ()),
                            "도로 상황에 따라 도착 시각이 달라질 수 있습니다. 좌석과 운임은 버스타고에서 확인하세요."]
                timeline = _finalize({"segments": segments, "warnings": warnings,
                                      "estimated": bool(approach.get("estimated") or onward.get("estimated")
                                                        or any(leg.get("estimated") for leg in segments))},
                                     query_at, origin=origin, destination=destination)
                if timeline is not None:
                    joined.append(timeline)
            if joined:
                candidates.append(min(joined, key=_journey_order))
        return sorted(candidates, key=_journey_order)[:2]

    def plan(self, origin, destination, query_at):
        departure_is_local = _distance_km(origin, CHUNCHEON_CENTER) < 14
        arrival_is_local = _distance_km(destination, CHUNCHEON_CENTER) < 14
        if departure_is_local == arrival_is_local:
            return [], False
        if query_at.date().isoformat() != self.service.today():
            return [], True
        remote = destination if departure_is_local else origin
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                local = executor.submit(self._chuncheon_terminal)
                regional = executor.submit(self._venues, remote)
                local, regional = local.result(), regional.result()
        except (PlaceSearchError, OSError, ValueError, KeyError):
            return [], True
        if local is None or not regional:
            return [], True
        results, gap = [], False
        for target in regional:
            board, alight = (local, target) if departure_is_local else (target, local)
            try:
                journeys = self._route(origin, destination, board, alight, query_at)
                results.extend(journeys)
                gap = gap or not journeys
            except IntercityError:
                gap = True
        return results, gap


class RegionalBusJourneyProvider:
    """Provide timed ground connections from live or dated bus data."""

    def __init__(self, repository, walking_time, local_transit=None, now=None):
        self.repository = repository
        self.walking_time = walking_time
        self.local_transit = local_transit
        self.now = now or (lambda: datetime.now(KST))

    def live_available(self, origin, destination, query_at):
        return (self.local_transit is not None
                and abs(query_at - self.now()) < LIVE_DEPARTURE_WINDOW
                and _distance_km(origin, CHUNCHEON_CENTER) < CHUNCHEON_LOCAL_RADIUS_KM
                and _distance_km(destination, CHUNCHEON_CENTER) < CHUNCHEON_LOCAL_RADIUS_KM)

    def published_available(self, origin, destination, service_date):
        return self.repository is not None and self.repository.has_published_connection(origin, destination, service_date)

    def connections(self, origin, destination, query_at):
        if self.live_available(origin, destination, query_at):
            options = self.live(origin, destination, query_at, station_access=True)
        elif self.repository is not None:
            options = self.repository.plan(origin, destination, query_at, self.walking_time,
                                           use_published_timing=True, timed_only=True)
        else:
            return []
        valid = []
        for option in options:
            result = _finalize(option, query_at, origin=origin, destination=destination)
            if result is None or not result["complete"]:
                continue
            ready = query_at + timedelta(minutes=BOARDING_MARGIN_MIN)
            for leg in result["segments"]:
                if leg["mode"] == "walk":
                    ready = _time(leg["arrival_at"]) + timedelta(minutes=BOARDING_MARGIN_MIN)
                elif leg["mode"] in ("bus", "shuttle"):
                    if _time(leg["departure_at"]) >= ready:
                        valid.append(result)
                    break
        return sorted(valid, key=_journey_order)

    def live(self, origin, destination, query_at, *, station_access=False):
        if not self.live_available(origin, destination, query_at):
            return []
        journeys = []
        try:
            if station_access:
                local = self.local_transit.plan(origin, destination, max_transfers=0, include_direct_walk=False, departure_at=query_at)
            else:
                local = self.local_transit.plan(origin, destination, include_direct_walk=False, departure_at=query_at)
        except PublicDataError:
            return journeys
        for option in local.get("journeys", []):
            legs = option.get("legs", [])
            if not legs:
                continue
            access = _quantity(option.get("access_walk", {}).get("minutes"))
            egress = _quantity(option.get("egress_walk", {}).get("minutes"))
            if access is None or egress is None:
                continue
            current = query_at + timedelta(minutes=access)
            segments = [_segment("walk", "첫 승차 지점까지", origin, legs[0]["board"], query_at, current, access)]
            transfer_margin = (_quantity(option.get("transfer_buffer_min")) or 0) / max(1, len(legs) - 1)
            for index, leg in enumerate(legs):
                is_shuttle = leg.get("transport_type") == "shuttle"
                ride = _quantity(leg.get("ride_min"))
                if is_shuttle:
                    start = _time(leg.get("scheduled_departure_at")) if leg.get("service_date_confirmed") else None
                    end = _time(leg.get("scheduled_arrival_at")) if start else None
                else:
                    eta_seconds = _quantity(leg.get("predicted_boarding_seconds"))
                    if eta_seconds is None and (estimated_minutes := _quantity(leg.get("arrival_min"))) is not None:
                        eta_seconds = estimated_minutes * 60
                    start = (query_at + timedelta(seconds=eta_seconds)
                             if leg.get("direction_verified") and leg.get("route_number_verified") is not False
                             and eta_seconds is not None else None)
                    end = start + timedelta(minutes=ride) if start and ride is not None else None
                if current is not None and start is not None:
                    margin = BOARDING_MARGIN_MIN + (transfer_margin if index else 0)
                    if start < current + timedelta(minutes=margin):
                        break
                if start is not None and current is not None and start > current:
                    segments.append(_segment("wait", "승차 대기", leg["board"], leg["board"], current, start,
                                             (start - current).total_seconds() / 60))
                segments.append(_segment("shuttle" if is_shuttle else "bus", leg.get("route_no") or "버스",
                                         leg["board"], leg["alight"], start, end, ride if start else None,
                                         path=leg.get("path", []), estimated=True, source="학교 셔틀" if is_shuttle else "TAGO",
                                         ride_time_basis=leg.get("ride_time_basis"), ride_time_source=leg.get("ride_time_source"),
                                         realtime=not is_shuttle and start is not None and end is not None))
                current = end
            else:
                total_at = current + timedelta(minutes=egress) if current else None
                segments.append(_segment("walk", "하차 지점에서 목적지까지", legs[-1]["alight"], destination,
                                         current, total_at, egress))
                journeys.append({"segments": segments, "estimated": True,
                                 "warnings": ["지역버스는 정류장 도착 예측과 공개 예정 시각 또는 구간 거리로 소요시간을 계산합니다."]})
        return journeys


class NationalJourneyService:
    def __init__(self, repository, walking_time, local_transit=None, intercity=None, bus_repository=None, now=None,
                 *, motis=None, regional_bus=None, rail_bus_transfers=None):
        self.repository = repository
        self.walking_time = walking_time
        self.intercity = intercity
        self.bus_repository = bus_repository
        self.now = now or (lambda: datetime.now(KST))
        self.motis = motis
        self.rail_bus_transfers = rail_bus_transfers
        self.regional_bus = regional_bus if regional_bus is not None else RegionalBusJourneyProvider(
            bus_repository, walking_time, local_transit, self.now,
        )

    def _walk(self, origin, destination, query_at):
        measured = _measured_walk(self.walking_time, origin, destination)
        if measured is None:
            return []
        minutes, meters = measured
        final = query_at + timedelta(minutes=minutes)
        return [{"segments": [_segment("walk", "목적지까지 도보", origin, destination, query_at, final, minutes,
                                      distance_m=meters, source="TMAP 보행")]}]

    def _motis_after_bus(self, gateway, destination, feeder):
        reached = _time(feeder["arrival_at"])
        ready = reached + timedelta(minutes=BOARDING_MARGIN_MIN)
        train_ready = reached + timedelta(seconds=BOARDING_SECONDS["train"])

        def rail_boarding(option):
            for leg in option["segments"]:
                if _mode(leg) in RAIL_MODES:
                    return _mode(leg), _time(leg.get("departure_at"))
            return None, None

        try:
            options = self.motis.plan(gateway, destination, ready)
        except MotisProviderError:
            return []
        valid = []
        missed_train = False
        for option in options:
            mode, departure = rail_boarding(option)
            if departure is None:
                continue
            if mode == "train" and departure < train_ready:
                missed_train = True
            else:
                valid.append(option)
        if missed_train:
            try:
                for option in self.motis.plan(gateway, destination, train_ready):
                    _, departure = rail_boarding(option)
                    if departure is not None and departure >= train_ready:
                        valid.append(option)
            except MotisProviderError:
                pass
        return [_join_journeys(feeder, option) for option in valid]

    def _rail_to(self, origin, destination, query_at, access_routes, *, native_direct=False):
        feeders = [route for route in access_routes if not _connected(route["gateway"], destination)]
        arguments = (origin, destination, query_at, self.walking_time)
        options = self.repository.plan(*arguments, access_routes=feeders) if feeders else self.repository.plan(*arguments)
        if self.motis is None:
            return options
        if native_direct:
            try:
                options = options + self.motis.plan(origin, destination, query_at)
            except MotisProviderError:
                pass
        by_gateway = {}
        for feeder in feeders:
            by_gateway.setdefault(feeder["gateway"]["name"], feeder)
        return options + [route for feeder in by_gateway.values()
                          for route in self._motis_after_bus(feeder["gateway"], destination, feeder)]

    def _rail_with_access(self, origin, destination, query_at):
        bus = self.regional_bus
        local_origin = _distance_km(origin, CHUNCHEON_CENTER) < CHUNCHEON_LOCAL_RADIUS_KM
        local_destination = _distance_km(destination, CHUNCHEON_CENTER) < CHUNCHEON_LOCAL_RADIUS_KM
        live_access = local_origin and _distance_km(origin, CHUNCHEON_CENTER) < CHUNCHEON_RAIL_ACCESS_RADIUS_KM
        live_access = live_access and not local_destination and bus.live_available(origin, origin, query_at)
        scheduled = bus.repository is not None and (local_origin or local_destination)
        remote_transfer = self.rail_bus_transfers if not local_destination else None
        if not live_access and not scheduled and remote_transfer is None:
            return self._rail_to(origin, destination, query_at, [])

        gateways = self.repository.chuncheon_gateways() if live_access or scheduled else []
        service_date = query_at.astimezone(KST).date()

        def station_access(gateway):
            if _connected(origin, gateway) or not (live_access or bus.published_available(origin, gateway, service_date)):
                return []
            try:
                return [{**option, "gateway": gateway, "arrival_at": _time(option["arrival_at"])}
                        for option in bus.connections(origin, gateway, query_at)]
            except (OSError, ValueError, PublicDataError):
                return []

        def station_egress(gateway, access_routes):
            if (_connected(origin, gateway) or _connected(gateway, destination)
                    or not any(bus.published_available(gateway, destination, service_date + timedelta(days=offset))
                               for offset in range(3))):
                return []
            options = self._rail_to(origin, gateway, query_at, access_routes, native_direct=True)
            arriving = []
            for option in options:
                timeline = _finalize(option, query_at, origin=origin, destination=gateway)
                if timeline and timeline["complete"] and any(leg["mode"] in RAIL_MODES for leg in timeline["segments"]):
                    arriving.append(timeline)
            connections = []
            checked = set()
            for route in sorted(arriving, key=_journey_order):
                ready = _time(route["arrival_at"])
                if ready in checked:
                    continue
                if len(checked) == 3:
                    break
                checked.add(ready)
                try:
                    onward = bus.connections(gateway, destination, ready)
                except (OSError, ValueError, PublicDataError):
                    continue
                connections.extend(_join_journeys(route, option) for option in onward)
            return connections

        with ThreadPoolExecutor(max_workers=3) as executor:
            access_routes = [route for routes in executor.map(station_access, gateways) for route in routes]
            stations = self.repository.transfer_stations(destination) if remote_transfer else []
            transfer_job = executor.submit(remote_transfer.links, stations, destination, query_at) if stations else None
            arrival_jobs = [executor.submit(station_egress, gateway, access_routes) for gateway in gateways] if local_destination else []
            options = self._rail_to(origin, destination, query_at, access_routes)
            for future in arrival_jobs:
                try:
                    options += future.result()
                except (OSError, ValueError, PublicDataError):
                    continue
            if transfer_job is not None:
                try:
                    links = transfer_job.result()
                    targets = [station for station in stations if (station["lat"], station["lon"]) in links]
                    if targets:
                        reference = [_time(route.get("arrival_at")) for route in options]
                        earliest = min((arrival for arrival in reference if arrival is not None), default=None)
                        deadline = earliest + timedelta(minutes=30) if earliest is not None else None
                        arrivals = self.repository.plan_to_stations(origin, destination, query_at, self.walking_time,
                                                                    targets, access_routes=access_routes,
                                                                    latest_arrival=deadline)
                        options += [_join_journeys(rail, onward) for rail, onward
                                    in remote_transfer.connections(links, arrivals, destination, query_at)]
                except (OSError, ValueError, PublicDataError):
                    pass
        return options

    def plan(self, origin, destination, query_at):
        jobs = []
        if self.motis is not None:
            jobs.append((self.motis.plan, (origin, destination, query_at)))
        jobs.append((self._rail_with_access, (origin, destination, query_at)))
        if self.bus_repository is not None:
            bus_plan = self.bus_repository.plan
            if abs(query_at - self.now()) >= LIVE_DEPARTURE_WINDOW:
                bus_plan = partial(bus_plan, use_published_timing=True)
            jobs.append((bus_plan, (origin, destination, query_at, self.walking_time)))
        if self.intercity is not None:
            jobs.append((self.intercity.plan, (origin, destination, query_at)))
        if _distance_km(origin, destination) <= 6:
            jobs.append((self._walk, (origin, destination, query_at)))
        if (_distance_km(origin, CHUNCHEON_CENTER) < CHUNCHEON_LOCAL_RADIUS_KM
                and _distance_km(destination, CHUNCHEON_CENTER) < CHUNCHEON_LOCAL_RADIUS_KM):
            jobs.append((self.regional_bus.live, (origin, destination, query_at)))
        raw, warnings, source_unavailable = [], [], False
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [(function, executor.submit(function, *args)) for function, args in jobs]
            for function, future in futures:
                try:
                    result = future.result()
                    if self.intercity is not None and function == self.intercity.plan:
                        result, missing = result
                        if missing:
                            warnings.append("이 날짜와 구간에서 연결되는 시외버스 시간표나 승차 위치를 일부 확인하지 못했습니다.")
                    raw.extend(result)
                except MotisProviderError:
                    source_unavailable = True
                    warnings.append("MOTIS 경로를 확인하지 못했습니다. 다른 운행 자료로 확인한 후보를 표시합니다.")
                except (OSError, ValueError, PublicDataError):
                    source_unavailable = True
        options = []
        for option in raw:
            journey = _finalize(option, query_at, origin=origin, destination=destination)
            if journey is not None:
                options.append(journey)
        adjusted_trains = set()
        for journey in options:
            for leg in journey["segments"]:
                if leg.get("source") == "MOTIS" and leg.get("realtime") is True:
                    scheduled = _scheduled_train_key(leg, original=True)
                    if scheduled is not None and scheduled != _scheduled_train_key(leg):
                        adjusted_trains.add(scheduled)
        if adjusted_trains:
            options = [journey for journey in options if not any(
                leg.get("realtime") is not True and _scheduled_train_key(leg) in adjusted_trains
                for leg in journey["segments"])]
        seen = set()
        distinct = []
        for journey in sorted(options, key=_journey_order):
            signature = _journey_signature(journey)
            if signature not in seen:
                seen.add(signature)
                distinct.append(journey)
        options = distinct
        if source_unavailable:
            warnings.append("연결된 교통 데이터 일부를 불러오지 못했습니다.")
        if not options or not options[0]["complete"]:
            warnings.append("이 출발 시각에 끝까지 이어지는 경로의 모든 시간표와 환승 시간을 확인하지 못했습니다.")
        if options and _distance_km(origin, destination) > 8:
            warnings.append("확인 가능한 운행표 기준입니다. 배차 변경, 운임, 잔여 좌석은 운송사에서 확인할 수 있습니다.")
        status = ("ok" if options[0]["complete"] else "partial") if options else (
            "unavailable" if source_unavailable else "partial" if self.intercity is not None and len(warnings) > 1 else "empty"
        )
        return {"mode": "national_journeys", "status": status, "origin": origin, "destination": destination,
                "departure_at": query_at.isoformat(), "journeys": options[:5], "warnings": warnings}
