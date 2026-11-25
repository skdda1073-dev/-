# 전국 통학 경로 자료 준비

2026-11-24에 수집한 자료의 출처, 기준일, 적용일, 범위, 확보 상태와 원본 SHA-256은 [sources.csv](sources.csv)에 있다. [raw](raw/)는 원본이고 [normalized](normalized/)는 통합 길찾기와 대조할 수 있도록 정리한 UTF-8 CSV다. 큰 CSV는 gzip으로 압축했다. 앱에서 사용하는 자료와 응답의 해석은 [경로 계산 안내](../docs/TRANSIT_ROUTING.md)를 참고한다.

## 데이터 사용 범위

| 자료 | 먼저 볼 파일과 주의할 범위 |
| --- | --- |
| 전국 도시·광역철도 | [노선](normalized/subway/kric_routes_20260630.csv), [역사](normalized/subway/kric_stations_20260630.csv), [운행자료 범위](normalized/subway/kric_line_coverage_20260630.csv). 원본 운행표 행과 한 셀에 묶인 정차 시각에서 꺼낸 행은 각각의 CSV에 있다. 둘을 합산하면 운행편이 중복될 수 있다. 역 ID가 없는 관측치, 시각 없는 운행 간격, 순서를 검증하지 못한 원문은 상태 열에 남겼다. |
| 경춘선·지역 운행표 | [경춘선 평일](normalized/subway/korail_gyeongchun_weekday_20260901.csv), [경춘선 휴일](normalized/subway/korail_gyeongchun_holiday_20260901.csv), [광역전철 가공본](normalized/subway/). 경춘선·대경선·동해선의 상·하행은 코레일의 2026-09-01 시행표를 기준으로 한다. 자정을 넘긴 운행은 `24:xx` 같은 서비스 날짜 기준 시각을 사용한다. 광주 1호선 역별 도착표는 열차 식별자를 제공하지 않는다. |
| 수도권 8호선·별내선 | [서울교통공사 운행시각표](https://www.data.go.kr/data/15098251/fileData.do)의 2026-09-01 기준 [8호선 가공본](normalized/subway/seoul_metro_line8_stop_times_20260901.csv.gz)을 읽는다. 별내·다산을 포함한 24개 역을 기존 국가철도공단 역사 ID에 대응시켰다. 평일·토요일·일요일 및 공휴일별로 열차의 정차 순서와 시각을 보존했다. |
| 전국 시내·마을버스 | [전국 정류장](normalized/bus/tago_stops_20261109.csv), [버스 배차 요약](normalized/bus/ts_bis_service_summary_20260716.csv), [버스 가공본](normalized/bus/). TAGO 도시별 노선번호와 TS-BIS 첫차·막차·배차 간격은 각 제공기관 ID를 사용한다. 정류장별 출발 시각이 필요하면 운영기관의 시간표를 확인한다. |
| 춘천 버스 | [공개 시각 가공본](normalized/bus/chuncheon_published_departures_20260930.csv), [원본 시간표 페이지 목록](normalized/bus/chuncheon_timetable_page_index_20260930.csv), [춘천 버스 원본](raw/bus/). 시간표 행의 요일, 방향, 기점이 명확한지 확인한다. 2026-12-01부터 시행 예정인 3·4번 변경은 공지의 적용일 이후에 반영한다. |
| 여객철도·버스 터미널 | [철도 및 터미널 가공본](normalized/rail/), [환승센터와 공휴일](normalized/geography/). TAGO의 역 목록은 시설 좌표와 대응을 확인해야 한다. 버스타고 자료는 2026-11-24에 확인한 공개 출발지와 춘천 출발 목적지 목록이다. 좌석, 배차와 요금은 경로의 날짜와 방향에 맞춰 다시 확인한다. |
| 철도역 환승 | [환승 이동경로](normalized/transfers/rail_transfer_walk_instructions.csv), [빠른 환승 위치](normalized/transfers/rail_fast_transfer_instructions.csv). 운영기관·노선·방향을 포함한 원본 열을 보존했다. 실제 도보 소요시간은 별도 확인이 필요하다. |
| 춘천에서 캠퍼스까지 | [교내 건물](normalized/campus/buildings.csv), [학교별 연결 근거](normalized/campus/campus_gateway_references.csv), [강원대 방향 승차 정류장](normalized/campus/city_boarding_stop_crosswalk.csv), [춘천역·남춘천역 ID 대응](normalized/rail/chuncheon_rail_facility_crosswalk.csv). [한림대 셔틀 운행편](normalized/campus/hallym_shuttle_trips.csv)은 [정류장 시각](normalized/campus/hallym_shuttle_stop_times.csv)과 [2026 운행 달력](normalized/campus/hallym_shuttle_calendar_2026.csv)을 같이 확인한다. 학교 안내의 버스 번호도 현재 운행과 대조한다. |

## 남은 자료와 활용 신청

- 현재 설정으로 **TAGO 시내버스 노선과 열차의 공개 조회**가 정상 응답했다. **TAGO 지하철·시외버스·고속버스·국내항공·국내선박** 목록 조회는 제공기관 오류 `30`을 반환했다. 해당 서비스별 활용 승인과 인증키 상태를 확인한 뒤 읽기 조회를 재검증해야 한다. [TAGO 서비스 목록](https://tago.go.kr/v5/use/openapi.jsp) · [오류 코드가 포함된 포털 명세](https://www.data.go.kr/data/15098554/openapi.do)
- 지하철 통합표에는 고시 시각이 없거나 운행 간격으로만 표기된 구간이 있다. 인천 2호선의 정류장별 시각과 GTX-A 등 남은 노선은 해당 운영기관 운행표로 보완한다. [자료별 범위와 상태](sources.csv)
- 국가교통DB의 GTFS는 2025년 3월 평일 하루를 담은 자료다. KTDB의 별도 자료 신청이 필요하다. [GTFS 수정 자료 안내](https://www.ktdb.go.kr/www/selectBbsNttView.do?bbsNo=2&key=45&nttNo=3831) · [KTDB 자료 신청](https://www.ktdb.go.kr/www/contents.do?key=202)
- 전국 주소를 검색할 때는 행정안전부 주소정보 API의 검색·좌표 변환을 사용할 수 있다. 실제 도보 거리와 환승 이동은 시설 데이터와 보행 경로로 계산한다. [주소정보 API](https://business.juso.go.kr/jst/jstAddressCuration)

## 데이터 갱신과 검증

원본과 가공본의 이름에 기준 버전을 기록하고, `sources.csv`에서 다운로드일과 시간표 적용일을 각각 관리한다. 시설은 운영기관과 서비스 ID를 먼저 확인한다. 시각이나 역 ID가 확인되지 않은 행에는 원문과 확인 상태를 남긴다.

캠퍼스별 좌표와 한림대 셔틀은 `raw/campus/`, 춘천 버스 참조 CSV는 `raw/bus/`에서 읽는다. 전처리 도구가 만드는 작업중 자료는 `preparation/generated/`로 저장되며, 확인한 가공본과 출처 목록을 함께 갱신한다.

프로젝트 루트에서 `python3 data/validate_transit_data.py`를 실행하면 원본 존재 여부와 SHA-256, 가공 CSV 구조, 전국 자료의 기본 행 수와 춘천 환승 자료의 식별자를 확인할 수 있다.
