"""로컬 춘천 노선 CSV를 검증해 정류소·노선 조회 인터페이스로 제공한다."""

import csv
from collections import defaultdict
from copy import deepcopy
from datetime import date
from io import StringIO
from math import asin, cos, isfinite, radians, sin, sqrt
from pathlib import Path
import re
from threading import RLock

from services.public_transit_client import PublicDataError


class CsvTransitRepository:
    CITY_CODE = "32010"
    COLUMNS = {
        "노선번호", "노선", "정류장순서", "정류장", "정류장명", "경도", "위도", "데이터기준일",
    }

    def __init__(self, path, radius_m=500):
        self.path = Path(path)
        self.radius_m = float(radius_m)
        if not isfinite(self.radius_m) or self.radius_m <= 0:
            raise ValueError("검색 반경은 양의 유한한 값이어야 합니다.")
        self._lock = RLock()
        self._loaded = False
        self._stops = {}
        self._routes = {}
        self._station_routes = {}
        self._route_info = {}
        self._metadata = {}

    @staticmethod
    def _identifier(value):
        match = re.fullmatch(r"(?:CCB)?([0-9]+)", str(value).strip())
        return f"CCB{match.group(1)}" if match else None

    @staticmethod
    def _error(message):
        return PublicDataError("CSV_DATA", f"버스 노선 CSV: {message}")

    @staticmethod
    def _number_is_verified(label):
        return not (
            re.search(r"\d+\s*월\s*\d+\s*일", label)
            or re.fullmatch(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", label)
        )

    @staticmethod
    def _distance(lat1, lon1, lat2, lon2):
        lat_delta = radians(lat2 - lat1)
        lon_delta = radians(lon2 - lon1)
        value = sin(lat_delta / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(lon_delta / 2) ** 2
        return 6371000 * 2 * asin(min(1, sqrt(value)))

    def _read_rows(self):
        try:
            content = self.path.read_bytes()
        except OSError:
            raise self._error("파일을 읽을 수 없습니다. 프로젝트의 노선 데이터 파일을 확인하세요.") from None
        for encoding in ("utf-8-sig", "cp949"):
            try:
                decoded = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise self._error("문자 인코딩을 해석하지 못했습니다.")
        try:
            reader = csv.DictReader(StringIO(decoded), strict=True)
            fields = reader.fieldnames or []
            if len(fields) != len(self.COLUMNS) or set(fields) != self.COLUMNS:
                raise self._error("필수 열 이름 또는 열 개수가 올바르지 않습니다.")
            rows = list(reader)
        except csv.Error:
            raise self._error("파일의 행 구조가 올바르지 않습니다.") from None
        if not rows:
            raise self._error("노선 데이터가 비어 있습니다.")
        return rows

    def _load(self):
        with self._lock:
            if self._loaded:
                return
            route_rows = defaultdict(list)
            identities = defaultdict(set)
            identity_sources = defaultdict(list)
            dates = set()
            rows = self._read_rows()
            for row in rows:
                if None in row or any(value is None or not str(value).strip() for value in row.values()):
                    raise self._error("누락되거나 추가된 셀이 있습니다.")
                node_id = self._identifier(row["정류장"])
                route_id = self._identifier(row["노선"])
                try:
                    order_text = row["정류장순서"].strip()
                    if not re.fullmatch(r"[1-9][0-9]*", order_text):
                        raise ValueError("order")
                    order = int(order_text)
                    lat = float(row["위도"])
                    lon = float(row["경도"])
                    if not isfinite(lat) or not isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                        raise ValueError("coordinates")
                    source_date = date.fromisoformat(row["데이터기준일"].strip()).isoformat()
                except (TypeError, ValueError, OverflowError):
                    raise self._error("정류장 순서·좌표·기준일이 올바르지 않습니다.") from None
                if node_id is None or route_id is None:
                    raise self._error("노선 또는 정류장 식별자가 올바르지 않습니다.")
                name = row["정류장명"].strip()
                raw_route_no = row["노선번호"].strip()
                route_rows[route_id].append({
                    "nodeid": node_id, "nodenm": name, "gpslati": lat, "gpslong": lon,
                    "nodeord": order, "updowncd": None, "citycode": self.CITY_CODE,
                    "routeid": route_id, "raw_route_no": raw_route_no, "data_date": source_date,
                })
                identities[node_id].add((name, lat, lon))
                identity_sources[node_id].append((source_date, name, lat, lon))
                dates.add(source_date)

            conflicts = {node_id for node_id, versions in identities.items() if len(versions) > 1}
            resolved = {}
            for node_id in sorted(conflicts):
                sources = identity_sources[node_id]
                newest = max(source[0] for source in sources)
                latest_versions = {source[1:] for source in sources if source[0] == newest}
                if len(latest_versions) != 1:
                    continue
                canonical = next(iter(latest_versions))
                if all(self._distance(canonical[1], canonical[2], lat, lon) <= 10 for _, lat, lon in identities[node_id]):
                    resolved[node_id] = canonical
            conflicts -= resolved.keys()
            routes = {}
            route_info = {}
            station_routes = defaultdict(list)
            warnings = []
            for route_id, stops in route_rows.items():
                stops.sort(key=lambda row: row["nodeord"])
                if [stop["nodeord"] for stop in stops] != list(range(1, len(stops) + 1)):
                    raise self._error("노선의 정류장 순서가 중복되거나 누락되었습니다.")
                labels = {stop["raw_route_no"] for stop in stops}
                label = " / ".join(sorted(labels))
                verified = len(labels) == 1 and self._number_is_verified(label)
                info = {
                    "routeid": route_id,
                    "routeno": label if verified else f"번호 미확인(ID {route_id[3:]})",
                    "raw_route_no": label, "raw_route_nos": sorted(labels), "route_number_verified": verified,
                    "startnodenm": stops[0]["nodenm"], "endnodenm": stops[-1]["nodenm"],
                    "sequence_basis": "csv_order", "direction_verified": False,
                    "data_dates": sorted({stop["data_date"] for stop in stops}),
                }
                if not verified:
                    reason = "서로 다른 번호가 혼재하여" if len(labels) > 1 else "날짜 형태여서"
                    warnings.append(f"노선 {route_id}: 원본 노선번호 '{label}'에 {reason} 번호 확인이 필요합니다.")
                for stop in stops:
                    if stop["nodeid"] in resolved:
                        stop["nodenm"], stop["gpslati"], stop["gpslong"] = resolved[stop["nodeid"]]
                        stop["stop_metadata_resolved"] = True
                    if stop["nodeid"] in conflicts:
                        stop["stop_conflict"] = True
                        stop["topology_break"] = True
                    stop["sequence_basis"] = "csv_order"
                    stop["direction_verified"] = False
                routes[route_id] = stops
                route_info[route_id] = info
                for node_id in {stop["nodeid"] for stop in stops} - conflicts:
                    station_routes[node_id].append(info)
            for node_id in sorted(conflicts):
                warnings.append(f"정류장 {node_id}: 이름 또는 좌표가 서로 달라 승차·환승 후보에서 제외했습니다.")
            for node_id in sorted(resolved):
                warnings.append(f"정류장 {node_id}: 10m 이내의 과거·최신 표기 차이를 최신 기준값으로 통일했습니다.")
            if len(dates) > 1:
                warnings.append(f"기준일이 혼재합니다: {', '.join(sorted(dates))}. 오래된 노선 정보가 포함되어 있습니다.")
            warnings.append("CSV의 정류장 순서를 사용하며 상·하행 구분은 별도로 검증되지 않았습니다.")
            stops = {}
            for node_id, versions in identities.items():
                if node_id in conflicts:
                    continue
                name, lat, lon = resolved.get(node_id) or next(iter(versions))
                stops[node_id] = {"nodeid": node_id, "nodenm": name, "gpslati": lat, "gpslong": lon, "citycode": self.CITY_CODE}
            self._stops = stops
            self._routes = routes
            self._route_info = route_info
            self._station_routes = dict(station_routes)
            self._metadata = {
                "source": "csv", "sequence_basis": "csv_order", "direction_verified": False,
                "row_count": len(rows), "route_count": len(routes),
                "stop_count": len(identities), "stop_count_all": len(identities),
                "usable_stop_count": len(stops), "conflict_stop_ids": sorted(conflicts),
                "resolved_stop_ids": sorted(resolved),
                "unverified_route_count": sum(not info["route_number_verified"] for info in route_info.values()),
                "data_dates": sorted(dates), "oldest_date": min(dates), "latest_date": max(dates),
                "warnings": warnings,
            }
            self._loaded = True

    def nearby_stops(self, lat, lon):
        self._load()
        try:
            lat, lon = float(lat), float(lon)
            if not isfinite(lat) or not isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("coordinates")
        except (TypeError, ValueError, OverflowError):
            raise self._error("검색 위치 좌표가 올바르지 않습니다.") from None
        nearby = []
        for stop in self._stops.values():
            distance = self._distance(lat, lon, stop["gpslati"], stop["gpslong"])
            if distance <= self.radius_m:
                nearby.append({**stop, "distance": distance})
        nearby.sort(key=lambda stop: (stop["distance"], stop["nodeid"]))
        return nearby

    def station_routes(self, node_id):
        self._load()
        return deepcopy(self._station_routes.get(self._identifier(node_id), []))

    def route_stops(self, route_id):
        self._load()
        return deepcopy(self._routes.get(self._identifier(route_id), []))

    def route_info(self, route_id):
        self._load()
        return deepcopy(self._route_info.get(self._identifier(route_id), {}))

    def metadata(self):
        self._load()
        return deepcopy(self._metadata)
