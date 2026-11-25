# 춘천 대학생 통학 마스터 작업공간

Flask 진입점은 `app.py`다. 실행 환경과 외부 API 설정은 [백엔드 실행 안내](BACKEND_SETUP.md), 브랜치 사용 규칙은 [팀 워크플로](../TEAM_WORKFLOW.md), 저장소 작업 규칙은 [AGENTS.md](../AGENTS.md)를 참고한다.

```text
hallym_capstone/
├── app.py                       Flask 진입점과 API 등록
├── services/                    검색·교통·날씨·택시 서버 로직
├── templates/                   화면 구조와 공통 부분 템플릿
├── static/
│   ├── css/                     검색·결과 카드·타임라인·교통 메뉴 스타일
│   └── js/                      입력·지도·API 호출·화면 표시
├── data/
│   ├── raw/                     공식 원본과 캠퍼스 입력 CSV
│   ├── normalized/              앱에서 읽는 검증된 가공 자료와 이전 수집본
│   ├── preparation/             전처리 노트북
│   ├── sources.csv              출처·기준일·해시·행 수
│   └── validate_transit_data.py 자료 검증
├── scripts/                     실행 준비·데이터 수집·검증용 도구
├── tests/                       Python 테스트와 브라우저 검증
└── docs/                        운영·기능 안내와 개발 회고
```

## 기능별로 확인할 파일

| 기능 | 주요 코드와 문서 |
| --- | --- |
| 전국 목적지 검색과 통합 경로 | `app.py`의 `/search_poi`와 `/api/journeys`, `services/place_search_service.py`, `services/national_journey_service.py`, `services/national_transit_repository.py`, `services/national_bus_repository.py`. [경로 계산 안내](TRANSIT_ROUTING.md) |
| 춘천 시내와 학교 셔틀 | `services/transit_service.py`, `services/route_planner.py`, `services/shuttle_service.py`, `services/walking_service.py`와 `/get_route_info`. [셔틀 안내](SHUTTLE_ROUTING.md) |
| 화면 초기화와 경로 결과 | `templates/index.html`, `static/js/main.js`, `static/js/api-service.js`, `static/js/ui-renderer.js`, `static/css/search.css`, `static/css/result-card.css`, `static/css/timeline.css` |
| 시외버스·택시·날씨 | 교통수단별 `services/` 라우트와 서비스 모듈. [시외버스](INTERCITY_DESTINATIONS.md) · [택시와 동승](TAXI_RIDE_ROOMS.md) · [날씨·대기질](COMMUTE_ADVICE.md) |
| 팀 데이터와 전처리 | `data/raw/campus/`의 학교별 좌표·셔틀 입력, `data/normalized/`의 가공 자료, `data/preparation/data_preprocessing.ipynb`. [공식 자료와 사용 범위](../data/TRANSIT_DATA.md) |
| 개발 이력 | 기존 여러 위치에 있던 내용을 한 문서에 모았다. [개발회고록](개발회고록.md) |

캠퍼스 좌표, 셔틀 CSV, 춘천 버스 참조 원본은 원래 파일명과 바이트를 보존한다. 전처리 노트북과 `scripts/collect_itx.py`가 새로 생성한 자료는 `data/preparation/generated/`에 저장한다. 자료를 검증해 정식으로 반영할 때는 `data/normalized/`와 `data/sources.csv`를 함께 갱신한다.

## 확인 명령

```sh
python3 data/validate_transit_data.py
python scripts/run_mobility_tests.py
git diff --check
```

화면을 검증할 때는 브라우저 테스트에 필요한 Node.js와 Playwright 환경을 준비한다. 실행 방법은 [백엔드 실행 안내](BACKEND_SETUP.md)를 참고한다.
