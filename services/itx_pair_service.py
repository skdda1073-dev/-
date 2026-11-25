"""선택한 출발역→도착역의 ITX-청춘 직통 시간표 조회.

별도 Flask Blueprint로 제공하며 기존 /api/itx/timetable 엔드포인트는 변경하지 않습니다.
인증키는 프로젝트 루트 .env의 TRAIN_API_KEY를 우선하고 기존 TAGO_API_KEY도 지원합니다.
"""

from __future__ import annotations

import re
import threading
import time
import xml.etree.ElementTree as ET
from csv import DictReader, Error as CsvError
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Blueprint, jsonify, request

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)

KST = timezone(timedelta(hours=9))
BASE_URL = "https://apis.data.go.kr/1613000/TrainInfo"
TRAIN_GRADE_CODE = "09"  # ITX-청춘
PAGE_SIZE = 100
MAX_PAGES = 10
STATION_CACHE_SECONDS = 24 * 60 * 60
PLANNED_ITX_CACHE_SECONDS = 60 * 60
PLANNED_ITX_RETRY_SECONDS = 60
PLANNED_ITX_TIMEOUT = (1.5, 3.5)
PLANNED_ITX_CACHE_LIMIT = 256

# 사용자 선택 후보입니다. 실제 정차 여부는 선택 구간의 TAGO 운행정보로 판단합니다.
# ITX가 모든 열차에서 모든 역에 정차한다는 뜻이 아닙니다.
STATION_CANDIDATES = (
    "춘천", "남춘천", "강촌", "가평", "청평", "마석", "평내호평",
    "사릉", "퇴계원", "상봉", "청량리", "왕십리", "옥수", "용산",
)

# 앞선 사용자 TAGO 테스트로 확인된 세 역의 ID만 기본값으로 보유합니다.
# 마석 등 나머지는 공식 역 목록 API에서 조회하고, 확인 없이 코드를 추측하지 않습니다.
CONFIRMED_STATIONS = {
    "춘천": "NAT140873",
    "남춘천": "NAT140840",
    "용산": "NAT010032",
}

bp = Blueprint("itx_pair", __name__)
_station_cache: dict[str, object] = {"expires_at": 0.0, "stations": {}, "warning": None}
_station_lock = threading.Lock()
_planned_cache: dict[tuple, tuple[float, tuple[dict, ...]]] = {}
_planned_inflight: dict[tuple, threading.Event] = {}
_planned_lock = threading.Lock()
_planned_throttled_until = 0.0


class TagoError(Exception):
    def __init__(self, message: str, code: str = "TAGO_ERROR"):
        super().__init__(message)
        self.code = code


def _items(body: dict) -> list[dict]:
    holder = body.get("items") or {}
    if not isinstance(holder, dict):
        return []
    value = holder.get("item") or []
    if isinstance(value, dict):
        value = [value]
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _field(row: dict, *keys: str):
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return None


def _xml_to_payload(value: str) -> dict:
    try:
        root = ET.fromstring(value)
    except ET.ParseError:
        raise TagoError("TAGO 응답을 JSON/XML로 읽지 못했습니다.", "BAD_RESPONSE") from None
    if root.findtext(".//returnReasonCode") == "22":
        raise TagoError("TAGO 조회 한도를 초과했습니다.", "TAGO_RATE_LIMIT")
    if root.findtext(".//returnAuthMsg"):
        raise TagoError("TAGO 인증 또는 이용 권한을 확인해 주세요.", "TAGO_AUTH")
    header = root.find(".//header")
    body = root.find(".//body")
    if header is None or body is None:
        raise TagoError("TAGO 응답 구조가 예상과 다릅니다.", "BAD_RESPONSE")
    entries = []
    for item in root.findall(".//items/item"):
        entries.append({child.tag: child.text for child in item})
    return {
        "response": {
            "header": {"resultCode": header.findtext("resultCode"), "resultMsg": header.findtext("resultMsg")},
            "body": {"totalCount": body.findtext("totalCount"), "items": {"item": entries}},
        }
    }


def _request_body(endpoint: str, params: dict, *, timeout=20) -> dict:
    import os
    key = (os.getenv("TRAIN_API_KEY") or "").strip() or (os.getenv("TAGO_API_KEY") or "").strip()
    if not key:
        raise TagoError(".env 파일에 TRAIN_API_KEY 또는 기존 TAGO_API_KEY를 설정해 주세요.", "MISSING_KEY")

    query = {"serviceKey": key, "_type": "json", **params}
    try:
        response = requests.get(f"{BASE_URL}/{endpoint}", params=query, timeout=timeout, allow_redirects=False)
    except requests.RequestException as error:
        # 원래 예외문에 URL과 API 키가 담길 수 있어 문자열 그대로 공개하지 않습니다.
        raise TagoError(f"TAGO 네트워크 요청 실패 ({type(error).__name__})", "TAGO_NETWORK") from None
    if response.status_code == 429:
        raise TagoError("TAGO 조회 한도를 초과했습니다.", "TAGO_RATE_LIMIT")
    if response.status_code != 200:
        raise TagoError(f"TAGO HTTP 응답 오류 ({response.status_code})", "TAGO_HTTP")
    try:
        payload = response.json()
    except ValueError:
        payload = _xml_to_payload(response.text)
    if not isinstance(payload, dict) or not isinstance(payload.get("response"), dict):
        raise TagoError("TAGO 응답 형식이 올바르지 않습니다.", "BAD_RESPONSE")
    envelope = payload["response"]
    header = envelope.get("header")
    code = str(header.get("resultCode", "")) if isinstance(header, dict) else ""
    if not re.fullmatch(r"[0-9]{1,2}", code):
        raise TagoError("TAGO 응답에 유효한 결과 코드가 없습니다.", "BAD_RESPONSE")
    code = code.zfill(2)
    if code == "03" and "DATA" in str(header.get("resultMsg", "")).upper():
        return {"items": {}, "totalCount": 0}
    if code == "22":
        raise TagoError("TAGO 조회 한도를 초과했습니다.", "TAGO_RATE_LIMIT")
    if code != "00":
        # 서버 메시지에 인증키가 포함될 수도 있으므로 결과코드만 노출합니다.
        raise TagoError(f"TAGO 조회 오류 코드: {code}", "TAGO_RESPONSE")
    body = envelope.get("body") or {}
    if not isinstance(body, dict):
        raise TagoError("TAGO 조회 결과의 body 구조가 올바르지 않습니다.", "BAD_RESPONSE")
    return body


def _all_pages(endpoint: str, *, timeout=20, **params) -> list[dict]:
    collected: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        body = _request_body(endpoint, {"pageNo": page, "numOfRows": PAGE_SIZE, **params}, timeout=timeout)
        rows = _items(body)
        collected.extend(rows)
        try:
            total_value = body.get("totalCount")
            total = int(total_value) if total_value is not None else None
        except (TypeError, ValueError):
            total = None
        if total == 0 or (total is not None and len(collected) >= total):
            return collected
        if not rows:
            if total is not None and len(collected) < total:
                raise TagoError("일부 조회 페이지가 비었습니다.", "PAGING_ERROR")
            return collected
        if total is None and len(rows) < PAGE_SIZE:
            return collected
    raise TagoError("조회 결과 페이지 한도를 초과했습니다.", "PAGING_LIMIT")


def _normalized_name(value: str) -> str:
    name = re.sub(r"\([^)]*\)", "", str(value or ""))
    name = re.sub(r"\s+", "", name).strip()
    if name.endswith("역"):
        name = name[:-1]
    return name


@lru_cache(maxsize=1)
def _planned_station_names() -> dict[str, str]:
    names = {identifier: name for name, identifier in CONFIRMED_STATIONS.items()}
    path = ROOT / "data/normalized/rail/tago_train_station_ids.csv"
    try:
        with path.open(encoding="utf-8-sig", newline="") as file:
            for row in DictReader(file):
                if row.get("station_id") and row.get("station_name"):
                    names[row["station_id"]] = _normalized_name(row["station_name"])
    except (OSError, UnicodeError, CsvError):
        pass
    return names


def _planned_time(value) -> datetime | None:
    digits = str(value)
    if not re.fullmatch(r"[0-9]{12}(?:[0-9]{2})?", digits):
        return None
    try:
        return datetime.strptime(digits.ljust(14, "0"), "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except ValueError:
        return None


def _parse_planned_itx(rows: list[dict], origin_id: str, destination_id: str, service_date: date) -> tuple[dict, ...]:
    names = _planned_station_names()
    expected = (names.get(origin_id), names.get(destination_id))
    trips = {}
    for row in rows:
        grade = re.sub(r"[-\s]", "", str(_field(row, "traingradename", "trainGradeName") or "")).upper()
        if grade != "ITX청춘":
            continue
        reported = (_normalized_name(_field(row, "depplacename", "depPlaceName")),
                    _normalized_name(_field(row, "arrplacename", "arrPlaceName")))
        if not all(reported) or any(name is not None and actual != name for actual, name in zip(reported, expected)):
            continue
        departure = _planned_time(_field(row, "depplandtime", "depPlandTime"))
        arrival = _planned_time(_field(row, "arrplandtime", "arrPlandTime"))
        if not departure or not arrival or departure.date() != service_date or not timedelta(0) < arrival - departure <= timedelta(days=1):
            continue
        number = str(_field(row, "trainno", "trainNo") or "").strip()
        if not re.fullmatch(r"[0-9]{1,5}", number) or not int(number):
            continue
        number = number.zfill(5)
        fare = str(_field(row, "adultcharge", "adultCharge"))
        key = (departure, arrival, number)
        if key not in trips or trips[key]["fare_won"] is None:
            trips[key] = {"train_number": number, "departure_at": departure, "arrival_at": arrival,
                          "fare_won": int(fare) if re.fullmatch(r"[0-9]{1,8}", fare) else None}
    return tuple(trips[key] for key in sorted(trips))


def planned_itx_trips(origin_id: str, destination_id: str, service_date: date | str) -> tuple[dict, ...]:
    """Return verified ITX-청춘 planning times in KST. Outages leave other providers usable."""
    global _planned_throttled_until
    if (not isinstance(origin_id, str) or not re.fullmatch(r"NAT[0-9]{6}", origin_id)
            or not isinstance(destination_id, str) or not re.fullmatch(r"NAT[0-9]{6}", destination_id)
            or origin_id == destination_id):
        return ()
    if isinstance(service_date, str) and re.fullmatch(r"(?:[0-9]{8}|[0-9]{4}-[0-9]{2}-[0-9]{2})", service_date):
        try:
            service_date = date.fromisoformat(service_date)
        except ValueError:
            return ()
    if type(service_date) is not date:
        return ()
    cache_key = (origin_id, destination_id, service_date)
    with _planned_lock:
        now = time.monotonic()
        cached = _planned_cache.get(cache_key)
        if cached and cached[0] > now:
            return tuple(dict(trip) for trip in cached[1])
        if now < _planned_throttled_until:
            return ()
        pending = _planned_inflight.get(cache_key)
        if pending is None:
            _planned_inflight[cache_key] = threading.Event()
    if pending is not None:
        if not pending.wait(sum(PLANNED_ITX_TIMEOUT)):
            return ()
        with _planned_lock:
            cached = _planned_cache.get(cache_key)
            return tuple(dict(trip) for trip in cached[1]) if cached else ()

    trips = ()
    ttl = PLANNED_ITX_RETRY_SECONDS
    throttled = False
    try:
        rows = _all_pages("GetStrtpntAlocFndTrainInfo", timeout=PLANNED_ITX_TIMEOUT,
                          depPlaceId=origin_id, arrPlaceId=destination_id,
                          depPlandTime=service_date.strftime("%Y%m%d"), trainGradeCode=TRAIN_GRADE_CODE)
        trips = _parse_planned_itx(rows, origin_id, destination_id, service_date)
        ttl = PLANNED_ITX_CACHE_SECONDS
    except TagoError as error:
        throttled = error.code == "TAGO_RATE_LIMIT"
    finally:
        with _planned_lock:
            expires = time.monotonic() + ttl
            if throttled:
                _planned_throttled_until = expires
            if len(_planned_cache) >= PLANNED_ITX_CACHE_LIMIT:
                _planned_cache.pop(next(iter(_planned_cache)))
            _planned_cache[cache_key] = (expires, trips)
            _planned_inflight.pop(cache_key).set()
    return tuple(dict(trip) for trip in trips)


def _discover_stations() -> dict[str, str]:
    """TAGO 도시 및 역 목록을 이용해 역 ID를 공식 응답에서만 확보합니다."""
    cities = _all_pages("GetCtyCodeList")
    codes = set()
    for city in cities:
        name = str(_field(city, "cityname", "cityName") or "")
        code = _field(city, "citycode", "cityCode")
        if code is not None and any(region in name for region in ("서울", "경기", "강원")):
            codes.add(str(code))
    if not codes:
        raise TagoError("서울·경기·강원 도시코드를 찾을 수 없습니다.", "STATION_LOOKUP")

    choices: dict[str, set[str]] = {name: set() for name in STATION_CANDIDATES}
    for city_code in sorted(codes):
        for row in _all_pages("GetCtyAcctoTrainSttnList", cityCode=city_code):
            name = _normalized_name(str(_field(row, "nodename", "nodeName") or ""))
            station_id = _field(row, "nodeid", "nodeId")
            if name in choices and station_id:
                choices[name].add(str(station_id))

    resolved = dict(CONFIRMED_STATIONS)
    for name, ids in choices.items():
        if name not in resolved and len(ids) == 1:
            resolved[name] = next(iter(ids))
    return resolved


def _get_stations() -> tuple[dict[str, str], str | None]:
    now = time.monotonic()
    with _station_lock:
        if now < float(_station_cache["expires_at"]):
            return dict(_station_cache["stations"]), _station_cache["warning"]
        try:
            stations = _discover_stations()
            warning = None
            ttl = STATION_CACHE_SECONDS
        except TagoError:
            # 역 목록 장애 중에도 확인된 3개 역의 조회는 계속 가능합니다.
            stations = dict(CONFIRMED_STATIONS)
            warning = "역 목록 API 조회에 실패해 일부 역만 표시됩니다. 잠시 후 다시 시도해 주세요."
            ttl = 60
        _station_cache.update(stations=stations, warning=warning, expires_at=now + ttl)
        return dict(stations), warning


def _time_string(value) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 12:
        return None
    try:
        parsed = datetime.strptime(digits[:12], "%Y%m%d%H%M")
    except ValueError:
        return None
    return parsed.strftime("%H:%M")


def _fare(value):
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _parse_route(rows: list[dict], origin: str, destination: str) -> list[dict]:
    results = {}
    for row in rows:
        grade = str(_field(row, "traingradename", "trainGradeName") or "")
        if grade and not ("ITX" in grade.upper() and "청춘" in grade):
            continue
        dep = _time_string(_field(row, "depplandtime", "depPlandTime"))
        arr = _time_string(_field(row, "arrplandtime", "arrPlandTime"))
        if dep is None or arr is None:
            continue
        trainno = str(_field(row, "trainno", "trainNo") or "").strip()
        if not trainno:
            continue
        trainno = trainno.zfill(5) if trainno.isdecimal() else trainno
        result = {
            "train_no": trainno,
            "train_type": grade or "ITX-청춘",
            "origin": origin,
            "destination": destination,
            "departure_time": dep,
            "arrival_time": arr,
            "fare": _fare(_field(row, "adultcharge", "adultCharge")),
        }
        results[(trainno, dep, arr)] = result
    return sorted(results.values(), key=lambda x: (x["departure_time"], x["train_no"]))


@bp.get("/api/itx/stations")
def station_options():
    stations, warning = _get_stations()
    options = [{"name": name} for name in STATION_CANDIDATES if name in stations]
    return jsonify({
        "stations": options,
        "date": datetime.now(KST).strftime("%Y-%m-%d"),
        "warning": warning,
        "missing_station_names": [name for name in STATION_CANDIDATES if name not in stations],
    })


@bp.get("/api/itx/route")
def timetable_by_pair():
    origin = request.args.get("origin", "").strip().removesuffix("역")
    destination = request.args.get("destination", "").strip().removesuffix("역")
    date_str = request.args.get("date", datetime.now(KST).strftime("%Y%m%d"))
    if not re.fullmatch(r"\d{8}", date_str or ""):
        return jsonify({"error": "날짜는 YYYYMMDD 형식이어야 합니다.", "code": "INVALID_DATE"}), 400
    try:
        datetime.strptime(date_str, "%Y%m%d")
    except ValueError:
        return jsonify({"error": "올바른 조회 날짜를 입력해 주세요.", "code": "INVALID_DATE"}), 400
    if origin == destination:
        return jsonify({"error": "출발역과 도착역을 다르게 선택해 주세요.", "code": "SAME_STATION"}), 400
    if origin not in STATION_CANDIDATES or destination not in STATION_CANDIDATES:
        return jsonify({"error": "지원하는 역 목록에서 출발역과 도착역을 골라 주세요.", "code": "UNKNOWN_STATION"}), 400

    stations, warning = _get_stations()
    if origin not in stations or destination not in stations:
        return jsonify({
            "error": "선택한 역의 TAGO 역 코드를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.",
            "code": "STATION_ID_NOT_FOUND",
            "warning": warning,
        }), 503

    try:
        rows = _all_pages(
            "GetStrtpntAlocFndTrainInfo",
            depPlaceId=stations[origin],
            arrPlaceId=stations[destination],
            depPlandTime=date_str,
            trainGradeCode=TRAIN_GRADE_CODE,
        )
    except TagoError as error:
        status = 503 if error.code == "MISSING_KEY" else 502
        return jsonify({"error": str(error), "code": error.code}), status

    trains = _parse_route(rows, origin, destination)
    return jsonify({
        "date": date_str,
        "origin": origin,
        "destination": destination,
        "count": len(trains),
        "trains": trains,
        "scope_warning": "TAGO의 선택 구간 직통 ITX-청춘 계획 시간표입니다. 실제 운행·지연·매진 여부는 코레일에서 확인해 주세요.",
    })
