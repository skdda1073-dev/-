"""TMAP 자동차 합계 견적과 짧은 프로세스 메모리 캐시. 원문은 저장하지 않는다."""

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import isfinite
from threading import RLock
from time import monotonic

import requests

from services.fare_split import preview, split_amount
from services.mobility_validation import named_point


class TaxiError(Exception):
    """공급자 원문이나 비밀키를 포함하지 않는 사용자용 오류."""

    def __init__(self, code, message, status_code=502):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class TaxiService:
    URL = "https://apis.openapi.sk.com/tmap/routes"
    ROUTE_OPTIONS = {
        "reqCoordType": "WGS84GEO", "resCoordType": "WGS84GEO",
        "searchOption": "24", "commercialVehicle": "Y", "carType": "1", "totalValue": "2",
    }

    def __init__(self, app_key, requester=None, clock=None, utcnow=None,
                 cache_ttl_seconds=60, max_cache_entries=512):
        self._app_key = str(app_key or "").strip()
        self.requester = requester or requests.post
        self.clock = clock or monotonic
        self.utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        if (isinstance(cache_ttl_seconds, bool) or not isinstance(cache_ttl_seconds, (int, float))
                or not isfinite(cache_ttl_seconds) or not 0 < cache_ttl_seconds <= 3600):
            raise ValueError("택시 캐시 시간 설정을 확인해 주세요.")
        if (isinstance(max_cache_entries, bool) or not isinstance(max_cache_entries, int)
                or not 1 <= max_cache_entries <= 10000):
            raise ValueError("택시 캐시 항목 수 설정을 확인해 주세요.")
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_cache_entries = max_cache_entries
        self._cache = OrderedDict()
        self._lock = RLock()

    def __repr__(self):
        return f"TaxiService(cache_ttl_seconds={self.cache_ttl_seconds!r})"

    def _key(self, origin, destination):
        return (origin["lat"], origin["lon"], destination["lat"], destination["lon"],
                tuple(sorted(self.ROUTE_OPTIONS.items())))

    def _cached(self, key):
        with self._lock:
            cached = self._cache.get(key)
            if cached is None:
                return None
            age = max(0, self.clock() - cached[0])
            if age >= self.cache_ttl_seconds:
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return deepcopy(cached[1]), age

    @staticmethod
    def _number(value, *, money=False, positive=False):
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not isfinite(number) or number < 0 or (positive and number == 0):
            return None
        if money and not number.is_integer():
            return None
        # JSON 정밀도를 넘는 공급자 수치는 표시/분담 대상으로 삼지 않는다.
        if number > 2 ** 53 - 1:
            return None
        return int(number) if number.is_integer() else number

    @classmethod
    def _summary(cls, payload):
        features = payload.get("features") if isinstance(payload, dict) else None
        if not isinstance(features, list):
            raise TaxiError("TAXI_PROVIDER_RESPONSE", "택시 예상 정보의 응답 형식을 확인할 수 없습니다.")
        candidates = [row.get("properties") for row in features
                      if isinstance(row, dict) and isinstance(row.get("properties"), dict)]
        candidates = [row for row in candidates if "totalTime" in row or "totalDistance" in row]
        # 전체 형상 응답의 S 합계를 우선하며, 요약 응답에는 pointType이 없을 수 있다.
        candidates.sort(key=lambda row: row.get("pointType") != "S")
        for properties in candidates:
            seconds = cls._number(properties.get("totalTime"))
            distance = cls._number(properties.get("totalDistance"))
            if seconds is None or distance is None:
                continue
            fare = cls._number(properties.get("taxiFare"), money=True, positive=True)
            total_fare = cls._number(properties.get("totalFare"), money=True)
            return seconds, distance, fare, total_fare
        raise TaxiError("TAXI_PROVIDER_RESPONSE", "택시 예상 시간과 거리 정보를 확인할 수 없습니다.")

    def _now(self):
        result = self.utcnow()
        if result.tzinfo is None or result.utcoffset() is None:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)

    def _core(self, seconds, distance, fare, total_fare, *, no_travel=False):
        fetched = self._now()
        return {
            "status": "no_travel_required" if no_travel else ("ok" if fare is not None else "partial"),
            "code": "TAXI_NOT_NEEDED" if no_travel else ("TAXI_OK" if fare is not None else "TAXI_FARE_UNAVAILABLE"),
            "ride_duration_seconds": seconds, "distance_m": distance,
            "fare": {"amount_krw": fare, "basis": "provider_estimate",
                     "provider_total_fare_krw": total_fare, "toll_included": None,
                     "surcharge_included": None, "call_fee_included": None},
            "pickup_wait_seconds": None, "door_to_door_seconds": None,
            "fetched_at": fetched.isoformat(), "provider_data_at": None,
            "expires_at": (fetched + timedelta(seconds=self.cache_ttl_seconds)).isoformat(),
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "warnings": (["출발지와 도착지가 같아 택시 이동이 필요하지 않아요."] if no_travel
                         else (["예상요금을 제공받지 못했어요."] if fare is None else [])),
        }

    @staticmethod
    def _decorate(core, origin, destination, split_count, *, cached=False, age=0):
        result = deepcopy(core)
        result.update({"origin": origin.copy(), "destination": destination.copy(),
                       "from_cache": cached, "cache_age_seconds": round(age, 3),
                       "split_count": split_count,
                       "split_amounts_krw": split_amount(result["fare"]["amount_krw"], split_count),
                       "split_preview": preview(result["fare"]["amount_krw"])})
        return result

    def peek(self, origin, destination):
        """유효한 메모리 견적만 확인한다. 어떤 경로에서도 외부 통신하지 않는다."""
        origin, destination = named_point(origin, "origin"), named_point(destination, "destination")
        cached = self._cached(self._key(origin, destination))
        if cached is None:
            return None
        return self._decorate(cached[0], origin, destination, 1, cached=True, age=cached[1])

    def estimate(self, origin, destination, split_count=1):
        origin, destination = named_point(origin, "origin"), named_point(destination, "destination")
        if isinstance(split_count, bool) or not isinstance(split_count, int) or not 1 <= split_count <= 4:
            from services.mobility_validation import MobilityInputError
            raise MobilityInputError("INVALID_SPLIT_COUNT", "분담 인원은 1~4의 정수로 입력해 주세요.")
        if origin["lat"] == destination["lat"] and origin["lon"] == destination["lon"]:
            return self._decorate(self._core(0, 0, None, None, no_travel=True), origin, destination, split_count)
        if not self._app_key:
            raise TaxiError("TAXI_KEY_MISSING", "TMAP 자동차 경로 설정을 확인한 뒤 서버를 다시 시작해 주세요.", 503)
        key = self._key(origin, destination)
        cached = self._cached(key)
        if cached is not None:
            return self._decorate(cached[0], origin, destination, split_count, cached=True, age=cached[1])
        form = dict(self.ROUTE_OPTIONS, startX=origin["lon"], startY=origin["lat"],
                    endX=destination["lon"], endY=destination["lat"])
        try:
            response = self.requester(
                self.URL, params={"version": 1}, data=form,
                headers={"Accept": "application/json", "appKey": self._app_key},
                timeout=(3, 8), allow_redirects=False,
            )
        except requests.Timeout:
            raise TaxiError("TAXI_PROVIDER_TIMEOUT", "택시 예상 정보 응답이 늦습니다. 잠시 후 다시 조회해 주세요.", 504) from None
        except requests.RequestException:
            raise TaxiError("TAXI_PROVIDER_NETWORK", "택시 예상 정보 서버에 연결하지 못했습니다.") from None
        if response.status_code in (401, 403):
            raise TaxiError("TAXI_PROVIDER_AUTH", "TMAP 키의 자동차·택시 경로 상품 권한을 확인해 주세요.")
        if response.status_code == 429:
            raise TaxiError("TAXI_PROVIDER_QUOTA", "택시 예상 정보 호출 한도에 도달했습니다. 잠시 후 다시 조회해 주세요.", 503)
        if response.status_code != 200:
            raise TaxiError("TAXI_PROVIDER_HTTP", "택시 예상 정보 서비스가 정상 응답하지 않았습니다.")
        try:
            payload = response.json()
        except (ValueError, TypeError):
            raise TaxiError("TAXI_PROVIDER_RESPONSE", "택시 예상 정보의 응답 형식을 확인할 수 없습니다.") from None
        core = self._core(*self._summary(payload))
        with self._lock:
            self._cache[key] = (self.clock(), deepcopy(core))
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_cache_entries:
                self._cache.popitem(last=False)
        return self._decorate(core, origin, destination, split_count)
