"""공공데이터 수집과 순서 기반 경로 탐색을 연결한다. Flask/UI에 의존하지 않는다."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from math import cos, hypot, radians, isfinite
from time import monotonic

from services.public_transit_client import PublicDataError
from services.route_planner import plan_journeys


class TransitService:
    MAX_STOPS = 8
    MAX_ROUTES = 32

    def __init__(self, client, walking_time, topology_repository=None, shuttle_service=None):
        self.client = client
        # 노선망은 로컬 CSV, 실시간 도착만 외부 API를 사용한다.
        # 주입하지 않은 기존 호출자는 API 어댑터와 호환된다.
        self.topology = topology_repository if topology_repository is not None else client
        self.csv_mode = topology_repository is not None
        self.walking_time = walking_time
        self.shuttle_service = shuttle_service

    @staticmethod
    def _collect(keys, function):
        values, errors = {}, []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(function, key): key for key in keys}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    values[key] = future.result()
                except PublicDataError as error:
                    errors.append(error)
        return values, errors

    @staticmethod
    def _coords(stop):
        try:
            if isinstance(stop["gpslati"], bool) or isinstance(stop["gpslong"], bool):
                return None
            lat, lon = float(stop["gpslati"]), float(stop["gpslong"])
            if not isfinite(lat) or not isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                return None
            return lat, lon
        except (ValueError, TypeError, KeyError, OverflowError):
            return None

    def _stops(self, position, warnings):
        stops = self.topology.nearby_stops(position["lat"], position["lon"])
        unique = {}
        for stop in stops:
            if stop.get("nodeid") and self._coords(stop):
                unique[str(stop["nodeid"])] = stop
        def distance(stop):
            lat, lon = self._coords(stop)
            return hypot(lat - position["lat"], (lon - position["lon"]) * cos(radians(position["lat"])))
        stops = sorted(unique.values(), key=lambda stop: (distance(stop), str(stop["nodeid"])))
        if len(stops) > self.MAX_STOPS:
            warnings.append(f"주변 정류장이 많아 각 지점에서 가까운 {self.MAX_STOPS}개를 검색했습니다.")
        return stops[:self.MAX_STOPS]

    def nearby(self, origin):
        """목적지 없는 기존 주변 정류장 화면의 응답 형식을 유지한다."""
        result = []
        stops = self._stops(origin, [])
        by_id = {str(stop["nodeid"]): stop for stop in stops}
        def walk_to(node_id):
            stop = by_id[node_id]
            lat, lon = self._coords(stop)
            return self.walking_time(origin["lat"], origin["lon"], origin["name"], lat, lon, str(stop.get("nodenm", "정류장")))
        walks, _ = self._collect(by_id, walk_to)
        valid = {node_id: stop for node_id, stop in by_id.items() if walks.get(node_id, (-1, -1))[0] >= 0}
        if stops and not valid:
            raise PublicDataError("WALK_UNAVAILABLE", "주변 정류장은 있지만 도보 경로를 확인하지 못했습니다. TMAP_APP_KEY 설정·호출 한도·네트워크를 확인하세요.")
        serving, _ = self._collect(valid, self.topology.station_routes)
        def captured_arrivals(node_id):
            try:
                rows = self.client.arrivals(node_id)
                return monotonic(), rows, None
            except PublicDataError as error:
                return monotonic(), [], str(error)
        snapshots, _ = self._collect(valid, captured_arrivals)
        now = monotonic()
        for node_id, stop in valid.items():
            lat, lon = self._coords(stop)
            name = str(stop.get("nodenm", "정류장"))
            walk_min, walk_dist = walks[node_id]
            routes = serving.get(node_id, [])
            metadata = {str(route["routeid"]): route for route in routes if route.get("routeid")}
            timestamp, arrivals, arrival_error = snapshots.get(node_id, (now, [], "실시간 도착정보 미확인"))
            buses = []
            for arrival in arrivals:
                try:
                    seconds = float(arrival["arrtime"]) - (now - timestamp)
                    if not isfinite(seconds) or seconds < 0:
                        continue
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
                minutes = round(seconds / 60, 1)
                spare = round(minutes - walk_min, 1)
                if spare >= 1:
                    status, badge, message = "relax", "여유", f"도보 도착 후 약 {spare:g}분의 여유가 있습니다."
                elif spare >= 0:
                    status, badge, message = "hurry", "촉박", "승차 여유시간이 부족합니다. 다음 배차도 확인하세요."
                else:
                    status, badge, message = "miss", "다음차", "도보로 도착하기 전에 지나가는 버스입니다."
                route = metadata.get(str(arrival.get("routeid", "")), {})
                buses.append({
                    "route": str(arrival.get("routeno", route.get("routeno", ""))),
                    "destination": f"노선 참고: {route.get('endnodenm', '정보 없음')}",
                    "min_left": minutes, "walk_min": walk_min, "spare_min": spare,
                    "status_badge": badge, "status_type": status, "guide_msg": message,
                    "prev_cnt": arrival.get("arrprevstationcnt", 0), "is_recommended": False,
                })
            result.append({
                "start_name": origin["name"], "station_name": name, "station_no": stop.get("nodeno", ""),
                "station_lat": lat, "station_lon": lon, "direction": "방향은 승차 전 정류장 안내를 확인하세요.",
                "walk_min": walk_min, "walk_dist": walk_dist, "buses": sorted(buses, key=lambda bus: bus["min_left"]),
                "has_recommended_bus": False, "matched_routes": [], "target_lat": origin["lat"], "target_lon": origin["lon"],
                "arrival_error": arrival_error, "source": "csv" if self.csv_mode else "api",
            })
        return sorted(result, key=lambda stop: stop["walk_min"])

    def plan(self, origin, destination):
        """두 공급자를 독립 조회한다. 셔틀 미주입 호출의 기존 계약은 유지한다."""
        if self.shuttle_service is None:
            return self._plan_city(origin, destination)

        city_status = {"status": "ok", "code": None}
        try:
            response = self._plan_city(origin, destination, journey_limit=None)
        except Exception as error:
            safe_codes = {"CSV_DATA", "WALK_UNAVAILABLE", "ROUTE_PERMISSION", "NETWORK", "INCOMPLETE"}
            code = getattr(error, "code", None)
            city_status = {"status": "error", "code": code if code in safe_codes else "CITY_UNAVAILABLE"}
            response = {
                "mode": "journeys", "origin": origin, "destination": destination,
                "journeys": [], "source": {"source": "unavailable"}, "search": {},
                "warnings": ["시내버스 경로를 조회하지 못했습니다. 셔틀 조회 결과는 별도로 확인하세요."],
            }

        try:
            shuttle = self.shuttle_service.plan(origin, destination)
        except Exception as error:
            code = "SHUTTLE_DATA" if getattr(error, "code", None) == "SHUTTLE_DATA" else "SHUTTLE_UNAVAILABLE"
            shuttle = {
                "journeys": [], "status": "error", "code": code,
                "warnings": ["셔틀 데이터를 조회하지 못했습니다. 시내버스 조회 결과는 별도로 확인하세요."],
            }
        city = response["journeys"]
        for journey in city:
            journey["transport_type"] = "city_bus"
            journey["time_basis"] = "realtime_estimate" if journey.get("has_live_departure") else "estimate"
            journey["realtime"] = bool(journey.get("has_live_departure"))
            for leg in journey.get("legs", []):
                leg["transport_type"] = "city_bus"
        response["journeys"] = self._merge_journeys(city, shuttle.get("journeys", []))
        response["shuttle"] = {key: value for key, value in shuttle.items() if key != "journeys"}
        response["providers"] = {
            "city_bus": city_status,
            "shuttle": {"status": shuttle["status"], "code": shuttle.get("code")},
        }
        response["warnings"].extend(shuttle.get("warnings", []))
        response["search"].update({
            "max_journeys": 3,
            "selection_policy": "transport_diversity_then_confirmed_total",
            "shuttle_max_transfers": 0,
        })
        if city_status["status"] == "error" and not response["journeys"]:
            # 대체 후보도 없으면 기존 실패와 '경로 없음'을 구분한다.
            # 셔틀 메타데이터는 오류 응답에도 남겨 로컬 조회 상태를 보존한다.
            response.update({
                "error": "시내버스 경로를 조회하지 못했고 연결 가능한 셔틀 후보도 없습니다. 공급자별 상태를 확인하세요.",
                "code": city_status["code"],
            })
        return response

    @staticmethod
    def _merge_journeys(city, shuttle, limit=3):
        """완전한 총시간만 수단 간 비교하고, 각 수단의 첫 후보를 보존한다.

        미확인 후보는 수단별 기존 순서를 유지한다. known_min은 서로 비교하지
        않는다. 이 표시용 선택은 전체 최단 경로 또는 탑승 가능 확정이 아니다.
        """
        groups = [city, shuttle]
        ranked = []
        for group_index, group in enumerate(groups):
            for position, journey in enumerate(group):
                total = journey.get("total_min")
                confirmed = (isinstance(total, (int, float)) and not isinstance(total, bool)
                             and isfinite(total) and total >= 0)
                ranked.append(((not confirmed, total if confirmed else 0, position, group_index), journey))
        ranked.sort(key=lambda pair: pair[0])
        required = {id(group[0]) for group in groups if group}
        selected = [pair for pair in ranked if id(pair[1]) in required]
        for pair in ranked:
            if len(selected) >= limit:
                break
            if id(pair[1]) not in required:
                selected.append(pair)
        selected.sort(key=lambda pair: pair[0])
        return [journey for _, journey in selected[:limit]]

    def _plan_city(self, origin, destination, journey_limit=3):
        warnings = [
            "출발·목적지 반경 500m 정류장 기준, 동일 정류장 내 1회 환승까지 검색합니다.",
            "버스 이동시간은 경유 정류장 좌표와 평균속도로 추정하며 실시간 교통·시간표를 반영하지 못합니다.",
        ]
        response = {
            "mode": "journeys", "origin": origin, "destination": destination,
            "journeys": [], "warnings": warnings,
            "search": {"max_transfers": 1, "nearby_radius_m": 500, "max_stops_per_endpoint": self.MAX_STOPS, "max_routes": self.MAX_ROUTES, "max_refined_candidates": 80},
        }
        if self.csv_mode:
            source = self.topology.metadata()
            response["source"] = source
            warnings.extend(source.get("warnings", []))
            response["search"]["max_routes"] = source["route_count"]
        else:
            response["source"] = {"source": "api", "sequence_basis": "api_direction"}
        origin_stops = self._stops(origin, warnings)
        destination_stops = self._stops(destination, warnings)
        if not origin_stops or not destination_stops:
            warnings.append("출발지 또는 목적지 반경 안의 춘천 정류장을 찾지 못했습니다.")
            return response

        by_id = {str(stop["nodeid"]): stop for stop in origin_stops + destination_stops}
        serving, errors = self._collect(by_id, self.topology.station_routes)
        if errors:
            if not serving:
                raise errors[0]
            warnings.append("일부 정류장의 경유 노선을 조회하지 못해 검색 범위가 줄었습니다.")

        def route_ids(stops):
            return {
                str(route["routeid"])
                for stop in stops for route in serving.get(str(stop["nodeid"]), [])
                if route.get("routeid")
            }
        start_ids, end_ids = route_ids(origin_stops), route_ids(destination_stops)
        common_ids = start_ids & end_ids
        metadata = {}
        for rows in serving.values():
            for route in rows:
                if route.get("routeid"):
                    metadata[str(route["routeid"])] = {
                        "route_no": str(route.get("routeno", "")),
                        "end_node": str(route.get("endnodenm", "")),
                        "sequence_basis": route.get("sequence_basis", "api_direction"),
                        "direction_verified": route.get("direction_verified", True),
                        "route_number_verified": route.get("route_number_verified", True),
                    }
        # 직통 후보를 먼저 조회한다. 제한된 범위라는 사실을 UI에도 반환한다.
        ids = sorted(start_ids | end_ids, key=lambda route_id: (route_id not in common_ids, route_id))
        if not self.csv_mode and len(ids) > self.MAX_ROUTES:
            warnings.append(f"호출량을 제한하기 위해 {self.MAX_ROUTES}개 노선까지만 확인했습니다. 전체 최단 경로를 보장하지 않습니다.")
            ids = ids[:self.MAX_ROUTES]
        sequences, errors = {}, []
        if ids:
            try:
                # 활용신청이 없는 경우 같은 거절 요청을 여러 노선에 반복하지 않는다.
                sequences[ids[0]] = self.topology.route_stops(ids[0])
            except PublicDataError as error:
                if error.authorization_failed:
                    raise PublicDataError("ROUTE_PERMISSION", "노선별 정류장 순서 조회 권한을 확인할 수 없습니다. 공공데이터포털에서 국토교통부_(TAGO)_버스노선정보 활용신청·인증키 상태를 확인하세요.") from None
                errors.append(error)
            remaining, other_errors = self._collect(ids[1:], self.topology.route_stops)
            sequences.update(remaining)
            errors.extend(other_errors)
        if errors:
            # 새 서비스 활용신청이 없으면 추정 노선번호 매칭으로 되돌아가지 않는다.
            denied = next((error for error in errors if error.authorization_failed), None)
            if denied:
                raise PublicDataError("ROUTE_PERMISSION", "노선별 정류장 순서 조회 권한을 확인할 수 없습니다. 공공데이터포털에서 국토교통부_(TAGO)_버스노선정보 활용신청·인증키 상태를 확인하세요.")
            if not any(sequences.values()):
                raise errors[0]
            warnings.append("일부 노선의 정류장 순서를 조회하지 못해 해당 노선을 제외했습니다.")
        routes = {route_id: {**metadata[route_id], "stops": rows} for route_id, rows in sequences.items() if rows}
        if not routes:
            warnings.append("순서를 검증할 수 있는 노선 정보가 없어 경로를 구성하지 못했습니다.")
            return response
        if not self.csv_mode and any(any(stop.get("updowncd") not in (0, 1, "0", "1") for stop in route["stops"]) for route in routes.values()):
            warnings.append("운행 방향을 확인할 수 없는 노선 구간은 추천에서 제외합니다.")

        # 접근/마지막 도보를 한 번씩만 조회하고, 배차 정보는 보행 조회 후 새로 받는다.
        walk_jobs = {}
        for stop in origin_stops:
            lat, lon = self._coords(stop)
            args = (origin["lat"], origin["lon"], origin["name"], lat, lon, str(stop.get("nodenm", "정류장")))
            walk_jobs[(args[0], args[1], args[3], args[4])] = args
        for stop in destination_stops:
            lat, lon = self._coords(stop)
            args = (lat, lon, str(stop.get("nodenm", "정류장")), destination["lat"], destination["lon"], destination["name"])
            walk_jobs[(args[0], args[1], args[3], args[4])] = args
        walks, _ = self._collect(walk_jobs, lambda key: self.walking_time(*walk_jobs[key]))
        if any(value[0] < 0 for value in walks.values()):
            warnings.append("일부 보행 경로를 조회하지 못해 그 정류장을 이용하는 경로를 제외했습니다.")
        def walking_time(start_lat, start_lon, start_name, end_lat, end_lon, end_name):
            return walks.get((start_lat, start_lon, end_lat, end_lon), (-1, -1))
        valid_origins = [stop for stop in origin_stops if walking_time(
            origin["lat"], origin["lon"], origin["name"], *self._coords(stop), "")[0] >= 0]
        valid_destinations = [stop for stop in destination_stops if walking_time(
            *self._coords(stop), "", destination["lat"], destination["lon"], destination["name"])[0] >= 0]
        if not valid_origins or not valid_destinations:
            raise PublicDataError("WALK_UNAVAILABLE", "CSV 노선은 읽었지만 출발지 또는 목적지의 도보 경로를 확인하지 못했습니다. TMAP_APP_KEY 설정·호출 한도·네트워크를 확인하세요.")

        def captured_arrivals(node_id):
            rows = self.client.arrivals(node_id)
            return monotonic(), rows
        captured, errors = self._collect([str(stop["nodeid"]) for stop in valid_origins], captured_arrivals)
        if errors:
            warnings.append("일부 실시간 도착정보를 조회하지 못했습니다. 확인되지 않은 배차 대기는 시간 합계에서 제외됩니다.")
            if any(error.code == "ARRIVAL_KEY_MISSING" for error in errors):
                warnings.append("PUBLIC_DATA_KEY가 없어 실시간 도착은 조회하지 않았습니다. CSV 경로 후보는 표시하되 배차·총 소요 시간은 미확인입니다.")
        now = monotonic()
        arrivals = {}
        for node_id, (timestamp, rows) in captured.items():
            values = []
            for row in rows:
                try:
                    seconds = float(row["arrtime"]) - (now - timestamp)
                    if isfinite(seconds) and seconds >= 0:
                        values.append({**row, "arrtime": seconds})
                except (TypeError, ValueError, KeyError, OverflowError):
                    continue
            arrivals[node_id] = values
        # 인증 오류를 키 값이나 외부 응답 원문 없이 화면에 전달한다.
        if errors:
            code = errors[0].code
            safe_codes = {"20", "30", "31", "401", "403", "429", "ARRIVAL_KEY_MISSING", "NETWORK", "INVALID_RESPONSE", "INCOMPLETE"}
            code = code if code in safe_codes else "ARRIVAL_UNAVAILABLE"
            if code == "ARRIVAL_KEY_MISSING":
                message = "실시간 도착 키가 설정되지 않았습니다. CSV 경로 후보는 조회할 수 있습니다."
            elif code in {"20", "30", "31", "401", "403"}:
                message = "버스도착정보 인증키와 공공데이터포털의 활용신청·사용기한을 확인해 주세요."
            else:
                message = "실시간 도착정보 일부를 확인하지 못했습니다. 잠시 후 다시 조회해 주세요."
            response["arrival_status"] = {"status": "partial" if captured else "unavailable", "code": code, "message": message}
        else:
            has_arrivals = any(arrivals.values())
            response["arrival_status"] = {
                "status": "ok" if has_arrivals else "empty", "code": None,
                "message": ("정류장별 실시간 도착정보를 조회했습니다. 탑승 방향은 별도로 확인하세요." if has_arrivals
                            else "도착정보 서버에 연결했지만 현재 조회 정류장의 도착 예정 버스 자료가 없습니다."),
            }
        # 환승 정류장의 현재 도착정보로 미래 배차를 꾸며내지 않는다.
        # 2번째 승차 대기는 미확인으로 두며 총 ETA도 확정하지 않는다.
        response["journeys"] = plan_journeys(
            origin, destination, valid_origins, valid_destinations,
            routes, arrivals, walking_time, max_transfers=1, use_transfer_arrivals=False,
            limit=journey_limit,
        )
        warnings.append("후보 경로는 도보·버스 이동구간 추정으로 80개까지 비교합니다. CSV 후보의 배차·전체 도착시간은 확정하지 않습니다." if self.csv_mode
                        else "후보 경로는 이동시간 추정으로 80개까지 비교하며, 확인된 배차가 있는 경로를 먼저 표시합니다.")
        return response
