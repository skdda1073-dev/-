"""Join supported timetable providers and measured walking into a journey timeline."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
from math import isfinite, sqrt
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from services.intercity_client import IntercityError
from services.mobility_validation import MobilityInputError, clean_text, coordinate
from services.motis_journey_provider import MotisProviderError
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
    return {"city_bus": "bus", "rail": "train", "metro": "subway", "coach": "intercity_bus"}.get(
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


def _scheduled_train_key(leg, *, original=False):
    if leg["mode"] != "train" or leg.get("booking_provider") not in {"korail", "srt"}:
        return None
    service = (leg.get("service_name") or "").upper().rsplit(maxsplit=1)
    if len(service) != 2 or not re.fullmatch(r"[A-Z]?[0-9]+", service[1]):
        return None
    prefix = "scheduled_" if original else ""
    start, end = (_time(leg.get(prefix + field)) for field in ("departure_at", "arrival_at"))
    if start is None or end is None:
        return None
    stations = tuple(re.sub(r"\s+", "", leg[field]["name"]).removesuffix("역")
                     for field in ("from_place", "to_place"))
    # MOTIS exposes minute-level rail times; the published timetable can contain seconds.
    times = tuple(value.replace(second=0, microsecond=0) for value in (start, end))
    return (leg["booking_provider"], service[0], service[1].lstrip("0"), stations, times)


def _finalize(journey, query_at):
    current = query_at
    complete = True
    segments = []
    for given in journey.get("segments", []):
        segment = {**given, "mode": _mode(given)}
        start, end = _time(segment.get("departure_at")), _time(segment.get("arrival_at"))
        if (start and start < current) or (start and end and end < start) or (end and end < current):
            return None
        if complete and start is not None and start > current:
            stop = segment["from_place"]
            segments.append(_segment("wait", "승차 대기" if segments else "출발까지 대기", stop, stop, current.isoformat(),
                                     start.isoformat(), round((start - current).total_seconds() / 60, 2), 0))
        if start is None or end is None:
            complete = False
        if end is not None:
            current = end
        segment.update(departure_at=start.isoformat() if start else None, arrival_at=end.isoformat() if end else None,
                       duration_min=_quantity(segment.get("duration_min")), fare_won=_quantity(segment.get("fare_won")))
        if segment["mode"] in ("walk", "wait"):
            segment["fare_won"] = 0
        segment.pop("booking", None)
        booking = _booking(segment)
        if booking:
            segment["booking"] = booking
        segments.append(segment)
    if not segments:
        return None
    complete = complete and journey.get("complete") is not False
    duration = round((current - query_at).total_seconds() / 60, 2) if complete else None
    fares = [segment["fare_won"] for segment in segments]
    return {**journey, "segments": segments, "complete": complete, "departure_at": query_at.isoformat(),
            "arrival_at": current.isoformat() if complete else None, "total_min": duration,
            "fare_won": sum(fares) if None not in fares else None,
            "transfer_count": max(0, sum(segment["mode"] not in ("walk", "wait", "connection")
                                         for segment in segments) - 1)}


def _terminal_key(name):
    # Same-region fingerprint from reviewed facility names. Never infer a match by substring.
    name = re.sub(r"\s+", "", name)
    return name.removesuffix("버스터미널") + "터미널" if name.endswith("버스터미널") else name


class IntercityJourneyProvider:
    """Join date-specific BusTago results using independently checked venue identity."""

    def __init__(self, intercity_service, place_search, profile_path, walking_time, regional_transit=None):
        self.service = intercity_service
        self.place_search = place_search
        self.profile_path = Path(profile_path)
        self.walking_time = walking_time
        self.regional_transit = regional_transit

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

    def _ground(self, start, end, when, label, regional):
        distance = _distance_km(start, end)
        measured = _measured_walk(self.walking_time, start, end) if distance <= 8 else None
        direct = None
        if measured is not None and measured[1] <= 9000:
            minutes, meters = measured
            arrival = when + timedelta(minutes=minutes) if when else None
            direct = [_segment("walk", label, start, end, when, arrival, minutes,
                               distance_m=meters, source="TMAP 보행")]
        if regional and when and self.regional_transit is not None and 2 <= distance <= 12 and (
            direct is None or direct[0]["duration_min"] > 25
        ):
            try:
                options = self.regional_transit.plan(start, end, when, self.walking_time)
            except (OSError, ValueError, PublicDataError):
                return direct
            candidates = []
            if direct:
                candidates.append((_time(direct[-1]["arrival_at"]), direct))
            for option in options:
                arrival = _time(option.get("arrival_at"))
                if arrival and option.get("segments") and timedelta(0) <= arrival - when <= timedelta(hours=2):
                    candidates.append((arrival, option["segments"]))
            if candidates:
                return min(candidates, key=lambda pair: pair[0])[1]
        return direct

    def _route(self, origin, destination, start, end, query_at):
        options = self.service.timetable(start["provider_id"], end["provider_id"], query_at.date().isoformat())
        upcoming = [(departure, ride) for ride in options["trips"]
                    if (departure := _time(ride.get("departure_at"))) is not None and departure >= query_at]
        if not upcoming:
            return []
        access = self._ground(origin, start, query_at, "승차 터미널까지", start["provider_id"] != "BUSTAGO_2037")
        access_end = _time(access[-1]["arrival_at"]) if access else None
        ready = access_end + timedelta(minutes=5) if access_end else query_at
        approach = access or [_segment("connection", "터미널 연결편 확인 필요", origin, start, None, None, None)]
        boardable = []
        for departure, ride in upcoming:
            arrival = _time(ride.get("arrival_at"))
            if departure < ready or (arrival is not None and departure > arrival):
                continue
            boardable.append((departure, arrival, ride))
        boardable.sort(key=lambda trip: (trip[1] is None, trip[1] or trip[0], trip[0]))
        candidates = []
        for departure, arrival, ride in boardable[:3]:
            egress = self._ground(end, destination, arrival, "터미널에서 목적지까지",
                                  end["provider_id"] != "BUSTAGO_2037")
            waiting = ([_segment("wait", "승차 대기", start, start, access_end, departure,
                                 (departure - access_end).total_seconds() / 60)] if access_end else [])
            onward = egress or [_segment("connection", "목적지 연결편 확인 필요", end, destination, None, None, None)]
            segments = [
                *approach,
                *waiting,
                _segment("intercity_bus", "시외버스", start, end, departure, arrival,
                         ride.get("duration_minutes"), ride.get("fare_krw"),
                         service_name=ride.get("grade"), booking_provider="bustago", source="버스타고",
                         estimated=ride.get("arrival_estimated") is True),
                *onward,
            ]
            candidates.append({
                "segments": segments, "estimated": ride.get("arrival_estimated") is True,
                "warnings": ["도로 상황에 따라 도착 시각이 달라질 수 있습니다. 좌석과 운임은 버스타고에서 확인하세요."],
            })
        return sorted(candidates, key=lambda journey: _time(journey["segments"][-1]["arrival_at"])
                      or datetime.max.replace(tzinfo=KST))[:2]

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


class NationalJourneyService:
    def __init__(self, repository, walking_time, local_transit=None, intercity=None, bus_repository=None, now=None, *, motis=None):
        self.repository = repository
        self.walking_time = walking_time
        self.local_transit = local_transit
        self.intercity = intercity
        self.bus_repository = bus_repository
        self.now = now or (lambda: datetime.now(KST))
        self.motis = motis

    def _walk(self, origin, destination, query_at):
        measured = _measured_walk(self.walking_time, origin, destination)
        if measured is None:
            return []
        minutes, meters = measured
        final = query_at + timedelta(minutes=minutes)
        return [{"segments": [_segment("walk", "목적지까지 도보", origin, destination, query_at, final, minutes,
                                      distance_m=meters, source="TMAP 보행")]}]

    def _live_local(self, origin, destination, query_at, *, station_access=False):
        if self.local_transit is None or not (
            _distance_km(origin, CHUNCHEON_CENTER) < CHUNCHEON_LOCAL_RADIUS_KM
            and _distance_km(destination, CHUNCHEON_CENTER) < CHUNCHEON_LOCAL_RADIUS_KM
            and abs(query_at - self.now()) < LIVE_DEPARTURE_WINDOW
        ):
            return []
        journeys = []
        try:
            if station_access:
                local = self.local_transit.plan(origin, destination, max_transfers=0, include_direct_walk=False)
            else:
                local = self.local_transit.plan(origin, destination)
        except PublicDataError:
            return journeys
        snapshot_at = self.now()
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
                eta, ride = _quantity(leg.get("arrival_min")), _quantity(leg.get("ride_min"))
                if is_shuttle:
                    start = _time(leg.get("scheduled_departure_at")) if leg.get("service_date_confirmed") else None
                    end = _time(leg.get("scheduled_arrival_at")) if start else None
                else:
                    start = (snapshot_at + timedelta(minutes=eta)
                             if leg.get("direction_verified") and leg.get("route_number_verified") is not False
                             and eta is not None else None)
                    end = start + timedelta(minutes=ride) if start and ride is not None else None
                if current is not None and start is not None:
                    margin = BOARDING_MARGIN_MIN + (transfer_margin if index else 0)
                    if start < current + timedelta(minutes=margin):
                        start, end = None, None
                if start is not None and current is not None and start > current:
                    segments.append(_segment("wait", "승차 대기", leg["board"], leg["board"], current, start,
                                             (start - current).total_seconds() / 60))
                segments.append(_segment("shuttle" if is_shuttle else "bus", leg.get("route_no") or "버스",
                                         leg["board"], leg["alight"], start, end, ride if start else None,
                                         path=leg.get("path", []), estimated=True, source="학교 셔틀" if is_shuttle else "TAGO",
                                         realtime=not is_shuttle and start is not None and end is not None))
                current = end
            total_at = current + timedelta(minutes=egress) if current else None
            segments.append(_segment("walk", "하차 지점에서 목적지까지", legs[-1]["alight"], destination,
                                     current, total_at, egress))
            journeys.append({"segments": segments, "estimated": True,
                             "warnings": ["지역버스 소요시간은 현재 정류장 도착 정보와 노선 거리 기준 예상치입니다."]})
        return journeys

    def _rail_with_access(self, origin, destination, query_at):
        arguments = (origin, destination, query_at, self.walking_time)
        if (self.local_transit is None or abs(query_at - self.now()) >= LIVE_DEPARTURE_WINDOW
                or _distance_km(origin, CHUNCHEON_CENTER) >= CHUNCHEON_RAIL_ACCESS_RADIUS_KM
                or _distance_km(destination, CHUNCHEON_CENTER) < CHUNCHEON_LOCAL_RADIUS_KM):
            return self.repository.plan(*arguments)

        def station_access(gateway):
            try:
                # Scheduled connections need a known departure for each feeder leg.
                # TAGO only verifies the first bus in each candidate.
                options = self._live_local(origin, gateway, query_at, station_access=True)
            except (OSError, ValueError):
                return [], []
            routes = []
            for option in options:
                timeline = _finalize(option, query_at)
                if timeline is not None and timeline["complete"]:
                    routes.append({**option, "gateway": gateway, "arrival_at": _time(timeline["arrival_at"])})
            if not routes or self.motis is None:
                return routes, []
            fastest = min(routes, key=lambda route: route["arrival_at"])
            ready = fastest["arrival_at"] + timedelta(minutes=BOARDING_MARGIN_MIN)
            try:
                onward = self.motis.plan(gateway, destination, ready)
            except MotisProviderError:
                return routes, []
            combined = [{**ride, "segments": fastest["segments"] + ride["segments"], "estimated": True,
                         "warnings": fastest["warnings"] + ride.get("warnings", [])} for ride in onward]
            return routes, combined

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(station_access, self.repository.chuncheon_gateways()))
        access_routes = [route for routes, _ in results for route in routes]
        if access_routes:
            combined = [route for _, routes in results for route in routes]
            return combined + self.repository.plan(*arguments, access_routes=access_routes)
        return self.repository.plan(*arguments)

    def plan(self, origin, destination, query_at):
        jobs = []
        if self.motis is not None:
            jobs.append((self.motis.plan, (origin, destination, query_at)))
        jobs.append((self._rail_with_access, (origin, destination, query_at)))
        if self.bus_repository is not None:
            jobs.append((self.bus_repository.plan, (origin, destination, query_at, self.walking_time)))
        if self.intercity is not None:
            jobs.append((self.intercity.plan, (origin, destination, query_at)))
        if _distance_km(origin, destination) <= 6:
            jobs += [(self._walk, (origin, destination, query_at)), (self._live_local, (origin, destination, query_at))]
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
        seen = set()
        for option in raw:
            journey = _finalize(option, query_at)
            if journey is None:
                continue
            signature = tuple((leg["mode"], leg.get("route_name"), leg.get("service_name"),
                               leg["from_place"].get("name"), leg["to_place"].get("name"),
                               leg["departure_at"], leg["arrival_at"]) for leg in journey["segments"])
            if signature not in seen:
                seen.add(signature)
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
        options.sort(key=lambda value: (not value["complete"], _time(value["arrival_at"]) or datetime.max.replace(tzinfo=KST),
                                        value["transfer_count"], -sum(leg.get("realtime") is True for leg in value["segments"])))
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
