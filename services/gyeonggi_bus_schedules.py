"""Read published route operating windows and headways from 경기버스정보."""

from dataclasses import dataclass
from datetime import date, datetime, time
from html.parser import HTMLParser
import re
from threading import Lock
from time import monotonic
from zoneinfo import ZoneInfo

import requests


KST = ZoneInfo("Asia/Seoul")
GBIS_URL = "https://www.gbis.go.kr/service/busInfo/"
_TABLES = (("metroBus.action", "metroBusList"), ("townBus.action", "townBus"))
# TAGO source city codes and the corresponding GBIS municipal center prefixes.
_CENTERS = {
    "31010": "41110", "31020": "41130", "31030": "41150", "31040": "41170",
    "31050": "41190", "31060": "41210", "31070": "41220", "31080": "41250",
    "31090": "41270", "31100": "41280", "31110": "41290", "31120": "41310",
    "31130": "41360", "31140": "41370", "31150": "41390", "31160": "41410",
    "31170": "41430", "31180": "41450", "31190": "41460", "31200": "41480",
    "31210": "41500", "31220": "41550", "31230": "41570", "31240": "41590",
    "31250": "41610", "31260": "41630", "31270": "41650", "31320": "41730",
    "31350": "41800", "31370": "41820", "31380": "41830",
}
_DAYS = {
    "평일": ("weekday",), "토요일": ("saturday",), "일요일": ("sunday",),
    "공휴일": ("holiday",), "주말": ("saturday", "sunday"),
}
_DAY = r"(평일|토요일|일요일|공휴일|주말)\s*:\s*"
_CLOCK_RANGE = re.compile(_DAY + r"(\d{2}:\d{2})\s*~\s*(\d{2}:\d{2})")
_HEADWAY_RANGE = re.compile(_DAY + r"(\d{1,4})\s*~\s*(\d{1,4})\s*분")
_ROUTE_LINK = re.compile(r"javascript:busInfoView\('([^']+)',\s*'([0-9]{9})'\);?\Z")


@dataclass(frozen=True)
class GyeonggiFrequency:
    first: time
    last: time
    minimum: int
    maximum: int
    observed: date


@dataclass(frozen=True)
class GyeonggiBusSchedule:
    name: str
    start: str
    end: str
    frequencies: dict[tuple[str, str], GyeonggiFrequency]


class _RouteTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.fields = None
        self.cell = None
        self.identity = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.fields, self.cell, self.identity = [], None, None
        elif tag == "td" and self.fields is not None:
            self.cell = []
        elif tag == "a" and self.fields == [] and self.cell is not None:
            match = _ROUTE_LINK.fullmatch(dict(attrs).get("href") or "")
            if match:
                self.identity = match.groups()
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_endtag(self, tag):
        if tag == "td" and self.cell is not None:
            self.fields.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr":
            if self.identity and self.fields is not None and len(self.fields) == 7:
                self.rows.append((self.identity, self.fields))
            self.fields, self.cell, self.identity = None, None, None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def _day_ranges(pattern, text):
    return {day: (first, last) for label, first, last in pattern.findall(text) for day in _DAYS[label]}


def _parse_schedules(html, observed):
    parser = _RouteTableParser()
    parser.feed(html)
    parser.close()
    schedules, ambiguous = {}, set()
    for (label, route_id), (_, start, end, outbound, inbound, headways, _) in parser.rows:
        if not start or not end or route_id in ambiguous:
            continue
        headway_by_day = _day_ranges(_HEADWAY_RANGE, headways)
        frequencies = {}
        for direction, windows in (("0", outbound), ("1", inbound)):
            for day, (first, last) in _day_ranges(_CLOCK_RANGE, windows).items():
                if day not in headway_by_day:
                    continue
                try:
                    first, last = time.fromisoformat(first), time.fromisoformat(last)
                except ValueError:
                    continue
                minimum, maximum = map(int, headway_by_day[day])
                if first < last and 0 < minimum <= maximum <= 1440:
                    frequencies[direction, day] = GyeonggiFrequency(first, last, minimum, maximum, observed)
        schedule = GyeonggiBusSchedule(label, start, end, frequencies)
        if route_id in schedules and schedules[route_id] != schedule:
            del schedules[route_id]
            ambiguous.add(route_id)
        else:
            schedules[route_id] = schedule
    return schedules


class GyeonggiBusSchedules:
    """Lookup official tables by municipal registration and exact GBIS route ID."""

    def __init__(self, *, requester=None, now=None):
        self._request = requester or requests.get
        self._now = now or (lambda: datetime.now(KST))
        self._locks = {city: Lock() for city in _CENTERS}
        self._cache = {}

    @staticmethod
    def supports(city_code):
        return city_code in _CENTERS

    def route(self, city_code, route_id):
        if not self.supports(city_code) or not re.fullmatch(r"GGB[0-9]{9}", route_id):
            return None
        with self._locks[city_code]:
            for table in _TABLES:
                schedules = self._table(city_code, table)
                if route := schedules.get(route_id.removeprefix("GGB")):
                    return route
        return None

    def _table(self, city_code, table):
        key = city_code, table
        cached = self._cache.get(key)
        if cached is not None and monotonic() < cached[0]:
            return cached[1]
        page, command = table
        schedules = {}
        try:
            response = self._request(GBIS_URL + page, params={"centerId": _CENTERS[city_code] + "00000", "cmd": command},
                                     timeout=(2, 4), allow_redirects=False)
            if response.status_code == 200:
                schedules = _parse_schedules(response.text, self._now().astimezone(KST).date())
        except requests.RequestException:
            pass
        self._cache[key] = monotonic() + (6 * 3600 if schedules else 600), schedules
        return schedules
