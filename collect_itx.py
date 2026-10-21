"""춘천역·남춘천역 ITX-청춘 계획 시간표 수집기 (TAGO).

필요 패키지: pip install requests python-dotenv
프로젝트 폴더 .env: TAGO_API_KEY=<공공데이터포털 인증키>

실행:
    python collect_itx.py
    python collect_itx.py --only-yongsan    # 이미 검증된 용산 왕복 4구간만
    python collect_itx.py --date 20261021
    python collect_itx.py --force          # 오늘 저장 파일 다시 수집

데이터 범위: TAGO의 출발역/도착역 지정형 조회 결과입니다.
두 역의 출발·도착 계획시각을 저장하며 실제 도착·지연 정보가 아닙니다.
용산·청량리 이외의 별도 운행계통은 포함되지 않을 수 있습니다.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
load_dotenv(ROOT_DIR / ".env", override=True)

BASE_URL = "https://apis.data.go.kr/1613000/TrainInfo"
STATIONS = {"춘천": "NAT140873", "남춘천": "NAT140840", "용산": "NAT010032"}
TARGET_STATIONS = ("춘천", "남춘천")
TRAIN_GRADE_CODE = "09"  # ITX-청춘
KST = timezone(timedelta(hours=9))
PAGE_SIZE = 100
MAX_PAGES = 20
WAIT_SECONDS = 0.25
CSV_FIELDS = [
    "기준일", "역", "구분", "열차번호", "열차종류", "시각", "출발역", "도착역",
    "출발시간", "도착예정시간", "요금(원)", "조회구간", "출발역ID", "도착역ID",
]


class APIError(Exception):
    """요청 주소/인증키를 포함하지 않은 안전한 API 오류."""


def as_items(body):
    holder = body.get("items") or {}
    if not isinstance(holder, dict):
        return []
    value = holder.get("item") or []
    if isinstance(value, dict):
        return [value]
    return value if isinstance(value, list) else []


def get_field(row, *names):
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return None


def parse_response_xml(text):
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise APIError("JSON/XML 응답 파싱 실패") from None
    auth_error = root.findtext(".//returnAuthMsg")
    if auth_error:
        raise APIError(f"공공데이터 API 인증 오류: {auth_error}")
    header = root.find(".//header")
    body = root.find(".//body")
    if header is None or body is None:
        raise APIError("예상하지 못한 XML 응답 구조")
    items_node = body.find("items")
    xml_items = []
    if items_node is not None:
        for item in items_node.findall("item"):
            xml_items.append({child.tag: child.text for child in item})
    return {
        "response": {
            "header": {"resultCode": header.findtext("resultCode"),
                       "resultMsg": header.findtext("resultMsg")},
            "body": {"items": {"item": xml_items},
                     "totalCount": body.findtext("totalCount")},
        }
    }


class TagoClient:
    def __init__(self, key):
        self.key = key
        self.session = requests.Session()
        self.calls = 0

    def get_body(self, endpoint, **kwargs):
        params = {"serviceKey": self.key, "_type": "json", **kwargs}
        try:
            response = self.session.get(f"{BASE_URL}/{endpoint}", params=params, timeout=20)
            self.calls += 1
            if response.status_code != 200:
                raise APIError(f"HTTP 오류: {response.status_code}")
            try:
                payload = response.json()
            except ValueError:
                payload = parse_response_xml(response.text)
        except requests.RequestException as error:
            # requests 예외 원문에는 인증키가 포함된 URL이 있을 수 있으므로 출력하지 않음.
            raise APIError(f"네트워크 오류: {type(error).__name__}") from None
        finally:
            time.sleep(WAIT_SECONDS)
        if not isinstance(payload, dict) or not isinstance(payload.get("response"), dict):
            raise APIError("예상하지 못한 응답 구조")
        data = payload["response"]
        header = data.get("header") or {}
        code = str(header.get("resultCode", "")).zfill(2)
        message = str(header.get("resultMsg", "내용 없음"))
        if code == "03" and "DATA" in message.upper():
            return {"items": {}, "totalCount": 0}
        if code != "00":
            raise APIError(f"TAGO 오류 코드 {code}: {message}")
        body = data.get("body") or {}
        if not isinstance(body, dict):
            raise APIError("body가 JSON 객체가 아닙니다")
        return body

    def list_all(self, endpoint, **params):
        result = []
        for page in range(1, MAX_PAGES + 1):
            body = self.get_body(endpoint, pageNo=page, numOfRows=PAGE_SIZE, **params)
            items = as_items(body)
            result.extend(item for item in items if isinstance(item, dict))
            total_value = body.get("totalCount")
            try:
                total = int(total_value) if total_value is not None else None
            except (TypeError, ValueError):
                total = None
            if total == 0 or (total is not None and len(result) >= total):
                return result
            if len(items) == 0:
                if total is not None and len(result) < total:
                    raise APIError(f"{endpoint}: 일부 페이지만 수신했습니다")
                return result
            if total is None and len(items) < PAGE_SIZE:
                return result
        raise APIError(f"{endpoint}: 최대 페이지 초과 (누락 방지를 위해 수집 중지)")


def lookup_cheongnyangni(client):
    """TAGO 역 목록을 조회해 청량리 ID를 동적으로 확인한다 (임의 코드 사용 금지)."""
    cities = client.list_all("GetCtyCodeList")
    seoul_codes = []
    for city in cities:
        city_name = str(get_field(city, "cityname", "cityName") or "")
        city_code = get_field(city, "citycode", "cityCode")
        if ("서울" in city_name) and city_code is not None:
            seoul_codes.append(str(city_code))
    if not seoul_codes:
        raise APIError("TAGO 도시코드 조회에서 서울을 찾지 못했습니다")

    matches = {}
    for city_code in seoul_codes:
        stations = client.list_all("GetCtyAcctoTrainSttnList", cityCode=city_code)
        for st in stations:
            name = str(get_field(st, "nodename", "nodeName") or "").strip()
            station_id = get_field(st, "nodeid", "nodeId")
            if "청량리" in name and station_id:
                matches[str(station_id)] = name

    if len(matches) != 1:
        raise APIError(f"청량리 역 ID를 하나로 특정하지 못했습니다 (후보 {len(matches)}개)")
    station_id, name = next(iter(matches.items()))
    print(f"청량리 역 코드 확인: {name} ({station_id})")
    return station_id


def planned_time(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) >= 12:
        try:
            dt = datetime.strptime(digits[:12], "%Y%m%d%H%M")
        except ValueError:
            return None
        return dt.strftime("%H:%M")
    return None


def train_number(value):
    raw = str(value or "").strip()
    return raw.zfill(5) if raw.isdecimal() else raw


def transform_item(item, origin, destination, dep_id, arr_id, date):
    """구간 조회 결과 하나를 춘천/남춘천의 출발 또는 도착 이벤트로 변환."""
    if origin in TARGET_STATIONS:
        station, event = origin, "출발"
        station_time = planned_time(get_field(item, "depplandtime", "depPlandTime"))
    elif destination in TARGET_STATIONS:
        station, event = destination, "도착"
        station_time = planned_time(get_field(item, "arrplandtime", "arrPlandTime"))
    else:
        return None

    if not station_time:
        return None
    number = train_number(get_field(item, "trainno", "trainNo"))
    if not number:
        return None
    origin_name = str(get_field(item, "depplacename", "depPlaceName") or origin)
    dest_name = str(get_field(item, "arrplacename", "arrPlaceName") or destination)
    fee = get_field(item, "adultcharge", "adultCharge")
    return {
        "기준일": date,
        "역": station,
        "구분": event,
        "열차번호": number,
        "열차종류": str(get_field(item, "traingradename", "trainGradeName") or "ITX-청춘"),
        "시각": station_time,
        "출발역": origin_name,
        "도착역": dest_name,
        "출발시간": planned_time(get_field(item, "depplandtime", "depPlandTime")) or "",
        "도착예정시간": planned_time(get_field(item, "arrplandtime", "arrPlandTime")) or "",
        "요금(원)": fee if fee is not None else "",
        "조회구간": f"{origin}→{destination}",
        "출발역ID": dep_id,
        "도착역ID": arr_id,
    }


def deduplicate(events):
    """동일 역/날짜/열차/이벤트를 용산 기준 구간 우선으로 중복 제거."""
    unique = {}
    for row in events:
        key = (row["기준일"], row["역"], row["구분"], row["열차번호"], row["시각"])
        # 청량리 경유를 용산 종착/시발편과 중복 카운트하지 않도록 한다.
        current = unique.get(key)
        if current is None or ("용산" in row["조회구간"] and "용산" not in current["조회구간"]):
            unique[key] = row
    return sorted(unique.values(), key=lambda row: (
        0 if row["역"] == "춘천" else 1,
        row["시각"], 0 if row["구분"] == "출발" else 1, row["열차번호"]
    ))


def save(date, events, successes, failures, cheong_included, calls, only_yongsan):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "_yongsan_only" if only_yongsan else ""
    base = DATA_DIR / f"itx_timetable_{date}{suffix}"
    csv_path = base.with_suffix(".csv")
    json_path = base.with_suffix(".json")

    departures = [row for row in events if row["구분"] == "출발"]
    arrivals = [row for row in events if row["구분"] == "도착"]
    counts = {
        st: {
            "departures": sum(r["역"] == st for r in departures),
            "arrivals": sum(r["역"] == st for r in arrivals),
        }
        for st in TARGET_STATIONS
    }
    unique_trains = len({(r["열차번호"], r["구분"]) for r in events})
    payload = {
        "date": date,
        "updated_at_kst": datetime.now(KST).isoformat(timespec="seconds"),
        "source": "국토교통부 TAGO 열차정보 GetStrtpntAlocFndTrainInfo",
        "scope": "춘천·남춘천 ↔ 용산" + ("·청량리" if cheong_included else ""),
        "scope_warning": (
            "구간별 계획시각 조회 자료이며 역 전체 운행/중간 정차를 100% 보장하지 않습니다. "
            "청량리 외 다른 시종착편은 별도 확인이 필요합니다. 실제 운행·지연은 코레일에서 확인하세요."
        ),
        "complete_for_requested_routes": not failures,
        "cheongnyangni_included": cheong_included,
        "queried_routes": successes,
        "failed_routes": failures,
        "api_calls": calls,
        "event_count": len(events),
        "unique_train_numbers_by_direction": unique_trains,
        "by_station": counts,
        "events": events,
        "departures": departures,
        "arrivals": arrivals,
    }
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(events)
    with json_path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    print(f"저장: {csv_path}")
    print(f"저장: {json_path}")
    for station, count in counts.items():
        print(f"{station}: 출발 {count['departures']}건 / 도착 {count['arrivals']}건")
    print(f"역별 이벤트 {len(events)}건, 열차번호·방향 기준 {unique_trains}편")
    if failures:
        print(f"주의: {len(failures)}개 구간 조회 실패. JSON의 failed_routes를 확인하세요.")
    if not cheong_included:
        print("주의: 청량리 구간을 포함하지 못했습니다. 용산 구간 자료만 저장했습니다.")


def main():
    today = datetime.now(KST).strftime("%Y%m%d")
    parser = argparse.ArgumentParser(description="춘천역·남춘천역 ITX-청춘 시간표 CSV/JSON 수집")
    parser.add_argument("--date", default=today, help="조회일 YYYYMMDD (기본 한국시간 오늘)")
    parser.add_argument("--only-yongsan", action="store_true", help="용산 왕복 4구간만 조회")
    parser.add_argument("--force", action="store_true", help="기존 파일이 있어도 새로 수집")
    args = parser.parse_args()
    try:
        datetime.strptime(args.date, "%Y%m%d")
    except ValueError:
        parser.error("날짜는 YYYYMMDD 형식이어야 합니다")

    key = os.getenv("TAGO_API_KEY", "").strip()
    if not key:
        parser.error("프로젝트 .env에서 TAGO_API_KEY를 설정해 주세요")
    suffix = "_yongsan_only" if args.only_yongsan else ""
    existing = DATA_DIR / f"itx_timetable_{args.date}{suffix}.json"
    if existing.exists() and not args.force:
        print(f"이미 저장된 파일이 있습니다: {existing}")
        print("다시 수집하려면 --force를 붙여 실행하세요.")
        return 0

    print("조회 날짜(KST):", args.date)
    client = TagoClient(key)
    station_ids = dict(STATIONS)
    cheong_included = False
    if not args.only_yongsan:
        try:
            station_ids["청량리"] = lookup_cheongnyangni(client)
            cheong_included = True
        except APIError as error:
            print("청량리 역 ID 자동 조회 실패:", error)
            print("현재 검증된 용산 왕복 구간만 수집합니다.")

    endpoints = ["용산"] + (["청량리"] if cheong_included else [])
    pairs = []
    for terminal in endpoints:
        for target in TARGET_STATIONS:
            pairs.extend([(target, terminal), (terminal, target)])

    raw_events = []
    successes, failures = [], []
    for index, (origin, destination) in enumerate(pairs, 1):
        label = f"{origin}→{destination}"
        print(f"[{index}/{len(pairs)}] {label} 조회 중...")
        try:
            rows = client.list_all(
                "GetStrtpntAlocFndTrainInfo",
                depPlaceId=station_ids[origin],
                arrPlaceId=station_ids[destination],
                depPlandTime=args.date,
                trainGradeCode=TRAIN_GRADE_CODE,
            )
        except APIError as error:
            failures.append({"route": label, "error": str(error)})
            print("  실패:", error)
            continue
        successes.append({"route": label, "count": len(rows)})
        for item in rows:
            event = transform_item(
                item, origin, destination, station_ids[origin], station_ids[destination], args.date
            )
            if event is not None:
                raw_events.append(event)
        print(f"  조회 {len(rows)}건")

    events = deduplicate(raw_events)
    if not events:
        print("운행시간표 0건: 빈 데이터로 기존 파일을 덮어쓰지 않습니다.")
        return 1
    save(args.date, events, successes, failures, cheong_included, client.calls, args.only_yongsan)
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
