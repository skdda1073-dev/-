"""예매처의 같은 식별자로 출발지·도착지·시간표를 연결한다.

춘천의 기본 도착 목록은 출처를 검토한 시설 목록과 제공처의 현재 연결 목록의
교집합이다. 나머지 하차지는 별도로 반환하며 이름 접미사나 예매 가능 여부로
버리거나 운행 여부를 추정하지 않는다. 기존 TAGO 서비스는 진단용으로 유지한다.
"""

from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re

from services.intercity_bustago import BustagoTimetableClient
from services.intercity_client import IntercityError, KST
from services.intercity_destinations import BustagoDestinationsClient, SOURCE_ID, terminal_identity
from services.intercity_service import BOOKING_LINKS


SOURCE = {"name": "버스타고 운행시간표", "url": "https://www.bustago.or.kr/newweb/kr/ticket/ticket.do"}
PREFIX = "BUSTAGO_"
PROFILE_PATH = Path(__file__).resolve().parent.parent / "data" / "chuncheon_intercity_terminals.json"


class BookingIntercityService:
    def __init__(self, destination_client=None, timetable_client=None, now=None, profile=None):
        self.now = now or (lambda: datetime.now(KST))
        self.destination_client = destination_client or BustagoDestinationsClient(now=self.now)
        self.timetable_client = timetable_client or BustagoTimetableClient(now=self.now)
        self._profile = deepcopy(profile) if profile is not None else None

    def today(self):
        return self.now().astimezone(KST).date().isoformat()

    def configuration(self):
        return {"today": self.today(), "date_policy": "today_only", "configured": True,
                "source": dict(SOURCE), "booking_links": deepcopy(BOOKING_LINKS),
                "preferred_departure_id": PREFIX + "2037"}

    @staticmethod
    def _source_id(identifier):
        if not isinstance(identifier, str) or not identifier.startswith(PREFIX):
            raise IntercityError("INVALID_INTERCITY_TERMINAL", "목록에서 출발·도착 터미널을 선택해 주세요.", 400)
        source_id = identifier[len(PREFIX):]
        if not SOURCE_ID.fullmatch(source_id):
            raise IntercityError("INVALID_INTERCITY_TERMINAL", "목록에서 출발·도착 터미널을 선택해 주세요.", 400)
        return source_id

    @staticmethod
    def _item(row, name=None):
        return {"id": PREFIX + row["source_id"], "name": name or row["name"], "city": row["city"]}

    def _terminal_profile(self):
        if self._profile is None:
            try:
                self._profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise IntercityError("INTERCITY_TERMINAL_PROFILE",
                                      "도착 터미널 목록을 준비하지 못했어요. 잠시 후 다시 확인해 주세요.", 503) from None
        profile = self._profile
        if (not isinstance(profile, dict) or not isinstance(profile.get("origin"), dict)
                or not isinstance(profile.get("terminals"), list)):
            raise IntercityError("INTERCITY_TERMINAL_PROFILE", "도착 터미널 목록을 확인하지 못했어요.", 503)
        seen = set()
        for row in [profile["origin"], *profile["terminals"]]:
            if (not isinstance(row, dict) or not isinstance(row.get("source_id"), str)
                    or not SOURCE_ID.fullmatch(row["source_id"]) or row["source_id"] in seen
                    or not isinstance(row.get("city"), str) or not row["city"].strip()):
                raise IntercityError("INTERCITY_TERMINAL_PROFILE", "도착 터미널 목록을 확인하지 못했어요.", 503)
            seen.add(row["source_id"])
        if not isinstance(profile["origin"].get("name"), str) or not profile["origin"]["name"].strip():
            raise IntercityError("INTERCITY_TERMINAL_PROFILE", "출발 터미널 목록을 확인하지 못했어요.", 503)
        for row in profile["terminals"]:
            if (not isinstance(row.get("kind"), str) or row["kind"] not in {"terminal", "airport"}
                    or any(not isinstance(row.get(key), str) or not row[key].strip()
                           for key in ("source_name", "display_name"))):
                raise IntercityError("INTERCITY_TERMINAL_PROFILE", "도착 터미널 목록을 확인하지 못했어요.", 503)
        return profile

    def terminals(self, query=""):
        if not isinstance(query, str) or len(query) > 100:
            raise IntercityError("INVALID_INTERCITY_QUERY", "터미널 검색어를 확인해 주세요.", 400)
        result = self.destination_client.terminals()
        if not result["terminals"]:
            raise IntercityError("INTERCITY_TERMINALS_EMPTY", "출발 터미널 목록을 불러오지 못했어요.", 503)
        keyword = query.strip().casefold()
        rows = [self._item(row) for row in result["terminals"]
                if keyword in row["name"].casefold() or keyword in row["city"].casefold()]
        rows.sort(key=lambda row: (row["name"], row["city"], row["id"]))
        return {"status": "ok", "terminals": rows, "today": self.today(),
                "fetched_at": result["fetched_at"], "from_cache": result["from_cache"], "source": dict(SOURCE)}

    def destinations(self, departure_id):
        departure_source_id = self._source_id(departure_id)
        departure = next((item for item in self.terminals()["terminals"] if item["id"] == departure_id), None)
        if departure is None:
            raise IntercityError("UNKNOWN_INTERCITY_TERMINAL", "목록에서 출발 터미널을 다시 선택해 주세요.", 400)
        result = self.destination_client.destinations(departure_source_id)
        profile = self._terminal_profile()
        scoped = (departure_source_id == profile["origin"]["source_id"]
                  and terminal_identity(departure) == terminal_identity(profile["origin"]))
        facilities = {row["source_id"]: row for row in profile["terminals"]} if scoped else {}
        main, additional = [], []
        for row in result["destinations"]:
            if row["source_id"] == departure_source_id:
                continue
            facility = facilities.get(row["source_id"])
            # An ID alone never authorizes a renamed/moved facility to inherit an old label.
            verified = facility is not None and terminal_identity(row) == terminal_identity({
                "name": facility["source_name"], "city": facility["city"],
            })
            item = self._item(row, facility["display_name"] if verified else None)
            (main if verified or not scoped else additional).append(item)
        for rows in (main, additional):
            rows.sort(key=lambda row: (row["name"], row["city"], row["id"]))
        return {
            "status": "ok" if main or additional else "empty", "departure": departure,
            "destinations": main, "additional_stops": additional,
            "today": self.today(), "fetched_at": result["fetched_at"], "from_cache": result["from_cache"],
            "source": dict(SOURCE), "warnings": [], "total_count": len(main),
            "additional_count": len(additional), "display_scope": "terminals" if scoped else "destinations",
        }

    @staticmethod
    def _text(value, limit=200):
        return value.strip()[:limit] if isinstance(value, str) else ""

    @staticmethod
    def _positive_integer(value, maximum):
        if isinstance(value, bool) or value in (None, ""):
            return None
        try:
            amount = Decimal(str(value).strip())
            if not amount.is_finite() or not 0 < amount <= maximum or amount != amount.to_integral_value():
                return None
            return int(amount)
        except (InvalidOperation, ValueError, TypeError):
            return None

    @staticmethod
    def _departure(row, requested_date):
        raw_time = row.get("DEP_TIME")
        if not isinstance(raw_time, str) or not re.fullmatch(r"[0-9]{4}", raw_time):
            return None
        # Public booking UI displays these unspecified-seat sentinel rows as --:--.
        raw_seats = str(row.get("TOT_SEAT_CNT", "")).strip()
        if raw_time in {"2358", "2359"} and re.fullmatch(r"0+", raw_seats):
            return None
        try:
            return datetime.strptime(requested_date.strftime("%Y%m%d") + raw_time, "%Y%m%d%H%M").replace(tzinfo=KST)
        except ValueError:
            return None

    def timetable(self, departure_id, arrival_id, query_date=None):
        departure_source_id, arrival_source_id = self._source_id(departure_id), self._source_id(arrival_id)
        if departure_id == arrival_id:
            raise IntercityError("SAME_INTERCITY_TERMINAL", "출발·도착 터미널을 다르게 선택해 주세요.", 400)
        query_date = self.today() if query_date is None else query_date
        try:
            if not isinstance(query_date, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", query_date):
                raise ValueError
            requested_date = date.fromisoformat(query_date)
        except ValueError:
            raise IntercityError("INVALID_INTERCITY_DATE", "조회 날짜 형식을 확인해 주세요.", 400) from None
        if query_date != self.today():
            raise IntercityError("INTERCITY_TODAY_ONLY", "오늘 날짜로 다시 조회해 주세요. 다른 날짜는 공식 예매처에서 확인할 수 있어요.", 400)
        connected = self.destinations(departure_id)
        arrival = next((item for item in [*connected["destinations"], *connected["additional_stops"]]
                        if item["id"] == arrival_id), None)
        if arrival is None:
            raise IntercityError("UNKNOWN_INTERCITY_ROUTE", "선택한 출발지의 도착 목록에서 다시 선택해 주세요.", 400)
        result = self.timetable_client.read(departure_source_id, arrival_source_id, requested_date.strftime("%Y%m%d"))
        if self.today() != query_date:
            raise IntercityError("INTERCITY_TODAY_ONLY", "날짜가 변경됐습니다. 오늘 날짜로 다시 조회해 주세요.", 400)
        trips, seen, discarded, partial = [], set(), 0, False
        for row in result["rows"]:
            departure = self._departure(row, requested_date)
            if departure is None:
                discarded += 1
                continue
            duration = self._positive_integer(row.get("DIST_TIME"), 48 * 60)
            arrival_at = departure + timedelta(minutes=duration) if duration is not None else None
            fare = self._positive_integer(row.get("FARE0"), 10_000_000)
            grade = self._text(row.get("BUS_TYPE_NM"), 80)
            partial = partial or duration is None or fare is None or not grade
            item = {
                "departure_at": departure.isoformat(), "arrival_at": arrival_at.isoformat() if arrival_at else None,
                "arrival_estimated": arrival_at is not None, "duration_minutes": duration,
                "fare_krw": fare, "grade": grade or "등급 미제공",
                "route_id": self._text(row.get("BUS_ROUTE_ID"), 100),
                "carrier": self._text(row.get("TRANSP_BIZR_ABBR_NM"), 100),
            }
            identity = tuple(item.values())
            if identity not in seen:
                trips.append(item)
                seen.add(identity)
        if result["rows"] and not trips:
            raise IntercityError("INTERCITY_SCHEDULE_RESPONSE", "이 구간의 출발 시각이 제공되지 않았어요. 공식 예매처에서 확인해 주세요.")
        trips.sort(key=lambda item: (item["departure_at"], item["grade"], item["route_id"]))
        warnings = ["예정 시간표입니다. 실제 운행 변경·잔여 좌석·최종 요금은 공식 예매처에서 확인해 주세요."]
        if any(item["arrival_estimated"] for item in trips):
            warnings.append("도착 예상 시각은 출발 시각에 제공된 소요시간을 더한 값이며 도로 상황에 따라 달라질 수 있어요.")
        if trips:
            warnings.append("터미널 간 이동 기준이며 캠퍼스에서 터미널까지의 이동 시간은 포함하지 않아요.")
        else:
            warnings.append("이 구간의 오늘 배차를 확인하지 못했어요. 실제 운행이 없다는 뜻은 아니므로 공식 예매처에서 확인해 주세요.")
        if partial:
            warnings.append("일부 편의 소요시간·등급·요금은 제공되지 않아 확인 가능한 정보만 표시합니다.")
        if discarded:
            warnings.append("정확한 출발 시각이 제공되지 않은 일부 항목은 표시하지 않았어요.")
        return {
            "status": ("partial" if partial or discarded else "ok") if trips else "empty",
            "date": query_date, "departure": connected["departure"], "arrival": arrival,
            "trips": trips, "fetched_at": result["fetched_at"], "from_cache": result["from_cache"],
            "source": dict(SOURCE), "warnings": warnings, "booking_links": deepcopy(BOOKING_LINKS),
        }
