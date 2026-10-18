"""검증된 정류장 연결과 시간표를 이용한 셔틀 조회. 외부 API를 직접 호출하지 않는다.

좌표·운행일·시간 열의 의미는 서로 독립된 검증 정보다. 원본 시간의 의미가
불명확하면 정류장 시각과 시각 차이만 반환하며 출발·도착·승차시간으로 승격하지
않는다. naive datetime은 운영체제 시간대 대신 명시적으로 한국 시각으로 해석한다.
"""

from copy import deepcopy
from datetime import date, datetime, timedelta
from math import asin, cos, isfinite, radians, sin, sqrt
from threading import RLock
from zoneinfo import ZoneInfo

from services.shuttle_repository import ShuttleDataError


SEOUL = ZoneInfo("Asia/Seoul")
UNKNOWN_CONDITIONS = {
    "weekdays": None, "term": None, "excluded_dates": None,
    "eligibility": None, "fare": None,
}


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _point(value):
    if not isinstance(value, dict):
        return None
    lat, lon = _number(value.get("lat")), _number(value.get("lon"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def _distance(left, right):
    lat1, lon1 = left
    lat2, lon2 = right
    delta_lat, delta_lon = radians(lat2 - lat1), radians(lon2 - lon1)
    value = sin(delta_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(delta_lon / 2) ** 2
    return 6371000 * 2 * asin(sqrt(min(1.0, max(0.0, value))))


class ShuttleService:
    NEARBY_RADIUS_M = 500

    def __init__(self, repository, walking_time, stop_mappings=None, clock=None,
                 boarding_margin_min=1.0, service_calendar=None, profile=None):
        self.repository = repository
        self.walking_time = walking_time
        self.clock = clock or (lambda: datetime.now(SEOUL))
        margin = _number(boarding_margin_min)
        if margin is None or margin < 0:
            raise ValueError("셔틀 탑승 여유시간은 음수가 아닌 유한한 분이어야 합니다.")
        # 기본 1분은 기존 시내버스 route_planner.BOARDING_MARGIN_MIN과 같다.
        self.boarding_margin_min = margin
        self.profile = profile
        self._profile_loaded = False
        self._profile_lock = RLock()
        self._set_mappings(stop_mappings)
        self.service_calendar = self._validate_calendar(service_calendar)

    def _set_mappings(self, stop_mappings):
        self.stop_mappings, self.mapping_diagnostics = {}, []
        for stop_id, mapping in (stop_mappings or {}).items():
            point = _point(mapping)
            evidence = mapping.get("evidence") if isinstance(mapping, dict) else None
            if not isinstance(stop_id, str) or not stop_id or point is None or not isinstance(evidence, str) or not evidence.strip():
                self.mapping_diagnostics.append({
                    "code": "invalid_stop_mapping", "message": "유효 좌표와 검증 근거가 없는 정류장 연결을 제외했습니다.",
                    "stop_id": str(stop_id),
                })
                continue
            metadata = {key: deepcopy(mapping[key]) for key in (
                "location_basis", "reference_name", "official_stop_name", "is_approximate",
                "stop_location_verified", "source_stop_id", "alternative_source_stop_ids",
            ) if key in mapping}
            self.stop_mappings[stop_id] = {
                **metadata, "lat": point[0], "lon": point[1], "evidence": evidence.strip(),
            }
            if metadata.get("is_approximate"):
                self.stop_mappings[stop_id]["stop_location_verified"] = False

    def _ensure_profile(self):
        # 앱 시작 시 데이터 오류가 시내버스 초기화까지 막지 않게 지연 로드한다.
        if self.profile is None or self._profile_loaded:
            return
        with self._profile_lock:
            if self._profile_loaded:
                return
            mappings = self.profile.stop_mappings()
            calendar = self._validate_calendar(self.profile.calendar())
            self._set_mappings(mappings)
            self.service_calendar = calendar
            self._profile_loaded = True

    @staticmethod
    def _validate_calendar(calendar):
        if calendar is None:
            return None
        if not isinstance(calendar, dict) or not isinstance(calendar.get("evidence"), str) or not calendar["evidence"].strip():
            raise ValueError("운행일 달력에는 명시적 검증 근거가 필요합니다.")
        weekdays = calendar.get("weekdays")
        if not isinstance(weekdays, (list, tuple)) or any(
                isinstance(day, bool) or not isinstance(day, int) or day not in range(7) for day in weekdays):
            raise ValueError("검증된 운행요일 목록이 필요합니다(월요일=0, 일요일=6).")
        excluded = calendar.get("excluded_dates", [])
        if not isinstance(excluded, (list, tuple)):
            raise ValueError("운휴일은 ISO 날짜 목록이어야 합니다.")
        try:
            excluded = {date.fromisoformat(value) for value in excluded}
        except (ValueError, TypeError):
            raise ValueError("운휴일은 ISO 날짜 목록이어야 합니다.") from None
        result = {"weekdays": set(weekdays), "excluded_dates": excluded, "evidence": calendar["evidence"].strip()}
        try:
            if "coverage_start" in calendar or "coverage_end" in calendar:
                start, end = (date.fromisoformat(calendar[key]) for key in ("coverage_start", "coverage_end"))
                if start > end:
                    raise ValueError("coverage")
                result.update({"coverage_start": start, "coverage_end": end})
            if "term_ranges" in calendar:
                ranges = []
                for item in calendar["term_ranges"]:
                    start, end = date.fromisoformat(item["start"]), date.fromisoformat(item["end"])
                    if start > end:
                        raise ValueError("term range")
                    ranges.append((start, end))
                result["term_ranges"] = ranges
        except (KeyError, TypeError, ValueError):
            raise ValueError("운행 달력의 확인 범위와 학기 기간은 올바른 ISO 날짜여야 합니다.") from None
        return result

    def _now(self, query_at):
        value = query_at if query_at is not None else self.clock()
        if not isinstance(value, datetime):
            raise ValueError("셔틀 조회 기준 시각은 datetime이어야 합니다.")
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=SEOUL)
        return value.astimezone(SEOUL)

    def _base(self, now):
        self._ensure_profile()
        snapshot = deepcopy((self.profile or self.repository).snapshot())
        conditions = {**UNKNOWN_CONDITIONS, **snapshot.get("conditions", {})}
        if self.service_calendar is not None:
            conditions.update({
                "weekdays": sorted(self.service_calendar["weekdays"]),
                "excluded_dates": sorted(day.isoformat() for day in self.service_calendar["excluded_dates"]),
                "calendar_evidence": self.service_calendar["evidence"],
            })
            if "coverage_start" in self.service_calendar:
                conditions["calendar_coverage"] = {
                    "start": self.service_calendar["coverage_start"].isoformat(),
                    "end": self.service_calendar["coverage_end"].isoformat(),
                }
            if "term_ranges" in self.service_calendar:
                conditions["term"] = {"semester_only": True, "ranges": [
                    {"start": start.isoformat(), "end": end.isoformat()}
                    for start, end in self.service_calendar["term_ranges"]
                ]}
        calendar_known = self.service_calendar is not None and self._calendar_covered(now.date())
        warnings = list(snapshot.get("warnings", []))
        if self.service_calendar is None:
            warnings.append("운행요일·운휴일이 확인되지 않았습니다. 조회 날짜의 운행과 다음 날 운행을 확정하지 않습니다.")
        elif not calendar_known:
            warnings.append("조회 날짜가 확인된 학기·공휴일 달력 범위 밖입니다. 실제 운행과 다음 운행일은 미확인입니다.")
        labels = {"term": "학기·방학", "eligibility": "탑승 대상", "fare": "요금"}
        unknown = [label for key, label in labels.items() if conditions.get(key) is None]
        if unknown:
            warnings.append(f"{', '.join(unknown)} 조건이 미확인입니다.")
        if snapshot.get("source", {}).get("time_semantics") != "departure_arrival":
            warnings.append("원본 시간 열의 출발·도착 의미가 미확인입니다. 정류장 시간표의 시각 차이를 승차 소요시간이나 도착 예정시각으로 확정하지 않습니다.")
        diagnostics = snapshot.get("diagnostics", []) + deepcopy(self.mapping_diagnostics)
        if diagnostics:
            warnings.append("데이터 또는 정류장 연결의 오류·미확인 항목은 diagnostics에서 확인할 수 있습니다.")
        return snapshot, {
            "status": "ok" if calendar_known else "unknown_calendar",
            "query_at": now.isoformat(), "timezone": "Asia/Seoul",
            "source": snapshot.get("source", {}), "conditions": conditions,
            "calendar_status": "verified" if calendar_known else "unknown",
            "diagnostics": diagnostics, "warnings": warnings,
            "time_basis": "schedule", "realtime": False,
        }

    @staticmethod
    def _events(trip):
        events = trip.get("events", [])
        if not trip.get("routing_usable") or not isinstance(events, list) or len(events) < 2:
            return []
        previous = -1
        for index, event in enumerate(events):
            if not isinstance(event, dict) or not event.get("stop_id"):
                return []
            minutes = event.get("minutes")
            if (index == len(events) - 1 and minutes is None
                    and event.get("time_role") == "arrival_unknown"
                    and trip.get("sequence_basis") == "official_route_endpoints"):
                continue
            if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < previous or minutes < 0:
                return []
            previous = minutes
        return events

    def _dates(self, events, now):
        # 24:xx 이상의 명시적인 시간이 있을 때에만 이전 서비스 날짜를 검토한다.
        lookback = max(event["minutes"] for event in events if event["minutes"] is not None) // 1440
        return [now.date() - timedelta(days=offset) for offset in range(lookback, -1, -1)]

    def _calendar_covered(self, service_date):
        calendar = self.service_calendar
        return calendar is not None and ("coverage_start" not in calendar or
               calendar["coverage_start"] <= service_date <= calendar["coverage_end"])

    def _operating(self, service_date, trip=None):
        if trip is not None and "operating_weekdays" in trip and service_date.weekday() not in trip["operating_weekdays"]:
            return False
        if self.service_calendar is None:
            return None
        calendar = self.service_calendar
        if service_date.weekday() not in calendar["weekdays"] or service_date in calendar["excluded_dates"]:
            return False
        if not self._calendar_covered(service_date):
            return None
        if "term_ranges" in calendar and not any(start <= service_date <= end for start, end in calendar["term_ranges"]):
            return False
        return True

    @staticmethod
    def _scheduled(service_date, event):
        if event.get("minutes") is None:
            return None
        return datetime.combine(service_date, datetime.min.time(), tzinfo=SEOUL) + timedelta(minutes=event["minutes"])

    @staticmethod
    def _matches(selector, events):
        if selector is None:
            return None
        exact_ids = {event["stop_id"] for event in events if event["stop_id"] == selector}
        return exact_ids or {event["stop_id"] for event in events if event["name"] == selector}

    def timetable(self, route=None, direction=None, board=None, alight=None, query_at=None):
        now = self._now(query_at)
        try:
            snapshot, result = self._base(now)
        except ShuttleDataError:
            if self.profile is None:
                raise
            # 보강정보를 원본에 억지 적용하지 않는다. 원본의 정상 회차 조회는
            # 별도로 유지하며 운행일·좌표·공식 종점 보완은 모두 미적용한다.
            fallback = ShuttleService(self.repository, self.walking_time,
                                      boarding_margin_min=self.boarding_margin_min)
            result = fallback.timetable(route, direction, board, alight, query_at=now)
            result["profile_status"] = {"status": "error", "code": "SHUTTLE_DATA"}
            result["warnings"].append("공식 보강정보를 적용하지 못해 원본 시간표만 조회했습니다. 운행 조건과 대표점 연결은 미확인입니다.")
            return result
        trips = [trip for trip in snapshot.get("trips", [])
                 if (route is None or trip["route_name"] == route)
                 and (direction is None or trip["direction"] == direction)]
        all_events = [event for trip in trips for event in trip.get("events", [])]
        result.update({"mode": "shuttle_timetable", "trips": trips, "routes": [], "stops": [], "timetable_candidates": []})
        routes, stops = {}, {}
        for trip in trips:
            key = trip["route_name"], trip["direction"]
            routes.setdefault(key, {"route_name": key[0], "direction": key[1], "trip_count": 0})["trip_count"] += 1
            for event in trip.get("events", []):
                stops[event["stop_id"]] = {"id": event["stop_id"], "name": event["name"]}
        result["routes"], result["stops"] = list(routes.values()), list(stops.values())
        if not trips:
            result["status"] = "no_connection"
            return result
        board_ids, alight_ids = self._matches(board, all_events), self._matches(alight, all_events)
        for selector, matched in ((board, board_ids), (alight, alight_ids)):
            if selector is not None and not matched:
                result["status"] = "unmatched"
                return result
            if matched is not None and len(matched) > 1:
                result["status"] = "ambiguous_stop"
                result["warnings"].append("동일 이름의 정류장이 여러 개입니다. 정확한 정류장 ID 또는 노선·방향을 지정하세요.")
                return result
        if board is None or alight is None:
            result["trips"] = [trip for trip in trips
                               if (board_ids is None or any(event["stop_id"] in board_ids for event in trip["events"]))
                               and (alight_ids is None or any(event["stop_id"] in alight_ids for event in trip["events"]))]
            for trip in result["trips"]:
                state = self._operating(now.date(), trip)
                trip["operation_status_at_query_date"] = "not_operating" if state is False else "verified" if state is True else "unknown"
            if result["trips"] and all(trip["operation_status_at_query_date"] == "not_operating" for trip in result["trips"]):
                result["status"] = "not_operating"
            return result
        connected, operating = False, False
        for trip in trips:
            events = self._events(trip)
            for start, first in enumerate(events):
                if first["stop_id"] not in board_ids:
                    continue
                for last in events[start + 1:]:
                    if last["stop_id"] not in alight_ids or first["stop_id"] == last["stop_id"]:
                        continue
                    connected = True
                    for service_date in self._dates(events, now):
                        confirmed = self._operating(service_date, trip)
                        if confirmed is False:
                            continue
                        operating = True
                        scheduled = self._scheduled(service_date, first)
                        if scheduled < now:
                            continue
                        candidate = self._schedule_fields(trip, first, last, service_date, confirmed, result["source"])
                        candidate.update({
                            "board": {"id": first["stop_id"], "name": first["name"], "scheduled_time": first["time"],
                                      **deepcopy(self.stop_mappings.get(first["stop_id"], {}))},
                            "alight": {"id": last["stop_id"], "name": last["name"], "scheduled_time": last["time"],
                                       **deepcopy(self.stop_mappings.get(last["stop_id"], {}))},
                            "selection_basis": "timetable_only_without_access_walk",
                            "availability_confirmed": False,
                            "total_min": None, "known_min": None, "walk_min": None,
                            "ride_min": candidate["scheduled_segment_min"] if result["source"].get("time_semantics") == "departure_arrival" else None,
                            "wait_min": None, "access_walk": None, "egress_walk": None,
                            "arrival_min": None, "has_live_departure": False,
                        })
                        result["timetable_candidates"].append(candidate)
        result["timetable_candidates"].sort(key=lambda item: (item["scheduled_stop_at"], item["trip_id"]))
        if not result["timetable_candidates"]:
            result["status"] = "no_connection" if not connected else "not_operating" if not operating else "no_departures_remaining"
        else:
            result["warnings"].append("시간표 참고 후보입니다. 출발지 도보와 탑승 여유시간을 반영한 승차 가능 판단은 경로 조회에서 별도로 확인해야 합니다.")
        return result

    @staticmethod
    def _schedule_fields(trip, first, last, service_date, confirmed, source):
        scheduled = ShuttleService._scheduled(service_date, first)
        end = ShuttleService._scheduled(service_date, last)
        semantics_known = source.get("time_semantics") == "departure_arrival"
        departure_known = semantics_known or (first.get("time_role") == "departure"
                            and trip.get("sequence_basis") == "official_route_endpoints")
        return {
            "trip_id": trip["id"], "route_id": trip.get("route_id", trip["id"]), "route_no": trip["route_name"],
            "route_name": trip["route_name"], "direction": trip["direction"],
            "sequence_basis": trip.get("sequence_basis", "csv_row_order"),
            "transport_type": "shuttle", "time_basis": "schedule", "realtime": False,
            "service_date": service_date.isoformat(), "service_date_confirmed": confirmed is True,
            "scheduled_stop_at": scheduled.isoformat(), "scheduled_alight_stop_at": end.isoformat() if end else None,
            "scheduled_departure_at": scheduled.isoformat() if departure_known else None,
            "scheduled_arrival_at": end.isoformat() if semantics_known and end else None,
            "scheduled_segment_min": last["minutes"] - first["minutes"] if last["minutes"] is not None else None,
        }

    def plan(self, origin, destination, query_at=None):
        now = self._now(query_at)
        snapshot, result = self._base(now)
        result.update({"journeys": [], "boarding_margin_min": self.boarding_margin_min,
                       "nearby_radius_m": self.NEARBY_RADIUS_M})
        result["warnings"].append("준비 완료 후의 정류장 시간표 시각을 고른 조건부 후보입니다. 대기시간은 시간표 기준이며 실제 출발·탑승을 보장하지 않습니다.")
        origin_point, destination_point = _point(origin), _point(destination)
        if origin_point is None or destination_point is None:
            result["status"] = "invalid_coordinates"
            return result
        trips = [(trip, self._events(trip)) for trip in snapshot.get("trips", [])]
        trips = [(trip, events) for trip, events in trips if events]
        stop_ids = {event["stop_id"] for _, events in trips for event in events}
        result["unmatched_stop_ids"] = sorted(stop_ids - self.stop_mappings.keys())
        if result["unmatched_stop_ids"]:
            result["warnings"].append("계산 기준 좌표가 없는 정류장은 GPS 경로의 승하차 연결에서 제외했습니다.")
        if not trips:
            result["status"] = "no_connection"
            return result
        accessible = {stop_id for stop_id in stop_ids & self.stop_mappings.keys()
                      if _distance(origin_point, _point(self.stop_mappings[stop_id])) <= self.NEARBY_RADIUS_M}
        egressible = {stop_id for stop_id in stop_ids & self.stop_mappings.keys()
                     if _distance(_point(self.stop_mappings[stop_id]), destination_point) <= self.NEARBY_RADIUS_M}
        if not accessible or not egressible:
            result["status"] = "unmatched"
            return result
        walk_cache = {}

        def walk(start, start_name, end, end_name):
            key = start, end
            if key not in walk_cache:
                try:
                    response = self.walking_time(*start, start_name, *end, end_name)
                    minutes, distance = _number(response[0]), _number(response[1])
                    walk_cache[key] = ({"minutes": minutes, "distance_m": distance}
                                       if minutes is not None and distance is not None and minutes >= 0 and distance >= 0 else None)
                except Exception:
                    walk_cache[key] = None
            return walk_cache[key]

        connected, operating, walk_failed = False, False, False
        for trip, events in trips:
            for start, first in enumerate(events):
                if first["stop_id"] not in accessible:
                    continue
                for end in range(start + 1, len(events)):
                    last = events[end]
                    if last["stop_id"] not in egressible or first["stop_id"] == last["stop_id"]:
                        continue
                    connected = True
                    for service_date in self._dates(events, now):
                        confirmed = self._operating(service_date, trip)
                        if confirmed is False:
                            continue
                        operating = True
                        scheduled = self._scheduled(service_date, first)
                        if scheduled < now:
                            continue
                        board_mapping, alight_mapping = self.stop_mappings[first["stop_id"]], self.stop_mappings[last["stop_id"]]
                        access = walk(origin_point, str(origin.get("name") or "출발지"), _point(board_mapping), first["name"])
                        if access is None:
                            walk_failed = True
                            continue
                        ready = now + timedelta(minutes=access["minutes"] + self.boarding_margin_min)
                        if scheduled < ready:
                            continue
                        egress = walk(_point(alight_mapping), last["name"], destination_point, str(destination.get("name") or "목적지"))
                        if egress is None:
                            walk_failed = True
                            continue
                        # 현재 조회에서는 보행 응답을 기다린 동안 지나간 시각도
                        # 반영한다. 명시적 과거/테스트 query_at은 고정한다.
                        planning_at = now if query_at is not None else max(now, self._now(None))
                        ready = planning_at + timedelta(minutes=access["minutes"] + self.boarding_margin_min)
                        if scheduled < ready:
                            continue
                        result["journeys"].append(self._journey(
                            trip, events[start:end + 1], service_date, confirmed, result,
                            access, egress, ready, scheduled, planning_at,
                        ))
        if query_at is None and result["journeys"]:
            # 다른 후보의 보행 응답을 기다리며 앞서 선택한 편이 지나갈 수도
            # 있다. 모든 보행 조회가 끝난 한 시각으로 다시 비교한다.
            planning_at = max(now, self._now(None), *(datetime.fromisoformat(item["query_at"]) for item in result["journeys"]))
            retained = []
            for journey in result["journeys"]:
                ready = planning_at + timedelta(minutes=journey["access_walk"]["minutes"] + self.boarding_margin_min)
                scheduled = datetime.fromisoformat(journey["scheduled_stop_at"])
                if scheduled < ready:
                    continue
                wait = round((scheduled - ready).total_seconds() / 60, 6)
                known = round(journey["known_min"] - journey["wait_min"] + wait, 6)
                journey.update({"query_at": planning_at.isoformat(), "ready_at": ready.isoformat(),
                                "wait_min": wait, "known_min": known})
                if journey["total_min"] is not None:
                    journey["total_min"] = known
                journey["legs"][0].update({"ready_at": ready.isoformat(), "wait_min": wait})
                retained.append(journey)
            result["journeys"] = retained
        result["journeys"].sort(key=lambda item: (item["scheduled_stop_at"], item["access_walk"]["minutes"],
                                                item["legs"][0]["route_id"], item["legs"][0]["board"]["id"]))
        if not result["journeys"]:
            result["status"] = ("no_connection" if not connected else "not_operating" if not operating
                                else "walking_unavailable" if walk_failed else "no_departures_remaining")
        return result

    def _journey(self, trip, events, service_date, confirmed, result, access, egress, ready, scheduled, planning_at):
        first, last = events[0], events[-1]
        schedule = self._schedule_fields(trip, first, last, service_date, confirmed, result["source"])
        semantics_known = result["source"].get("time_semantics") == "departure_arrival"
        wait = max(0.0, (scheduled - ready).total_seconds() / 60)
        ride = schedule["scheduled_segment_min"] if semantics_known else None
        walk_min = access["minutes"] + egress["minutes"]
        known = walk_min + self.boarding_margin_min + wait + (ride if ride is not None else 0)
        reference_keys = ("is_approximate", "stop_location_verified", "location_basis", "reference_name", "official_stop_name")
        board_mapping, alight_mapping = self.stop_mappings[first["stop_id"]], self.stop_mappings[last["stop_id"]]
        access = {**access, **{key: deepcopy(board_mapping[key]) for key in reference_keys if key in board_mapping}}
        egress = {**egress, **{key: deepcopy(alight_mapping[key]) for key in reference_keys if key in alight_mapping}}
        approximate = bool(board_mapping.get("is_approximate") or alight_mapping.get("is_approximate"))
        path_complete = all(event["stop_id"] in self.stop_mappings for event in events)
        warnings = list(result["warnings"])
        if approximate:
            warnings.append("건물·장소 대표점 기준의 대략적인 도보입니다. 정확한 승강장까지의 거리나 탑승 가능 여부를 확정하지 않습니다.")
        if not path_complete:
            warnings.append("중간 정류장 좌표가 부족해 지도용 경로 좌표를 만들지 않았습니다.")
        board = {"id": first["stop_id"], "name": first["name"], **self.stop_mappings[first["stop_id"]]}
        alight = {"id": last["stop_id"], "name": last["name"], **self.stop_mappings[last["stop_id"]]}
        leg = {
            **schedule, "direction_verified": False, "direction_basis": "csv_direction_label_and_row_order",
            "route_number_verified": True,
            "board": board, "alight": alight, "stop_count": len(events) - 1,
            "ride_min": ride, "arrival_min": None, "arrival_reference_only": False,
            "wait_min": round(wait, 6), "ready_at": ready.isoformat(),
            "location_is_approximate": approximate,
            "path_basis": "reference_points" if approximate else "stop_coordinates",
            "path": [{"lat": self.stop_mappings[event["stop_id"]]["lat"],
                      "lon": self.stop_mappings[event["stop_id"]]["lon"]} for event in events] if path_complete else [],
            "source": deepcopy(result["source"]), "conditions": deepcopy(result["conditions"]),
        }
        return {
            **schedule, "transfer_count": 0, "transfer_buffer_min": 0,
            "total_min": round(known, 6) if ride is not None and confirmed is True and not approximate else None,
            "total_unavailable_reason": (("arrival_unknown" if schedule["scheduled_departure_at"] else "time_semantics_unknown") if ride is None
                                         else "service_calendar_unknown" if confirmed is not True
                                         else "reference_location_approximate" if approximate else None),
            "known_min": round(known, 6), "known_min_complete": ride is not None and not approximate,
            "walk_min": round(walk_min, 6), "ride_min": ride, "wait_min": round(wait, 6),
            "has_live_departure": False, "access_walk": access, "egress_walk": egress,
            "boarding_margin_min": self.boarding_margin_min, "ready_at": ready.isoformat(),
            "query_at": planning_at.isoformat(),
            "location_is_approximate": approximate,
            "path_basis": "reference_points" if approximate else "stop_coordinates",
            "availability_confirmed": False,
            "legs": [leg], "warnings": warnings, "is_estimate": True,
            "source": deepcopy(result["source"]), "conditions": deepcopy(result["conditions"]),
        }
