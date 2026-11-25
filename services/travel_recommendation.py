"""조회된 후보의 도보·정류장 보행·확인된 대기를 비교한다. 외부 조회는 하지 않는다."""

from math import isfinite


# 제품 추천을 위한 튜닝값이며 보행 가능 여부나 교통수단 운행을 보장하지 않는다.
SHORT_WALK_MAX_MIN = 15
SHORT_WALK_MAX_M = 1000
LONG_WALK_MIN = 20
COMPARISON_MARGIN_MIN = 2
REASONABLE_WALK_MAX_MIN = 25
REASONABLE_WALK_MAX_M = 2000


def _number(value, lower=0, upper=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if isfinite(number) and number >= lower and (upper is None or number <= upper) else None


def _point(position):
    if not isinstance(position, dict):
        return None
    lat = _number(position.get("lat"), -90, 90)
    lon = _number(position.get("lon"), -180, 180)
    return (lat, lon) if lat is not None and lon is not None else None


def _walk_minutes(walk):
    if not isinstance(walk, dict):
        return None
    if "distance_m" in walk and _number(walk["distance_m"]) is None:
        return None
    return _number(walk.get("minutes"))


def _candidate(journey, index):
    if not isinstance(journey, dict):
        return None
    legs = journey.get("legs")
    if not isinstance(legs, (list, tuple)) or not legs or any(not isinstance(leg, dict) for leg in legs):
        return None
    shuttle = journey.get("transport_type") == "shuttle"
    planned_shuttle = journey.get("schedule_comparable") is True and journey.get("service_date_confirmed") is True
    if shuttle and journey.get("availability_confirmed") is not True and not planned_shuttle:
        return None
    if not shuttle and journey.get("transport_type") not in (None, "city_bus"):
        return None
    for leg in legs:
        if leg.get("route_number_verified") is not True:
            return None
        if not shuttle and (leg.get("direction_verified") is not True or leg.get("arrival_reference_only") is not False):
            return None
    access, egress = _walk_minutes(journey.get("access_walk")), _walk_minutes(journey.get("egress_walk"))
    if access is None or egress is None:
        return None
    stop_walk = _number(access + egress)
    if stop_walk is None:
        return None
    wait = _number(journey.get("wait_min"))
    total = _number(journey.get("total_min"))
    complete = (total is not None and not journey.get("total_unavailable_reason") and wait is not None
                and _number(journey.get("walk_min")) is not None and _number(journey.get("ride_min")) is not None
                and all(_number(leg.get("wait_min")) is not None for leg in legs)
                and (shuttle or journey.get("has_live_departure") is True))
    return {
        "candidate_index": index, "total_min": total if complete else None,
        "access_walk_min": access, "egress_walk_min": egress, "stop_walk_min": stop_walk, "wait_min": wait,
        "transfer_count": len(legs) - 1,
    }


def build_recommendation(origin, destination, direct_walk, journeys):
    """반환된 후보만 비교한다. 대기 미확인 때 탑승 추정시간을 하한으로 쓰지 않는다."""
    candidates = []
    if isinstance(journeys, (list, tuple)):
        candidates = [candidate for index, journey in enumerate(journeys)
                      if (candidate := _candidate(journey, index)) is not None]
    complete = [candidate for candidate in candidates if candidate["total_min"] is not None]
    best_complete = min(complete, key=lambda item: (
        item["total_min"], item["transfer_count"], item["stop_walk_min"], item["candidate_index"],
    ), default=None)
    least_walk = min(candidates, key=lambda item: (item["stop_walk_min"], item["candidate_index"])) if candidates else None
    result = {
        "status": "unavailable", "preferred_mode": "transit" if candidates else "undetermined",
        "reason": "walk_unavailable", "walk": None, "transit": dict(best_complete or least_walk) if candidates else None,
        "time_saved_min": None,
        "min_bus_access_min": min(item["access_walk_min"] for item in candidates) if candidates else None,
        "min_bus_walk_min": least_walk["stop_walk_min"] if least_walk else None,
        "scooter": {"status": "not_suggested", "total_min": None, "availability_confirmed": False, "route_confirmed": False},
        "scope": "returned_candidates",
        "criteria": {"short_walk_max_min": SHORT_WALK_MAX_MIN, "short_walk_max_m": SHORT_WALK_MAX_M,
                     "comparison_margin_min": COMPARISON_MARGIN_MIN},
    }
    start, end = _point(origin), _point(destination)
    if start is None or end is None:
        return result
    if start == end:
        result.update(status="ok", preferred_mode="none", reason="same_point", walk={"minutes": 0, "distance_m": 0})
        return result
    if not isinstance(direct_walk, dict) or direct_walk.get("status") != "ok":
        return result
    minutes, distance = _number(direct_walk.get("minutes")), _number(direct_walk.get("distance_m"))
    if minutes is None or distance is None or minutes == 0 or distance == 0:
        return result
    result.update(status="ok", preferred_mode="undetermined", reason="comparison_unknown",
                  walk={"minutes": minutes, "distance_m": distance})
    short = minutes <= SHORT_WALK_MAX_MIN and distance <= SHORT_WALK_MAX_M
    reasonable = minutes <= REASONABLE_WALK_MAX_MIN and distance <= REASONABLE_WALK_MAX_M
    overhead = least_walk is not None and minutes + COMPARISON_MARGIN_MIN <= least_walk["stop_walk_min"]
    if best_complete:
        bus_time = best_complete["total_min"]
        if bus_time < minutes:
            result.update(preferred_mode="transit", reason="transit_faster", time_saved_min=round(minutes - bus_time, 2))
        elif (minutes >= LONG_WALK_MIN
              and max(best_complete["access_walk_min"], best_complete["egress_walk_min"]) < LONG_WALK_MIN):
            result.update(preferred_mode="transit", reason="less_walking")
        elif minutes < bus_time:
            result.update(preferred_mode="walk", reason="stop_walk_overhead" if overhead else "walk_faster",
                          time_saved_min=round(bus_time - minutes, 2))
        else:
            transit_wins_tie = (best_complete["transfer_count"], best_complete["stop_walk_min"]) < (0, minutes)
            result.update(preferred_mode="transit" if transit_wins_tie else "walk", reason="equal_total")
    elif reasonable and overhead:
        result.update(preferred_mode="walk", reason="stop_walk_overhead")
    elif short:
        result.update(preferred_mode="walk", reason="short_trip" if candidates else "walk_only")
    if (result["preferred_mode"] in {"walk", "undetermined"} and 300 <= distance <= REASONABLE_WALK_MAX_M
            and minutes <= REASONABLE_WALK_MAX_MIN):
        # 보행 거리는 참고값이다. PM 재고·허용 경로·주행 시간은 계산하거나 확정하지 않는다.
        result["scooter"]["status"] = "conditional"
    return result
