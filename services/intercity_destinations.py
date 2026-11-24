"""버스타고 공개 예매 화면의 출발지별 등록 도착지 목록을 읽는다.

공공 OpenAPI가 아닌 공개 웹 화면의 조회 요청이다. 날짜별 배차나 잔여 좌석을
뜻하지 않으며, 화면 계약이 바뀌면 오류로 처리하고 전국 목록을 대신 내보내지 않는다.
"""

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime
import re
from threading import RLock
from time import monotonic
import unicodedata

import requests

from services.intercity_client import IntercityError, KST


DESTINATION_SOURCE = {"name": "버스타고 등록 도착지",
                      "url": "https://www.bustago.or.kr/newweb/kr/index.do"}
# The current public catalogue includes values such as b107 as well as 0001.
SOURCE_ID = re.compile(r"[A-Za-z0-9]{1,12}\Z")
REGION_NAMES = {
    "서울": ("서울", "서울시", "서울특별시"),
    "부산": ("부산", "부산시", "부산광역시"),
    "대구": ("대구", "대구시", "대구광역시"),
    "인천": ("인천", "인천시", "인천광역시"),
    "광주": ("광주", "광주시", "광주광역시"),
    "대전": ("대전", "대전시", "대전광역시"),
    "울산": ("울산", "울산시", "울산광역시"),
    "세종": ("세종", "세종시", "세종특별자치시"),
    "경기": ("경기", "경기도"),
    "강원": ("강원", "강원도", "강원특별자치도"),
    "충북": ("충북", "충청북도"),
    "충남": ("충남", "충청남도"),
    "전북": ("전북", "전라북도", "전북특별자치도"),
    "전남": ("전남", "전라남도"),
    "경북": ("경북", "경상북도"),
    "경남": ("경남", "경상남도"),
    "제주": ("제주", "제주도", "제주특별자치도"),
}
REGIONS = {alias: key for key, aliases in REGION_NAMES.items() for alias in aliases}


def terminal_identity(item):
    """Only harmless Unicode/whitespace normalization and province aliases.

    Parentheses, directions, digits and terminal-type words remain significant.
    An empty region cannot establish a cross-provider identity.
    """
    name = " ".join(unicodedata.normalize("NFC", item.get("name", "")).split()).casefold()
    city = " ".join(unicodedata.normalize("NFC", item.get("city", "")).split())
    return name, REGIONS.get(city, city)


class BustagoDestinationsClient:
    BASE_URL = "https://www.bustago.or.kr/newweb/kr/common"
    CACHE_TTL = 6 * 3600
    MAX_CACHE = 64
    MAX_ROWS = 10_000

    def __init__(self, requester=None, clock=None, now=None):
        self.requester = requester or requests.post
        self.clock = clock or monotonic
        self.now = now or (lambda: datetime.now(KST))
        self._cache = OrderedDict()
        self._lock = RLock()

    @staticmethod
    def _invalid():
        return IntercityError("INTERCITY_DESTINATIONS_RESPONSE",
                              "예매처의 도착지 목록 형식을 확인할 수 없어요. 공식 예매처에서 확인해 주세요.")

    @classmethod
    def _rows(cls, payload, field):
        if not isinstance(payload, dict) or not isinstance(payload.get(field), list):
            raise cls._invalid()
        rows = payload[field]
        if len(rows) > cls.MAX_ROWS:
            raise cls._invalid()
        result = {}
        for row in rows:
            if not isinstance(row, dict):
                raise cls._invalid()
            ident, name, city = row.get("TERMINAL_ID"), row.get("TERMINAL_NM"), row.get("TERMINAL_AREA")
            if (not isinstance(ident, str) or not SOURCE_ID.fullmatch(ident)
                    or not isinstance(name, str) or not name.strip() or len(name) > 200
                    or not isinstance(city, str) or not city.strip() or len(city) > 100):
                raise cls._invalid()
            item = {"source_id": ident, "name": name.strip(), "city": city.strip()}
            if field == "terminalEndList":
                check = row.get("RESERVATION_CHK")
                direction = row.get("ONE_WAY_CD")
                # These fields describe online booking, never remaining seats or trips.
                if isinstance(check, int) and not isinstance(check, bool) and check > 0:
                    item["booking_mode"] = ({"0": "one_way", "1": "round_trip"}.get(direction, "unknown")
                                            if isinstance(direction, str) else "unknown")
                else:
                    item["booking_mode"] = "on_site" if type(check) is int and check in {0, -1} else "unknown"
            if ident in result and result[ident] != item:
                raise cls._invalid()
            result[ident] = item
        return list(result.values())

    def _read(self, operation, parameters, field, result_field):
        key = (operation, tuple(sorted(parameters.items())))
        with self._lock:
            cached = self._cache.get(key)
            if cached and self.clock() - cached[0] < self.CACHE_TTL:
                self._cache.move_to_end(key)
                result = deepcopy(cached[1])
                result["from_cache"] = True
                return result
        try:
            response = self.requester(f"{self.BASE_URL}/{operation}", params=parameters,
                                      data={}, timeout=(10, 20), allow_redirects=False)
        except requests.Timeout:
            raise IntercityError("INTERCITY_DESTINATIONS_TIMEOUT",
                                  "도착지 목록을 불러오는 데 시간이 걸려요. 잠시 후 다시 시도해 주세요.", 504) from None
        except requests.RequestException:
            raise IntercityError("INTERCITY_DESTINATIONS_NETWORK",
                                  "도착지 목록 제공처에 연결하지 못했어요. 다시 불러오기를 눌러 주세요.") from None
        if response.status_code != 200:
            raise IntercityError("INTERCITY_DESTINATIONS_UNAVAILABLE",
                                  "도착지 목록 제공처가 정상 응답하지 않았어요. 공식 예매처에서 확인해 주세요.")
        try:
            payload = response.json()
        except (ValueError, TypeError):
            raise self._invalid() from None
        rows = self._rows(payload, field)
        # The published booking page obtains each complete list in one request.
        result = {result_field: rows, "fetched_at": self.now().astimezone(KST).isoformat(), "from_cache": False}
        with self._lock:
            self._cache[key] = (self.clock(), deepcopy(result))
            self._cache.move_to_end(key)
            while len(self._cache) > self.MAX_CACHE:
                self._cache.popitem(last=False)
        return result

    def terminals(self):
        return self._read("terminalListAjax.do", {"area": "", "searchTerminalNm": ""},
                          "terminalList", "terminals")

    def destinations(self, source_id):
        if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
            raise IntercityError("INVALID_INTERCITY_TERMINAL", "출발 터미널을 다시 선택해 주세요.", 400)
        return self._read("terminalEndListAjax.do", {"area": "", "country": "", "terCode": source_id},
                          "terminalEndList", "destinations")
