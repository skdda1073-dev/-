"""TAGO 정류소·노선·도착정보 조회만 담당한다. 응답에서 인증키를 노출하지 않는다."""

from copy import deepcopy
from threading import RLock
from time import monotonic
from urllib.parse import unquote
from xml.etree import ElementTree

import requests


class PublicDataError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = str(code)

    @property
    def authorization_failed(self):
        return self.code in {"20", "30", "31", "401", "403"}


class PublicTransitClient:
    BASE_URL = "https://apis.data.go.kr/1613000"
    PAGE_SIZE = 100
    MAX_PAGES = 20

    def __init__(self, service_key, city_code, requester=None):
        self.service_key = unquote(service_key)
        self.city_code = str(city_code)
        self.requester = requester or requests.get
        self._cache = {}
        self._lock = RLock()

    @staticmethod
    def _provider_error_code(payload=None, xml_text=""):
        """게이트웨이 오류에서도 숫자 코드만 읽고 응답 원문은 노출하지 않는다."""
        values = []
        if isinstance(payload, dict):
            gateway = payload.get("OpenAPI_ServiceResponse", payload)
            if isinstance(gateway, dict) and isinstance(gateway.get("cmmMsgHeader"), dict):
                values.append(gateway["cmmMsgHeader"].get("returnReasonCode"))
            result = payload.get("response")
            if isinstance(result, dict) and isinstance(result.get("header"), dict):
                values.append(result["header"].get("resultCode"))
        if xml_text:
            try:
                root = ElementTree.fromstring(xml_text)
            except ElementTree.ParseError:
                pass
            else:
                values.extend(node.text for node in root.iter()
                              if node.tag.rsplit("}", 1)[-1] in {"returnReasonCode", "resultCode"})
        for value in values:
            code = str(value).strip()
            if 1 <= len(code) <= 3 and code.isascii() and code.isdigit() and int(code) != 0:
                return code
        return None

    def _page(self, service, operation, parameters, page):
        if not self.service_key.strip():
            raise PublicDataError("PUBLIC_DATA_KEY_MISSING", "버스 인증키가 없어 공식 노선·정류소 정보를 조회할 수 없습니다. TAGO_API_KEY 또는 기존 PUBLIC_DATA_KEY를 설정하세요.")
        params = {
            "serviceKey": self.service_key,
            "_type": "json",
            **parameters,
        }
        if page is not None:
            params.update(pageNo=page, numOfRows=self.PAGE_SIZE)
        try:
            response = self.requester(
                f"{self.BASE_URL}/{service}/{operation}",
                params=params,
                timeout=(3, 8),
                allow_redirects=False,
            )
        except requests.RequestException:
            # requests 예외 문자열에는 serviceKey가 든 URL이 포함될 수 있다.
            raise PublicDataError("NETWORK", "공공데이터 서버에 연결하지 못했습니다.") from None
        try:
            payload = response.json()
        except (ValueError, requests.exceptions.JSONDecodeError):
            # 인증 실패는 _type=json을 지정해도 XML로 반환될 수 있다.
            code = self._provider_error_code(xml_text=response.text)
            if response.status_code != 200:
                raise PublicDataError(code or str(response.status_code), "공공데이터 서버 응답을 확인하지 못했습니다. API 활용신청·인증키를 확인하세요.") from None
            raise PublicDataError(code or "INVALID_RESPONSE", "공공데이터 응답을 해석하지 못했습니다. API 활용신청·인증키를 확인하세요.") from None
        code = self._provider_error_code(payload)
        if code:
            raise PublicDataError(code, "공공데이터 조회가 거절되었습니다. API 활용신청·인증키·호출 한도를 확인하세요.")
        if response.status_code != 200:
            raise PublicDataError(str(response.status_code), "공공데이터 서버 응답을 확인하지 못했습니다.")
        if not isinstance(payload, dict) or not isinstance(payload.get("response"), dict):
            raise PublicDataError("INVALID_RESPONSE", "공공데이터 응답 구조가 올바르지 않습니다.")
        result = payload["response"]
        header = result.get("header")
        if not isinstance(header, dict):
            raise PublicDataError("INVALID_RESPONSE", "공공데이터 헤더 구조가 올바르지 않습니다.")
        code = str(header.get("resultCode", ""))
        if code not in {"00", "0"}:
            raise PublicDataError("INVALID_RESPONSE", "공공데이터 조회가 거절되었습니다. API 활용신청·호출 한도를 확인하세요.")
        body = result.get("body") or {}
        if not isinstance(body, dict):
            raise PublicDataError("INVALID_RESPONSE", "공공데이터 본문 구조가 올바르지 않습니다.")
        items = body.get("items") or {}
        if not isinstance(items, dict):
            raise PublicDataError("INVALID_RESPONSE", "공공데이터 목록 구조가 올바르지 않습니다.")
        rows = items.get("item") or []
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise PublicDataError("INVALID_RESPONSE", "공공데이터 항목 구조가 올바르지 않습니다.")
        try:
            total = int(body["totalCount"]) if body.get("totalCount") is not None else None
            if total is not None and total < 0:
                raise ValueError("negative totalCount")
        except (TypeError, ValueError, OverflowError):
            raise PublicDataError("INVALID_RESPONSE", "공공데이터 전체 개수가 올바르지 않습니다.") from None
        return rows, total

    def _list(self, service, operation, parameters, ttl, *, paginated=True):
        cache_key = (service, operation, tuple(sorted(parameters.items())))
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and cached[0] > monotonic():
                return deepcopy(cached[1])
        rows = []
        previous_page = None
        for page in range(1, self.MAX_PAGES + 1):
            batch, total = self._page(service, operation, parameters, page if paginated else None)
            if batch and batch == previous_page:
                raise PublicDataError("INCOMPLETE", "공공데이터 페이지가 반복되어 전체 목록을 확인하지 못했습니다.")
            rows.extend(batch)
            if not paginated:
                if total is not None and len(rows) < total:
                    raise PublicDataError("INCOMPLETE", "공공데이터 목록 일부가 누락되었습니다.")
                break
            if total is not None and len(rows) >= total:
                break
            if not batch or (total is None and len(batch) < self.PAGE_SIZE):
                if total is not None and len(rows) < total:
                    raise PublicDataError("INCOMPLETE", "공공데이터 목록 일부가 누락되었습니다.")
                break
            previous_page = batch
        else:
            raise PublicDataError("INCOMPLETE", "공공데이터 전체 목록이 조회 한도를 초과했습니다.")
        with self._lock:
            # 임의 위치 검색으로 메모리 캐시가 무한히 커지는 것을 방지한다.
            if len(self._cache) >= 1024:
                self._cache = {key: value for key, value in self._cache.items() if value[0] > monotonic()}
                if len(self._cache) >= 1024:
                    self._cache.pop(next(iter(self._cache)))
            self._cache[cache_key] = (monotonic() + ttl, deepcopy(rows))
        return rows

    def nearby_stops(self, lat, lon):
        rows = self._list(
            "BusSttnInfoInqireService", "getCrdntPrxmtSttnList",
            {"gpsLati": lat, "gpsLong": lon}, 300,
        )
        return [row for row in rows if str(row.get("citycode", self.city_code)) == self.city_code]

    def station_routes(self, node_id):
        return self._list(
            "BusSttnInfoInqireService", "getSttnThrghRouteList",
            {"cityCode": self.city_code, "nodeid": str(node_id)}, 3600,
        )

    def route_stops(self, route_id):
        rows = self._list(
            "BusRouteInfoInqireService", "getRouteAcctoThrghSttnList",
            {"cityCode": self.city_code, "routeId": str(route_id)}, 3600,
        )
        if any(str(row.get("routeid", route_id)).strip() != str(route_id) for row in rows):
            raise PublicDataError("ROUTE_DATA", "조회한 노선과 경유 정류소의 노선 ID가 일치하지 않습니다.")
        return rows

    def route_info(self, route_id):
        """공식 노선 ID에 대응하는 표시번호를 단건 조회한다. ID로 번호를 추측하지 않는다."""
        rows = self._list(
            "BusRouteInfoInqireService", "getRouteInfoIem",
            {"cityCode": self.city_code, "routeId": str(route_id)}, 3600,
            paginated=False,
        )
        number = rows[0].get("routeno") if len(rows) == 1 else None
        if (len(rows) != 1 or str(rows[0].get("routeid", "")).strip() != str(route_id)
                or not isinstance(number, (str, int)) or isinstance(number, bool)
                or not str(number).strip()):
            raise PublicDataError("ROUTE_DATA", "공식 노선번호와 노선 ID의 대응을 확인하지 못했습니다.")
        return rows[0]

    def arrivals(self, node_id):
        if not self.service_key.strip():
            raise PublicDataError("ARRIVAL_KEY_MISSING", "버스 인증키가 없어 실시간 도착정보를 조회하지 않았습니다. TAGO_API_KEY 또는 기존 PUBLIC_DATA_KEY를 설정하세요.")
        # arrtime은 조회 시점 기준 초 단위. 오래된 캐시로 탑승 가능 여부를 판단하지 않는다.
        return self._list(
            "ArvlInfoInqireService", "getSttnAcctoArvlPrearngeInfoList",
            {"cityCode": self.city_code, "nodeId": str(node_id)}, 0,
        )
