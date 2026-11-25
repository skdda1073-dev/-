# 백엔드 실행과 API 연결 확인

## 팀원 첫 실행

`이준엽` 브랜치를 연 VS Code에서 프로젝트 루트의 터미널을 사용한다. 팀 검증에 사용한 버전은 Python 3.14.2다.

```sh
# macOS · Linux
python3 scripts/setup_dev.py
```

```powershell
# Windows PowerShell
py scripts/setup_dev.py
```

초기 설정은 `.venv`에 `requirements.txt`의 패키지를 설치한다. `.env`가 없을 때는 예제에서 초기 설정을 만들고 이 환경의 택시 채팅 세션용 비밀값을 생성한다. 기존 `.env`는 그대로 사용한다. 팀 환경에서 쓸 수 있도록 승인된 API 키를 별도 전달 경로로 받아 `.env`에 입력한다. 동승방 데이터베이스는 개인 실행 환경마다 따로 생성된다.

## 전국 길찾기용 MOTIS 실행

Apple Silicon macOS와 Linux x64·ARM64에서 아래 명령을 실행한다. 최초 실행은 [MOTIS v2.11.4](https://github.com/motis-project/motis/releases/tag/v2.11.4)와 [Geofabrik 대한민국 지도](https://download.geofabrik.de/asia/south-korea.html)를 내려받아 `.motis/`에 보행 및 대중교통 색인을 만든다. 다운로드와 색인 생성에는 인터넷과 여유 디스크 공간이 필요하다.

```sh
mkdir -p .motis
.venv/bin/python scripts/export_motis_gtfs.py --output .motis/metro.zip --days 14 --without-intercity-rail
.venv/bin/python scripts/setup_motis.py --gtfs .motis/metro.zip --korail --serve
```

다른 터미널에서 `.env`의 `MOTIS_BASE_URL=http://127.0.0.1:8889`를 설정하고 Flask를 다시 실행한다. Flask는 MOTIS와 기존 교통 데이터의 경로를 함께 조회해 확인 가능한 후보를 도착 시각으로 정렬한다. MOTIS가 중단되면 다른 공급자의 경로를 표시한다. 지도와 색인은 로컬에서 실행하며 루프백 설정에서는 앱의 길찾기 좌표를 MOTIS 외부 서비스에 전송하지 않는다.

`metro.zip`은 저장소의 공개 전철 운행표를 GTFS로 변환한 결과다. 지도는 Geofabrik이 배포한 대한민국 OpenStreetMap 데이터다. 코레일 운행정보가 반영된 구간만 실시간으로 표시한다. 전철은 시간표 기준이고, 버스·셔틀은 기존 앱 공급자의 조회 범위에 따른다. 전철 ZIP의 조회 기간을 연장하려면 공개 원본 시간표의 기준일을 확인한 뒤 ZIP 생성 명령을 다시 실행하고 MOTIS를 재시작한다.

`--korail`을 사용하면 Mikołaj Kuranowski가 배포하는 코레일 GTFS와 GTFS-RT를 함께 불러온다. 코레일 피드는 CC BY 4.0으로 배포되며, 지도 데이터는 OpenStreetMap 기여자가 제공한다. [코레일 피드](https://mkuran.pl/gtfs/) · [OpenStreetMap 저작권](https://www.openstreetmap.org/copyright)

## 1. 키 입력

프로젝트 루트의 `.env`를 VS Code에서 열고 작은따옴표 안에 값을 넣는다.

```dotenv
TMAP_APP_KEY='본인 TMAP 키'
PUBLIC_DATA_KEY='본인 공공데이터 키'
TAGO_API_KEY='시내버스 3종 API를 승인받은 계정의 키'
TRAIN_API_KEY='기존 ITX 조회에 사용하던 키'
NAVER_MAP_CLIENT_ID='본인 네이버 지도 클라이언트 ID'
APP_DEBUG=false
PORT=5050
```

실제 `.env`는 Git에서 제외한다. `.env.example`은 값이 없는 팀 공용 예제다. 팀원은 예제 파일을 `.env`로 복사한 뒤 각자 입력한다. 키를 채팅·스크린샷·PR 본문에 올리지 않는다. TMAP·공공데이터 키는 브라우저에 보내지 않는다. 네이버 지도 클라이언트 ID는 지도 SDK 로딩에 쓰이는 공개 식별자이며 시크릿을 넣는 칸이 아니다.

전처리 노트북에서 네이버 주소를 좌표로 변환하려면 `.env`에 서버용 `NAVER_GEOCODING_CLIENT_ID`와 `NAVER_GEOCODING_CLIENT_SECRET`을 입력한다. 노트북의 첫 번째 셀을 실행해 설정을 불러온다.

시내버스의 노선·정류소·도착 API는 `TAGO_API_KEY`를 우선 사용하고, 비어 있으면 기존 `PUBLIC_DATA_KEY`로 호환한다. 기존 공용 키가 다른 계정의 키라면 그대로 보존하고 본인 계정에서 승인받은 버스 키를 `TAGO_API_KEY`에 넣는다. 시외버스의 `INTERCITY_DATA_KEY → PUBLIC_DATA_KEY` 우선순위는 바뀌지 않는다. 두 키 항목에 같은 값을 넣으면 호출 권한도 같으며, 다른 계정의 활용신청 승인이 자동으로 적용되지는 않는다.

기존 `TAGO_API_KEY`는 ITX 조회와 `scripts/collect_itx.py`에서도 사용했다. 버스 키를 교체할 때는 기존 열차 키를 `TRAIN_API_KEY`에 보존한다. 두 열차 호출자는 `TRAIN_API_KEY`를 우선하며, 비어 있을 때만 이전 방식인 `TAGO_API_KEY`를 사용한다. 버스 키 교체로 열차의 인증 계정이 바뀌지 않게 하는 설정이다.

시내버스 경로 조회에는 `TAGO_API_KEY` 또는 기존 `PUBLIC_DATA_KEY`가 필요하다. 공공데이터포털에서 TAGO **버스정류소정보·버스노선정보·버스도착정보** 각각의 활용신청과 키 적용 상태를 확인한다. 같은 키로 정류소·도착 조회가 성공해도 노선상세·경유정류소 권한까지 확인된 것은 아니다. 공식 노선번호·순서를 확인하지 못하면 CSV나 노선 ID 숫자로 대체하지 않는다.

서버는 실행 위치와 무관하게 프로젝트 루트의 `.env`를 읽는다. 이미 터미널·배포 환경에 같은 이름의 환경변수를 설정했다면 그 값이 우선한다. 키를 바꾸면 서버를 종료하고 다시 시작한다. 변수 확장(`\${...}`)은 비활성화하여 키의 특수문자를 보존한다.

.env는 실행 파일이 아닌 설정 파일이다. VS Code에서 편집·저장하며 각 설정은 `이름=값` 한 줄로 작성한다. 설명은 `#`으로 시작하는 주석에 적고, 터미널 명령이나 일반 문장을 설정 줄에 섞지 않는다.

## 2. 실행

위 초기 설정을 마친 뒤 프로젝트 루트의 VS Code 터미널에서 다음 명령을 실행한다.

```bash
.venv/bin/python scripts/check_backend.py
.venv/bin/python scripts/check_backend.py --live
.venv/bin/python app.py
```

첫 번째 점검은 외부 API를 호출하지 않는다. 두 번째 `--live`는 **실제 TMAP 검색·보행 및 TAGO 정류소·노선·도착 API를 호출하므로 사용량이 발생한다**. 한림대 건물→춘천역 검색·경로를 점검하며 사용자의 실제 GPS를 수집하지 않는다. 키 값·원문 응답·인증 URL은 출력하지 않는다. 서버를 켜기 전에도 실행할 수 있다.

위 예시대로 PORT를 설정했다면 서버 실행 후 브라우저에서 `http://localhost:5050`을 연다. 이전 점검에서 네이버 지도는 이 주소에서 정상 표시됐고 `127.0.0.1`로 접속하면 지도 인증에 실패했다. 설정이 없을 때 코드 기본 포트는 5000이다. `Address already in use`가 나오면 다른 프로그램을 임의 종료하지 말고 사용 가능한 포트로 바꾼다. `Ctrl+C`로 서버를 종료한다. 기본값은 로컬 전용 주소와 디버그 비활성화다. 외부 공개용 운영 배포 설정은 별도 작업이다.

다른 Mac에서 처음 설치:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Windows PowerShell에서 프로젝트 폴더를 연 뒤:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# .env가 아직 없을 때만 예제를 복사한다. 기존 키 파일을 덮어쓰지 않는다.
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# .env에 키를 입력하고 저장한 뒤:
.\.venv\Scripts\python.exe scripts/check_backend.py
.\.venv\Scripts\python.exe scripts/check_backend.py --live
.\.venv\Scripts\python.exe app.py
```

가상환경 폴더 자체는 GitHub에 올리지 않는다. 다른 PC는 위 명령으로 새로 만든다. VS Code Python 인터프리터도 해당 프로젝트의 `.venv`를 선택한다.

## 3. 점검 결과 해석

| 결과 | 의미와 할 일 |
| --- | --- |
| 로컬 준비 완료 | 셔틀 참조 CSV·건물·TMAP·공공데이터 설정 존재 확인. API 인증 성공을 뜻하지 않음 |
| `TMAP_KEY_MISSING` | `.env`의 TMAP 키 입력 및 서버 재시작 |
| `TMAP_AUTH` | 키와 장소(POI) 통합 검색 API 사용 권한 확인 |
| `TMAP_QUOTA` | TMAP 호출 한도 확인 |
| `TMAP_TIMEOUT`, `TMAP_NETWORK` | 검색 서버 응답·연결 확인 |
| `TMAP_RESPONSE` | 응답 구조·유효한 장소 좌표를 해석하지 못함 |
| `WALK_UNAVAILABLE` | TMAP 보행자 경로 API 권한·키·한도·연결 확인. 버스가 없다는 뜻이 아님 |
| `PUBLIC_DATA_KEY_MISSING` | `.env`의 `TAGO_API_KEY` 또는 기존 `PUBLIC_DATA_KEY` 입력 및 서버 재시작. 오류코드 이름은 기존 계약을 유지 |
| `ROUTE_PERMISSION` | TAGO 버스노선정보 활용신청 승인·서비스 키 등록·반영 상태 확인 |
| `ROUTE_DATA` | 같은 노선 ID의 공식번호 또는 경유정류소 ID를 확인하지 못함. 다른 번호나 CSV로 대체하지 않음 |
| `CSV_DATA` | 보존된 프로젝트 CSV 파일·열·순번·인코딩 확인. 셔틀 참조·로컬 준비 상태에도 사용 |
| `NO_JOURNEYS` | 요청은 처리됐지만 검색 범위에서 경로 후보를 확인하지 못함. 경로 성공 검증으로 보지 않음 |
| 도착정보 `20`, `30`, `31`, `401`, `403` | 버스도착정보 활용신청·인증키·사용기한 확인 |
| 도착자료 없음 | 서버 응답은 받았지만 현재 운행 도착값은 검증하지 못함 |
| 부분 성공 | 장소·도보·공식 노선 기반 후보는 성공했지만 설정된 도착 API는 실패함 |

점검 도구 종료 코드: `0` 요청한 범위 통과, `2` 로컬 준비 실패, `3` 목적지 검색·경로 조회 실패, `4` 검색·경로는 성공했으나 설정된 실시간 도착 API 실패.

## 4. 제공 API

- `GET /health`: 설정의 존재 여부와 로컬 CSV·캠퍼스 준비 상태를 반환. 현재 필수 준비는 캠퍼스·셔틀 참조 CSV·TMAP 키·공공데이터 키이며, 준비되면 HTTP 200, 빠지면 503이다. `routing_mode`는 `tago_route_candidates`, `checks.bus_routes`는 TAGO 설정 유무다. `configured`는 인증 성공을 뜻하지 않으며 외부 호출 없이 `external_apis_verified: false`를 유지한다.
- `GET /search_poi?q=춘천역&campus=hallym`: 주소·좌표가 포함된 최대 8개 목적지 배열. 캠퍼스 인자는 선택한 학교 결과를 우선할 뿐 다른 학교 목적지를 배제하지 않는다.
- `POST /get_route_info`: GPS 또는 캠퍼스 건물을 출발지로 받아 기존 주변 정류장 배열 또는 목적지 경로 후보를 반환한다.

목적지 없는 주변 버스 화면도 API 모드에서는 도착정보의 노선 ID로 공식번호를 확인한다. 번호가 충돌하는 도착 항목은 제외하고, 공식 노선상세를 확인하지 못하면 빈 `buses`와 `arrival_error`로 안내한다.

목적지 검색은 명시적인 WGS84 좌표계를 사용하고 보행자 입구(`pnsLat/pnsLon`)→일반 입구→중심점 순으로 유효한 좌표를 선택한다. 이름이 같아도 ID·위치가 다르면 유지한다. 다른 도시의 주소는 춘천 결과에 섞지 않는다. 외부 검색이 실패해도 교내 결과는 `search_warning`과 함께 남기며, 교내 결과도 없으면 명확한 오류를 반환한다.

## 5. 확인된 범위와 남은 한계

현재 시내버스 추천은 정류소별 경유노선의 ID로 노선상세의 번호를 확인한 뒤 전체 경유 순서를 연결한다. `legs[].next_stop`으로 승차 직후 정류장을 내려주고 화면에 “다음 정류장 ○○ 방면”을 표시한다. 상·하행이 없는 `api_order` 응답은 순서 기반 후보로 남기며 방향·회차·배차와 총시간을 확정하지 않는다. 보존된 CSV는 셔틀 참조와 기존 데이터 검증에 사용한다.

자동 검증은 모의 외부 응답으로 공식번호 우선·ID 매칭·승차 직후 정류장·방향 경계·페이지 완전성·오류 처리를 확인한다. 기존 CSV 검증은 과거 자료의 처리 규칙에 관한 검증이다. 2026-10-16에 확인했던 CSV 기반 후보 3개는 현재 API 기반 시내버스 경로의 실환경 검증 결과로 간주하지 않는다.

2026-11-21 초기 실호출에서 기존 키의 정류소·도착정보는 성공했지만 노선정보는 HTTP 403·코드 30으로 거절됐다. 포털 승인 계정의 키와 파일 설정 키의 불일치를 확인해 사용자 승인 후 버스 전용 키를 교체했다. 이후 노선상세·300번의 경유 57개·정류소·도착 API 모두 정상 응답했고, 기존 열차 키도 별도로 보존해 열차 API 성공을 확인했다. 한림대 두 승강장에는 300번이 있고 400번은 없으며, 목적지 쪽 포스코더샵A에는 두 번호가 모두 있다. 목적지 쪽 노선만으로 승차 버스를 정하면 안 된다.

이전 점검에서 로컬 서버는 5050번 포트로 실행됐으며 `http://localhost:5050`의 네이버 지도·출발 마커, 학교 전환과 건물 목록 갱신을 확인했다. `127.0.0.1`에서는 지도 인증에 실패했으므로 주소를 혼용하지 않는다. 다른 주소나 배포 도메인은 네이버 클라우드의 등록된 Web 서비스 URL을 확인한다. [공식 인증 오류 안내](https://www.ncloud.com/support/faq/all/1047?searchKeyword=map)

기존 브라우저 점검에서 한림대 대학본부·인문1관 → 춘천역 검색·선택과 CSV 후보·지도 승하차 마커·정류장 연결선 표시, 390px 화면의 가로 넘침 여부를 확인했다. 2026-11-21 네이버 지도 UI에서 한림대학교 대표장소→맥도날드 춘천후평DT점의 직통 최적 경로가 300번, 다음 정류장 성심경로당, 하차 포스코더샵A인 것을 확인했다. 네이버 API는 추가하지 않았다. 5050 개발 서버도 최신 코드로 재시작해 실제 의학관→후평DT점 API 응답에서 같은 번호·다음 정류장·하차 정류장을 확인했다. 모든 건물·시각과 종점 회차의 연속 탑승까지 검증한 것은 아니다.

키를 연결했다고 운행 방향이 자동으로 검증되는 것은 아니다. 확인된 상·하행 경계는 연결하지 않고, 순서만 확인된 후보는 미확인 총시간을 숫자로 채우지 않는다. 상세 계산·제한·검증 범위는 `TRANSIT_ROUTING.md`, 보존된 CSV의 품질 문제는 `../data/README.md`를 참고한다.

## 6. 회귀 테스트

```bash
.venv/bin/python scripts/run_mobility_tests.py
.venv/bin/python data/validate_transit_data.py
git diff --check
```

Windows PowerShell에서는 `.venv/bin/python` 대신 `.\.venv\Scripts\python.exe`를 사용한다. 회귀 테스트는 개인 `.env`와 외부 API 없이 실행된다.

브라우저 테스트에는 Node.js, Playwright와 Chromium이 필요하다. 첫 터미널에서 `.venv/bin/python scripts/run_mobility_test_server.py`를 실행하고, 다른 터미널에서 `node tests/frontend_smoke.cjs`를 실행한다. 검증 서버는 기본 포트 8765에서 모의 교통 정보와 임시 모집방 데이터베이스를 사용한다.

## 공식 문서

- [TMAP 장소 검색](https://tmap-skopenapi.readme.io/reference/%EC%9E%A5%EC%86%8C%ED%86%B5%ED%95%A9%EA%B2%80%EC%83%89)
- [TMAP 보행자 입구 좌표 추가 안내](https://tmapapi.tmapmobility.com/popup_update2_2025_05_2.html)
- [TAGO 버스정류소정보](https://www.data.go.kr/data/15098534/openapi.do)
- [TAGO 버스노선정보](https://www.data.go.kr/data/15098529/openapi.do)
- [TAGO 버스도착정보](https://www.data.go.kr/data/15098530/openapi.do)
- [python-dotenv 설정 로딩](https://github.com/theskumar/python-dotenv/blob/main/README.md)
