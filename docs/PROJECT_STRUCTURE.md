# 프로젝트 폴더와 담당 파일 안내

이 문서는 춘천 대학생 통학 마스터의 파일을 찾고, 팀원이 검토할 범위를 안내한다. 담당 구분을 위해 코드를 옮기거나 복제하지 않는다. 파일명·상대경로·API 응답·데이터 열을 바꾸면 다른 역할의 작업에 영향을 줄 수 있으므로 먼저 협의한다.

## 먼저 읽을 문서

- [AGENTS.md](../AGENTS.md): 작업 전 설명·승인과 기존 변경 보호 규칙. 규칙은 그대로 유지한다.
- [TEAM_WORKFLOW.md](../TEAM_WORKFLOW.md): 팀 역할과 이후 Git 작업 방법. 이번 폴더 정리에서는 Git 쓰기나 GitHub 작업을 수행하지 않는다.
- [BACKEND_SETUP.md](BACKEND_SETUP.md): 기존 가상환경·환경변수·서버 실행 및 연결 점검 안내.
- [TRANSIT_ROUTING.md](TRANSIT_ROUTING.md): 버스 경로 계산 규칙·응답 계약·데이터 한계.
- [data/README.md](../data/README.md): 버스 CSV의 출처·열·기준일·품질 문제.
- [개발회고록](개발회고록.md): 이전 개발 과정과 시행착오 기록. 현재 실행 절차는 BACKEND_SETUP.md를 따른다.

## 폴더 구조

```text
hallym_capstone/
├── app.py                         # Flask 진입점과 API 라우트
├── requirements.txt               # Python 의존성 목록
├── AGENTS.md                      # 작업 규칙, 루트 유지
├── TEAM_WORKFLOW.md               # 협업 안내, 루트 유지
├── .gitignore                     # 추적 제외 규칙, 루트 유지
├── .env.example                   # 설정 예제, 내용·위치 유지
├── .env                           # 개인 비밀 설정, 읽기·이동·수정 대상 아님
├── .venv/                         # 기존 Python 가상환경, 유지
├── .git/                          # Git 내부 정보, 유지
├── templates/
│   └── index.html                 # 화면 구조, CSS·JS·지도 SDK 연결
├── static/
│   ├── css/                       # main·search·result-card·timeline 스타일
│   └── js/                        # main·GPS·검색·지도·API·결과 표시
├── services/                      # 설정·검색·CSV 조회·외부 API·경로 계산
├── data/
│   ├── chuncheon_bus_routes_20260326.csv
│   └── README.md                  # 노선 데이터 설명
├── scripts/
│   └── check_backend.py           # 준비 상태 및 선택적 실제 API 점검 도구
├── tests/                         # Python 회귀 테스트·프런트엔드 모의 테스트
├── docs/
│   ├── PROJECT_STRUCTURE.md       # 이 파일: 구조와 담당 안내
│   ├── BACKEND_SETUP.md            # 실행 안내
│   ├── TRANSIT_ROUTING.md          # 경로 계산 설명
│   └── 개발회고록.md               # 루트에서 이동한 회고 자료
├── 한림대학교 건물별 좌표.csv
├── 강원대학교 건물별 좌표.csv
├── 한림대_강원대_캠퍼스맵_통합_좌표.csv
└── data_preprocessing.ipynb        # 데이터 전처리·실험 기록
```

숨김 메타데이터·Python 캐시는 위 그림에서 생략했다. 이 정리에서는 실행 파일과 데이터의 이름·위치·내용을 유지한다. 루트 README.md는 정리 시작 시 없었으므로 별도로 만들지 않는다.

## 실행 파일과 보조 자료의 연결

| 구분 | 주요 파일 | 역할과 호출 관계 |
| --- | --- | --- |
| 서버 진입점 | [app.py](../app.py) | 설정·서비스를 초기화하고 `/`, `/health`, `/search_poi`, `/get_route_info` 요청을 처리한다. |
| 화면 | [templates/index.html](../templates/index.html), `static/css/` | Flask가 렌더링한 HTML이 스타일과 기존 순서의 JS 파일을 불러온다. |
| 시작·공용 상태 | [main.js](../static/js/main.js) | `AppState`·`AppModules` 연결과 초기화를 맡는다. 다른 JS가 함께 사용한다. |
| 화면 제어 | `gps-controller.js`, `search-controller.js`, `map-controller.js`, `ui-renderer.js` | 위치·목적지 입력, 지도 표시, 결과 렌더링을 맡는다. GPS와 외부 지도 동작은 기존 코드에 따른다. |
| 화면→서버 | [api-service.js](../static/js/api-service.js) | Flask 경로 조회와 응답 전달·요청 취소를 맡는다. 장소 검색은 search-controller.js에서 `/search_poi`를 호출한다. |
| 설정 | [settings.py](../services/settings.py) | 루트 `.env`와 환경변수에서 설정을 읽는다. 기존 실행 환경변수의 우선순위를 유지한다. |
| 목적지 검색 | [place_search_service.py](../services/place_search_service.py) | 교내 건물과 TMAP 장소 결과를 검색·검증한다. |
| CSV 조회 | [csv_transit_repository.py](../services/csv_transit_repository.py) | `data/chuncheon_bus_routes_20260326.csv`를 검증하고 정류장·노선·순서를 조회한다. |
| 경로 서비스 | [transit_service.py](../services/transit_service.py) | CSV·도보·실시간 도착정보를 경로 계산기에 연결한다. |
| 경로 계산 | [route_planner.py](../services/route_planner.py) | 외부 호출 없이 제공된 자료로 직통·1회 환승 후보와 추정 시간을 계산한다. |
| 외부 API | [walking_service.py](../services/walking_service.py), [public_transit_client.py](../services/public_transit_client.py) | TMAP 보행과 공공데이터 버스 도착정보 등을 조회한다. |
| 캠퍼스 데이터 | 루트의 캠퍼스별 좌표 CSV 2개 | app.py가 읽어 학교별 건물 선택과 교내 검색에 사용한다. |
| 보조 데이터 | 루트 통합 좌표 CSV, [data_preprocessing.ipynb](../data_preprocessing.ipynb) | 통합 데이터·전처리 자료. 상대경로나 향후 사용 가능성을 고려해 루트에 보존한다. 웹 실행에 노트북 실행은 필요하지 않다. |
| 점검·테스트 | `scripts/check_backend.py`, `tests/` | 실행 준비·회귀 검증용이다. `--live`는 외부 API를 실제 호출하므로 이번 문서 정리 검증에서는 사용하지 않는다. |
| 설명 자료 | `docs/`, `data/README.md` | 팀원이 읽는 안내다. 데이터 설명은 해당 데이터 옆에 유지한다. |

`services/__init__.py`는 services 패키지 표시용이다. `requirements.txt`는 서버 환경 준비에 쓰며, 프런트엔드 모의 테스트용 Node는 Flask 실행 의존성이 아니다. 실행 방법은 기존 BACKEND_SETUP.md를 따른다.

## 역할별 검토 범위

| 담당 | 주 검토 파일 | 다른 역할과 협의할 부분 |
| --- | --- | --- |
| 고경완 · UI/UX | `templates/index.html`, `static/css/`, `static/js/ui-renderer.js`, `search-controller.js`, `gps-controller.js`, `map-controller.js`, `tests/frontend_smoke.cjs` | API 응답을 화면에서 소비하는 방식, GPS·검색·지도 사용자 흐름. |
| 권오준 · 데이터 | `data/`, 루트 좌표 CSV 3개, `data_preprocessing.ipynb` | 데이터 열·인코딩·좌표·파일명·경로 변경은 백엔드와 협의. CSV 관련 테스트는 이준엽과 함께 검토. |
| 이준엽 · 백엔드 | `app.py`, `services/`, `requirements.txt`, `static/js/api-service.js`, `scripts/check_backend.py`, 백엔드 Python 테스트 | 라우트·외부 API·오류 처리·데이터 계약과 화면 연결. |
| 공용 협의 | `static/js/main.js`, `AGENTS.md`, `TEAM_WORKFLOW.md`, `.gitignore`, 공용 설정 예제·문서 | 공용 초기화·설정, API 응답 형식, 데이터 컬럼과 경로는 관련 담당자들이 먼저 합의한다. |

위 JS 약칭의 경로는 모두 `static/js/` 아래다. 이 표는 검토 책임을 안내하며, Git 상태만으로 실제 작성자나 기여자를 추정하지 않는다. 각 팀원은 자신이 작성하거나 직접 검토해 책임질 수 있는 변경만 나중에 선별해 커밋해야 한다. 미추적 파일도 기존 작업물이므로 삭제하거나 다른 사람 변경과 일괄 커밋하지 않는다.

## 정리 시작 시 변경 파일 안내

2026-10-16 로컬 `refactor/module-split` 브랜치에서 확인한 상태다. `수정`은 추적 중인 파일의 미스테이징 변경, `미추적`은 아직 Git 인덱스에 없는 파일을 뜻한다. 시작 시 staged 변경은 없었다. 아래는 작성자 목록이 아니며, 이후 작업으로 상태가 달라질 수 있다.

| 검토 담당 | 수정 파일 | 미추적 파일 |
| --- | --- | --- |
| 고경완 · UI/UX | `templates/index.html`; `static/css/main.css`, `result-card.css`, `search.css`; `static/js/gps-controller.js`, `map-controller.js`, `search-controller.js`, `ui-renderer.js` | `tests/frontend_smoke.cjs` |
| 권오준 · 데이터 | 루트 CSV·노트북의 Git 변경 없음 | `data/README.md`, `data/chuncheon_bus_routes_20260326.csv` |
| 이준엽 · 백엔드 | `app.py`, `requirements.txt`; `services/public_transit_client.py`, `route_planner.py`, `transit_service.py`, `walking_service.py`; `static/js/api-service.js`; `tests/test_route_planner.py`, `test_transit_client.py` | `services/csv_transit_repository.py`, `place_search_service.py`, `settings.py`; `scripts/check_backend.py`; `tests/test_backend_endpoints.py`, `test_backend_probe.py`, `test_csv_transit_integration.py`, `test_csv_transit_repository.py`, `test_place_search_service.py`, `test_route_ranking.py`, `test_settings.py` |
| 공용 협의 | `.gitignore`, `static/js/main.js`, `docs/TRANSIT_ROUTING.md` | `.env.example`, `docs/BACKEND_SETUP.md` |
| 자동 생성물 · 별도 판단 | `.DS_Store`, `__pycache__/app.cpython-314.pyc` | `static/.DS_Store` |

같은 셀에서 디렉터리를 생략한 파일은 바로 앞의 같은 디렉터리 기준이다. 예: `services/…`, `tests/…`, `static/css/…`, `static/js/…`. 변경 목록에 없는 파일도 현재 실행·도구의 일부이므로 보존한다.

## 이번 정리로 바뀌는 문서

- `개발회고록.md` → `docs/개발회고록.md`: 실행·도구 참조가 없는 회고 자료를 문서 폴더로 이동한다. 내용은 그대로 유지한다.
- `TEAM_WORKFLOW.md`: 회고록 링크만 새 위치에 맞춘다.
- `docs/PROJECT_STRUCTURE.md`: 이 파일을 생성해 구조·담당·검토 범위를 한곳에서 안내한다.

기존 `docs/TRANSIT_ROUTING.md`, `docs/BACKEND_SETUP.md`, `data/README.md`의 변경과 내용은 그대로 보존한다. Git 인덱스를 변경하지 않으므로 회고록 이동은 나중에 팀원이 검토할 때 삭제된 옛 경로와 미추적 새 경로로 보일 수 있다.

## 임시파일·검증 시 주의

- Git 추적 확인된 `.DS_Store`, `templates/.DS_Store`, `__pycache__/app.cpython-314.pyc`는 그대로 보존했다. 삭제가 필요하면 별도 팀 검토가 필요하다.
- 미추적 `static/.DS_Store`와 무시된 `services/__pycache__/`도 보존했다. 실행 중인 프로그램과의 관련성을 확정하지 않은 상태에서 정리하지 않는다.
- `.env`, `.venv`, `.git`은 이동·수정·삭제·복제하지 않는다. 실제 키는 문서나 감사 기록에 넣지 않는다.
- 이번 전후 비교는 코드·데이터·테스트·노트북·의존성 목록의 경로와 SHA-256을 비교한다. 비밀 설정과 Git 내부·가상환경 내용은 감사 목록에서 제외한다.
- Python 검증은 기존 `.venv`와 `PYTHONDONTWRITEBYTECODE=1`을 사용한다. 테스트 자식 프로세스에서 프로젝트 `.env` 로딩을 가로채고 외부 HTTP·소켓 통신을 차단한다. 설정 테스트의 임시 가짜 `.env`는 허용한다.
- 기존 Node가 있으면 `tests/frontend_smoke.cjs`로 가짜 DOM·지도·GPS·fetch를 검증한다. 패키지 설치, 실서비스 API 호출, 실제 GPS 수집은 이 정리에 필요하지 않다.
- 파일·해시 일치와 모의 테스트는 보존 근거다. 실제 브라우저 화면·GPS·외부 API의 현재 상태까지 검증했다는 뜻은 아니다.
