"""Pure, bounded bus routing from complete public-data stop sequences.

The caller supplies complete/paginated TAGO routes and an arrival snapshot.
Only strictly ordered stops on the same route ID and direction are connected.
Transfers use the exact same stop ID and a different route ID AND bus number.
An explicitly marked CSV may supply a forward sequence without direction.
Such results are unverified candidates, never confirmed through services or
total ETAs. No backwards wrap or nearby-stop transfer is inferred.

Ride estimates sum every consecutive stop-to-stop great-circle distance, use
a 1.2 road factor at 20 km/h, and allow 0.4 minutes per intermediate stop.
They are not timetables or actual road/traffic measurements. ``arrival_min``
is the observed ETA from the arrival snapshot. Missing or missed arrivals
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
        csv_ordered = info.get("sequence_basis") == "csv_order"
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
            continue
        ordered.sort(key=lambda item: item[0])
        segment, route_segments = [], []
        for order, raw in ordered:
            point = _coordinates(raw.get("gpslati"), raw.get("gpslong"))
            node_id = str(raw.get("nodeid", "")).strip()
            direction = "unknown" if csv_ordered else str(raw.get("updowncd", "")).strip()
            if (not point or not node_id or raw.get("topology_break") or raw.get("stop_conflict")
                    or (not csv_ordered and direction not in ("0", "1"))):
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


def _leg(route_id, route_no, segment, counts, info, start, end):
    stops = segment[start:end + 1]
    metres = sum(_distance((left.lat, left.lon), (right.lat, right.lon))
                 for left, right in zip(stops, stops[1:]))
    minutes = round(max(1.0, metres * 1.2 / 1000 / 20 * 60 + (len(stops) - 2) * 0.4), 2)
    csv_ordered = info.get("sequence_basis") == "csv_order"
    direction = (f"{stops[1].name} 다음 경유 · CSV 순서 기준" if csv_ordered
                 else f"{segment[-1].name} 방면(정류장 순서 기준)")
    return _Leg(route_id, route_no, direction, stops, minutes, counts[stops[0].id] > 1,
                "csv_order" if csv_ordered else "api_direction", not csv_ordered,
                info.get("route_number_verified", True) is not False)


def _structural_candidates(origin, destination, origin_ids, destination_ids, routes, transfers):
    segments = _segments(routes)
    onward = defaultdict(list)
    if transfers:
        for route_id, route_no, segment, counts, info in segments:
            for end, stop in enumerate(segment):
                if stop.id in destination_ids:
                    for start in range(end):
                        if segment[start].id != stop.id:
                            onward[segment[start].id].append(
                                _leg(route_id, route_no, segment, counts, info, start, end))
    best = {}
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
                first = _leg(route_id, route_no, segment, counts, info, start, end)
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
                    if key not in best or heuristic < best[key][0]:
                        best[key] = (heuristic, legs)
                    if len(best) > MAX_STRUCTURAL_CANDIDATES * 2:
                        keep = heapq.nsmallest(MAX_STRUCTURAL_CANDIDATES, best,
                                              key=lambda choice: best[choice][0])
                        best = {choice: best[choice] for choice in keep}
    return heapq.nsmallest(MAX_WALKING_CANDIDATES, best.values(), key=lambda item: item[0])


def _arrival_for(leg, ready_min, records):
    if ready_min is None or leg.ambiguous_board:
        return None
    usable = []
    for raw in records:
        if not isinstance(raw, dict) or str(raw.get("routeid", "")).strip() != leg.route_id:
            continue
        direction = raw.get("updowncd")
        if leg.direction_verified and direction is not None and str(direction).strip() != leg.board.direction:
            continue
        seconds = _number(raw.get("arrtime"))
        # CSV에는 상·하행/방문 차례를 확인할 정보가 없어 도착 참고값만 준다.
        threshold = ready_min + BOARDING_MARGIN_MIN if leg.direction_verified else 0
        if seconds is not None and seconds / 60 >= threshold:
            usable.append(seconds / 60)
    return min(usable) if usable else None


def plan_journeys(origin, destination, origin_stops, destination_stops, routes,
                  arrivals, walking_time, max_transfers=1, use_transfer_arrivals=True):
    """Return the best three candidates, preserving unknown waits as ``None``.

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
    candidates = _structural_candidates(origin_point, destination_point, _nearby_ids(origin_stops),
                                        _nearby_ids(destination_stops), routes, max_transfers >= 1)
    walk_cache, arrival_cache = {}, {}

    def routed_walk(start, start_name, end, end_name):
        key = (start, end)
        if key not in walk_cache:
            try:
                response = walking_time(*start, start_name, *end, end_name)
                minutes, metres = _number(response[0]), _number(response[1])
                walk_cache[key] = ({"minutes": round(minutes, 2), "distance_m": round(metres, 1)}
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
        warnings = ["버스 탑승 시간은 정류장 순서와 구간 거리로 추정하며 실제 교통 상황과 다를 수 있습니다."]
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
                "sequence_basis": leg.sequence_basis, "direction_verified": leg.direction_verified,
                "route_number_verified": leg.route_number_verified,
                "arrival_reference_only": not leg.direction_verified,
                "board": leg.board.public(), "alight": leg.alight.public(),
                "stop_count": len(leg.stops) - 1, "ride_min": leg.ride_min,
                "arrival_min": None if arrival_min is None else round(arrival_min, 2),
                "wait_min": wait_min,
                "path": [{"lat": stop.lat, "lon": stop.lon} for stop in leg.stops],
            })
        direction_unverified = any(not leg.direction_verified for leg in legs)
        if direction_unverified:
            warnings.append("CSV 정류장 순서 기반 후보입니다. 상·하행과 종점·회차 구간의 연속 탑승 여부는 승차 전 확인해야 하며, 총 소요 시간은 확정하지 않습니다.")
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
            "total_min": round(known_min, 2) if waits_known else None,
            "total_unavailable_reason": ("direction_unverified" if direction_unverified else
                                         "wait_unknown" if not waits_known else None),
            "known_min": round(known_min, 2), "walk_min": round(walk_min, 2),
            "ride_min": round(sum(leg.ride_min for leg in legs), 2),
            "wait_min": round(wait_sum, 2) if waits_known else None,
            "has_live_departure": has_live_departure, "access_walk": access,
            "egress_walk": egress, "transfer_buffer_min": buffer,
            "legs": public_legs, "warnings": warnings, "is_estimate": True,
        })
    journeys.sort(key=lambda item: (item["total_min"] is None, not item["has_live_departure"],
                                    item["total_min"] if item["total_min"] is not None else item["known_min"],
                                    item["transfer_count"],
                                    sum(leg.get("route_number_verified") is False for leg in item["legs"]),
                                    tuple((leg["route_id"], leg["board"]["id"], leg["alight"]["id"])
                                          for leg in item["legs"])))
    return journeys[:3]
