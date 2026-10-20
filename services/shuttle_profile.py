"""해시로 고정한 셔틀 원본에 공식 운행조건과 승인된 대표점을 보강한다.

원본 repository의 행·해시·시각은 유지한다. 좌표는 지정된 건물/시내 지점에서만
읽으며, 서로 다른 셔틀 정류장 ID를 병합하거나 정확한 승강장으로 승격하지 않는다.
공식 자료와 매핑의 검증 이력은 별도 JSON에 두고 실행 중 네트워크를 사용하지 않는다.
"""

import csv
from copy import deepcopy
from datetime import date
from hashlib import sha256
from io import StringIO
import json
from math import isfinite
from pathlib import Path
import re
from threading import RLock

from services.shuttle_repository import ShuttleDataError, ShuttleRepository


class ShuttleProfile:
    def __init__(self, repository, profile_path, campus_csv_path, transit_repository):
        self.repository = repository
        self.profile_path = Path(profile_path)
        self.campus_csv_path = Path(campus_csv_path)
        self.transit_repository = transit_repository
        self._lock = RLock()
        self._profile = None
        self._snapshot = None
        self._mappings = None
        self._mapping_diagnostics = []

    @staticmethod
    def _require(condition):
        if not condition:
            raise ShuttleDataError("셔틀 공식 보강정보의 형식을 확인할 수 없습니다.")

    @classmethod
    def _text(cls, value):
        cls._require(isinstance(value, str) and bool(value.strip()))
        return value

    @classmethod
    def _day(cls, value):
        cls._text(value)
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise ShuttleDataError("셔틀 공식 보강정보의 날짜 형식을 확인할 수 없습니다.") from None
        cls._require(parsed.isoformat() == value)
        return parsed

    @classmethod
    def _url(cls, value):
        cls._text(value)
        cls._require(value.startswith("https://") and "\n" not in value)

    @staticmethod
    def _coords(lat, lon):
        try:
            if isinstance(lat, bool) or isinstance(lon, bool):
                return None
            point = float(lat), float(lon)
            if isfinite(point[0]) and isfinite(point[1]) and -90 <= point[0] <= 90 and -180 <= point[1] <= 180:
                return point
        except (TypeError, ValueError, OverflowError):
            pass
        return None

    def _load_profile(self):
        if self._profile is not None:
            return
        try:
            raw = self.profile_path.read_bytes()
            profile = json.loads(raw.decode("utf-8-sig"))
        except (OSError, UnicodeError, ValueError):
            raise ShuttleDataError("셔틀 공식 보강정보 파일을 읽을 수 없습니다.") from None
        self._require(isinstance(profile, dict) and type(profile.get("schema_version")) is int and profile["schema_version"] == 1)
        self._day(profile.get("checked_on"))
        source = profile.get("raw_source")
        self._require(isinstance(source, dict))
        self._text(source.get("filename"))
        self._require(Path(source["filename"]).name == source["filename"])
        self._require(isinstance(source.get("sha256"), str) and bool(re.fullmatch(r"[0-9a-f]{64}", source["sha256"])))
        self._require(type(source.get("row_count")) is int and source["row_count"] > 0)
        sources = profile.get("sources")
        self._require(isinstance(sources, list) and bool(sources))
        for item in sources:
            self._require(isinstance(item, dict))
            self._text(item.get("id"))
            self._url(item.get("url"))
            self._day(item.get("checked_on"))
        calendar = profile.get("calendar")
        self._require(isinstance(calendar, dict))
        self._text(calendar.get("evidence"))
        weekdays = calendar.get("weekdays")
        self._require(isinstance(weekdays, list) and bool(weekdays) and all(type(day) is int and day in range(7) for day in weekdays))
        start, end = self._day(calendar.get("coverage_start")), self._day(calendar.get("coverage_end"))
        self._require(start <= end)
        self._require(isinstance(calendar.get("excluded_dates"), list))
        for value in calendar["excluded_dates"]:
            self._require(start <= self._day(value) <= end)
        self._require(isinstance(calendar.get("term_ranges"), list) and bool(calendar["term_ranges"]))
        previous_end = None
        for period in calendar["term_ranges"]:
            self._require(isinstance(period, dict))
            first, last = self._day(period.get("start")), self._day(period.get("end"))
            self._require(start <= first <= last <= end and (previous_end is None or previous_end < first))
            previous_end = last
        self._require(isinstance(profile.get("conditions"), dict))
        first_route = profile.get("first_route")
        self._require(isinstance(first_route, dict) and first_route.get("route_name") == "제1노선")
        self._url(first_route.get("evidence"))
        directions = first_route.get("directions")
        self._require(isinstance(directions, dict) and bool(directions))
        for direction, info in directions.items():
            self._text(direction)
            self._require(isinstance(info, dict))
            self._text(info.get("origin"))
            self._text(info.get("destination"))
            times = info.get("friday_excluded_departures")
            self._require(isinstance(times, list) and all(isinstance(value, str) and ShuttleRepository._minutes(value) is not None for value in times))
        representatives, references = profile.get("representatives"), profile.get("stop_references")
        self._require(isinstance(representatives, dict) and bool(representatives) and isinstance(references, dict) and bool(references))
        for key, representative in representatives.items():
            self._text(key)
            self._require(isinstance(representative, dict))
            self._text(representative.get("evidence"))
            self._require(type(representative.get("source_line")) is int and representative["source_line"] >= 2)
            if representative.get("kind") == "campus":
                self._text(representative.get("school"))
                self._text(representative.get("building"))
            else:
                self._require(representative.get("kind") == "city_stop")
                self._text(representative.get("expected_name"))
                stop_id, alternatives = representative.get("source_stop_id"), representative.get("alternative_source_stop_ids")
                self._require(isinstance(stop_id, str) and bool(re.fullmatch(r"CCB[0-9]+", stop_id)))
                self._require(isinstance(alternatives, list) and all(isinstance(value, str) and bool(re.fullmatch(r"CCB[0-9]+", value)) for value in alternatives))
        for name, key in references.items():
            self._text(name)
            self._require(isinstance(key, str) and key in representatives)
        unmatched = profile.get("unmatched_stops")
        self._require(isinstance(unmatched, list) and all(isinstance(name, str) and bool(name) and name not in references for name in unmatched))
        self._profile, self._profile_sha = profile, sha256(raw).hexdigest()

    def _ensure_snapshot(self):
        if self._snapshot is not None:
            return
        self._load_profile()
        original = self.repository.snapshot()
        self._require(isinstance(original, dict) and isinstance(original.get("source"), dict) and isinstance(original.get("trips"), list))
        expected = self._profile["raw_source"]
        if any(original["source"].get(key) != expected[key] for key in ("filename", "sha256", "row_count")):
            raise ShuttleDataError("셔틀 원본이 공식 보강정보의 기준 자료와 다릅니다. 자료를 다시 확인하세요.")
        snapshot = deepcopy(original)
        calendar = self._profile["calendar"]
        added = 0
        for trip in snapshot["trips"]:
            trip["operating_weekdays"] = list(calendar["weekdays"])
            if trip.get("route_name") != self._profile["first_route"]["route_name"]:
                continue
            rule = self._profile["first_route"]["directions"].get(trip.get("direction"))
            events = trip.get("events", [])
            if rule is None or len(events) != 1 or events[0].get("name") != rule["origin"] or type(events[0].get("minutes")) is not int:
                raise ShuttleDataError("제1노선 원본과 공식 보강정보의 연결을 확인할 수 없습니다.")
            events[0]["time_role"] = "departure"
            events[0]["source_url"] = self._profile["first_route"]["evidence"]
            events.append({
                "stop_id": ShuttleRepository._id("stop", trip["route_name"], trip["direction"], rule["destination"]),
                "name": rule["destination"], "minutes": None, "time": None, "line": None,
                "time_role": "arrival_unknown", "generated_endpoint": True,
                "source_url": self._profile["first_route"]["evidence"],
            })
            trip.update({
                "routing_usable": True, "sequence_basis": "official_route_endpoints",
                "source_endpoint_evidence": self._profile["first_route"]["evidence"],
                "time_semantics": "departure_only",
            })
            if events[0]["time"] in rule["friday_excluded_departures"]:
                trip["operating_weekdays"] = [day for day in calendar["weekdays"] if day != 4]
            added += 1
        snapshot["source"].update({
            "official_sources": deepcopy(self._profile["sources"]),
            "official_profile": {"filename": self.profile_path.name, "checked_on": self._profile["checked_on"], "sha256": self._profile_sha},
            "endpoint_enrichment": {"route_name": "제1노선", "generated_event_count": added, "original_row_count_preserved": True},
        })
        snapshot["conditions"].update(deepcopy(self._profile["conditions"]))
        snapshot["conditions"].update({
            "weekdays": list(calendar["weekdays"]), "excluded_dates": list(calendar["excluded_dates"]),
            "term": {"ranges": deepcopy(calendar["term_ranges"]), "coverage_start": calendar["coverage_start"], "coverage_end": calendar["coverage_end"]},
            "calendar_evidence": calendar["evidence"],
        })
        self._snapshot = snapshot

    def _campus_point(self, representative):
        try:
            content = self.campus_csv_path.read_bytes()
            for encoding in ("utf-8-sig", "cp949"):
                try:
                    decoded = content.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise ValueError("encoding")
            reader = csv.DictReader(StringIO(decoded), strict=True)
            required = {"학교", "건물명", "위도(lat)", "경도(lon)"}
            if not required <= set(reader.fieldnames or []):
                raise ValueError("columns")
            rows = [row for row in reader if row.get("학교") == representative["school"] and row.get("건물명") == representative["building"]]
            if len(rows) != 1:
                raise ValueError("ambiguous building")
            point = self._coords(rows[0].get("위도(lat)"), rows[0].get("경도(lon)"))
            if point is None:
                raise ValueError("coordinates")
            return point
        except (OSError, UnicodeError, csv.Error, ValueError):
            raise ShuttleDataError("교내 대표 건물의 좌표를 확인할 수 없습니다.") from None

    def snapshot(self):
        with self._lock:
            self._ensure_snapshot()
            result = deepcopy(self._snapshot)
            result["diagnostics"].extend(deepcopy(self._mapping_diagnostics))
            return result

    def calendar(self):
        with self._lock:
            self._ensure_snapshot()
            return deepcopy(self._profile["calendar"])

    def stop_mappings(self):
        with self._lock:
            self._ensure_snapshot()
            if self._mappings is not None:
                return deepcopy(self._mappings)
            representatives, city_failed = {}, False
            for key, representative in self._profile["representatives"].items():
                if representative["kind"] == "campus":
                    try:
                        point = self._campus_point(representative)
                    except ShuttleDataError:
                        self._mapping_diagnostics.append({"line": representative["source_line"], "code": "CAMPUS_COORDINATES_UNAVAILABLE", "message": "교내 건물 대표점을 확인하지 못해 해당 좌표 연결을 제외했습니다."})
                        continue
                    representatives[key] = {"lat": point[0], "lon": point[1], "reference_name": representative["building"], "location_basis": "building_reference"}
                elif not city_failed:
                    try:
                        stop = self.transit_repository.stop(representative["source_stop_id"])
                    except Exception:
                        city_failed = True
                        self._mapping_diagnostics.append({"line": None, "code": "CITY_COORDINATES_UNAVAILABLE", "message": "시내 CSV 좌표를 확인하지 못했습니다. 교내 건물 대표점과 셔틀 시간표는 별도로 유지합니다."})
                        continue
                    point = self._coords(stop.get("gpslati"), stop.get("gpslong")) if isinstance(stop, dict) else None
                    if (point is None or stop.get("nodeid") != representative["source_stop_id"] or stop.get("nodenm") != representative["expected_name"]):
                        self._mapping_diagnostics.append({"line": representative["source_line"], "code": "SOURCE_STOP_UNMATCHED", "message": "지정한 원본 ID·명칭·좌표를 확인하지 못해 해당 대표점을 제외했습니다.", "source_stop_id": representative["source_stop_id"]})
                        continue
                    representatives[key] = {
                        "lat": point[0], "lon": point[1], "reference_name": representative["expected_name"],
                        "location_basis": "place_representative", "source_stop_id": representative["source_stop_id"],
                        "alternative_source_stop_ids": list(representative["alternative_source_stop_ids"]),
                    }
            mappings = {}
            for trip in self._snapshot["trips"]:
                for event in trip["events"]:
                    reference = self._profile["stop_references"].get(event["name"])
                    if reference not in representatives:
                        continue
                    representative = self._profile["representatives"][reference]
                    mapping = deepcopy(representatives[reference])
                    mapping.update({
                        "evidence": representative["evidence"], "official_stop_name": event["name"],
                        "is_approximate": True, "stop_location_verified": False,
                        "source_line": representative["source_line"],
                    })
                    if representative["kind"] == "campus":
                        mapping["official_stop_name"] = "CLC(희망터) 앞" if trip["route_name"] == "제1노선" else "대학교(교내 대표점: CLC)"
                        if trip["route_name"] != "제1노선":
                            mapping["evidence"] += " 사용자 승인 교내 계산 기준점이며, 제1노선과 동일 승강장이라는 확인이 아니다."
                    else:
                        mapping["evidence"] += f" data/chuncheon_bus_routes_20260326.csv 원본 {representative['source_line']}행, {representative['source_stop_id']}."
                    mappings[event["stop_id"]] = mapping
            self._mappings = mappings
            return deepcopy(mappings)
