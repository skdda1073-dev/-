"""통학 준비 안내의 설정 또는 명시적으로 요청한 외부 API 연결을 점검한다.

기본 실행은 키 존재 여부만 출력한다. --live에서만 기상청·에어코리아를
조회한다. 키, 요청 URL, 공급자 원문, 예외 원문은 출력하지 않는다.
Flask 서버를 시작하거나 .env를 수정하지 않는다.
"""

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from io import StringIO
import json
from math import isfinite
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROVIDER_STATUSES = {"ok", "partial", "unavailable", "stale"}
RESULT_STATUSES = {"ok", "partial", "unavailable"}
GRADES = {"좋음", "보통", "나쁨", "매우 나쁨"}
ALERT_TYPES = {"rain", "snow", "cold_wind", "cold", "wind", "dust"}
ERROR_SUFFIXES = {
    "KEY_MISSING", "PROVIDER_AUTH", "PROVIDER_QUOTA", "PROVIDER_TIMEOUT",
    "PROVIDER_NETWORK", "PROVIDER_HTTP", "PROVIDER_RESPONSE", "NO_DATA",
    "INCOMPLETE", "STALE", "PARTIAL", "MEASUREMENT_UNAVAILABLE", "STATION_INVALID",
}
ERROR_CODES = {
    prefix + "_" + suffix
    for prefix in ("WEATHER", "AIR_QUALITY")
    for suffix in ERROR_SUFFIXES
}


class _ArgumentError(ValueError):
    pass


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # 잘못 입력한 옵션에 비밀값이 있어도 원문을 되풀이하지 않는다.
        raise _ArgumentError() from None


def _parser():
    parser = _SafeParser(
        description="통학 준비 안내의 설정·기상청·에어코리아 연결 확인",
        epilog="기본 실행은 외부 요청 없음. --live에서만 API 조회 사용량이 발생합니다.",
        allow_abbrev=False,
    )
    parser.add_argument("--live", action="store_true", help="외부 API 실제 조회")
    parser.add_argument("--campus", choices=("hallym", "knu"), default="hallym",
                        help="조회할 캠퍼스 (기본: hallym)")
    return parser


def _emit(output, payload):
    output(json.dumps(payload, ensure_ascii=False, allow_nan=False))


def _fail(output, code, exit_code):
    _emit(output, {"status": "error", "code": code})
    return exit_code


def _timestamp(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("invalid timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone required")
    return parsed.isoformat()


def _number(value, lower, upper):
    if value is None:
        return None
    if type(value) not in (int, float) or not isfinite(value) or not lower <= value <= upper:
        raise ValueError("invalid metric")
    return value


def _component(value):
    if not isinstance(value, dict) or value.get("status") not in PROVIDER_STATUSES:
        raise ValueError("invalid provider status")
    code = value.get("code")
    if code is not None and code not in ERROR_CODES:
        code = "UNKNOWN_PROVIDER_ERROR"
    return {"status": value["status"], "code": code}


def _pollutant(value):
    if not isinstance(value, dict):
        raise ValueError("invalid pollutant")
    grade = value.get("grade")
    if grade is not None and grade not in GRADES:
        raise ValueError("invalid grade")
    return {"value": _number(value.get("value"), 0, 10000), "grade": grade}


def _summary(result, campus):
    """허용한 상태·시각·수치·안내 종류만 출력한다. 자유 형식 문구는 제외한다."""
    if not isinstance(result, dict) or result.get("status") not in RESULT_STATUSES:
        raise ValueError("invalid response")
    weather = result.get("weather")
    air = result.get("air")
    weather_summary = _component(weather)
    air_summary = _component(air)
    weather_summary.update({
        "issued_at": _timestamp(weather.get("issued_at")),
        "forecast_at": _timestamp(weather.get("forecast_at")),
        "temperature_c": _number(weather.get("temperature_c"), -100, 100),
        "wind_speed_ms": _number(weather.get("wind_speed_ms"), 0, 150),
        "rain_probability": _number(weather.get("rain_probability"), 0, 100),
    })
    air_summary.update({
        "observed_at": _timestamp(air.get("observed_at")),
        "pm10": _pollutant(air.get("pm10")),
        "pm25": _pollutant(air.get("pm25")),
    })
    alerts = result.get("alerts")
    if not isinstance(alerts, list):
        raise ValueError("invalid alerts")
    alert_types = []
    for item in alerts:
        if not isinstance(item, dict) or item.get("id") not in ALERT_TYPES:
            raise ValueError("invalid alert")
        if item["id"] not in alert_types:
            alert_types.append(item["id"])
    return {
        "status": result["status"], "campus": campus,
        "weather": weather_summary, "air": air_summary,
        "alert_types": alert_types,
    }


def main(argv=None, *, settings_loader=None, service_factory=None, output=print):
    try:
        arguments = _parser().parse_args(argv)
    except _ArgumentError:
        return _fail(output, "COMMUTE_ADVICE_ARGUMENTS_INVALID", 2)

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            if settings_loader is None:
                from services.settings import load_settings
                settings_loader = load_settings
            settings = settings_loader(PROJECT_ROOT)
            configured = {
                "weather_data_key_configured": bool(settings.weather_data_key),
                "airkorea_data_key_configured": bool(settings.airkorea_data_key),
            }
    except Exception:
        return _fail(output, "COMMUTE_ADVICE_CONFIGURATION_INVALID", 2)

    if not arguments.live:
        _emit(output, configured)
        return 0 if all(configured.values()) else 2

    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            if service_factory is None:
                from services.commute_advice_service import CommuteAdviceService
                service_factory = CommuteAdviceService
            service = service_factory(
                settings.weather_data_key, settings.airkorea_data_key,
                station_name=settings.airkorea_station_name,
            )
            result = service.advice(campus=arguments.campus)
    except Exception:
        return _fail(output, "COMMUTE_ADVICE_CHECK_FAILED", 3)

    try:
        summary = _summary(result, arguments.campus)
    except (TypeError, ValueError, OverflowError, KeyError):
        return _fail(output, "COMMUTE_ADVICE_RESPONSE_INVALID", 3)
    _emit(output, summary)
    if not any(configured.values()):
        return 2
    return {"ok": 0, "partial": 4, "unavailable": 3}[summary["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
