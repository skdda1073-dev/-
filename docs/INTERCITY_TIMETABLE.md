# 시외버스 시간표 설정과 확인

시외버스 기능은 국토교통부 TAGO의 **한국 시간 기준 오늘 배차**를 조회한다. 출발·도착은 시외버스 터미널이다. 표시되는 소요 시간에는 캠퍼스에서 터미널까지의 이동이 포함되지 않는다.

공식 서비스는 현재 당일 배차만 제공한다. 터미널 목록에 이름이 있어도 선택한 구간의 배차가 제공된다고 보장할 수 없다. `empty`는 공공데이터에서 배차를 확인하지 못했다는 뜻이며, 실제 운행이 없다는 판정이 아니다. [공식 서비스 안내](https://www.data.go.kr/data/15098541/openapi.do)

## 1. API 사용 설정

공공데이터포털에서 **국토교통부_(TAGO)_시외버스정보** 활용신청과 승인 상태를 확인한다. 기존 시내버스·철도 등 다른 TAGO 서비스의 키가 동작해도 시외버스 서비스 권한은 별도로 확인해야 한다.

현재 프로젝트의 `app.py` 옆 `.env`에 발급받은 키를 저장한다. 기존 `.env`를 예제로 덮어쓰지 않는다.

```dotenv
INTERCITY_DATA_KEY=
```

`INTERCITY_DATA_KEY`가 비어 있으면 기존 `PUBLIC_DATA_KEY`를 사용한다. 설정값은 서버에서만 사용하며 CLI 인자로 받지 않는다. 키를 채팅·스크린샷·Git에 넣지 않는다. 실제 키 대신 존재 여부와 오류 코드로 연결을 점검할 수 있다.

## 2. 설정만 확인 — 외부 호출 없음

아래 명령은 **현재 캡스톤 프로젝트 루트**에서 PowerShell로 실행한다.

```powershell
$capstonePython = ".\.venv\Scripts\python.exe"
& $capstonePython .\scripts\check_intercity_api.py
```

이 프로젝트 안에 가상환경이 없고 이전 `hallym_capstone`의 가상환경을 사용 중이면, 기존에 실행되던 경로를 사용한다. 대화에서 확인한 폴더 구조의 예시는 다음과 같다.

```powershell
$capstonePython = "..\..\hallym_capstone\.venv\Scripts\python.exe"
& $capstonePython .\scripts\check_intercity_api.py
```

결과는 `{"intercity_data_key_configured": true}` 또는 `false`다. `true`는 설정이 있다는 뜻이며, 서비스 권한이나 실제 배차 조회 성공을 뜻하지 않는다. 이 명령은 Flask를 실행하거나 외부 API에 접속하지 않는다. Linux/macOS에서는 `.venv/bin/python scripts/check_intercity_api.py`를 사용한다.

## 3. 실제 터미널과 오늘 배차 확인

`--live`를 붙였을 때만 실제 API 조회를 수행한다. 기본 검색어는 `춘천`이다.

```powershell
& $capstonePython .\scripts\check_intercity_api.py --live
& $capstonePython .\scripts\check_intercity_api.py --live --query "서울"
```

출력의 `terminals`에서 이름·지역을 확인하고 실제 `id`를 사용한다. 임의 ID, 다른 서비스의 고속버스 ID 또는 예전 문서의 샘플 ID를 넣지 않는다. 터미널 검색은 이름과 지역에 적용되며 `matched_count: 0`이면 검색어와 일치한 항목이 없다는 뜻이다.

```powershell
$capstoneDepartureId = Read-Host "조회 결과의 출발 터미널 id"
$capstoneArrivalId = Read-Host "조회 결과의 도착 터미널 id"
& $capstonePython .\scripts\check_intercity_api.py --live --departure-id $capstoneDepartureId --arrival-id $capstoneArrivalId
```

출발·도착 ID는 반드시 함께 전달한다. 날짜는 한국 시간의 오늘로 자동 선택한다. 시간표 출력은 `trip_count`와 앞 5개 `preview_trips`를 제공한다. `preview_limit: 5`는 출력 미리보기의 한도이며, 전체 조회를 5편으로 제한한다는 뜻은 아니다.

| 결과 | 의미와 다음 확인 |
|---|---|
| `ok` | 조회·검증 완료. 실제 운행 변경과 최종 요금은 공식 예매처 확인 |
| `partial` | 배차 일부의 도착 시각·등급·요금이 없거나 잘못된 항목이 제외됨. `warnings` 확인 |
| `empty` | 현재 제공 데이터에서 배차를 찾지 못함. 공식 예매처 확인 |
| `INTERCITY_KEY_MISSING` | 유효한 설정이 없음. 두 키 설정의 존재 여부 확인 |
| `INTERCITY_PROVIDER_AUTH` | 해당 시외버스 서비스 활용신청·승인·키 사용 기간 확인 |
| `INTERCITY_PROVIDER_QUOTA` | 조회 한도에 도달함. 잠시 후 확인하거나 제공기관의 한도 확인 |
| `INTERCITY_INCOMPLETE` | 페이지가 반복·누락·변경됨. 일부 결과를 완전한 시간표로 표시하지 않음 |
| `INTERCITY_SCHEDULE_RESPONSE` | 반환된 배차의 날짜·출발 시각을 검증할 수 없음 |
| `INTERCITY_PROVIDER_NETWORK` / `INTERCITY_PROVIDER_TIMEOUT` | 연결 또는 응답 지연. 잠시 후 다시 확인 |

CLI는 JSON을 출력하며 공급자의 오류 원문·인증키·요청 URL을 출력하지 않는다. 종료 코드는 `0`=조회 또는 설정 확인 완료, `2`=인자·설정·사용자 입력 확인 필요, `3`=제공 서비스 오류, `4`=부분 배차다. 빈 배차 조회는 정상 응답이므로 `0`일 수 있다.

## 4. 웹 화면 확인

```powershell
& $capstonePython -m flask --app app:app run --host 127.0.0.1 --port 5051 --no-reload
```

브라우저에서 `http://127.0.0.1:5051/`을 열어 시외버스 시간표 영역을 확인한다. `.env`를 바꾼 뒤에는 서버를 다시 시작한다.

## API 계약

| 앱 경로 | 입력과 결과 |
|---|---|
| `GET /api/intercity/config` | 외부 호출 없이 키 존재 여부, 오늘 날짜, `today_only` 정책, 출처·공식 링크 |
| `GET /api/intercity/terminals?q=춘천` | 검증한 전체 터미널 목록을 이름·지역으로 검색. `id`, `name`, `city` 반환 |
| `GET /api/intercity/timetable` | `departure_id`, `arrival_id` 필수. `date=YYYY-MM-DD` 생략 시 오늘; 오늘 이외 날짜 거부 |

중복·허용되지 않은 쿼리는 거부한다. 입력 오류는 JSON `400`, 키·권한·한도 오류는 JSON `503`이다. 다른 제공 오류는 `502` 또는 지연 시 `504`다. JSON의 `code`로 원인을 구분한다.

제공기관 기본 URL은 `https://apis.data.go.kr/1613000/SuburbsBusInfo`다. 터미널은 `GetSuberbsBusTrminlList`, 배차는 `GetStrtpntAlocFndSuberbsBusInfo`를 사용한다. 배차 요청의 `depTerminalId`, `arrTerminalId`는 조회된 ID이고, `depPlandTime`은 `YYYYMMDD`다. 응답의 출발·도착 시각은 공식 설명의 12자리 `YYYYMMDDHHMI`와 방어적 14자리 형식을 처리한다. 요금 `charge`의 단위는 원이며, 알 수 없는 요금은 `null`로 유지한다. [공식 활용 가이드](https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId=FILE_000000003605927&fileDetailSn=1)

## 검증 범위와 공식 확인 경로

`tests/test_intercity.py`의 28개 테스트는 가짜 키·응답·시계를 사용한다. 인증 오류, 페이지 누락, 날짜 경계, 요금 미제공, 실제 빈 응답과 오류의 구분, JSON 응답 계약을 검증한다. Flask 테스트 클라이언트로 화면·정적 파일·설정·키 누락 응답도 확인했다. **사용자 키를 이용한 실시간 구간 조회와 실제 브라우저 화면 점검은 사용자 환경에서 확인해야 한다.** 모의 테스트 통과가 실제 춘천 노선 데이터 제공을 보장하지 않는다.

```powershell
& $capstonePython -m unittest discover -s tests -p test_intercity.py
```

공식 확인 링크: [버스타고](https://www.bustago.or.kr/newweb/kr/index.do), [티머니 시외버스](https://txbus.t-money.co.kr/). 이 링크는 사용자 확인·예매 화면이며, 본 기능에서 별도의 예약·결제를 실행하지 않는다.
