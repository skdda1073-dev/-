"""날씨·대기질의 안전한 HTTP 응답 처리와 작은 서버 공통 캐시."""

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import isfinite
from threading import RLock
from time import monotonic
from urllib.parse import unquote
import xml.etree.ElementTree as ET

import requests


KST = timezone(timedelta(hours=9), "KST")


def kst_now(value=None):
    current = value if value is not None else datetime.now(KST)
    if current.tzinfo is None or current.utcoffset() is None:
        current = current.replace(tzinfo=KST)
    return current.astimezone(KST)


def number(value, minimum, maximum):
    """문자 결측·bool·비유한 수와 항목별 허용 범위 밖의 값을 거른다."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        parsed = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if not isfinite(parsed) or not minimum <= parsed <= maximum:
        return None
    return int(parsed) if parsed.is_integer() else parsed


class EnvironmentProviderError(Exception):
    """인증키·요청 URL·공급자 원문을 보관하거나 출력하지 않는다."""

    def __init__(self, code):
        self.code = code
        super().__init__("환경 정보를 확인하지 못했어요. 잠시 후 다시 확인해 주세요.")


class ProviderCache:
    """동일 클라이언트 요청을 직렬화해 초기 다중 접속의 중복 호출을 막는다."""

    def __init__(self, *, clock=None, ttl=900, failure_ttl=60, limit=8):
        self.clock = clock or monotonic
        self.ttl = ttl
        self.failure_ttl = failure_ttl
        self.limit = limit
        self.entries = OrderedDict()
        self.lock = RLock()

    def get(self, key, loader):
        with self.lock:
            current = self.clock()
            entry = self.entries.get(key)
            if entry is not None and 0 <= current - entry[0] < entry[1]:
                self.entries.move_to_end(key)
                if entry[3] is not None:
                    raise EnvironmentProviderError(entry[3]) from None
                return deepcopy(entry[2])
            if entry is not None:
                del self.entries[key]
            try:
                result = loader()
            except EnvironmentProviderError as error:
                self.entries[key] = (self.clock(), self.failure_ttl, None, error.code)
                self._trim()
                raise
            self.entries[key] = (self.clock(), self.ttl, deepcopy(result), None)
            self._trim()
            return deepcopy(result)

    def _trim(self):
        while len(self.entries) > self.limit:
            self.entries.popitem(last=False)


class EnvironmentHTTP:
    """고정 공공데이터 URL만 호출하는 클라이언트 공통 부분."""

    def __init__(self, key, prefix, *, requester=None, now=None, clock=None,
                 timeout_seconds=5, cache_ttl_seconds=900):
        self._key = unquote(str(key or "").strip())
        self.prefix = prefix
        self.requester = requester or requests.get
        self.now = now or (lambda: datetime.now(KST))
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (float, int))
                or not isfinite(timeout_seconds) or not 0 < timeout_seconds <= 15):
            raise ValueError("환경 정보 응답 제한 시간을 확인해 주세요.")
        self.timeout = (min(3, timeout_seconds), timeout_seconds)
        if (isinstance(cache_ttl_seconds, bool) or not isinstance(cache_ttl_seconds, (float, int))
                or not 60 <= cache_ttl_seconds <= 3600):
            raise ValueError("환경 정보 캐시 시간을 확인해 주세요.")
        self.cache = ProviderCache(clock=clock, ttl=cache_ttl_seconds)

    def __repr__(self):
        return self.__class__.__name__ + "(configured=" + str(bool(self._key)) + ")"

    def error(self, suffix):
        return EnvironmentProviderError(self.prefix + "_" + suffix)

    def _provider_code(self, code):
        text = str(code or "").strip().upper()
        if text in {"0", "00"}:
            return
        if text in {"20", "21", "30", "31", "32", "SERVICE_ACCESS_DENIED_ERROR",
                    "SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "DEADLINE_HAS_EXPIRED_ERROR",
                    "PERMISSION_DENIED", "SERVICE_KEY_IS_NULL"}:
            raise self.error("PROVIDER_AUTH")
        if text in {"22", "23", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR",
                    "LIMITED_NUMBER_OF_SERVICE_REQUESTS_PER_SECOND_EXCEEDS_ERROR"}:
            raise self.error("PROVIDER_QUOTA")
        if text in {"03", "3", "NO_DATA", "NO_DATA_ERROR"}:
            raise self.error("NO_DATA")
        raise self.error("PROVIDER_RESPONSE")

    def get_body(self, url, params):
        if not self._key:
            raise self.error("KEY_MISSING")
        query = dict(params, serviceKey=self._key)
        try:
            response = self.requester(url, params=query, headers={"Accept": "application/json"},
                                      timeout=self.timeout, allow_redirects=False)
        except requests.Timeout:
            raise self.error("PROVIDER_TIMEOUT") from None
        except requests.RequestException:
            raise self.error("PROVIDER_NETWORK") from None
        if response.status_code in (401, 403):
            raise self.error("PROVIDER_AUTH")
        if response.status_code == 429:
            raise self.error("PROVIDER_QUOTA")
        if response.status_code != 200:
            raise self.error("PROVIDER_HTTP")
        content = getattr(response, "content", None)
        if isinstance(content, (bytes, bytearray, str)) and len(content) > 2 * 1024 * 1024:
            raise self.error("PROVIDER_RESPONSE")
        try:
            payload = response.json()
        except (TypeError, ValueError):
            # 게이트웨이 인증 오류는 JSON 요청에도 XML로 돌아올 수 있다.
            text = getattr(response, "text", "")
            if isinstance(text, str) and len(text) <= 65536:
                try:
                    root = ET.fromstring(text)
                    code = root.findtext(".//returnReasonCode") or root.findtext(".//resultCode")
                    if code is not None:
                        self._provider_code(code)
                except ET.ParseError:
                    pass
            raise self.error("PROVIDER_RESPONSE") from None
        try:
            outer = payload["response"]
            header = outer["header"]
            self._provider_code(header["resultCode"])
            body = outer["body"]
            if not isinstance(body, dict):
                raise TypeError("body")
            return body
        except (KeyError, TypeError):
            raise self.error("PROVIDER_RESPONSE") from None

    def items(self, body):
        values = body.get("items")
        if isinstance(values, dict):
            values = values.get("item")
        if isinstance(values, dict):
            values = [values]
        if values in (None, ""):
            return []
        if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
            raise self.error("PROVIDER_RESPONSE")
        return values
