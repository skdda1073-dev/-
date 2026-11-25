"""Time Seoul bus legs by matching one vehicle across ordered stops."""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from threading import Lock
from time import monotonic
from urllib.parse import unquote
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import requests


ARRIVAL_URL = "http://ws.bus.go.kr/api/rest/arrive/getArrInfoByRouteAll"
KST = ZoneInfo("Asia/Seoul")
MAX_OBSERVATION_AGE = timedelta(minutes=2)
MAX_CLOCK_SKEW = timedelta(seconds=30)
MAX_PREDICTION_SECONDS = 7200
CACHE_TTL_SECONDS = 20
MAX_CACHE_ENTRIES = 128
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
ROUTE_LOCK_COUNT = 32
_IDENTIFIER = re.compile(r"[0-9]{9}")


@dataclass(frozen=True)
class _Prediction:
    arrival_at: datetime
    full: bool


def _positive_integer(value, maximum):
    if not value.isascii() or not value.isdecimal() or len(value) > len(str(maximum)):
        return None
    number = int(value)
    return number if 0 < number <= maximum else None


def _observation_time(value, now):
    try:
        observed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=KST)
    return observed if -MAX_CLOCK_SKEW <= now - observed <= MAX_OBSERVATION_AGE else None


class SeoulBusArrivals:
    def __init__(self, service_key, *, requester=None, now=None):
        self._service_key = unquote(service_key.strip())
        self._request = requester if requester is not None else requests.get
        self._now = now if now is not None else lambda: datetime.now(KST)
        self._cache = OrderedDict()
        self._lock = Lock()
        self._route_locks = tuple(Lock() for _ in range(ROUTE_LOCK_COUNT))

    def snapshot(self, route_id):
        """Return forecasts indexed by (stop ID, ordinal), then vehicle ID."""
        if not self._service_key or not isinstance(route_id, str) or not _IDENTIFIER.fullmatch(route_id):
            return {}
        # Direct and transfer searches can request the same route concurrently.
        with self._route_locks[int(route_id) % ROUTE_LOCK_COUNT]:
            with self._lock:
                cached = self._cache.get(route_id)
                if cached is not None and cached[0] > monotonic():
                    self._cache.move_to_end(route_id)
                    return cached[1]

            snapshot = self._read_snapshot(route_id)
            with self._lock:
                self._cache[route_id] = (monotonic() + CACHE_TTL_SECONDS, snapshot)
                self._cache.move_to_end(route_id)
                if len(self._cache) > MAX_CACHE_ENTRIES:
                    self._cache.popitem(last=False)
        return snapshot

    def _read_snapshot(self, route_id):
        try:
            response = self._request(
                ARRIVAL_URL, params={"serviceKey": self._service_key, "busRouteId": route_id},
                timeout=(2, 4), allow_redirects=False,
            )
            if response.status_code != 200 or len(response.content) > MAX_RESPONSE_BYTES:
                return {}
            root = ElementTree.fromstring(response.content)
        except (requests.RequestException, ElementTree.ParseError, ValueError):
            return {}
        if root.tag != "ServiceResult" or root.findtext("./msgHeader/headerCd") != "0":
            return {}

        now = self._now()
        stops = {}
        for row in root.findall("./msgBody/itemList"):
            if row.findtext("busRouteId") != route_id or row.findtext("deTourAt", "").strip() not in ("", "00"):
                continue
            stop_id = row.findtext("stId", "").strip()
            order = _positive_integer(row.findtext("staOrd", "").strip(), 9999)
            observed = _observation_time(row.findtext("mkTm", "").strip(), now)
            if not _IDENTIFIER.fullmatch(stop_id) or order is None or observed is None:
                continue
            key = (stop_id, order)
            if key in stops:
                stops[key] = {}
                continue
            vehicles = {}
            for slot in (1, 2):
                vehicle = row.findtext(f"vehId{slot}", "").strip()
                seconds = _positive_integer(row.findtext(f"exps{slot}", "").strip(), MAX_PREDICTION_SECONDS)
                if _positive_integer(vehicle, 9999999999) is None or seconds is None:
                    continue
                full = row.findtext(f"full{slot}", "").strip() == "1"
                vehicles[vehicle] = None if vehicle in vehicles else _Prediction(observed + timedelta(seconds=seconds), full)
            stops[key] = {vehicle: prediction for vehicle, prediction in vehicles.items() if prediction is not None}
        return stops

    @staticmethod
    def trip_times(snapshot, board_id, board_order, alight_id, alight_order, ready_at):
        """Return board and alight predictions for the fastest boardable vehicle."""
        if (type(board_order) is not int or type(alight_order) is not int
                or board_order >= alight_order or board_id == alight_id
                or not isinstance(ready_at, datetime) or ready_at.tzinfo is None):
            return None
        board = snapshot.get((board_id, board_order), {})
        alight = snapshot.get((alight_id, alight_order), {})
        choices = (
            (prediction.arrival_at, onward.arrival_at)
            for vehicle, prediction in board.items()
            if not prediction.full and prediction.arrival_at >= ready_at
            and (onward := alight.get(vehicle)) is not None and onward.arrival_at > prediction.arrival_at
        )
        return min(choices, key=lambda pair: (pair[1], pair[0]), default=None)
