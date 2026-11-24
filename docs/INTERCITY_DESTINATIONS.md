# 춘천 출발 터미널 목록과 시간표

## 화면 동작

시외버스 탭을 열면 출발지 `춘천`을 선택하고, 연결된 도착 터미널·공항 목록을 바로 표시한다. 기본 목록에는 이름만 표시한다. 동명이인 시설에는 지역을 붙이고, 원주의 직통·경유처럼 실제 조회 구간이 다른 경우에는 구분을 유지한다. 검색어 없이도 기본 목록 전체를 볼 수 있으며 30개로 자르지 않는다.

간척2리 같은 다른 하차지는 기본 목록에 섞지 않는다. `다른 하차지도 보기`를 선택하면 같은 출발지에 연결된 나머지 하차지도 조회할 수 있다. 두 목록의 모든 항목은 시간표 조회에 사용할 수 있는 ID를 갖는다. `시간표 연결 확인 필요`라는 회색 선택 항목은 사용하지 않는다.

출발지를 바꾸면 이전 도착지와 시간표를 비운다. 늦게 도착한 이전 요청은 반영하지 않는다. 출발·도착 교환도 새 출발지의 연결 목록을 확인한 뒤 역방향 항목이 있을 때만 선택한다.

## 시설 분류와 실제 운행의 구분

`data/chuncheon_intercity_terminals.json`은 출처를 대조한 **시설 분류 자료**이며 고정 시간표가 아니다. 원본 버스타고 ID·이름·지역, 표시 이름, 터미널/공항 구분, 확인한 시설명과 출처를 기록한다.

2026-11-10 검토 자료에는 52개 선택 항목(터미널 49개, 공항 3개)이 있다. 원주 직통·경유의 두 ID를 별도 항목으로 포함하므로 물리적 시설 52곳이라는 뜻은 아니다.

기본 목록은 이 시설 자료와 **현재 버스타고 춘천 출발 연결 목록의 교집합**이다. ID뿐 아니라 현재 이름·지역까지 일치해야 기존 표시 이름을 적용한다. 시설 자료에 있어도 현재 연결 목록에 없으면 표시하지 않는다. 이름·지역이 변경되거나 확인하지 못한 항목은 추가 하차지에 원문 이름으로 유지한다.

프로그램에서 `터미널`이라는 접미사, TOP22 여부, 인터넷 예매 가능 여부, 예매 가능 일수 0을 기준으로 필터링하지 않는다. 춘천 외의 출발지는 해당 출발지에 연결된 예매처 목록을 그대로 표시한다.

목록에 있다는 사실은 특정 날짜의 실제 배차나 잔여 좌석을 보장하지 않는다. 선택 후 오늘 시간표를 조회한다. 빈 시간표와 제공처 오류를 구분하며, 어느 쪽도 노선 폐지로 단정하지 않는다. 철원(사)·지포리(사)·호산처럼 기본 시설 분류를 확정하지 못한 항목도 추가 하차지에서 선택할 수 있다.

## 목록과 시간표가 같은 ID를 사용

웹 기능은 `BookingIntercityService`를 사용한다. 출발 목록, 도착 목록, 시간표가 모두 [버스타고 공개 예매 화면](https://www.bustago.or.kr/newweb/kr/ticket/ticket.do)의 같은 ID를 사용한다. 앱의 `BUSTAGO_2037`과 `BUSTAGO_0001`은 원본 `2037`과 `0001`을 보존한다. 선행 0과 영문자를 유지하며 TAGO의 이름과 비교해 ID를 추정하지 않는다.

기존 TAGO 서비스와 `scripts/check_intercity_api.py`는 별도 공공 API 진단용으로 유지한다. 웹 시간표 조회에는 공공데이터 키를 전달하지 않는다. `.env`는 변경하지 않는다.

## 공개 웹 조회 계약

별도 공식 OpenAPI 계약이 아니라 현재 공개 웹 화면에서 확인한 읽기 요청이다. 제공처가 요청·응답 구조를 바꾸면 어댑터를 점검해야 한다. 로그인·사용자 쿠키·좌석 선택·예약 생성·결제 요청은 사용하지 않는다.

| 목적 | POST 경로 | 주요 입력 | 응답 배열 |
|---|---|---|---|
| 출발지 | `/newweb/kr/common/terminalListAjax.do` | `area=`, `searchTerminalNm=` | `terminalList` |
| 연결 도착지 | `/newweb/kr/common/terminalEndListAjax.do` | `area=`, `country=`, `terCode=<출발 ID>` | `terminalEndList` |
| 선택 구간 시간표 | `/newweb/kr/ticket/ticketListJson3.do` | 아래 폼 | `ticketSingleList` |

시간표 폼:

```text
startType=S
orderDate=YYYYMMDD
orderBackDate=
depTerId=<출발 원본 ID>
arrTerId=<도착 원본 ID>
depTime=0000
arrTime=0000
goBusGrade=
goBackBusGrade=
```

호스트는 `https://www.bustago.or.kr`로 고정한다. 목록은 성공 응답만 6시간·최대 64개, 시간표는 성공 응답만 60초·최대 128개로 캐시한다. 연결/읽기 제한은 10초/20초다. 추측한 페이지 인자, 빈 도착 ID를 쓰거나 모든 목적지의 시간표를 대량 요청하지 않는다.

시간표의 출발 ID·도착 ID·날짜가 요청과 다르면 응답 전체를 거부한다. `DEP_DATE`와 `DEP_TIME`이 출발 시각, `DIST_TIME`는 소요시간(분), `FARE0`는 성인 요금(원)이다. 직접 제공되지 않는 도착 시각은 소요시간이 있을 때만 계산하고 **도착 예상**으로 표시한다. 소요시간·요금이 0 또는 미제공이면 `null`로 둔다.

공개 화면이 `--:--`로 표시하는 `TOT_SEAT_CNT=0`이면서 `DEP_TIME=2358/2359`인 행을 실제 밤 출발편으로 만들지 않는다. 현장 구매나 매진 여부는 시간표 자체를 제외하는 기준이 아니다.

## 앱 API

| 경로 | 계약 |
|---|---|
| `GET /api/intercity/config` | KST 오늘 날짜, `today_only`, 공개 조회 설정, 출처 |
| `GET /api/intercity/terminals?q=` | 출발지 검색. `{id,name,city}` 목록 |
| `GET /api/intercity/destinations?departure_id=` | 기본 `destinations`와 별도 `additional_stops`. 모든 항목은 문자열 ID |
| `GET /api/intercity/timetable?departure_id=&arrival_id=&date=` | 출발지 연결 목록에 실제로 있는 도착지의 오늘 배차 |

도착 목록에는 `departure`, `today`, `fetched_at`, `from_cache`, `source`, `warnings`, `total_count`, `additional_count`, `display_scope`도 포함한다. 연결 목록이 있으면 `ok`, 없으면 `empty`다. 시간표는 정상 `ok`, 일부 정보 미제공 `partial`, 빈 응답 `empty`를 구분한다.

날짜는 앱의 현재 범위를 유지하기 위해 한국 시간의 오늘만 허용한다. 이는 버스타고 자체가 오늘만 제공한다는 뜻이 아니다. 조회 중 자정이 지나면 오늘 날짜로 다시 조회하도록 한다.

잘못된 ID·연결되지 않은 구간·다른 날짜·중복 또는 알 수 없는 쿼리는 400이다. 제공처 연결·HTTP·형식 오류는 502, 지연은 504다. 응답은 `Cache-Control: no-store`이며 오류 원문을 노출하지 않는다.

## 실행과 검증

프로젝트 루트에서 기존 가상환경으로 실행한다.

```powershell
$capstonePython = "..\..\hallym_capstone\.venv\Scripts\python.exe"
& $capstonePython -m flask --app app:app run --host 127.0.0.1 --port 5051 --no-reload
```

서버 재시작 후 `http://127.0.0.1:5051/`에서 Ctrl+F5를 누르고 시외버스 탭을 연다. 기본 도착 목록과 추가 하차지 보기를 확인한 뒤 원하는 도착지의 오늘 시간표를 조회한다.

```powershell
& $capstonePython -m unittest discover -s tests -p "test_intercity*.py"
node tests/intercity_frontend_unit.cjs
```

Python 검사는 가짜 응답·시계로 잘못된 구간 연결, 시설 분류, 오류와 빈 목록의 구분, 자정 변경, 시간·요금 해석을 검증한다. Node 검사는 실제 화면 제어 코드를 가상 DOM에서 실행한다. 별도 브라우저 검사는 `tests/intercity_frontend_smoke.cjs`다. 모의 검사 통과가 모든 목적지의 오늘 운행을 보장하지는 않는다.

2026-11-09 늦은 밤(KST)에 공개 요청으로 2026-11-10 춘천→동서울, 원주 직통, 원주 경유, 간척2리 응답 구조를 확인했다. 응답을 고정 시간표로 사용하지 않는다. 시설 분류의 원문 출처·확인 날짜는 JSON 자료에 기록한다.
