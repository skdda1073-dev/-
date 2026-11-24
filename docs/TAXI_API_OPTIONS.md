# 권오준 담당: 택시 API 조사와 연결 점검

수정일: 2026-11-10. 이 문서는 현재 브랜치의 코드, 사용자가 제공한 연결 점검 결과와 아래 공식 문서를 기준으로 한다.

## 결론과 범위

**기존 TMAP 자동차 경로 연동을 사용한다.** 권오준 사용자의 CLI 점검에서 `TAXI_OK`, 예상 주행시간 582초, 거리 3,280m, 예상요금 8,310원을 확인했다. 이는 해당 구간의 점검 당시 응답이며 고정 요금이나 모든 구간의 성공을 보장하는 값은 아니다. 일반 택시 탭에서 이 서비스를 직접 조회하도록 화면을 연결했다.

이 기능은 **택시 예상 이동 정보**를 제공한다. 택시 호출·배차·예약·결제, 주변 차량 위치, 배차 대기 시간은 제공하지 않는다. 2·3·4명 분담 금액은 예상요금을 나눈 앱의 계산이며 결제나 정산 결과가 아니다. 현재 담당 목록의 “택시 API 찾기”와 실제 호출 서비스 개발은 범위가 다르다.

## 현재 구현 상태

| 위치 | 확인한 동작 |
|---|---|
| `app.py` | 프로젝트 설정으로 `TaxiService`를 만들고 택시 Blueprint 등록 |
| `services/taxi_routes.py` | `POST /api/taxi/estimate`; 캠퍼스 건물 또는 입력한 좌표를 출발지로 사용 |
| `services/taxi_service.py` | TMAP 자동차 경로 조회, 예상요금·시간·거리 검증, 오류 코드, 짧은 메모리 캐시 |
| `static/js/api-service.js` | 공통 출발지·목적지로 택시만 조회하며, 입력 변경 시 진행 중 요청과 오래된 견적을 무효화 |
| `static/js/taxi-controller.js` | 일반 택시 탭의 조회·갱신 버튼, 시간·거리·예상요금, 2~4명 분담액, 오류·만료 상태 표시 |
| `static/js/gps-controller.js` | 명시적인 조회에서 기존 위치 확인·권한 안내·짧은 위치 캐시를 재사용 |
| `templates/index.html` | 일반 택시 탭에 견적 카드와 스크립트를 연결. 기존 택시팟 UI 시연은 유지 |
| `scripts/check_backend.py` | 장소 검색·도보·버스 점검용. 택시 견적 실호출은 점검하지 않음 |

### 앱에서 사용하는 순서

1. 상단에서 출발 캠퍼스와 교내 건물을 고르거나 현재 위치 출발을 선택한다.
2. 도착할 장소를 검색하고 검색 결과 중 한 곳을 선택한다. 검색어만 입력한 상태는 목적지 선택이 아니다.
3. **택시** 탭에서 **택시 예상요금 조회**를 누른다. 버스 경로 조회는 필요하지 않다.
4. 예상 주행시간·거리·요금과 2~4명 분담액을 확인한다. 출발지나 목적지를 바꾸면 이전 견적은 지워지며 다시 조회해야 한다.

페이지를 열거나 택시 탭을 선택하는 것만으로 위치 권한을 요청하거나 택시 API를 호출하지 않는다. GPS 출발을 선택하고 조회한 경우에만 기존 위치 확인 로직을 사용한다. 위치를 확인하지 못하면 교내 건물을 선택해 조회할 수 있다.

요금이 없는 부분 응답에서는 확인된 시간·거리만 표시하며, 같은 지점의 조회는 이동 불필요로 안내한다. 분담액은 서버가 계산한 정수 원 단위 결과이며 합계가 예상요금과 일치한다. 견적이 만료되면 갱신 안내를 표시한다.

실제 동승 모집 화면은 여전히 `real-mobility-template` 안에 있으며 일반 페이지에서 활성화하지 않는다. 택시 탭 아래의 기존 택시팟은 시연용이다. `?mobility=real`로 실제 모집을 켜는 기능은 없다. 이전 동승 구현 설명은 [TAXI_RIDE_ROOMS.md](TAXI_RIDE_ROOMS.md)를 참고한다.

## TMAP 설정과 실제 요청

1. [SK Open API](https://openapi.sk.com/)에서 앱과 [TMAP 상품](https://openapi.sk.com/products/detail?linkMenuSeq=13)을 확인한다.
2. 해당 앱의 자동차 경로 및 사용 중인 옵션 권한을 확인하고 appKey를 준비한다. 기존 도보 키가 있다는 사실만으로 모든 자동차 경로 옵션의 권한을 확정하지 않는다.
3. 프로젝트 루트의 개인 `.env`에서 `TMAP_APP_KEY`를 설정한다. 기존 설정은 유지한다. 키는 서버 설정으로만 읽고 브라우저 코드나 명령행 인자로 넣지 않는다.
4. 웹 앱에서 사용할 때는 서버를 다시 시작한다. 아래 CLI는 매번 설정을 새로 읽는다.

`services/settings.py`는 실행 위치와 무관하게 프로젝트의 `.env`를 읽고, 같은 이름의 실행 환경 변수가 있으면 그것을 우선한다. `TMAP_APP_KEY` 존재 여부는 인증 성공이나 상품 권한을 뜻하지 않는다.

기존 서비스의 실제 요청은 다음과 같다. `searchOption=24`(택시 전용)와 `commercialVehicle=Y`(영업용)는 [현행 자동차 경로안내 본문](https://tmapapi.tmapmobility.com/webservice/docs/tmapRouteDoc.html)에서 확인했다. 합계 응답은 [자동차 경로안내](https://tmap-skopenapi.readme.io/reference/자동차-경로안내)와 [응답 예제](https://tmap-skopenapi.readme.io/reference/경로안내-샘플예제)도 참고한다.

- `POST https://apis.openapi.sk.com/tmap/routes?version=1`
- 인증: 서버의 `appKey` 헤더
- 전송: `requests.post(data=payload)` form 방식
- 좌표: `startX`·`endX`는 경도, `startY`·`endY`는 위도; 요청·응답 `WGS84GEO`
- 현재 옵션: `searchOption=24`, `commercialVehicle=Y`, `carType=1`, `totalValue=2`
- 합계 필드: `taxiFare`는 예상 택시요금(원), `totalTime`은 초, `totalDistance`는 m

`totalFare`는 별도 경로 총요금이다. 현재 서비스는 이를 `fare.provider_total_fare_krw`로 구분하고 택시요금에 합산하지 않는다. 택시요금이 없으면 시간·거리를 유지한 `partial / TAXI_FARE_UNAVAILABLE` 응답을 사용하며, 요금과 분담액은 null이다. 통행료·할증·호출료 포함 여부, 대기 시간, 문 앞에서 문 앞까지의 총시간도 확인되지 않은 값으로 구분한다.

캐시는 기본 60초, 설정 허용 범위 1~300초이며 원문 경로를 저장하지 않는다. 공급자 이용 조건과 실제 호출 한도는 [TMAP 약관](https://tmapapi.tmapmobility.com/terms.html) 및 계정에 적용된 상품에서 확인한다. 무료 한도나 가격을 코드에 고정하지 않는다.

## 키를 노출하지 않는 점검 도구

`scripts/check_taxi_api.py`는 기존 `TaxiService`를 사용한다. Flask 앱을 가져오지 않으며 채팅 DB를 만들거나 모집·호출·예약·결제를 실행하지 않는다. 새 의존성은 없다.

프로젝트 루트에서 기본 점검:

```powershell
.\.venv\Scripts\python.exe -B scripts\check_taxi_api.py
```

외부 API 호출 없이 다음 형식으로 **설정 존재 여부만** 출력한다.

```json
{"tmap_app_key_configured": true}
```

`true`는 키가 비어 있지 않다는 뜻이다. 실제 인증·요금 응답을 검증한 결과가 아니다. `.env`를 읽을 수 없거나 형식에 문제가 있으면 설정 오류 코드만 출력한다.

실제 연결을 확인하려면 사용자가 선택한 출발지와 목적지의 좌표 4개를 직접 제공한다. 아래 입력 안내에는 실제 장소나 임의의 예제 좌표가 들어 있지 않다.

```powershell
$taxiOriginLat = Read-Host "출발지 위도"
$taxiOriginLon = Read-Host "출발지 경도"
$taxiDestinationLat = Read-Host "도착지 위도"
$taxiDestinationLon = Read-Host "도착지 경도"
.\.venv\Scripts\python.exe -B scripts\check_taxi_api.py --live `
    --origin-lat $taxiOriginLat --origin-lon $taxiOriginLon `
    --destination-lat $taxiDestinationLat --destination-lon $taxiDestinationLon
```

Linux/macOS에서는 같은 옵션에 `./.venv/bin/python -B scripts/check_taxi_api.py`를 사용한다. `--help`로 인자를 확인할 수 있다.

`--live`는 API 조회 사용량이 발생할 수 있는 연결 점검이다. 좌표가 빠지거나 숫자가 아니거나 NaN·무한대·범위 밖이면 요청 전에 거절한다. 출발지와 목적지가 같으면 기존 서비스가 외부 요청 없이 `no_travel_required`를 반환한다. 이는 무료 택시나 외부 API 인증 성공을 의미하지 않는다.

성공 출력에는 다음 다섯 필드만 있다. 장소명·좌표·키·공급자 응답 원문은 출력하지 않는다.

| 필드 | 의미 |
|---|---|
| `status`, `code` | 기존 서비스의 상태와 허용된 코드 |
| `ride_duration_seconds` | 예상 차량 주행 시간, 초 |
| `distance_m` | 예상 주행 거리, m |
| `fare_amount_krw` | 예상 택시요금, 원. 확인 불가는 null |

| 종료 코드 | 의미 |
|---|---|
| 0 | 기본 점검에서 키 있음, 또는 실호출 결과 `ok` / `no_travel_required` |
| 2 | 인자·좌표·설정 문제 또는 키 없음 |
| 3 | 공급자 인증·한도·네트워크·응답 오류 또는 점검 실패 |
| 4 | 시간·거리는 받았으나 요금은 확인하지 못한 부분 성공 |

오류 시 `status=error`와 안전한 코드만 출력한다. `TAXI_PROVIDER_AUTH`면 상품 권한과 키를 확인하고, `TAXI_PROVIDER_QUOTA`면 계정 한도를 확인한다. `TAXI_ARGUMENTS_INVALID`면 `--help`를 확인한다. 키를 확인하려고 출력하거나 채팅에 붙여 넣을 필요는 없다.

이 도구는 서비스와 공급자 사이의 연결을 확인한다. Flask 라우트·브라우저 표시, 실제 미터요금 정확도, 배차 가능 여부까지 검증하는 도구는 아니다. 실제 연결 성공은 사용자가 자신의 PC에서 실행해 제공한 CLI 응답으로 확인했다. 개발 환경에서 사용자의 키를 복사하거나 추가 실호출하지 않았다.

## 대안 API 비교

| 항목 | TMAP: 현재 사용 | Kakao Mobility 자동차 길찾기 | NAVER Directions5 |
|---|---|---|---|
| 요청 | `POST /tmap/routes?version=1` | `GET /v1/directions` | `GET /map-direction/v1/driving` |
| 서버 | `apis.openapi.sk.com` | `apis-navi.kakaomobility.com` | `maps.apigw.ntruss.com` |
| 인증 | `appKey` | `Authorization: KakaoAK {REST_API_KEY}` | `x-ncp-apigw-api-key-id` + `x-ncp-apigw-api-key` |
| 요금 | 합계 `properties.taxiFare` | `routes[0].summary.fare.taxi` | `route.traoptimal[0].summary.taxiFare` |
| 시간 | `totalTime`: 초 | `summary.duration`: 초 | `summary.duration`: **밀리초** |
| 거리 | `totalDistance`: m | `summary.distance`: m | `summary.distance`: m |
| 별도 비용 | `totalFare` | `summary.fare.toll` | `summary.tollFare` |
| 적용 판단 | 기존 서비스·설정 재사용 | 공급자 변경 시 후보 | 기존 Naver 지도와 서버 Directions5 설정은 별개 |

Kakao의 공개 `/v1/directions`는 [공개 자동차 길찾기 문서](https://developers.kakaomobility.com/guide/navi-api/directions)를 따른다. `origin=경도,위도`, `destination=경도,위도`, `priority=RECOMMEND`, 필요 시 `summary=true`를 사용하고 경로의 `result_code`를 확인한다. [시작 가이드](https://developers.kakaomobility.com/guide/navi-api/start)에 따라 앱의 REST API 키를 준비한다. 별도의 `/affiliate/v1/directions`는 제휴 계약용이므로 혼동하지 않는다.

NAVER는 [Directions5 공식 문서](https://api.ncloud-docs.com/docs/ko/application-maps-directions5)를 따른다. 위 요금 경로는 `option=traoptimal` 기준이며 선택한 옵션에 따라 `route`의 키가 달라진다. 프로젝트의 `NAVER_MAP_CLIENT_ID`만으로 서버 길찾기를 사용할 수 있다고 가정하면 안 된다. 서버 API 키와 해당 상품 설정이 필요하다.

## 실제 택시 호출 가능 여부

[Kakao Mobility 현행 개발자 제품 목록](https://developers.kakaomobility.com/)에서 일반 개발자가 즉시 이용할 공개 택시 호출 API는 확인하지 못했다. 공개 자동차 길찾기의 예상 택시요금은 호출 API가 아니다. [2025년 공식 담당자 답변](https://devtalk.kakao.com/t/api/145839)도 실시간 택시 위치 API 미제공과 기술 제휴 문의를 안내한다. 이 답변은 위치 API에 관한 것이므로 모든 제휴 호출 가능성을 부정하는 근거로 확대하지 않는다.

팀 보고에는 “TMAP으로 예상 시간·거리·요금의 실제 응답을 확인했고 일반 택시 탭에 조회와 2~4명 예상 분담액 표시를 연결했다. 실제 택시 호출·배차는 별도의 공급자 지원 또는 제휴 확인이 필요하다”라고 구분하면 된다.
