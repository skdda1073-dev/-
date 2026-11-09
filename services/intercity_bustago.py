"""버스타고 공개 예매 화면과 같은 요청으로 날짜별 시간표만 조회한다.

터미널 선택부터 시간표까지 같은 제공처 ID를 유지한다. 좌석 선택·예약 요청은
하지 않으며, 시간·요금 해석은 호출 서비스에서 담당한다.
"""

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime
import re
from threading import RLock
from time import monotonic

import requests

from services.intercity_client import IntercityError, KST
from services.intercity_destinations import SOURCE_ID


class BustagoTimetableClient:
    URL = "https://www.bustago.or.kr/newweb/kr/ticket/ticketListJson3.do"
    PAGE_URL = "https://www.bustago.or.kr/newweb/kr/ticket/ticket.do"
    CACHE_TTL = 60
    MAX_CACHE = 128
    MAX_ROWS = 10_000
    # These are the observed public page's successful response fields. An error
    # envelope must never be converted into an empty timetable.
    RESPONSE_FIELDS = frozenset({"ticketSingleList", "ticketDoubleList", "language"})

    def __init__(self, requester=None, clock=None, now=None):
        self.requester = requester or requests.post
        self.clock = clock or monotonic
        self.now = now or (lambda: datetime.now(KST))
        self._cache = OrderedDict()
        self._lock = RLock()

    @staticmethod
    def _invalid():
        return IntercityError(
            "INTERCITY_BUSTAGO_RESPONSE",
            "예매처의 시간표 응답을 확인할 수 없어요. 공식 예매처에서 확인해 주세요.",
        )

    @staticmethod
    def _validate_request(departure_id, arrival_id, query_date):
        for identifier in (departure_id, arrival_id):
            if not isinstance(identifier, str) or not SOURCE_ID.fullmatch(identifier):
                raise IntercityError(
                    "INVALID_INTERCITY_TERMINAL", "목록에서 출발·도착 터미널을 선택해 주세요.", 400,
                )
        if departure_id == arrival_id:
            raise IntercityError(
                "SAME_INTERCITY_TERMINAL", "출발·도착 터미널을 다르게 선택해 주세요.", 400,
            )
        try:
            if not isinstance(query_date, str) or not re.fullmatch(r"[0-9]{8}", query_date):
                raise ValueError
            datetime.strptime(query_date, "%Y%m%d")
        except ValueError:
            raise IntercityError(
                "INVALID_INTERCITY_DATE", "조회 날짜 형식을 확인해 주세요.", 400,
            ) from None

    @classmethod
    def _rows(cls, payload, departure_id, arrival_id, query_date):
        if (not isinstance(payload, dict)
                or set(payload) - cls.RESPONSE_FIELDS
                or not isinstance(payload.get("ticketSingleList"), list)):
            raise cls._invalid()
        if ("ticketDoubleList" in payload and payload["ticketDoubleList"] is not None
                and not isinstance(payload["ticketDoubleList"], list)):
            raise cls._invalid()
        if "language" in payload and not isinstance(payload["language"], str):
            raise cls._invalid()
        rows = payload["ticketSingleList"]
        if len(rows) > cls.MAX_ROWS:
            raise cls._invalid()
        for row in rows:
            if (not isinstance(row, dict)
                    or row.get("sterCode") != departure_id
                    or row.get("eterCode") != arrival_id
                    or row.get("DEP_DATE") != query_date
                    or ("BUS_ORDER_DT" in row and row["BUS_ORDER_DT"] != query_date)):
                raise cls._invalid()
        return deepcopy(rows)

    def read(self, departure_id, arrival_id, query_date):
        """Return raw timetable rows for a validated source-ID pair and date.

        Date availability and terminal catalogue membership belong to the service.
        Cached results are isolated by both directions and date, including empties.
        """
        self._validate_request(departure_id, arrival_id, query_date)
        key = (departure_id, arrival_id, query_date)
        with self._lock:
            cached = self._cache.get(key)
            if cached and 0 <= self.clock() - cached[0] < self.CACHE_TTL:
                self._cache.move_to_end(key)
                result = deepcopy(cached[1])
                result["from_cache"] = True
                return result
        try:
            response = self.requester(
                self.URL,
                data={"startType": "S", "orderDate": query_date, "orderBackDate": "",
                      "depTerId": departure_id, "arrTerId": arrival_id,
                      "depTime": "0000", "arrTime": "0000", "goBusGrade": "", "goBackBusGrade": ""},
                headers={"Referer": self.PAGE_URL, "X-Requested-With": "XMLHttpRequest"},
                timeout=(10, 20), allow_redirects=False,
            )
        except requests.Timeout:
            raise IntercityError(
                "INTERCITY_BUSTAGO_TIMEOUT",
                "시간표를 불러오는 데 시간이 걸려요. 잠시 후 다시 조회해 주세요.", 504,
            ) from None
        except requests.RequestException:
            raise IntercityError(
                "INTERCITY_BUSTAGO_NETWORK",
                "시간표 제공처에 연결하지 못했어요. 잠시 후 다시 조회해 주세요.",
            ) from None
        if response.status_code != 200:
            raise IntercityError(
                "INTERCITY_BUSTAGO_UNAVAILABLE",
                "시간표 제공처가 정상 응답하지 않았어요. 공식 예매처에서 확인해 주세요.",
            )
        try:
            payload = response.json()
        except (ValueError, TypeError):
            raise self._invalid() from None
        result = {"rows": self._rows(payload, departure_id, arrival_id, query_date),
                  "fetched_at": self.now().astimezone(KST).isoformat(), "from_cache": False}
        with self._lock:
            self._cache[key] = (self.clock(), deepcopy(result))
            self._cache.move_to_end(key)
            while len(self._cache) > self.MAX_CACHE:
                self._cache.popitem(last=False)
        return result
