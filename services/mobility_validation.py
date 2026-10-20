"""택시·동승 API의 입력 경계. 오류에 입력 원문을 담지 않는다."""

from datetime import datetime, timezone
from math import isfinite
import re
import unicodedata
from uuid import UUID


class MobilityInputError(ValueError):
    status_code = 400

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _invalid(field, message):
    code = re.sub(r"[^A-Z0-9_]+", "_", str(field).upper()).strip("_")
    return MobilityInputError("INVALID_" + (code or "REQUEST"), message)


def require_json_object(value):
    if not isinstance(value, dict):
        raise MobilityInputError("INVALID_REQUEST", "요청 본문은 JSON 객체여야 합니다.")
    return value


def clean_text(value, field, max_length, default=None):
    if value is None and default is not None:
        value = default
    if not isinstance(value, str):
        raise _invalid(field, "문자열 입력을 확인해 주세요.")
    # 제어 문자·줄바꿈을 정규화로 숨기지 않고 입력 단계에서 거절한다.
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise _invalid(field, "제어 문자를 제외한 일반 문자를 입력해 주세요.")
    value = unicodedata.normalize("NFC", value).strip()
    if not value or len(value) > max_length:
        raise _invalid(field, f"문자열은 1~{max_length}자로 입력해 주세요.")
    return value


def coordinate(value, lower, upper, field):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise _invalid(field, "위도·경도 입력을 확인해 주세요.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise _invalid(field, "위도·경도 입력을 확인해 주세요.") from None
    if not isfinite(number) or not lower <= number <= upper:
        raise _invalid(field, "위도·경도 범위를 확인해 주세요.")
    return number


def named_point(value, field):
    if not isinstance(value, dict):
        raise _invalid(field, "장소 이름과 좌표를 함께 선택해 주세요.")
    return {
        "name": clean_text(value.get("name"), field + "_name", 100),
        "lat": coordinate(value.get("lat"), -90, 90, field + "_lat"),
        "lon": coordinate(value.get("lon"), -180, 180, field + "_lon"),
    }


def aware_datetime(value, field):
    if not isinstance(value, str) or len(value) > 50:
        raise _invalid(field, "시간대가 포함된 ISO 8601 시각을 입력해 주세요.")
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise _invalid(field, "시간대가 포함된 ISO 8601 시각을 입력해 주세요.") from None


def uuid_text(value, field):
    if not isinstance(value, str) or len(value) > 50:
        raise _invalid(field, "요청 식별자 형식을 확인해 주세요.")
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise _invalid(field, "요청 식별자 형식을 확인해 주세요.") from None
