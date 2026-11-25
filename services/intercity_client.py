"""TAGO 시외버스 전용 전송·페이지 처리. 공급자 원문과 인증키를 오류에 넣지 않는다."""

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import RLock
from time import monotonic
from urllib.parse import unquote
from xml.etree import ElementTree

import requests

KST = timezone(timedelta(hours=9))


class IntercityError(Exception):
    def __init__(self, code, message, status_code=502):
        super().__init__(message)
        self.code, self.status_code = code, status_code


class IntercityClient:
    BASE_URL = "https://apis.data.go.kr/1613000/SuburbsBusInfo"
    TERMINALS = "GetSuberbsBusTrminlList"
    TIMETABLE = "GetStrtpntAlocFndSuberbsBusInfo"
    PAGE_SIZE = 100
    MAX_PAGES = 100
    MAX_CACHE = 256

    def __init__(self, service_key, requester=None, clock=None, now=None):
        # requests performs URL encoding once. unquote preserves literal '+'.
        self._service_key = unquote(str(service_key or "").strip())
        self.requester = requester or requests.get
        self.clock = clock or monotonic
        self.now = now or (lambda: datetime.now(KST))
        self._cache = OrderedDict()
        self._lock = RLock()

    @property
    def configured(self):
        return bool(self._service_key)

    @staticmethod
    def _error_for_code(code):
        if code in {"20", "30", "31", "32", "401", "403"}:
            return IntercityError(
                "INTERCITY_PROVIDER_AUTH",
                "시외버스 정보 연결 권한을 확인해야 합니다. 공식 예매처에서 확인해 주세요.", 503,
            )
        if code in {"22", "23", "429"}:
            return IntercityError(
                "INTERCITY_PROVIDER_QUOTA", "시간표 제공 서비스의 조회 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.", 503,
            )
        return IntercityError("INTERCITY_PROVIDER_ERROR", "시간표 제공 서비스가 정상 응답하지 않았습니다.")

    @staticmethod
    def _invalid():
        return IntercityError("INTERCITY_PROVIDER_RESPONSE", "시간표 제공 서비스의 응답을 확인할 수 없습니다.")

    def _payload(self, response):
        try:
            payload = response.json()
        except (ValueError, TypeError):
            try:
                root = ElementTree.fromstring(response.text)
            except (ElementTree.ParseError, TypeError, ValueError):
                raise self._invalid() from None
            # data.go.kr authorization errors use XML even when _type=json.
            codes = {node.tag.split("}")[-1]: (node.text or "").strip()
                     for node in root.iter()
                     if node.tag.split("}")[-1] in {"returnReasonCode", "resultCode"}}
            code = codes.get("returnReasonCode") or codes.get("resultCode")
            if code and code not in {"00", "0", "03", "3"}:
                raise self._error_for_code(code) from None
            if code in {"03", "3"}:
                return [], 0
            # Normal data is requested as JSON. Unexpected XML isn't an empty timetable.
            raise self._invalid() from None
        if not isinstance(payload, dict) or not isinstance(payload.get("response"), dict):
            raise self._invalid()
        envelope = payload["response"]
        header = envelope.get("header")
        if not isinstance(header, dict):
            raise self._invalid()
        code = str(header.get("resultCode", ""))
        if code in {"03", "3"}:
            return [], 0
        if code not in {"00", "0"}:
            raise self._error_for_code(code)
        body = envelope.get("body")
        if not isinstance(body, dict):
            raise self._invalid()
        items = body.get("items")
        if items in (None, ""):
            rows = []
        elif isinstance(items, dict):
            rows = items.get("item")
            if rows in (None, ""):
                rows = []
            if isinstance(rows, dict):
                rows = [rows]
        else:
            raise self._invalid()
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise self._invalid()
        total = body.get("totalCount")
        if total is not None:
            raw_total = str(total)
            if isinstance(total, bool) or not raw_total.isascii() or not raw_total.isdigit() or len(raw_total) > 9:
                raise self._invalid()
            total = int(total)
        return rows, total

    def _page(self, operation, parameters, page):
        try:
            response = self.requester(
                f"{self.BASE_URL}/{operation}",
                params={"serviceKey": self._service_key, "_type": "json",
                        "pageNo": page, "numOfRows": self.PAGE_SIZE, **parameters},
                timeout=(3, 8), allow_redirects=False,
            )
        except requests.Timeout:
            raise IntercityError(
                "INTERCITY_PROVIDER_TIMEOUT", "시간표 응답이 늦습니다. 잠시 후 다시 조회해 주세요.", 504,
            ) from None
        except requests.RequestException:
            raise IntercityError(
                "INTERCITY_PROVIDER_NETWORK", "시간표 제공 서비스에 연결하지 못했습니다.",
            ) from None
        if response.status_code in (401, 403, 429):
            raise self._error_for_code(str(response.status_code))
        if response.status_code != 200:
            raise IntercityError("INTERCITY_PROVIDER_HTTP", "시간표 제공 서비스가 정상 응답하지 않았습니다.")
        return self._payload(response)

    def read(self, operation, parameters, ttl):
        if not self.configured:
            raise IntercityError(
                "INTERCITY_KEY_MISSING", "시외버스 시간표 연결을 준비 중입니다. 공식 예매처에서 확인해 주세요.", 503,
            )
        if operation not in {self.TERMINALS, self.TIMETABLE}:
            raise ValueError("Unknown intercity operation")
        key = (operation, tuple(sorted(parameters.items())))
        with self._lock:
            cached = self._cache.get(key)
            if cached and self.clock() - cached[0] < ttl:
                self._cache.move_to_end(key)
                result = deepcopy(cached[1])
                result["from_cache"] = True
                return result
        rows, previous, expected_total = [], None, None
        started = self.clock()
        for page in range(1, self.MAX_PAGES + 1):
            if self.clock() - started > 25:
                raise IntercityError("INTERCITY_PROVIDER_TIMEOUT", "전체 시간표 조회가 지연되고 있습니다.", 504)
            batch, total = self._page(operation, parameters, page)
            if page == 1:
                expected_total = total
            elif total != expected_total:
                raise IntercityError("INTERCITY_INCOMPLETE", "조회 중 시간표 목록이 변경됐습니다. 다시 조회해 주세요.")
            if batch and batch == previous:
                raise IntercityError("INTERCITY_INCOMPLETE", "시간표 일부가 반복되어 전체 목록을 확인하지 못했습니다.")
            rows.extend(batch)
            if total is not None and len(rows) >= total:
                if len(rows) != total:
                    raise self._invalid()
                break
            if not batch or (total is None and len(batch) < self.PAGE_SIZE):
                if total is not None and len(rows) < total:
                    raise IntercityError("INTERCITY_INCOMPLETE", "시간표 목록 일부가 누락됐습니다. 다시 조회해 주세요.")
                break
            previous = batch
        else:
            raise IntercityError("INTERCITY_INCOMPLETE", "시간표 전체 목록이 조회 범위를 초과했습니다.")
        result = {"rows": rows, "fetched_at": self.now().astimezone(KST).isoformat(), "from_cache": False}
        with self._lock:
            self._cache[key] = (self.clock(), deepcopy(result))
            self._cache.move_to_end(key)
            while len(self._cache) > self.MAX_CACHE:
                self._cache.popitem(last=False)
        return result
