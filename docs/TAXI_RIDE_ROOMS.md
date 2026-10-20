# 택시 견적과 웹 내부 동승 모집·채팅

기존 버스·셔틀 `/get_route_info`의 응답 계약과 순위를 유지하고 택시를 별도 카드와 API로 제공한다. 닉네임은 대학생·실명 인증이 아니다. 결제·정산·택시 호출 서비스는 제공하지 않는다.

## 실행 설정

프로젝트 루트(`app.py`가 있는 폴더)에서 기존 프로젝트 Python을 직접 지정한다. 가상환경을 옮겼다면 활성화 스크립트에 이전 경로가 남아 있을 수 있으므로 `source`에 의존하지 않는다.

```bash
./.venv/bin/python app.py
```

`.env.example`의 새 설정을 참고하여 개인 `.env`에 직접 추가하고 서버를 재시작한다. 기존 키를 덮어쓰지 않는다. 코드 변경 과정에서 실제 `.env`는 수정하지 않았다.

| 설정 | 기본값·정책 |
|---|---|
| `TMAP_APP_KEY` | 기존 키. 자동차·택시 경로 상품 사용 권한 별도 필요 |
| `FLASK_SECRET_KEY` | 기본 미설정. 최소 32바이트의 고정 비밀값, 기존 API 키와 다른 값. 모든 worker가 공유 |
| `RIDE_ROOM_DB_PATH` | `instance/ride_rooms.sqlite3`, 상대경로는 프로젝트 루트 기준 |
| `SESSION_COOKIE_SECURE` | 코드 기본 `true`. 로컬 HTTP 개발에서만 `false`; HTTPS 배포에서는 `true` |
| `TAXI_CACHE_TTL_SECONDS` | 60초 (설정 범위 1~300초) |
| `TAXI_CACHE_MAX_ENTRIES` | 512개 (1~4096) |
| `RIDE_ROOM_MESSAGE_RATE_PER_MINUTE` | 익명 세션당 15회 (1~120) |
| `RIDE_ROOM_CREATE_RATE_PER_HOUR` | 익명 세션당 3회 (1~30) |

고정 비밀키를 처음 준비할 때의 예시 명령은 `./.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))'`이다. 결과를 개인 설정에 보관하고 채팅·스크린샷·Git에 공개하지 않는다. 재시작마다 새 키를 생성하지 않는다. 키가 없거나 짧거나 기존 API 키와 같으면 모집·채팅 API만 `503 CHAT_NOT_CONFIGURED`이며 버스·셔틀·택시와 기존 `/health` 준비 조건은 유지된다.

브라우저 쿠키는 HttpOnly·SameSite=Lax이다. 쿠키를 지우거나 다른 기기·다른 브라우저를 사용하면 이전 참가 권한이 이어지지 않는다. `localhost`와 `127.0.0.1`도 쿠키 출처가 다르므로 혼용하지 않는다. 네이버 지도 접속 주소는 기존 `BACKEND_SETUP.md`의 등록 URL 안내를 따른다.

새 Python 의존성·외부 채팅 서비스는 없다. SQLite는 표준 라이브러리를 사용한다. DB는 단일 서버의 영속 로컬 디스크를 전제로 하며 다중 호스트·서버리스 임시 디스크의 공유 DB로 사용할 수 없다. 현재 검증 환경의 SQLite 3.50.4에서는 기본 rollback journal과 `BEGIN IMMEDIATE`를 사용했다. WAL을 강제하지 않는다. 연결마다 foreign_keys·busy_timeout을 설정하고 요청 작업 종료 시 연결을 닫는다.

## 화면 사용

1. 기존 화면에서 출발지와 목적지를 선택하고 검색한다. 버스·셔틀과 택시는 독립적으로 조회된다. 목적지 없는 주변 정류장 검색에는 택시 요청을 보내지 않는다.
2. 택시 카드의 2·3·4명 분담은 가정이다. 요금이 없으면 시간은 유지하고 요금·분담만 조회 불가로 표시한다.
3. 같이 탈 사람 찾기로 목적지·서울 시각 출발 구간·상태를 필터링한다. 목록 열람은 참가가 아니다.
4. 방 만들기에서 공개 집결지를 별도로 검색·선택·확인한다. GPS 현재 위치는 자동 공개하지 않는다. 제목·닉네임·출발 예정 시각·정원을 입력한다. 방장은 참가자 1명으로 포함된다.
5. 방에 참가한 사람만 메시지와 방 견적을 볼 수 있다. 일반 참가자는 나가기, 방장은 종료를 사용한다. 종료 확인 문구는 새 참가·메시지 전송 중단을 알린다.

출발 시각은 Asia/Seoul 기준으로 입력·표시하며 서버 기준 `현재 < 출발 <= 현재+24시간`만 새 방으로 허용한다. API는 반드시 시간대가 있는 ISO 8601을 받는다. DB에는 UTC 시각을 저장한다.

상태는 `closed > expired > full > open` 순서다. full은 새 참가만 막고 대화는 허용한다. 출발 시각이 지나면 서버가 새 참가·새 메시지를 거절한다. 종료·만료 방은 기존 활성 참가자에게 읽기 전용이다. 브라우저를 닫았다는 이유로 참가자를 자동 탈퇴시키지 않는다. 자동 방장 이전은 없다.

메시지는 열린 방에서 마지막 요청 완료 후 약 3초 뒤 확인한다. 방 전환·나가기·화면 숨김 때 취소하며, 복귀 시 참가 상태를 다시 확인한다. 실패에는 backoff를 둔다. 메시지 확인과 인원 변동은 TMAP을 호출하지 않는다. 방 견적은 방을 열 때 또는 명시적 갱신 버튼으로만 요청한다.

## 택시 API

`POST /api/taxi/estimate`, JSON 객체:

```json
{
  "location": "대학본부",
  "campus": "hallym",
  "dest_name": "사용자가 선택한 목적지",
  "dest_lat": 37.87,
  "dest_lon": 127.73,
  "split_count": 3
}
```

예시 좌표·이름은 API 형식 설명이며 실제 운임·검증된 경로가 아니다. 건물 출발은 서버의 해당 캠퍼스 건물 좌표로 해석한다. GPS는 `lat`, `lon`을 함께 넣는다. 목적지 좌표는 필수다. 좌표는 기존 API와 호환되게 숫자 문자열도 허용하지만 bool·NaN·무한대·범위 밖·부분 좌표는 거절한다. 이름은 문자열 1~100자, `split_count`는 JSON 정수 1~4(기본 1)이며 bool·실수·문자열은 거절한다.

응답은 다음 필드를 포함한다.

- `status`: `ok`, `partial`, `no_travel_required`; `code`, `origin`, `destination`
- `ride_duration_seconds`, `distance_m`: 차량 주행시간(초), 거리(m)
- `fare.amount_krw`: 공급자가 제공한 유효한 예상 택시요금 또는 null
- `fare.basis=provider_estimate`, `fare.provider_total_fare_krw`: 별도 경로 총요금. 택시요금에 합산하지 않음
- `fare.toll_included`, `surcharge_included`, `call_fee_included`: 포함 여부 미확인으로 null
- `pickup_wait_seconds`, `door_to_door_seconds`: 배차·승차 대기시간 미제공으로 null
- `fetched_at`: 서버 조회 시각; `provider_data_at=null`: 공급자 원본 갱신 시각 미확인
- `from_cache`, `cache_age_seconds`, `expires_at`, `cache_ttl_seconds`
- `split_count`, `split_amounts_krw`, `split_preview:[{count,amounts_krw}]`, `warnings`

택시 대기시간은 제외하며 실제 미터요금은 달라질 수 있다. 미래 출발 시각의 교통 예측이라고 표시하지 않는다. 같은 좌표는 외부 호출 없이 이동 불필요로 처리하고 시간·거리 0, 요금·분담 null로 반환한다. 무료 택시 의미가 아니다.

TMAP 호출은 `POST https://apis.openapi.sk.com/tmap/routes?version=1`, 서버 appKey 헤더, `requests.post(data=payload)` form 방식이다. X는 경도, Y는 위도, WGS84GEO이며 `searchOption=24`, `commercialVehicle=Y`, `carType=1`, `totalValue=2`를 사용한다. carType은 통행료 산정 구분이며 정원·택시 등급이 아니다. 요약 응답의 검증된 합계 properties를 읽고 전체 형상 응답은 S 합계를 우선한다. 첫 LineString 구간 시간을 전체 시간으로 사용하지 않는다. 옵션 권한 오류 시 일반 경로로 자동 대체하지 않는다.

프로세스 메모리 캐시는 좌표·경로 옵션 기준이고 인원수는 키에 넣지 않는다. 원본 형상은 저장하지 않으며 실패·만료 값을 최신값으로 반환하지 않는다. 공급자 견적을 DB·localStorage·오프라인 캐시에 영구 보관하지 않는다. 사용자가 선택·확인한 집결지·목적지만 사용자 입력으로 방 DB에 저장한다.

택시 오류: 키 미설정 `TAXI_KEY_MISSING` 503, 공급자 권한 `TAXI_PROVIDER_AUTH` 502, 쿼터 `TAXI_PROVIDER_QUOTA` 503, timeout `TAXI_PROVIDER_TIMEOUT` 504, 네트워크·응답 오류 502. 공급자 인증 실패는 최종 사용자의 로그인 문제로 안내하지 않는다.

## 모집·채팅 API

모든 경로는 `/api/ride-rooms` 기준이다. JSON 변경 요청은 같은 출처 쿠키와 `X-CSRF-Token`이 필요하다. 먼저 `/session`을 읽어 CSRF 토큰을 얻는다. Origin 또는 Referer가 같은 출처인지도 검사한다. wildcard CORS는 없다. 사용자 ID·방장 ID·요금·현재 인원을 클라이언트가 권한 근거로 보낼 수 없다.

| Method / 경로 | 입력·응답 |
|---|---|
| GET `/session` | `{ready:true,csrf_token}`; actor 토큰·해시 비공개 |
| GET `` | `q` 목적지명 부분검색, `from`/`to` 시간대 포함 출발구간, `status` 기본 open(`all` 가능), `limit` 기본20/최대50, `cursor`. `{rooms,next_cursor,has_more}` |
| POST `` | 아래 생성 JSON. 최초201/동일 재시도200, `{room,self,participants}` |
| GET `/<id>` | `{room,self,participants?}`; 회원 목록은 실제 참가자에게만 제공 |
| POST `/<id>/join` | `{nickname}`. 중복 참가로 인원 증가하지 않음 |
| POST `/<id>/leave` | `{}`. 일반 참가자만, 반복 요청도 추가 차감 없음 |
| POST `/<id>/close` | `{}`. 방장만, 반복 종료도 같은 결과 |
| GET `/<id>/estimate` | 참가자만. `{estimate,room_version,participant_count,current_split,capacity_preview}` |
| GET `/<id>/messages` | `after_id`, `limit` 기본50/최대100. 아래 메시지 응답 |
| POST `/<id>/messages` | `{body,client_message_id}`. 최초201/동일 재시도200, `{message,room_version}` |

```json
{
  "client_room_id": "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx",
  "title": "사용자가 정한 모집 제목",
  "nickname": "사용자 닉네임",
  "meeting": {"name": "공개할 집결 장소", "lat": 37.88, "lon": 127.73},
  "destination": {"name": "목적지", "lat": 37.87, "lon": 127.74},
  "departure_at": "2026-10-20T18:00:00+09:00",
  "capacity": 4
}
```

위 식별자 표기는 자리표시자다. 실제 요청에는 `crypto.randomUUID()` 등 유효 UUID를 사용하고 출발 시각도 현재 기준으로 바꾼다. 닉네임 1~20자, 제목 1~60자, 장소명 1~100자, 메시지 1~500자다. 같은 세션의 같은 생성 ID·같은 본문은 기존 방을 반환하고 다른 본문이면409다. 메시지는 `(방, actor, client_message_id)` 기준으로 같은 규칙을 적용하므로 다른 사람이 같은 ID를 사용해도 기존 메시지를 바꾸지 않는다. 재시도는 빈도 제한을 추가 차감하지 않는다.

`room`은 `id,title,meeting,destination,departure_at,capacity,participant_count,status,room_version,created_at,closed_at`을 포함한다. `self`는 `joined,is_host,member_id`이다. 공개 회원은 `member_id,nickname,is_host`만 안정적인 최초 참가 순서로 제공한다.

메시지 응답은 `{messages,next_after_id,has_more,room,participants,self,cached_estimate}`이다. 메시지에는 `id,member_id,nickname,body,created_at`이 있다. 첫 조회는 최근 N개를 오래된 순서로 반환한다(이전 N개보다 오래된 기록의 역방향 페이지 기능은 없음). 이후 after_id 조회는 해당 ID 이후 N+1개로 has_more를 계산하고, 클라이언트는 has_more인 동안 직렬로 따라잡는다. 빈 응답은 기존 after_id를 유지한다. 메시지가 없어도 인원·상태·room_version을 함께 받는다.

`cached_estimate`는 해당 프로세스의 아직 유효한 견적이 있을 때만 서버가 현재 인원으로 재배분한 방 견적 응답이며 없으면 null이다. 폴링으로 외부 견적을 만들지 않는다. 캐시는 worker별이므로 다른 worker에 캐시가 없으면 갱신 안내를 제공한다. 확정 서버 분담은 현재 room_version·활성 인원과 함께 제공한다. 만료 견적의 금액을 최신 분담으로 유지하지 않는다.

`current_split`은 `[{member_id,nickname,amount_krw}]`, `capacity_preview`는 `{count,amounts_krw}`이다. 전체 10,001원을 3명이 나누면 안정적인 순서로 3,334·3,334·3,333원이며 합계가 일치한다. 이는 예상 분담이며 결제·정산 완료가 아니다. 견적 실패가 방 생성·대화를 막지 않는다.

모든 신규 API 오류는 `{code,error}`이고 개인화·세션·채팅·견적 응답은 오류도 `Cache-Control:no-store`이다. 400 입력 오류, 403 참가 권한·CSRF·출처, 404 방 없음, 409 정원·상태·재시도 충돌, 413 본문 16KiB 초과, 429 빈도 제한(`Retry-After`), 503 설정·DB 이용 불가로 구분한다. DB 경로·외부 원문·키·스택은 응답에 포함하지 않는다.

## 보관과 수동 정리

출발 시각이 지나면 즉시 expired 상태이며 데이터 삭제와는 별개다. 출발 예정 시각 이후 24시간이 지난 방·종속 참가자·메시지는 다음 명령으로 정리한다.

```bash
./.venv/bin/python scripts/cleanup_ride_rooms.py --limit 200
./.venv/bin/python scripts/cleanup_ride_rooms.py --limit 200 --apply
```

첫 명령은 미리보기이고 DB가 없어도 새 DB를 만들지 않는다. 실제 삭제는 두 번째처럼 관리자가 명시적으로 선택한다. 자동 크론·배포·실제 방 정리는 수행하지 않았다. 방 DB와 journal/WAL/SHM은 Git에서 제외한다.

## 검증과 두 브라우저 점검

```bash
./.venv/bin/python -B scripts/run_mobility_tests.py
./.venv/bin/python -B scripts/run_mobility_test_server.py
# 위 임시 서버를 켠 상태에서 다른 터미널:
node tests/frontend_smoke.cjs
CAPSTONE_LIVE_ROOM_TESTS=1 node tests/mobility_frontend_smoke.cjs
```

첫 명령은 모든 기존·신규 unittest를 실행하되 프로젝트 `.env` 로딩을 차단하고 가짜 설정·임시 DB·외부 HTTP 차단을 적용한다. 설정 테스트 자체의 임시 `.env`는 허용한다. 두 번째 명령은 실제 `.env`를 읽지 않고 외부 API를 차단한 임시 서버를 `127.0.0.1:8765`에서 실행하며, 합성 견적(10,001원)은 테스트용일 뿐 실제 춘천 운임이 아니다. 테스트 DB는 종료 시 제거된다. 일반 `unittest discover`도 가능하지만 개인 설정을 읽을 수 있으므로 위 격리 실행기를 권장한다.

프런트 테스트는 이 임시 서버와 Node·Playwright·Chromium이 필요하다. `NODE_PATH`, `CAPSTONE_BROWSER_EXECUTABLE`, `CAPSTONE_BASE_URL`로 기존 환경을 지정한다. `CAPSTONE_LIVE_ROOM_TESTS=1`은 외부 API 실호출이 아니라 임시 서버의 실제 방 HTTP를 사용하는 두 브라우저 검증이다. 이 옵션을 실제 운영 방 DB에 사용하지 않는다. 테스트는 mock/임시 DB/가짜 고정 비밀키를 사용하고 실제 키·실제 방 DB를 사용하지 않는다. 공급자 연결 점검은 자동 테스트와 분리한다.

두 브라우저 수동 확인: A에서 공개 장소를 선택·확인해 방을 만들고, B(다른 브라우저 또는 별도 프로필)에서 목록을 열어 참가한다. 인원이 2명인지, 양쪽 메시지가 갱신되는지 확인한다. A의 같은 브라우저 새 탭에서 다시 참가해도 인원은 늘지 않아야 한다. B가 나가면 인원이 줄고 B는 이후 메시지를 읽거나 보내지 못한다. A가 종료하면 기존 참가자에게 읽기 전용이며 새 참가·새 전송은 거절돼야 한다.

실행 결과: 격리된 전체 Python unittest 319개 통과(ResourceWarning 오류 승격 포함). 기존 브라우저 smoke 10개·신규 브라우저 smoke 14개 통과. 신규 검증에는 임시 SQLite의 실제 방 HTTP를 사용한 두 독립 브라우저 생성·참가·대화·응답 유실 재시도·정원 마감·같은 브라우저 새 탭·탈퇴·종료가 포함된다. 실제 방 테스트는 전용 테스트 서버의 헤더를 확인하여 운영 서버에서의 실수 실행을 차단한다. 정리 명령의 없는 DB dry-run은 대상 0건이며 DB 파일을 만들지 않는 것도 확인했다. 모바일390px·데스크톱1440px의 택시 카드와 생성 폼은 합성 데이터 화면으로 렌더링해 확인했다.

실제 TMAP 연결: 사용자가 좌표·기존 키 사용·대상을 명시 승인한 뒤 한림대 공학관(37.88695,127.73585) → 강원대 60주년 기념관(37.86815,127.74165)으로 자동차 API를 정확히 1회 호출했다. 택시 전용·영업용·요약 옵션에서 status=ok, 유효 주행시간·거리·예상요금 필드 수신을 확인했다. 키·원문 응답·견적 금액은 출력하거나 저장하지 않았다. 이 확인은 실제 미터 운임 정확도·배차 대기·미래 출발 교통 예측 검증을 의미하지 않는다.

## 변경 파일

추가23개·수정8개이며 기존 사용자 파일과 상위 Git·브랜치는 변경하지 않는다. 테스트 환경을 재현하기 위한 두 실행기도 포함한다. 실제 `.env`·가상환경·CSV·공용 main.js·기존 지도/버스/셔틀 서비스는 수정하지 않았다.

| 구분 | 파일 |
|---|---|
| 수정 | `app.py`, `services/settings.py`, `static/js/api-service.js`, `templates/index.html`, `.env.example`, `.gitignore`, `tests/test_settings.py`, `tests/frontend_smoke.cjs` |
| 신규 서버 | `services/taxi_service.py`, `services/taxi_routes.py`, `services/mobility_validation.py`, `services/fare_split.py`, `services/ride_room_repository.py`, `services/ride_room_service.py`, `services/ride_room_routes.py` |
| 신규 화면 | `static/js/taxi-controller.js`, `static/js/ride-room-controller.js`, `static/css/taxi.css`, `static/css/ride-room.css` |
| 신규 도구·문서 | `scripts/cleanup_ride_rooms.py`, `scripts/run_mobility_tests.py`, `scripts/run_mobility_test_server.py`, `docs/TAXI_RIDE_ROOMS.md` |
| 신규 검증 | `tests/test_taxi_service.py`, `tests/test_taxi_api.py`, `tests/test_fare_split.py`, `tests/test_ride_room_repository.py`, `tests/test_ride_room_service.py`, `tests/test_ride_room_api.py`, `tests/test_ride_room_cleanup.py`, `tests/mobility_frontend_smoke.cjs` |

## 공식 근거 (확인일 2026-10-20)

- [TMAP 자동차 최신 명세](https://tmapapi.tmapmobility.com/main.html#webservice/docs/tmapRouteDoc), [동적 본문](https://tmapapi.tmapmobility.com/webservice/docs/tmapRouteDoc.html)
- [TMAP 약관](https://tmapapi.tmapmobility.com/terms.html): 경로안내 무료 일1,000건 안내, 저장 데이터 24시간 이상 사용 제한. 실제 한도·과금은 계정 상품 기준
- [Flask 세션](https://flask.palletsprojects.com/en/stable/quickstart/#sessions), [웹 보안](https://flask.palletsprojects.com/en/stable/web-security/)
- [SQLite 트랜잭션](https://www.sqlite.org/lang_transaction.html), [WAL](https://www.sqlite.org/wal.html): 같은 호스트·로컬 디스크 조건과 SQLite 버전에 맞춰 선택
