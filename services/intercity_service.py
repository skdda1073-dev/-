"""시외버스 터미널·당일 배차를 검증하고 화면용 시간표로 변환한다."""

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from math import ceil
import re

from services.intercity_client import IntercityClient, IntercityError, KST
from services.intercity_destinations import BustagoDestinationsClient, DESTINATION_SOURCE, terminal_identity

SOURCE = {"name": "국토교통부 TAGO 시외버스정보",
          "url": "https://www.data.go.kr/data/15098541/openapi.do"}
BOOKING_LINKS = [
    {"label": "버스타고에서 확인", "url": "https://www.bustago.or.kr/newweb/kr/index.do"},
    {"label": "티머니 시외버스에서 확인", "url": "https://txbus.t-money.co.kr/"},
]
TERMINAL_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


class IntercityService:
    def __init__(self, service_key, client=None, now=None, destination_client=None):
        self.now = now or (lambda: datetime.now(KST))
        self.client = client or IntercityClient(service_key, now=self.now)
        self.destination_client = destination_client or BustagoDestinationsClient(now=self.now)

    def today(self):
        return self.now().astimezone(KST).date().isoformat()

    def configuration(self):
        return {"today": self.today(), "date_policy": "today_only", "configured": self.client.configured,
                "source": dict(SOURCE), "booking_links": deepcopy(BOOKING_LINKS)}

    @staticmethod
    def _text(value, limit=200):
        return value.strip()[:limit] if isinstance(value, str) else ""

    def terminals(self, query=""):
        if not isinstance(query, str) or len(query) > 100:
            raise IntercityError("INVALID_INTERCITY_QUERY", "터미널 검색어를 확인해 주세요.", 400)
        result = self.client.read(self.client.TERMINALS, {}, 6 * 3600)
        terminals = {}
        for row in result["rows"]:
            raw_id = row.get("terminalId")
            ident = raw_id.strip() if isinstance(raw_id, str) else ""
            name = self._text(row.get("terminalNm"))
            if not TERMINAL_ID.fullmatch(ident) or not name:
                raise IntercityError("INTERCITY_TERMINAL_RESPONSE", "터미널 목록의 정보를 확인할 수 없습니다.")
            item = {"id": ident, "name": name, "city": self._text(row.get("cityName"))}
            if ident in terminals and terminals[ident] != item:
                raise IntercityError("INTERCITY_TERMINAL_RESPONSE", "터미널 식별 정보가 일치하지 않습니다.")
            terminals[ident] = item
        if not terminals:
            raise IntercityError("INTERCITY_TERMINALS_EMPTY", "제공된 터미널 목록이 없습니다. 공식 예매처에서 확인해 주세요.", 503)
        keyword = query.strip().casefold()
        selected = [item for item in terminals.values()
                    if keyword in item["name"].casefold() or keyword in item["city"].casefold()]
        selected.sort(key=lambda item: (item["name"], item["city"], item["id"]))
        return {"status": "ok", "terminals": selected, "today": self.today(),
                "fetched_at": result["fetched_at"], "from_cache": result["from_cache"]}

    def destinations(self, departure_id):
        if not isinstance(departure_id, str) or not TERMINAL_ID.fullmatch(departure_id):
            raise IntercityError("INVALID_INTERCITY_TERMINAL", "목록에서 출발 터미널을 선택해 주세요.", 400)
        catalogue = self.terminals()["terminals"]
        departure = next((item for item in catalogue if item["id"] == departure_id), None)
        if departure is None:
            raise IntercityError("UNKNOWN_INTERCITY_TERMINAL", "현재 제공되는 목록에서 출발 터미널을 다시 선택해 주세요.", 400)
        identity = terminal_identity(departure)
        origins = self.destination_client.terminals()["terminals"]
        source_origins = [item for item in origins if terminal_identity(item) == identity]
        same_origins = [item for item in catalogue if terminal_identity(item) == identity]
        if not identity[1] or len(source_origins) != 1 or len(same_origins) != 1:
            raise IntercityError("INTERCITY_DEPARTURE_UNMAPPED",
                                  "이 출발지의 도착지 목록을 정확히 연결하지 못했어요. 공식 예매처에서 확인해 주세요.", 503)
        result = self.destination_client.destinations(source_origins[0]["source_id"])
        source_rows = result["destinations"]
        counts = Counter(terminal_identity(item) for item in source_rows)
        tago_by_identity = defaultdict(list)
        for item in catalogue:
            tago_by_identity[terminal_identity(item)].append(item)
        destinations = []
        for item in source_rows:
            key = terminal_identity(item)
            matches = tago_by_identity.get(key, []) if key[1] else []
            ambiguous = counts[key] != 1 or len(matches) > 1
            matched = not ambiguous and len(matches) == 1 and matches[0]["id"] != departure_id
            destinations.append({
                **item, "id": matches[0]["id"] if matched else None,
                "match_status": "matched" if matched else "ambiguous" if ambiguous else "unmapped",
            })
        destinations.sort(key=lambda item: (item["name"], item["city"], item["source_id"]))
        matched_count = sum(item["id"] is not None for item in destinations)
        warnings = ["예매처에 등록된 도착지 목록입니다. 오늘 실제 배차는 시간표 조회로 확인해 주세요."]
        if matched_count < len(destinations):
            warnings.append("시간표 연결 확인이 필요한 도착지는 공식 예매처에서 조회해 주세요.")
        return {
            "status": ("ok" if matched_count == len(destinations) else "partial") if destinations else "empty",
            "departure": departure, "destinations": destinations, "today": self.today(),
            "fetched_at": result["fetched_at"], "from_cache": result["from_cache"],
            "source": dict(DESTINATION_SOURCE), "warnings": warnings,
            "total_count": len(destinations), "matched_count": matched_count,
        }

    @staticmethod
    def _timestamp(value):
        raw = str(value or "").strip()
        if not raw.isdigit() or len(raw) not in {12, 14}:
            return None
        try:
            return datetime.strptime(raw, "%Y%m%d%H%M" if len(raw) == 12 else "%Y%m%d%H%M%S").replace(tzinfo=KST)
        except ValueError:
            return None

    @staticmethod
    def _fare(value):
        if isinstance(value, bool) or value in (None, ""):
            return None
        try:
            amount = Decimal(str(value).strip())
            if not amount.is_finite() or not 0 < amount <= 10_000_000 or amount != amount.to_integral_value():
                return None
            return int(amount)
        except (InvalidOperation, ValueError, TypeError):
            return None

    def timetable(self, departure_id, arrival_id, query_date=None):
        for ident in (departure_id, arrival_id):
            if not isinstance(ident, str) or not TERMINAL_ID.fullmatch(ident):
                raise IntercityError("INVALID_INTERCITY_TERMINAL", "목록에서 출발·도착 터미널을 선택해 주세요.", 400)
        if departure_id == arrival_id:
            raise IntercityError("SAME_INTERCITY_TERMINAL", "출발·도착 터미널을 다르게 선택해 주세요.", 400)
        today = self.today()
        query_date = today if query_date is None else query_date
        try:
            if not isinstance(query_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", query_date):
                raise ValueError
            requested_date = date.fromisoformat(query_date)
        except ValueError:
            raise IntercityError("INVALID_INTERCITY_DATE", "조회 날짜 형식을 확인해 주세요.", 400) from None
        if query_date != today:
            raise IntercityError(
                "INTERCITY_TODAY_ONLY", "공공데이터는 한국 시간 기준 오늘 배차만 제공합니다. 다른 날짜는 공식 예매처에서 확인해 주세요.", 400,
            )
        terminal_map = {item["id"]: item for item in self.terminals()["terminals"]}
        if departure_id not in terminal_map or arrival_id not in terminal_map:
            raise IntercityError("UNKNOWN_INTERCITY_TERMINAL", "현재 제공되는 목록에서 터미널을 다시 선택해 주세요.", 400)
        result = self.client.read(self.client.TIMETABLE, {
            "depTerminalId": departure_id, "arrTerminalId": arrival_id,
            "depPlandTime": requested_date.strftime("%Y%m%d"),
        }, 60)
        # A request crossing midnight must not be presented as today's schedule.
        if self.today() != query_date:
            raise IntercityError("INTERCITY_TODAY_ONLY", "날짜가 변경됐습니다. 오늘 날짜로 다시 조회해 주세요.", 400)
        trips, seen, discarded, partial = [], set(), 0, False
        for row in result["rows"]:
            departure = self._timestamp(row.get("depPlandTime"))
            if departure is None or departure.date() != requested_date:
                discarded += 1
                continue
            arrival = self._timestamp(row.get("arrPlandTime"))
            if arrival is not None and not 0 <= (arrival - departure).total_seconds() <= 48 * 3600:
                arrival = None
            amount = self._fare(row.get("charge"))
            grade = self._text(row.get("gradeNm"), 80)
            partial = partial or arrival is None or amount is None or not grade
            item = {
                "departure_at": departure.isoformat(),
                "arrival_at": arrival.isoformat() if arrival else None,
                "duration_minutes": ceil((arrival - departure).total_seconds() / 60) if arrival else None,
                "fare_krw": amount, "grade": grade or "등급 미제공",
                "route_id": self._text(row.get("routeId"), 100),
            }
            identity = tuple(item.values())
            if identity not in seen:
                trips.append(item)
                seen.add(identity)
        if result["rows"] and not trips:
            raise IntercityError("INTERCITY_SCHEDULE_RESPONSE", "제공된 배차의 날짜·시각을 확인할 수 없습니다. 공식 예매처에서 확인해 주세요.")
        trips.sort(key=lambda item: (item["departure_at"], item["grade"], item["route_id"]))
        warnings = [
            "예정 시간표이며 실제 운행 변경·잔여 좌석·최종 요금은 공식 예매처에서 확인해 주세요.",
            "표시된 소요 시간은 터미널 간 이동이며 캠퍼스에서 터미널까지의 이동 시간은 포함하지 않습니다.",
        ]
        if not trips:
            warnings.insert(0, "공공데이터에서 이 구간의 오늘 배차를 확인하지 못했습니다. 실제 운행이 없다는 뜻은 아닙니다.")
        if partial:
            warnings.append("일부 편의 도착 예정 시각·등급·요금이 제공되지 않아 확인 가능한 정보만 표시합니다.")
        if discarded:
            warnings.append("날짜 또는 출발 시각이 확인되지 않는 일부 항목은 표시하지 않았습니다.")
        return {
            "status": ("partial" if partial or discarded else "ok") if trips else "empty",
            "date": query_date, "departure": terminal_map[departure_id], "arrival": terminal_map[arrival_id],
            "trips": trips, "fetched_at": result["fetched_at"], "from_cache": result["from_cache"],
            "source": dict(SOURCE), "warnings": warnings, "booking_links": deepcopy(BOOKING_LINKS),
        }
