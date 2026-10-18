"""키를 출력하지 않는 백엔드 준비/실제 호출 점검 도구.

기본 실행은 GET /health만 로컬에서 확인한다. --live를 명시한 경우에만
TMAP 장소 검색·보행과 공공데이터 도착 API를 호출하며 사용량이 발생한다.
"""

import argparse
from contextlib import redirect_stderr, redirect_stdout
import importlib
from io import StringIO
from math import cos, isfinite, radians
from pathlib import Path
import sys


SAFE_CODES = {
    "NETWORK", "INVALID_RESPONSE", "INCOMPLETE", "CSV_DATA", "ARRIVAL_KEY_MISSING",
    "WALK_UNAVAILABLE", "POI_KEY_MISSING", "POI_NETWORK", "POI_UNAVAILABLE",
    "TMAP_KEY_MISSING", "TMAP_NETWORK", "TMAP_AUTH", "TMAP_QUOTA", "TMAP_TIMEOUT", "TMAP_HTTP", "TMAP_RESPONSE",
    "INVALID_REQUEST", "INVALID_ORIGIN", "INVALID_DESTINATION", "INVALID_CAMPUS",
    "20", "30", "31", "401", "403", "429",
}


def _error_code(payload, status_code):
    """응답/예외의 임의 문자열 대신 허용된 코드만 출력한다."""
    code = str(payload.get("code", "")) if isinstance(payload, dict) else ""
    return code if code in SAFE_CODES else f"HTTP_{status_code}"


def _valid_destination(item):
    if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
        return False
    try:
        if isinstance(item["lat"], bool) or isinstance(item["lon"], bool):
            return False
        lat, lon = float(item["lat"]), float(item["lon"])
        return isfinite(lat) and isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def _origin_building(application):
    """앱에 로드된 한림대 건물 중 한림대학교 정류장에 가까운 건물을 고른다."""
    buildings = application.CAMPUS_BUILDINGS.get("hallym", {})
    valid = []
    for name, coords in buildings.items():
        try:
            lat, lon = coords
            if not (isfinite(lat) and isfinite(lon)):
                continue
            distance = (lat - 37.88355) ** 2 + ((lon - 127.7384) * cos(radians(37.88355))) ** 2
            valid.append((distance, str(name)))
        except (TypeError, ValueError, OverflowError):
            continue
    if not valid:
        return None
    return min(valid)[1]


def _check_arrivals(application, output):
    """별도 한 정류장만 조회해, 빈 도착자료와 조회 오류를 구분한다."""
    try:
        rows = application.transit_client.arrivals("CCB250002127")
    except Exception as error:
        code = str(getattr(error, "code", ""))
        code = code if code in SAFE_CODES else "ARRIVAL_CHECK_FAILED"
        output(f"실시간 도착: 확인 실패 ({code}). CSV 경로 후보 조회와는 별개입니다.")
        return False
    count = len(rows) if isinstance(rows, list) else 0
    if count:
        output(f"실시간 도착: 응답 {count}건 확인. 실제 승차 방향 검증을 대신하지 않습니다.")
    else:
        output("실시간 도착: 응답 수신, 현재 도착자료 없음. 실제 도착값은 검증하지 못했습니다.")
    return True


def run_checks(application, *, live=False, output=print):
    """로컬 Flask 테스트 클라이언트로 점검하고 종료 코드를 반환한다."""
    output("모드: 실제 외부 호출 점검 (--live, API 사용량 발생)" if live
           else "모드: 로컬 준비 점검 (외부 API 호출 없음)")
    # 예상하지 못한 Flask 오류도 이 도구에서 원문 traceback으로 노출하지 않는다.
    logger = application.app.logger
    was_disabled = logger.disabled
    logger.disabled = True
    try:
        with application.app.test_client() as client:
            try:
                response = client.get("/health")
                health = response.get_json(silent=True)
            except Exception:
                output("로컬 상태 점검 실패: HEALTH_CHECK_FAILED")
                return 2
            if not isinstance(health, dict):
                output("로컬 상태 점검 실패: HEALTH_RESPONSE_INVALID")
                return 2
            configured = health.get("configured", {})
            if not isinstance(configured, dict):
                configured = {}
            tmap = configured.get("tmap_app_key") is True
            public = configured.get("public_data_key") is True
            naver = configured.get("naver_map_client_id") is True
            output(f"설정: TMAP {'있음' if tmap else '없음'} / 버스 도착 {'있음' if public else '없음'} / 네이버 지도 {'있음' if naver else '없음'}")
            output("설정 존재는 API 인증·연결 성공을 뜻하지 않습니다. 키 값은 출력하지 않습니다.")
            checks = health.get("checks", {})
            checks = checks if isinstance(checks, dict) else {}
            csv_check = checks.get("csv", {})
            campus_check = checks.get("campus", {})
            csv_ready = isinstance(csv_check, dict) and csv_check.get("status") == "ok"
            campus_ready = isinstance(campus_check, dict) and campus_check.get("status") == "ok"
            count = csv_check.get("route_count") if isinstance(csv_check, dict) else None
            count_text = f" ({count}개 노선)" if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else ""
            output(f"CSV: {'준비됨' if csv_ready else '확인 필요'}{count_text} / 캠퍼스 건물: {'준비됨' if campus_ready else '확인 필요'}")
            if not tmap or not csv_ready or not campus_ready:
                output("준비 미완료: .env의 TMAP_APP_KEY와 로컬 데이터 설정을 확인한 뒤 다시 실행하세요.")
                return 2
            if not public:
                output("버스 도착 키가 없어 실시간 자료는 미확인입니다. CSV 경로 후보는 조회할 수 있습니다.")
            if not live:
                output("로컬 준비 완료. 실제 연결은 python scripts/check_backend.py --live로 별도 확인하세요.")
                return 0

            try:
                response = client.get("/search_poi", query_string={"q": "춘천역"})
                places = response.get_json(silent=True)
            except Exception:
                output("목적지 검색 실패: SEARCH_CHECK_FAILED")
                return 3
            if response.status_code != 200:
                output(f"목적지 검색 실패: {_error_code(places, response.status_code)}")
                return 3
            if not isinstance(places, list):
                output("목적지 검색 실패: SEARCH_RESPONSE_INVALID")
                return 3
            valid_places = [place for place in places if _valid_destination(place)]
            if not valid_places:
                output("목적지 검색: 유효한 좌표의 결과 없음 (SEARCH_EMPTY). API 인증 성공으로 간주하지 않습니다.")
                return 3
            output(f"목적지 검색: 좌표가 유효한 결과 {len(valid_places)}건 확인.")
            building = _origin_building(application)
            if building is None:
                output("경로 조회 실패: CAMPUS_BUILDING_MISSING")
                return 3
            destination = valid_places[0]
            payload = {
                "campus": "hallym", "location": building,
                "dest_name": destination["name"],
                "dest_lat": float(destination["lat"]), "dest_lon": float(destination["lon"]),
            }
            try:
                response = client.post("/get_route_info", json=payload)
                body = response.get_json(silent=True)
            except Exception:
                output("경로 조회 실패: ROUTE_CHECK_FAILED")
                return 3
            arrivals_ok = _check_arrivals(application, output) if public else True
            if response.status_code != 200:
                output(f"경로 조회 실패: {_error_code(body, response.status_code)}")
                return 3
            journeys = body.get("journeys") if isinstance(body, dict) else None
            if not isinstance(journeys, list):
                output("경로 조회 실패: ROUTE_RESPONSE_INVALID")
                return 3
            if not journeys:
                output("경로 조회 응답은 정상이나 검색 범위 내 후보 없음 (NO_JOURNEYS). 경로 성공 검증은 미완료입니다.")
                return 3
            output(f"경로 조회: CSV 기반 후보 {len(journeys)}개 확인 (도보 조회 포함).")
            if not arrivals_ok:
                output("부분 성공: 장소 검색·도보·CSV 후보는 확인했지만 공공데이터 도착 API 점검은 실패했습니다.")
                return 4
            output("점검 완료. CSV의 상·하행·회차·시간표 미확인과 지도 화면 확인은 별도입니다.")
            return 0
    finally:
        logger.disabled = was_disabled


def main(argv=None):
    parser = argparse.ArgumentParser(description="키를 공개하지 않는 백엔드 로컬/실제 API 점검")
    parser.add_argument("--live", action="store_true", help="외부 API 실제 호출 허용 (사용량 발생)")
    arguments = parser.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            application = importlib.import_module("app")
    except Exception:
        print("백엔드를 불러오지 못했습니다 (BACKEND_IMPORT_FAILED). 의존성 설치와 .env 형식을 확인하세요.")
        return 2
    return run_checks(application, live=arguments.live)


if __name__ == "__main__":
    raise SystemExit(main())
