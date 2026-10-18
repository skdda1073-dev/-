# 백엔드 실행과 API 연결 확인

## 1. 키 입력

프로젝트 루트의 `.env`를 VS Code에서 열고 작은따옴표 안에 값을 넣는다.

```dotenv
TMAP_APP_KEY='본인 TMAP 키'
PUBLIC_DATA_KEY='본인 공공데이터 키'
NAVER_MAP_CLIENT_ID='본인 네이버 지도 클라이언트 ID'
APP_DEBUG=false
PORT=5050
```

실제 `.env`는 Git에서 제외한다. `.env.example`은 값이 없는 팀 공용 예제다. 팀원은 예제 파일을 `.env`로 복사한 뒤 각자 입력한다. 키를 채팅·스크린샷·PR 본문에 올리지 않는다. TMAP·공공데이터 키는 브라우저에 보내지 않는다. 네이버 지도 클라이언트 ID는 지도 SDK 로딩에 쓰이는 공개 식별자이며 시크릿을 넣는 칸이 아니다.

서버는 실행 위치와 무관하게 프로젝트 루트의 `.env`를 읽는다. 이미 터미널·배포 환경에 같은 이름의 환경변수를 설정했다면 그 값이 우선한다. 키를 바꾸면 서버를 종료하고 다시 시작한다. 변수 확장(`\${...}`)은 비활성화하여 키의 특수문자를 보존한다.

.env는 실행 파일이 아닌 설정 파일이다. VS Code에서 편집·저장하며 각 설정은 `이름=값` 한 줄로 작성한다. 설명은 `#`으로 시작하는 주석에 적고, 터미널 명령이나 일반 문장을 설정 줄에 섞지 않는다.

## 2. 실행

이 Mac에는 프로젝트 `.venv`와 필요한 `python-dotenv`를 준비했다. VS Code 터미널에서:

```bash
cd /Users/guala/Desktop/hallym_capstone
.venv/bin/python scripts/check_backend.py
.venv/bin/python scripts/check_backend.py --live
.venv/bin/python app.py
```

첫 번째 점검은 외부 API를 호출하지 않는다. 두 번째 `--live`는 **실제 TMAP 검색·보행 및 공공데이터 도착 API를 호출하므로 사용량이 발생한다**. 한림대 건물→춘천역 검색·경로를 점검하며 사용자의 실제 GPS를 수집하지 않는다. 키 값·원문 응답·인증 URL은 출력하지 않는다. 서버를 켜기 전에도 실행할 수 있다.

위 예시대로 PORT를 설정했다면 서버 실행 후 브라우저에서 `http://localhost:5050`을 연다. 현재 네이버 지도는 이 주소에서 정상 표시를 확인했으며 `127.0.0.1`로 접속하면 지도 인증에 실패했다. 설정이 없을 때 코드 기본 포트는 5000이다. `Address already in use`가 나오면 다른 프로그램을 임의 종료하지 말고 사용 가능한 포트로 바꾼다. `Ctrl+C`로 서버를 종료한다. 기본값은 로컬 전용 주소와 디버그 비활성화다. 외부 공개용 운영 배포 설정은 별도 작업이다.

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
| 로컬 준비 완료 | CSV·건물·TMAP 설정 존재 확인. API 인증 성공을 뜻하지 않음 |
| `TMAP_KEY_MISSING` | `.env`의 TMAP 키 입력 및 서버 재시작 |
| `TMAP_AUTH` | 키와 장소(POI) 통합 검색 API 사용 권한 확인 |
| `TMAP_QUOTA` | TMAP 호출 한도 확인 |
| `TMAP_TIMEOUT`, `TMAP_NETWORK` | 검색 서버 응답·연결 확인 |
| `TMAP_RESPONSE` | 응답 구조·유효한 장소 좌표를 해석하지 못함 |
| `WALK_UNAVAILABLE` | TMAP 보행자 경로 API 권한·키·한도·연결 확인. 버스가 없다는 뜻이 아님 |
| `CSV_DATA` | 프로젝트 노선 CSV 파일·열·순번·인코딩 확인 |
| `NO_JOURNEYS` | 요청은 처리됐지만 검색 범위에서 경로 후보를 확인하지 못함. 경로 성공 검증으로 보지 않음 |
| 도착정보 `20`, `30`, `31`, `401`, `403` | 버스도착정보 활용신청·인증키·사용기한 확인 |
| 도착자료 없음 | 서버 응답은 받았지만 현재 운행 도착값은 검증하지 못함 |
| 부분 성공 | 장소·도보·CSV 후보는 성공했지만 설정된 도착 API는 실패함 |

점검 도구 종료 코드: `0` 요청한 범위 통과, `2` 로컬 준비 실패, `3` 목적지 검색·경로 조회 실패, `4` 검색·경로는 성공했으나 설정된 실시간 도착 API 실패.

## 4. 제공 API

- `GET /health`: 설정의 존재 여부와 로컬 CSV·캠퍼스 준비 상태만 반환. 준비되면 HTTP 200, 필수 준비가 빠지면 503. 외부 호출은 없으며 `external_apis_verified`는 항상 false다.
- `GET /search_poi?q=춘천역&campus=hallym`: 주소·좌표가 포함된 최대 8개 목적지 배열. 캠퍼스 인자는 선택한 학교 결과를 우선할 뿐 다른 학교 목적지를 배제하지 않는다.
- `POST /get_route_info`: GPS 또는 캠퍼스 건물을 출발지로 받아 기존 주변 정류장 배열 또는 목적지 경로 후보를 반환한다.

목적지 검색은 명시적인 WGS84 좌표계를 사용하고 보행자 입구(`pnsLat/pnsLon`)→일반 입구→중심점 순으로 유효한 좌표를 선택한다. 이름이 같아도 ID·위치가 다르면 유지한다. 다른 도시의 주소는 춘천 결과에 섞지 않는다. 외부 검색이 실패해도 교내 결과는 `search_warning`과 함께 남기며, 교내 결과도 없으면 명확한 오류를 반환한다.

## 5. 확인된 범위와 남은 한계

자동 검증은 가짜 외부 응답과 실제 프로젝트 CSV를 연결해 수행한다. 2026-10-16 로컬 실호출 점검에서는 TMAP 목적지 검색 8건, 보행 조회를 포함한 CSV 경로 후보 3개, 공공데이터 버스 도착 API의 정상 응답을 확인했다. 당시 도착자료는 빈 목록이었으므로 실제 버스의 도착 시각·정확도까지 검증한 것은 아니다.

로컬 서버는 5050번 포트에서 실행 확인했다. `http://localhost:5050`에서 네이버 지도·출발 마커, 학교 전환과 건물 목록 갱신이 정상 동작했다. `127.0.0.1`에서는 지도 인증에 실패했으므로 주소를 혼용하지 않는다. 다른 주소나 배포 도메인은 네이버 클라우드의 등록된 Web 서비스 URL을 확인한다. [공식 인증 오류 안내](https://www.ncloud.com/support/faq/all/1047?searchKeyword=map)

브라우저에서 한림대 대학본부·인문1관 → 춘천역 검색·선택 → 경로 후보 3개와 지도 승하차 마커·정류장 연결선 표시를 확인했다. 390px 모바일 화면에서 가로 넘침이 없음을 확인했다. GPS 권한과 실제 운행 방향·회차 확인은 별도로 남아 있다.

CSV의 상·하행·회차·시간표 미확인 정책은 그대로 유지한다. 키를 연결했다고 운행 방향이 자동으로 검증되는 것은 아니다. 결과는 순서 기반 후보이며 미확인 총 소요 시간을 숫자로 꾸며내지 않는다. 상세 계산·데이터 보완은 `TRANSIT_ROUTING.md`와 `../data/README.md`를 참고한다.

## 6. 회귀 테스트

```bash
.venv/bin/python -m unittest discover -s tests
node tests/frontend_smoke.cjs
git diff --check
```

Windows는 Python 명령을 `.\.venv\Scripts\python.exe -m unittest discover -s tests`로 바꾼다. Node.js는 프런트엔드 모의 테스트에만 필요하며 Flask 실행 필수 도구는 아니다.

## 공식 문서

- [TMAP 장소 검색](https://tmap-skopenapi.readme.io/reference/%EC%9E%A5%EC%86%8C%ED%86%B5%ED%95%A9%EA%B2%80%EC%83%89)
- [TMAP 보행자 입구 좌표 추가 안내](https://tmapapi.tmapmobility.com/popup_update2_2025_05_2.html)
- [TAGO 버스도착정보](https://www.data.go.kr/data/15098530/openapi.do)
- [python-dotenv 설정 로딩](https://github.com/theskumar/python-dotenv/blob/main/README.md)
