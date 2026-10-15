"""TMAP 보행 경로 시간·거리 조회. 실패한 경로를 0분으로 바꾸지 않는다."""

from math import ceil, isfinite
from threading import RLock
from time import monotonic

import requests


class WalkingService:
    def __init__(self, app_key, requester=None):
        self.app_key = app_key
        self.requester = requester or requests.post
        self._cache = {}
        self._lock = RLock()

    def get_time(self, start_lat, start_lon, start_name, end_lat, end_lon, end_name):
        coords = (start_lat, start_lon, end_lat, end_lon)
        try:
            valid = all(isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) for value in coords)
        except OverflowError:
            valid = False
        if not valid:
            return -1, -1
        if not (-90 <= start_lat <= 90 and -90 <= end_lat <= 90 and -180 <= start_lon <= 180 and -180 <= end_lon <= 180):
            return -1, -1
        if start_lat == end_lat and start_lon == end_lon:
            return 0, 0
        with self._lock:
            cached = self._cache.get(coords)
            if cached and cached[0] > monotonic():
                return cached[1]
        try:
            response = self.requester(
                "https://apis.openapi.sk.com/tmap/routes/pedestrian",
                params={"version": 1},
                json={
                    "startX": start_lon, "startY": start_lat,
                    "endX": end_lon, "endY": end_lat,
                    "startName": str(start_name), "endName": str(end_name),
                    "reqCoordType": "WGS84GEO", "resCoordType": "WGS84GEO",
                },
                headers={"Accept": "application/json", "Content-Type": "application/json", "appKey": self.app_key},
                timeout=(3, 8),
            )
            if response.status_code != 200:
                return -1, -1
            properties = response.json()["features"][0]["properties"]
            seconds = float(properties["totalTime"])
            distance = float(properties["totalDistance"])
            if not isfinite(seconds) or not isfinite(distance) or seconds < 0 or distance < 0:
                return -1, -1
            result = (max(1, ceil(seconds / 60)), ceil(distance))
        except (requests.RequestException, ValueError, TypeError, KeyError, IndexError, OverflowError):
            return -1, -1
        with self._lock:
            if len(self._cache) >= 512:
                self._cache.clear()
            self._cache[coords] = (monotonic() + 900, result)
        return result
