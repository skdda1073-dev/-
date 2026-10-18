"""춘천 목적지 검색: 교내 CSV와 TMAP POI를 안전한 동일 형식으로 반환한다."""

from math import isfinite
import re
import unicodedata

import requests


class PlaceSearchError(Exception):
    """사용자에게 전달해도 되는 코드·메시지·HTTP 상태만 보관한다."""

    def __init__(self, code, message, status_code=502):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class PlaceSearchService:
    URL = "https://apis.openapi.sk.com/tmap/pois"
    MAX_RESULTS = 8
    MAX_CAMPUS_RESULTS = 4
    MAX_QUERY_LENGTH = 100
    CAMPUS_NAMES = {"hallym": "한림대학교", "knu": "강원대학교"}
    CAMPUS_ALIASES = {
        "hallym": ("한림대학교", "한림대", "hallym"),
        "knu": ("강원대학교", "강원대", "knu"),
    }

    def __init__(self, app_key, campus_buildings, requester=None):
        self.app_key = str(app_key or "").strip()
        self.campus_buildings = campus_buildings
        self.requester = requester or requests.get

    @staticmethod
    def _text(value):
        return " ".join(unicodedata.normalize("NFC", value).split()) if isinstance(value, str) else ""

    @classmethod
    def _compact(cls, value):
        return cls._text(value).replace(" ", "").casefold()

    @staticmethod
    def _coordinates(lat, lon):
        if isinstance(lat, bool) or isinstance(lon, bool):
            return None
        try:
            latitude, longitude = float(lat), float(lon)
        except (ValueError, TypeError, OverflowError):
            return None
        # WGS84GEO 결과인지 검사한다. 투영 좌표·(0, 0)·잘못된 수치는 배제한다.
        if isfinite(latitude) and isfinite(longitude) and 33 <= latitude <= 39.5 and 124 <= longitude <= 132:
            return latitude, longitude
        return None

    def _campus_matches(self, query, campus):
        compact_query = self._compact(query)
        requested_campuses = set()
        for key, aliases in self.CAMPUS_ALIASES.items():
            if any(self._compact(alias) in compact_query for alias in aliases):
                requested_campuses.add(key)
        order = sorted(self.campus_buildings, key=lambda key: (key != campus, key))
        matches = []
        for key in order:
            if requested_campuses and key not in requested_campuses:
                continue
            school = self.CAMPUS_NAMES.get(key, key)
            building_query = compact_query
            for alias in self.CAMPUS_ALIASES.get(key, ()):
                building_query = building_query.replace(self._compact(alias), "")
            building_query = building_query.removeprefix("춘천시").removeprefix("춘천")
            for name, raw_coords in self.campus_buildings[key].items():
                name = self._text(name)
                if not name or building_query not in self._compact(name):
                    continue
                if not isinstance(raw_coords, (tuple, list)) or len(raw_coords) != 2:
                    continue
                coords = self._coordinates(*raw_coords)
                if coords is None:
                    continue
                full_name = f"{school} {name}"
                matches.append({
                    "id": f"campus:{key}:{name}", "name": full_name,
                    "sub": f"춘천시 {school} 교내 건물", "lat": coords[0], "lon": coords[1],
                    "source": "campus", "campus": key, "coordinate_source": "campus_csv",
                })
        # 정확히 일치한 건물이 단순 부분 일치보다 먼저 보이게 한다.
        matches.sort(key=lambda item: (
            self._compact(item["name"]) != compact_query
            and self._compact(item["name"].split(" ", 1)[-1]) != compact_query,
            item["campus"] != campus,
        ))
        return matches[:self.MAX_CAMPUS_RESULTS]

    @classmethod
    def _poi_coordinates(cls, poi):
        # pns*는 보행자 입구, front*는 일반 입구, noor*는 건물 중심점이다.
        for prefix in ("pns", "front", "noor"):
            coords = cls._coordinates(poi.get(f"{prefix}Lat"), poi.get(f"{prefix}Lon"))
            if coords is not None:
                return coords, prefix
        return None, None

    @classmethod
    def _address(cls, poi):
        address_parts = [cls._text(poi.get(field)) for field in (
            "upperAddrName", "middleAddrName", "lowerAddrName", "detailAddrName",
        )]
        addresses = poi.get("newAddressList", {})
        rows = addresses.get("newAddress", []) if isinstance(addresses, dict) else []
        if isinstance(rows, dict):
            rows = [rows]
        road = next((cls._text(row.get("fullAddressRoad")) for row in rows
                     if isinstance(row, dict) and cls._text(row.get("fullAddressRoad"))), "") if isinstance(rows, list) else ""
        old_address = " ".join(part for part in address_parts if part)
        chuncheon = re.compile(r"(?:^|\s)춘천(?:시)?(?:\s|$)")
        if not chuncheon.search(old_address) and not chuncheon.search(road):
            return None
        return road or old_address

    def _remote_matches(self, query):
        if not self.app_key:
            raise PlaceSearchError("TMAP_KEY_MISSING", "TMAP_APP_KEY를 설정한 뒤 서버를 다시 시작해 주세요.", 503)
        keyword = query if "춘천" in query else f"춘천 {query}"
        try:
            response = self.requester(
                self.URL,
                params={"version": "1", "searchKeyword": keyword, "searchType": "all",
                        "searchtypCd": "A", "page": 1, "count": 20,
                        "reqCoordType": "WGS84GEO", "resCoordType": "WGS84GEO"},
                headers={"Accept": "application/json", "appKey": self.app_key},
                timeout=(3, 6), allow_redirects=False,
            )
        except requests.Timeout:
            raise PlaceSearchError("TMAP_TIMEOUT", "목적지 검색 응답이 늦습니다. 잠시 후 다시 검색해 주세요.", 504) from None
        except requests.RequestException:
            raise PlaceSearchError("TMAP_NETWORK", "목적지 검색 서버에 연결하지 못했습니다. 네트워크를 확인해 주세요.") from None
        status = response.status_code
        if status in (401, 403):
            raise PlaceSearchError("TMAP_AUTH", "TMAP 키와 장소(POI) 통합 검색 API 사용 권한을 확인해 주세요.")
        if status == 429:
            raise PlaceSearchError("TMAP_QUOTA", "TMAP 검색 호출 한도에 도달했습니다. 잠시 후 다시 검색해 주세요.", 503)
        if status != 200:
            raise PlaceSearchError("TMAP_HTTP", "목적지 검색 서비스가 정상 응답하지 않았습니다. 잠시 후 다시 시도해 주세요.")
        try:
            payload = response.json()
        except (ValueError, TypeError):
            raise PlaceSearchError("TMAP_RESPONSE", "목적지 검색 응답 형식을 확인할 수 없습니다.") from None
        info = payload.get("searchPoiInfo") if isinstance(payload, dict) else None
        if not isinstance(info, dict):
            raise PlaceSearchError("TMAP_RESPONSE", "목적지 검색 응답 형식을 확인할 수 없습니다.")
        count = info.get("totalCount")
        try:
            valid_count = not isinstance(count, bool) and int(str(count)) >= 0
        except (ValueError, TypeError, OverflowError):
            valid_count = False
        if not valid_count:
            raise PlaceSearchError("TMAP_RESPONSE", "목적지 검색 결과 개수를 확인할 수 없습니다.")
        if int(count) == 0:
            return []
        pois = info.get("pois")
        rows = pois.get("poi") if isinstance(pois, dict) else None
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list) or not rows:
            raise PlaceSearchError("TMAP_RESPONSE", "목적지 검색 결과 목록을 확인할 수 없습니다.")
        matches, seen = [], set()
        invalid_rows = 0
        for poi in rows:
            if not isinstance(poi, dict):
                invalid_rows += 1
                continue
            name = self._text(poi.get("name"))
            address = self._address(poi)
            coords, coordinate_source = self._poi_coordinates(poi)
            if not name or coords is None:
                invalid_rows += 1
                continue
            if address is None:
                continue
            provider_id = self._text(str(poi.get("id") or ""))
            identity = (provider_id or name, round(coords[0], 7), round(coords[1], 7))
            if identity in seen:
                continue
            seen.add(identity)
            matches.append({
                "id": f"tmap:{provider_id}" if provider_id else f"tmap:{name}:{coords[0]}:{coords[1]}",
                "poi_id": provider_id, "name": name, "sub": address,
                "lat": coords[0], "lon": coords[1], "source": "tmap",
                "coordinate_source": coordinate_source,
            })
        if invalid_rows == len(rows):
            raise PlaceSearchError("TMAP_RESPONSE", "검색 결과에 유효한 장소 좌표가 없습니다.")
        return matches

    def search(self, query, campus=None):
        """배열 계약 유지. 외부 장애 시 교내 결과에는 search_warning을 표시한다."""
        if not isinstance(query, str):
            raise PlaceSearchError("INVALID_QUERY", "검색어는 문자열이어야 합니다.", 400)
        query = self._text(query)
        if len(query) > self.MAX_QUERY_LENGTH or any(ord(char) < 32 for char in query):
            raise PlaceSearchError("INVALID_QUERY", "검색어는 100자 이내의 일반 문자로 입력해 주세요.", 400)
        if campus is not None and (not isinstance(campus, str) or campus not in self.campus_buildings):
            raise PlaceSearchError("INVALID_CAMPUS", "캠퍼스를 확인해 주세요.", 400)
        if not query:
            return []
        local = self._campus_matches(query, campus)
        try:
            remote = self._remote_matches(query)
        except PlaceSearchError as error:
            if not local:
                raise
            for item in local:
                item["search_warning"] = {"code": error.code, "message": str(error)}
            return local
        return (local + remote)[:self.MAX_RESULTS]
