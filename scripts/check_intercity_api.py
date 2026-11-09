"""시외버스 키 설정 또는 명시적으로 요청한 실제 연결을 점검한다.

기본 실행은 설정 존재 여부만 확인한다. --live에서만 TAGO를 호출하며,
Flask 서버를 시작하지 않는다. 키·공급자 오류 원문·요청 URL은 출력하지 않는다.
"""

import argparse
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ERROR_CODES = {
    "INTERCITY_KEY_MISSING", "INTERCITY_PROVIDER_AUTH", "INTERCITY_PROVIDER_QUOTA",
    "INTERCITY_PROVIDER_TIMEOUT", "INTERCITY_PROVIDER_NETWORK", "INTERCITY_PROVIDER_HTTP",
    "INTERCITY_PROVIDER_ERROR", "INTERCITY_PROVIDER_RESPONSE", "INTERCITY_INCOMPLETE",
    "INTERCITY_TERMINAL_RESPONSE", "INTERCITY_TERMINALS_EMPTY", "INTERCITY_SCHEDULE_RESPONSE",
    "INVALID_INTERCITY_QUERY", "INVALID_INTERCITY_TERMINAL", "SAME_INTERCITY_TERMINAL",
    "UNKNOWN_INTERCITY_TERMINAL", "INVALID_INTERCITY_DATE", "INTERCITY_TODAY_ONLY",
}


class _ArgumentError(ValueError):
    pass


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # 잘못 전달한 인자에 비밀값이 포함됐더라도 원문을 되풀이하지 않는다.
        raise _ArgumentError() from None


def _parser():
    parser = _SafeParser(description="시외버스 설정·오늘 배차 연결 확인", allow_abbrev=False)
    parser.add_argument("--live", action="store_true", help="TAGO 실제 조회 실행")
    parser.add_argument("--query", default="춘천", metavar="NAME", help="터미널 이름·지역 검색 (기본: 춘천)")
    parser.add_argument("--departure-id", metavar="ID", help="실제 조회된 출발 터미널 ID")
    parser.add_argument("--arrival-id", metavar="ID", help="실제 조회된 도착 터미널 ID")
    return parser


def _emit(output, payload):
    output(json.dumps(payload, ensure_ascii=False, allow_nan=False))


def _fail(output, code, exit_code, http_status=None):
    result = {"status": "error", "code": code}
    if http_status in (400, 502, 503, 504):
        result["http_status"] = http_status
    _emit(output, result)
    return exit_code


def main(argv=None, *, settings_loader=None, service_factory=None, output=print):
    try:
        arguments = _parser().parse_args(argv)
    except _ArgumentError:
        return _fail(output, "INTERCITY_ARGUMENTS_INVALID", 2)
    supplied = (arguments.departure_id is not None, arguments.arrival_id is not None)
    if any(supplied) and not all(supplied):
        return _fail(output, "INTERCITY_BOTH_TERMINALS_REQUIRED", 2)
    if any(supplied) and not arguments.live:
        return _fail(output, "INTERCITY_LIVE_REQUIRED", 2)

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            if settings_loader is None:
                from services.settings import load_settings
                settings_loader = load_settings
            settings = settings_loader(PROJECT_ROOT)
    except Exception:
        return _fail(output, "INTERCITY_CONFIGURATION_INVALID", 2)

    if not arguments.live:
        configured = bool(settings.intercity_data_key)
        _emit(output, {"intercity_data_key_configured": configured})
        return 0 if configured else 2

    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            if service_factory is None:
                from services.intercity_service import IntercityService
                service_factory = IntercityService
            service = service_factory(settings.intercity_data_key)
            if all(supplied):
                result = service.timetable(arguments.departure_id, arguments.arrival_id)
                summary = {
                    "mode": "timetable", "status": result["status"], "date": result["date"],
                    "departure": result["departure"], "arrival": result["arrival"],
                    "trip_count": len(result["trips"]), "preview_trips": result["trips"][:5],
                    "preview_limit": 5, "fetched_at": result["fetched_at"],
                    "warnings": result["warnings"], "booking_links": result["booking_links"],
                }
            else:
                result = service.terminals(arguments.query)
                summary = {
                    "mode": "terminals", "status": result["status"], "today": result["today"],
                    "matched_count": len(result["terminals"]), "terminals": result["terminals"],
                    "fetched_at": result["fetched_at"],
                }
    except Exception as error:
        # IntercityError의 알려진 코드만 통과시킨다. 예외 메시지는 출력하지 않는다.
        code = getattr(error, "code", None)
        code = code if isinstance(code, str) and code in ERROR_CODES else "INTERCITY_CHECK_FAILED"
        status = getattr(error, "status_code", None)
        exit_code = 2 if code == "INTERCITY_KEY_MISSING" or status == 400 else 3
        return _fail(output, code, exit_code, status)
    _emit(output, summary)
    return 4 if summary["status"] == "partial" else 0


if __name__ == "__main__":
    raise SystemExit(main())
