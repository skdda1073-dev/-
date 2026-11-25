"""Pure, bounded bus routing from complete public-data stop sequences.

The caller supplies complete/paginated TAGO routes and an arrival snapshot.
Only strictly ordered stops on the same route ID and direction are connected.
Transfers use the exact same stop ID and a different route ID AND bus number.
An explicitly marked CSV or official API order may supply a forward sequence without direction.
Such results are unverified candidates, never confirmed through services or
total ETAs. No backwards wrap or nearby-stop transfer is inferred.

Ride estimates use published timing data for an exact directed stop pattern
when available. The fallback sums stop-to-stop distance, uses a 1.2 road factor
at 20 km/h and allows 0.4 minutes per intermediate stop. Neither is a timetable.
``arrival_min`` is the ETA from the arrival snapshot. Missing or missed arrivals
leave waiting and total time unknown. ``known_min`` is a lower bound using
known walks, estimated riding, transfer buffers and any usable observed waits.

Walking must return a successfully routed (minutes, distance_metres) pair.
Failure is never replaced by zero or a straight-line walking estimate.
Straight-line walking distances are used only to bound candidate search.
The arrivals Mapping may load data lazily in ``get(stop_id, [])``.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
import heapq
import math


MAX_STRUCTURAL_CANDIDATES = 300
MAX_WALKING_CANDIDATES = 80
BOARDING_MARGIN_MIN = 1.0
TRANSFER_BUFFER_MIN = 2.0


@dataclass(frozen=True)
class _Stop:
    id: str
    name: str
    lat: float
    lon: float
    order: int
    direction: str

    def public(self):
        return {"id": self.id, "name": self.name, "lat": self.lat, "lon": self.lon}


@dataclass(frozen=True)
class _Leg:
    route_id: str
    route_no: str
    direction: str
    stops: tuple
    ride_min: float
    ride_time_basis: str
    ride_time_source: str
    ambiguous_board: bool = False
    sequence_basis: str = "api_direction"
    direction_verified: bool = True
    route_number_verified: bool = True

    @property
    def board(self):
        return self.stops[0]

    @property
    def alight(self):
        return self.stops[-1]

    def key(self):
        return self.route_id, self.board.id, self.alight.id, self.board.direction


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _coordinates(lat, lon):
    lat, lon = _number(lat), _number(lon)
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None
    return lat, lon


def _distance(a, b):
    lat1, lon1 = a
    lat2, lon2 = b
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = (math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 6371000 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, h))))


def _nearby_ids(stops):
    return {str(stop.get("nodeid", "")).strip()
            for stop in stops if isinstance(stop, dict)
            and str(stop.get("nodeid", "")).strip()
            and _coordinates(stop.get("gpslati"), stop.get("gpslong")) is not None}


def _segments(routes):
    """Never bridge a missing ordinal, direction boundary or invalid point."""
    result = []
    for route_id, info in routes.items():
        route_id = str(route_id).strip()
        if not route_id or not isinstance(info, dict):
            continue
        route_no = str(info.get("route_no", "")).strip()
        sequence_basis = info.get("sequence_basis", "api_direction")
        csv_ordered = sequence_basis == "csv_order"
        order_only_allowed = sequence_basis in ("csv_order", "api_order")
        raw_stops = info.get("stops", [])
        if not route_no or not isinstance(raw_stops, (list, tuple)):
            continue
        ordered = []
        for raw in raw_stops:
            if not isinstance(raw, dict):
                ordered = []
                break
            order = _number(raw.get("nodeord"))
            if order is None or order < 1 or not order.is_integer():
                ordered = []
                break
            ordered.append((int(order), raw))
        if len({order for order, _ in ordered}) != len(ordered):
            # 일부 API는 상·하행마다 순번을 다시 시작한다. 같은 방향의 중복은 거절한다.
            direction_orders = [(str(raw.get("updowncd", "")).strip(), order) for order, raw in ordered]
            if (csv_ordered or any(direction not in ("0", "1") for direction, _ in direction_orders)
                    or len(set(direction_orders)) != len(ordered)):
                continue
            ordered.sort(key=lambda item: (str(item[1]["updowncd"]), item[0]))
        else:
            ordered.sort(key=lambda item: item[0])
        segment, route_segments = [], []
        for order, raw in ordered:
            point = _coordinates(raw.get("gpslati"), raw.get("gpslong"))
            node_id = str(raw.get("nodeid", "")).strip()
            direction = "unknown" if csv_ordered else str(raw.get("updowncd", "")).strip()
            if order_only_allowed and direction not in ("0", "1"):
                direction = "unknown"
            if (not point or not node_id or raw.get("topology_break") or raw.get("stop_conflict")
                    or (not order_only_allowed and direction not in ("0", "1"))):
                if len(segment) >= 2:
                    route_segments.append(tuple(segment))
                segment = []
                continue
            stop = _Stop(node_id, str(raw.get("nodenm") or node_id), *point, order, direction)
            if segment and (segment[-1].direction != direction or segment[-1].order + 1 != order):
                if len(segment) >= 2:
                    route_segments.append(tuple(segment))
                segment = []
            segment.append(stop)
        if len(segment) >= 2:
            route_segments.append(tuple(segment))
        counts = Counter(stop.id for segment in route_segments for stop in segment)
        for segment in route_segments:
            result.append((route_id, route_no, segment, counts, info))
    return result


def _leg(route_id, route_no, segment, counts, info, start, end, ride_estimates):
    stops = segment[start:end + 1]
    sequence_basis = info.get("sequence_basis", "api_direction")
    direction_verified = (sequence_basis != "csv_order" and stops[0].direction in ("0", "1")
                          and info.get("direction_verified", True) is not False)
    route_number_verified = info.get("route_number_verified", True) is not False
    estimate = ride_estimates.get((route_id, stops[0].direction, tuple(stop.id for stop in stops))) if (
        direction_verified and route_number_verified and ride_estimates) else None
    minutes, source = estimate if isinstance(estimate, (tuple, list)) and len(estimate) == 2 else (None, None)
    minutes = _number(minutes)
    if minutes is not None and minutes > 0 and isinstance(source, str) and source.strip():
        basis = "published_segment_estimate"
    else:
        metres = sum(_distance((left.lat, left.lon), (right.lat, right.lon))
                     for left, right in zip(stops, stops[1:]))
        minutes = max(1.0, metres * 1.2 / 1000 / 20 * 60 + (len(stops) - 2) * 0.4)
        basis, source = "geometric_estimate", "경유 정류장 거리·평균속도"
    direction = f"다음 정류장 {stops[1].name} 방면"
    return _Leg(route_id, route_no, direction, stops, round(minutes, 2), basis, source, counts[stops[0].id] > 1,
                sequence_basis, direction_verified, route_number_verified)


def _structural_candidates(origin, destination, origin_ids, destination_ids, routes, transfers, timed_direct,
                           ride_estimates):
    segments = _segments(routes)
    onward = defaultdict(list)
    if transfers:
        for route_id, route_no, segment, counts, info in segments:
            for end, stop in enumerate(segment):
                if stop.id in destination_ids:
                    for start in range(end):
                        if segment[start].id != stop.id:
                            onward[segment[start].id].append(
                                _leg(route_id, route_no, segment, counts, info, start, end, ride_estimates))
    references, timed = {}, {}
    for route_id, route_no, segment, counts, info in segments:
        for start, stop in enumerate(segment):
            if stop.id not in origin_ids:
                continue
            for end in range(start + 1, len(segment)):
                target = segment[end]
                if target.id == stop.id:
                    continue
                if target.id not in destination_ids and not (transfers and target.id in onward):
                    continue
                first = _leg(route_id, route_no, segment, counts, info, start, end, ride_estimates)
                options = [(first,)] if first.alight.id in destination_ids else []
                if transfers:
                    for second in onward.get(first.alight.id, []):
                        if (first.route_id != second.route_id and first.route_no != second.route_no
                                and first.board.id != second.alight.id):
                            options.append((first, second))
                for legs in options:
                    buffer = TRANSFER_BUFFER_MIN * (len(legs) - 1)
                    heuristic = (sum(leg.ride_min for leg in legs) + buffer +
                                 _distance(origin, (legs[0].board.lat, legs[0].board.lon)) / 75 +
                                 _distance((legs[-1].alight.lat, legs[-1].alight.lon), destination) / 75)
                    key = tuple(leg.key() for leg in legs)
                    best = timed if len(legs) == 1 and timed_direct(first) else references
                    if key not in best or heuristic < best[key][0]:
                        best[key] = (heuristic, legs)
                    if len(references) > MAX_STRUCTURAL_CANDIDATES * 2:
                        keep = heapq.nsmallest(MAX_STRUCTURAL_CANDIDATES, references,
                                              key=lambda choice: references[choice][0])
                        references = {choice: references[choice] for choice in keep}
    return [*timed.values(), *heapq.nsmallest(MAX_WALKING_CANDIDATES, references.values(), key=lambda item: item[0])]


def _arrival_for(leg, ready_min, records):
    if ready_min is None or leg.ambiguous_board:
        return None
    usable = []
    for raw in records:
        if not isinstance(raw, dict) or str(raw.get("routeid", "")).strip() != leg.route_id:
            continue
        if raw.get("routeno") is not None and str(raw["routeno"]).strip() != leg.route_no:
            continue
        direction = raw.get("updowncd")
        if leg.direction_verified and direction is not None and str(direction).strip() != leg.board.direction:
            continue
        seconds = _number(raw.get("arrtime"))
        # 방향이 없는 정류장 순서는 상·하행/방문 차례를 확정할 수 없어 참고값만 준다.
        threshold = ready_min + BOARDING_MARGIN_MIN if leg.direction_verified else 0
        if seconds is not None and seconds / 60 >= threshold:
            usable.append(seconds / 60)
    return min(usable) if usable else None


def plan_journeys(origin, destination, origin_stops, destination_stops, routes,
                  arrivals, walking_time, max_transfers=1, use_transfer_arrivals=True,
                  limit=3, *, ride_estimates=None):
    """Return bounded candidates, preserving unknown waits as ``None``.

    Points are {name, lat, lon}; stops use TAGO keys; routes is
    {route_id: {route_no, end_node, stops}}. All observed ETAs must share one
    snapshot time. The first boarding requires walking plus a one-minute
    margin. A transfer adds a two-minute buffer and another boarding margin.
    """
    if not isinstance(origin, dict) or not isinstance(destination, dict):
        return []
    origin_point = _coordinates(origin.get("lat"), origin.get("lon"))
    destination_point = _coordinates(destination.get("lat"), destination.get("lon"))
    if not origin_point or not destination_point or not isinstance(routes, dict):
        return []
    walk_cache, arrival_cache = {}, {}

    def routed_walk(start, start_name, end, end_name):
        key = (start, end)
        if key not in walk_cache:
            try:
                response = walking_time(*start, start_name, *end, end_name)
                minutes, metres = _number(response[0]), _number(response[1])
                walk_cache[key] = ({"minutes": minutes, "distance_m": round(metres, 1)}
                                   if minutes is not None and metres is not None
                                   and minutes >= 0 and metres >= 0 else None)
            except Exception:
                walk_cache[key] = None
        return walk_cache[key]

    def live_records(stop_id):
        if stop_id not in arrival_cache:
            try:
                records = arrivals.get(stop_id, []) or []
                arrival_cache[stop_id] = records if isinstance(records, (list, tuple)) else []
            except Exception:
                arrival_cache[stop_id] = []
        return arrival_cache[stop_id]

    def timed_direct(leg):
        # A usable departure deserves a full walking check regardless of a geometric reference rank.
        return (leg.direction_verified and leg.route_number_verified
                and _arrival_for(leg, 0, live_records(leg.board.id)) is not None)

    candidates = _structural_candidates(origin_point, destination_point, _nearby_ids(origin_stops),
                                        _nearby_ids(destination_stops), routes, max_transfers >= 1, timed_direct,
                                        ride_estimates)
    journeys = []
    for _, legs in candidates:
        access = routed_walk(origin_point, str(origin.get("name") or "출발지"),
                             (legs[0].board.lat, legs[0].board.lon), legs[0].board.name)
        egress = routed_walk((legs[-1].alight.lat, legs[-1].alight.lon), legs[-1].alight.name,
                             destination_point, str(destination.get("name") or "목적지"))
        if access is None or egress is None:
            continue
        walk_min = access["minutes"] + egress["minutes"]
        buffer = TRANSFER_BUFFER_MIN * (len(legs) - 1)
        known_min = walk_min + buffer + sum(leg.ride_min for leg in legs)
        elapsed, wait_sum = access["minutes"], 0.0
        waits_known, has_live_departure = True, False
        public_legs = []
        warnings = []
        if any(leg.ride_time_basis == "geometric_estimate" for leg in legs):
            warnings.append("버스 탑승 시간은 정류장 순서와 구간 거리로 추정하며 실제 교통 상황과 다를 수 있습니다.")
        if any(leg.ride_time_basis == "published_segment_estimate" for leg in legs):
            warnings.append("해당 버스 구간의 탑승 시간은 공개 정류장 시각 자료를 기준으로 추정했습니다.")
        for index, leg in enumerate(legs):
            if index and elapsed is not None:
                elapsed += TRANSFER_BUFFER_MIN
            records = live_records(leg.board.id) if (elapsed is not None and not leg.ambiguous_board
                       and (index == 0 or use_transfer_arrivals)) else []
            arrival_min = _arrival_for(leg, elapsed, records)
            wait_min = (None if arrival_min is None or not leg.direction_verified
                        else round(arrival_min - elapsed, 2))
            if wait_min is None:
                waits_known, elapsed = False, None
            else:
                known_min += wait_min
                wait_sum += wait_min
                elapsed = arrival_min + leg.ride_min
                if index == 0:
                    has_live_departure = True
            public_legs.append({
                "route_id": leg.route_id, "route_no": leg.route_no, "direction": leg.direction,
                "next_stop": leg.stops[1].public(),
                "sequence_basis": leg.sequence_basis, "direction_verified": leg.direction_verified,
                "route_number_verified": leg.route_number_verified,
                "arrival_reference_only": not leg.direction_verified,
                "board": leg.board.public(), "alight": leg.alight.public(),
                "stop_count": len(leg.stops) - 1, "ride_min": leg.ride_min,
                "ride_time_basis": leg.ride_time_basis, "ride_time_source": leg.ride_time_source,
                "arrival_min": None if arrival_min is None else round(arrival_min, 2),
                "predicted_boarding_seconds": None if arrival_min is None else arrival_min * 60,
                "wait_min": wait_min,
                "path": [{"lat": stop.lat, "lon": stop.lon} for stop in leg.stops],
            })
        direction_unverified = any(not leg.direction_verified for leg in legs)
        if direction_unverified:
            warnings.append("경유 정류장 순서로 찾은 후보입니다. 상·하행 또는 종점·회차 구간의 연속 탑승 여부가 미확인이라 총 소요 시간은 확정하지 않습니다.")
        if any(not leg.route_number_verified for leg in legs):
            warnings.append("원본 노선번호에 손상 또는 혼재가 있어 번호를 추측하지 않았습니다. 노선 ID로 확인하세요.")
        if not waits_known and not direction_unverified:
            warnings.append("이동 후 탈 수 있는 실시간 도착 정보가 없어 대기 시간과 총 소요 시간을 알 수 없습니다.")
        if any(leg.ambiguous_board for leg in legs):
            warnings.append("같은 정류장을 반복 경유하는 노선은 승차 차례를 구분할 수 없어 실시간 대기를 계산하지 않았습니다.")
        if len(legs) > 1:
            warnings.append("환승 버스 도착 예측은 첫 버스의 실제 운행에 따라 달라지며 환승 성공을 보장하지 않습니다.")
        journeys.append({
            "transfer_count": len(legs) - 1,
            "total_min": round(elapsed + egress["minutes"], 2) if waits_known else None,
            "total_unavailable_reason": ("direction_unverified" if direction_unverified else
                                         "wait_unknown" if not waits_known else None),
            "known_min": round(known_min, 2), "walk_min": round(walk_min, 2),
            "ride_min": round(sum(leg.ride_min for leg in legs), 2),
            "wait_min": round(wait_sum, 2) if waits_known else None,
            "has_live_departure": has_live_departure, "access_walk": access,
            "egress_walk": egress, "transfer_buffer_min": buffer,
            "legs": public_legs, "warnings": warnings, "is_estimate": True,
        })
    journeys.sort(key=lambda item: (item["total_min"] is None, item["total_min"] is None and not item["has_live_departure"],
                                    item["total_min"] if item["total_min"] is not None else item["known_min"],
                                    item["transfer_count"], item["walk_min"],
                                    sum(leg.get("route_number_verified") is False for leg in item["legs"]),
                                    tuple((leg["route_id"], leg["board"]["id"], leg["alight"]["id"])
                                          for leg in item["legs"])))
    # 통합 서비스는 교통수단을 합친 뒤 최종 제한한다. 기존 호출은 3개 유지.
    return journeys if limit is None else journeys[:limit]
