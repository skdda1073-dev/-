"""TMAP 택시 견적 설정 또는 명시한 좌표의 실제 연결을 점검한다.

기본 실행은 키 존재 여부만 출력하며 외부 요청을 보내지 않는다.
--live는 제공한 좌표로 기존 TaxiService를 사용한다. 택시 호출·예약·결제가
아니며 API 조회 사용량이 발생할 수 있다. 비밀키와 공급자 원문은 출력하지 않는다.
"""

import argparse
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from math import isfinite
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULT_CODES = {
    "ok": "TAXI_OK",
    "partial": "TAXI_FARE_UNAVAILABLE",
    "no_travel_required": "TAXI_NOT_NEEDED",
}
ERROR_CODES = {
    "TAXI_KEY_MISSING", "TAXI_PROVIDER_AUTH", "TAXI_PROVIDER_QUOTA",
    "TAXI_PROVIDER_TIMEOUT", "TAXI_PROVIDER_NETWORK", "TAXI_PROVIDER_HTTP",
    "TAXI_PROVIDER_RESPONSE",
}


class _ArgumentError(ValueError):
    pass


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse의 기본 오류는 잘못 입력한 인자 원문을 그대로 출력한다.
        raise _ArgumentError() from None


def _parser():
    parser = _SafeParser(
        description="택시 예상 시간·거리·요금 점검. 택시 호출·예약·결제는 하지 않습니다.",
        epilog="기본 실행: 키 존재 여부만 확인. --live: 좌표 4개 필수, API 조회 사용량 발생 가능.",
        allow_abbrev=False,
    )
    parser.add_argument("--live", action="store_true", help="명시한 좌표로 TMAP 견적 연결 확인")
    parser.add_argument("--origin-lat", metavar="LAT", help="출발 위도 (-90~90)")
    parser.add_argument("--origin-lon", metavar="LON", help="출발 경도 (-180~180)")
    parser.add_argument("--destination-lat", metavar="LAT", help="도착 위도 (-90~90)")
    parser.add_argument("--destination-lon", metavar="LON", help="도착 경도 (-180~180)")
    return parser


def _emit(output, payload):
    output(json.dumps(payload, ensure_ascii=False, allow_nan=False))


def _fail(output, code, exit_code):
    _emit(output, {"status": "error", "code": code})
    return exit_code


def _points(arguments):
    values = (
        arguments.origin_lat, arguments.origin_lon,
        arguments.destination_lat, arguments.destination_lon,
    )
    if not arguments.live:
        if any(value is not None for value in values):
            raise _ArgumentError("TAXI_LIVE_REQUIRED")
        return None
    if any(value is None for value in values):
        raise _ArgumentError("TAXI_COORDINATES_REQUIRED")
    coordinates = []
    for value, limit in zip(values, (90, 180, 90, 180)):
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            raise _ArgumentError("TAXI_COORDINATES_INVALID") from None
        if not isfinite(number) or not -limit <= number <= limit:
            raise _ArgumentError("TAXI_COORDINATES_INVALID")
        coordinates.append(number)
    return (
        {"name": "직접 지정한 출발지", "lat": coordinates[0], "lon": coordinates[1]},
        {"name": "직접 지정한 도착지", "lat": coordinates[2], "lon": coordinates[3]},
    )


def _summary(estimate):
    """허용한 상태와 수치만 통과시킨다. 이름·좌표·원문 메시지는 출력하지 않는다."""
    if not isinstance(estimate, dict):
        raise ValueError()
    status = estimate.get("status")
    if not isinstance(status, str) or status not in RESULT_CODES:
        raise ValueError()
    code = RESULT_CODES[status]
    if estimate.get("code") != code or not isinstance(estimate.get("fare"), dict):
        raise ValueError()
    duration = estimate.get("ride_duration_seconds")
    distance = estimate.get("distance_m")
    fare = estimate["fare"].get("amount_krw")
    for value in (duration, distance):
        if type(value) not in (int, float) or not isfinite(value) or value < 0:
            raise ValueError()
    if fare is not None and (type(fare) is not int or not 0 < fare <= 2 ** 53 - 1):
        raise ValueError()
    if (status == "ok") != (fare is not None):
        raise ValueError()
    return {
        "status": status, "code": code,
        "ride_duration_seconds": duration, "distance_m": distance,
        "fare_amount_krw": fare,
    }


def main(argv=None, *, settings_loader=None, service_factory=None, output=print):
    """기본적으로 기존 설정·서비스를 사용하며 검증에서는 의존성을 주입할 수 있다."""
    try:
        arguments = _parser().parse_args(argv)
    except _ArgumentError:
        return _fail(output, "TAXI_ARGUMENTS_INVALID", 2)
    try:
        points = _points(arguments)
    except _ArgumentError as error:
        return _fail(output, error.args[0], 2)

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            if settings_loader is None:
                from services.settings import load_settings
                settings_loader = load_settings
            settings = settings_loader(PROJECT_ROOT)
    except Exception:
        return _fail(output, "TAXI_CONFIGURATION_INVALID", 2)

    if not arguments.live:
        configured = bool(settings.tmap_app_key)
        _emit(output, {"tmap_app_key_configured": configured})
        return 0 if configured else 2

    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            if service_factory is None:
                from services.taxi_service import TaxiService
                service_factory = TaxiService
            service = service_factory(
                settings.tmap_app_key,
                cache_ttl_seconds=settings.taxi_cache_ttl_seconds,
                max_cache_entries=settings.taxi_cache_max_entries,
            )
            estimate = service.estimate(*points)
    except Exception as error:
        code = getattr(error, "code", None)
        code = code if isinstance(code, str) and code in ERROR_CODES else "TAXI_CHECK_FAILED"
        return _fail(output, code, 2 if code == "TAXI_KEY_MISSING" else 3)
    try:
        summary = _summary(estimate)
    except (TypeError, ValueError, OverflowError):
        return _fail(output, "TAXI_PROVIDER_RESPONSE", 3)
    _emit(output, summary)
    return 4 if summary["status"] == "partial" else 0


if __name__ == "__main__":
    raise SystemExit(main())
