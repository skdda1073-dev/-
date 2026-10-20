"""실제 임시 SQLite/Flask 통합. 참가자·운임은 가짜이고 외부 통신은 하지 않는다."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from flask import Flask

from services.ride_room_repository import RideRoomRepository
from services.ride_room_routes import create_ride_room_blueprint
from services.ride_room_service import RideRoomService
from services.settings import Settings
from services.taxi_service import TaxiService


SECRET = "fixed-test-secret-never-used-outside-temporary-tests-123456789"
COOKIE = "capstone_ride_session"


class RideRoomApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.db_path = self.directory / "rooms.sqlite3"
        self.now = datetime(2026, 10, 20, 3, 0, tzinfo=timezone.utc)
        self.epoch = self.now
        remote = Mock(status_code=200)
        remote.json.return_value = {"features": [{"properties": {
            "totalTime": 601, "totalDistance": 3210, "taxiFare": 10001, "totalFare": 0,
        }}]}
        self.remote = Mock(return_value=remote)
        actual_taxi = TaxiService("fake-provider-key", self.remote,
                                 clock=lambda: (self.now - self.epoch).total_seconds(),
                                 utcnow=lambda: self.now)
        self.taxi = Mock(spec=TaxiService, wraps=actual_taxi)
        self.repository = RideRoomRepository(self.db_path)
        self.service = RideRoomService(self.repository, self.taxi, clock=lambda: self.now)
        self.app = self.make_app(self.service)
        self.tokens = {}
        self.host = self.new_client()

    @staticmethod
    def make_app(service, *, secure=False, configured=True, secret=SECRET):
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY=secret, MAX_CONTENT_LENGTH=16 * 1024,
                          SESSION_COOKIE_NAME=COOKIE, SESSION_COOKIE_HTTPONLY=True,
                          SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=secure)
        app.register_blueprint(create_ride_room_blueprint(service, configured=configured))
        return app

    def new_client(self, app=None):
        client = (app or self.app).test_client()
        result = client.get("/api/ride-rooms/session", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(result.status_code, 200)
        self.tokens[client] = result.json["csrf_token"]
        return client

    def headers(self, client):
        return {"X-CSRF-Token": self.tokens[client], "Origin": "http://localhost"}

    def post(self, client, path, payload):
        return client.post(path, json=payload, headers=self.headers(client),
                           environ_overrides={"REMOTE_ADDR": "127.0.0.1"})

    def room_payload(self, **changes):
        value = {
            "client_room_id": str(uuid4()), "title": "검증용 모집방", "nickname": "검증 방장",
            "meeting": {"name": "가상 공개 집결지", "lat": 37.88, "lon": 127.73},
            "destination": {"name": "가상 목적지", "lat": 37.86, "lon": 127.72},
            "departure_at": (self.now + timedelta(hours=2)).astimezone(
                timezone(timedelta(hours=9))).isoformat(), "capacity": 4,
        }
        value.update(changes)
        return value

    def create(self, client=None, **changes):
        result = self.post(client or self.host, "/api/ride-rooms", self.room_payload(**changes))
        self.assertEqual(result.status_code, 201, result.json)
        return result.json["room"]["id"], result.json

    def join(self, client, room_id, nickname="검증 참가자"):
        return self.post(client, f"/api/ride-rooms/{room_id}/join", {"nickname": nickname})

    def send(self, client, room_id, body="안녕하세요", message_id=None):
        return self.post(client, f"/api/ride-rooms/{room_id}/messages",
                         {"body": body, "client_message_id": message_id or str(uuid4())})

    def assert_error(self, response, status, code=None):
        self.assertEqual(response.status_code, status, response.get_data(as_text=True))
        self.assertEqual(set(response.json), {"code", "error"})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        if code is not None:
            self.assertEqual(response.json["code"], code)

    def test_mutations_require_session_csrf_and_same_origin(self):
        path, payload = "/api/ride-rooms", self.room_payload()
        tests = [({}, "CSRF_INVALID"), ({"Origin": "http://localhost"}, "CSRF_INVALID"),
                 ({"X-CSRF-Token": self.tokens[self.host]}, "ORIGIN_INVALID"),
                 ({"X-CSRF-Token": "wrong", "Origin": "http://localhost"}, "CSRF_INVALID"),
                 ({"X-CSRF-Token": self.tokens[self.host], "Origin": "https://evil.example"}, "ORIGIN_INVALID"),
                 ({**self.headers(self.host), "Sec-Fetch-Site": "cross-site"}, "ORIGIN_INVALID")]
        for headers, code in tests:
            with self.subTest(headers=list(headers), code=code):
                self.assert_error(self.host.post(path, json=payload, headers=headers), 403, code)
        outsider = self.app.test_client()
        self.assert_error(outsider.post(path, json=payload, headers=self.headers(self.host)), 403, "CSRF_INVALID")
        # 같은 출처 Referer도 허용하되 Origin이 명시되면 그것을 우선 검사한다.
        headers = {"X-CSRF-Token": self.tokens[self.host], "Referer": "http://localhost/planner"}
        self.assertEqual(self.host.post(path, json=payload, headers=headers).status_code, 201)
        self.remote.assert_not_called()

    def test_session_returns_csrf_only_and_public_payloads_hide_actor_and_hash(self):
        session_response = self.host.get("/api/ride-rooms/session")
        self.assertEqual(set(session_response.json), {"ready", "csrf_token"})
        self.assertEqual(session_response.headers["Cache-Control"], "no-store")
        with self.host.session_transaction() as stored:
            actor = stored["ride_actor"]
        actor_hash = sha256(actor.encode()).hexdigest()
        room_id, created = self.create()
        guest = self.new_client()
        public = guest.get(f"/api/ride-rooms/{room_id}")
        self.assertFalse(public.json["self"]["joined"])
        self.assertNotIn("participants", public.json)
        self.assertEqual(created["room"]["participant_count"], 1)
        for result in (public, self.host.get("/api/ride-rooms"), self.host.get(f"/api/ride-rooms/{room_id}/messages")):
            text = result.get_data(as_text=True)
            for secret in (actor, actor_hash, "host_actor_hash", "actor_hash", "ride_actor", "fake-provider-key"):
                self.assertNotIn(secret, text)
            self.assertEqual(result.headers["Cache-Control"], "no-store")

    def test_secure_cookie_flags_and_insecure_http_are_limited_to_loopback(self):
        secure_app = self.make_app(self.service, secure=True)
        secure_client = secure_app.test_client()
        response = secure_client.get("/api/ride-rooms/session", base_url="https://campus.example",
                                     environ_overrides={"REMOTE_ADDR": "203.0.113.1"})
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["Set-Cookie"]
        for flag in ("Secure", "HttpOnly", "SameSite=Lax"):
            self.assertIn(flag, cookie)
        local = self.app.test_client().get("/api/ride-rooms/session",
                                           environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(local.status_code, 200)
        self.assertNotIn("; Secure", local.headers["Set-Cookie"])
        for base_url, remote in (("http://campus.example", "127.0.0.1"),
                                 ("http://localhost", "203.0.113.1"),
                                 ("http://campus.example", "203.0.113.1")):
            with self.subTest(base_url=base_url, remote=remote):
                blocked = self.app.test_client().get("/api/ride-rooms/session", base_url=base_url,
                                                     environ_overrides={"REMOTE_ADDR": remote})
                self.assert_error(blocked, 403, "CHAT_HTTPS_REQUIRED")

    def test_json_object_limits_and_forbidden_client_authority_fields(self):
        for value in (None, [], "text", 3):
            with self.subTest(value=value):
                self.assert_error(self.post(self.host, "/api/ride-rooms", value), 400, "INVALID_REQUEST")
        malformed = self.host.post("/api/ride-rooms", data="{broken", content_type="application/json",
                                   headers=self.headers(self.host))
        self.assert_error(malformed, 400, "INVALID_REQUEST")
        huge = self.host.post("/api/ride-rooms", data=" " * (16 * 1024 + 1),
                             content_type="application/json", headers=self.headers(self.host))
        self.assert_error(huge, 413, "REQUEST_TOO_LARGE")
        for field, value in (("owner_id", "attacker"), ("participant_count", 4), ("fare", 1)):
            self.assert_error(self.post(self.host, "/api/ride-rooms", self.room_payload(**{field: value})),
                              400, "INVALID_REQUEST")
        self.remote.assert_not_called()

    def test_create_retry_is_idempotent_and_body_change_conflicts_without_rate_cost(self):
        self.service.create_limit = 1
        payload = self.room_payload()
        first = self.post(self.host, "/api/ride-rooms", payload)
        repeated = self.post(self.host, "/api/ride-rooms", payload)
        self.assertEqual((first.status_code, repeated.status_code), (201, 200))
        self.assertEqual(first.json, repeated.json)
        self.assertEqual(repeated.json["room"]["participant_count"], 1)
        changed = self.post(self.host, "/api/ride-rooms", dict(payload, title="다른 내용"))
        self.assert_error(changed, 409, "ROOM_RETRY_CONFLICT")
        limited = self.post(self.host, "/api/ride-rooms", self.room_payload())
        self.assert_error(limited, 429, "ROOM_RATE_LIMIT")
        self.assertGreater(int(limited.headers["Retry-After"]), 0)
        self.remote.assert_not_called()

    def test_full_room_allows_existing_members_retries_and_messages_and_reopens_after_leave(self):
        room_id, created = self.create(capacity=2)
        guest = self.new_client()
        joined = self.join(guest, room_id)
        self.assertEqual(joined.status_code, 200)
        self.assertEqual(joined.json["room"]["status"], "full")
        version = joined.json["room"]["room_version"]
        member_id = joined.json["self"]["member_id"]
        for client in (self.host, guest):
            repeated = self.join(client, room_id)
            self.assertEqual(repeated.status_code, 200)
            self.assertEqual(repeated.json["room"]["participant_count"], 2)
            self.assertEqual(repeated.json["room"]["room_version"], version)
            self.assertEqual(self.send(client, room_id).status_code, 201)
        outsider = self.new_client()
        self.assert_error(self.join(outsider, room_id), 409, "ROOM_FULL")
        left = self.post(guest, f"/api/ride-rooms/{room_id}/leave", {})
        self.assertEqual(left.json["room"]["status"], "open")
        self.assertEqual(left.json["room"]["participant_count"], 1)
        self.assertFalse(left.json["self"]["joined"])
        rejoined = self.join(guest, room_id)
        self.assertEqual(rejoined.json["self"]["member_id"], member_id)
        self.assertEqual(rejoined.json["room"]["participant_count"], 2)
        self.assertGreater(rejoined.json["room"]["room_version"], version)
        self.assertEqual(created["room"]["participant_count"], 1)

    def test_nonmembers_and_former_members_cannot_read_send_or_estimate(self):
        room_id, _ = self.create()
        guest = self.new_client()
        for joined_before in (False, True):
            if joined_before:
                self.assertEqual(self.join(guest, room_id).status_code, 200)
                self.assertEqual(self.post(guest, f"/api/ride-rooms/{room_id}/leave", {}).status_code, 200)
            for suffix in ("messages", "estimate"):
                self.assert_error(guest.get(f"/api/ride-rooms/{room_id}/{suffix}"), 403, "ROOM_FORBIDDEN")
            self.assert_error(self.send(guest, room_id), 403, "ROOM_FORBIDDEN")
        self.taxi.estimate.assert_not_called()
        self.remote.assert_not_called()

    def test_host_only_close_is_idempotent_and_closed_room_remains_readable(self):
        room_id, _ = self.create()
        guest = self.new_client()
        self.join(guest, room_id)
        self.send(self.host, room_id, "종료 전 기록")
        self.assert_error(self.post(guest, f"/api/ride-rooms/{room_id}/close", {}), 403, "ROOM_FORBIDDEN")
        self.assert_error(self.post(self.host, f"/api/ride-rooms/{room_id}/leave", {}), 409, "HOST_MUST_CLOSE")
        first = self.post(self.host, f"/api/ride-rooms/{room_id}/close", {})
        repeated = self.post(self.host, f"/api/ride-rooms/{room_id}/close", {})
        self.assertEqual(first.json, repeated.json)
        self.assertEqual(first.json["room"]["status"], "closed")
        self.assert_error(self.send(guest, room_id), 409, "ROOM_CLOSED")
        self.assert_error(self.join(self.new_client(), room_id), 409, "ROOM_CLOSED")
        readable = guest.get(f"/api/ride-rooms/{room_id}/messages")
        self.assertEqual(readable.status_code, 200)
        self.assertEqual(readable.json["messages"][0]["body"], "종료 전 기록")
        self.assertEqual(readable.json["room"]["status"], "closed")
        self.assertEqual(readable.json["room"]["participant_count"], 2)

    def test_departure_boundary_expires_without_losing_read_access_or_membership(self):
        departure = self.now + timedelta(minutes=1)
        room_id, _ = self.create(departure_at=departure.isoformat())
        self.send(self.host, room_id, "출발 전 기록")
        self.now = departure
        self.assert_error(self.join(self.new_client(), room_id), 409, "ROOM_EXPIRED")
        self.assert_error(self.send(self.host, room_id), 409, "ROOM_EXPIRED")
        readable = self.host.get(f"/api/ride-rooms/{room_id}/messages")
        self.assertEqual(readable.status_code, 200)
        self.assertEqual(readable.json["room"]["status"], "expired")
        self.assertEqual(readable.json["room"]["participant_count"], 1)
        self.assertTrue(readable.json["self"]["joined"])
        closed = self.post(self.host, f"/api/ride-rooms/{room_id}/close", {})
        self.assertEqual(closed.json["room"]["status"], "closed")

    def test_departure_policy_requires_timezone_and_future_within_24_hours(self):
        for departure in (self.now.isoformat(), (self.now - timedelta(seconds=1)).isoformat(),
                          (self.now + timedelta(hours=24, microseconds=1)).isoformat(),
                          "2026-10-20T13:00:00"):
            with self.subTest(departure=departure):
                self.assert_error(self.post(self.host, "/api/ride-rooms", self.room_payload(departure_at=departure)),
                                  400, "INVALID_DEPARTURE_AT")
        self.create(departure_at=(self.now + timedelta(hours=24)).isoformat())

    def test_message_retry_is_scoped_to_actor_and_conflicting_body_is_409(self):
        room_id, _ = self.create()
        guest = self.new_client()
        self.join(guest, room_id)
        message_id = str(uuid4())
        first = self.send(self.host, room_id, "같은 내용", message_id)
        repeated = self.send(self.host, room_id, "같은 내용", message_id)
        self.assertEqual((first.status_code, repeated.status_code), (201, 200))
        self.assertEqual(first.json, repeated.json)
        self.assert_error(self.send(self.host, room_id, "수정한 내용", message_id), 409, "MESSAGE_RETRY_CONFLICT")
        other_actor = self.send(guest, room_id, "다른 참가자의 내용", message_id)
        self.assertEqual(other_actor.status_code, 201)
        self.assertNotEqual(first.json["message"]["id"], other_actor.json["message"]["id"])
        rows = self.host.get(f"/api/ride-rooms/{room_id}/messages").json["messages"]
        self.assertEqual([row["body"] for row in rows], ["같은 내용", "다른 참가자의 내용"])

    def test_message_rate_limit_survives_new_service_and_retry_does_not_consume_quota(self):
        self.service.message_limit = 2
        room_id, _ = self.create()
        identifier = str(uuid4())
        self.assertEqual(self.send(self.host, room_id, message_id=identifier).status_code, 201)
        self.assertEqual(self.send(self.host, room_id).status_code, 201)
        limited = self.send(self.host, room_id)
        self.assert_error(limited, 429, "ROOM_RATE_LIMIT")
        self.assertGreater(int(limited.headers["Retry-After"]), 0)
        self.assertEqual(self.send(self.host, room_id, message_id=identifier).status_code, 200)
        new_service = RideRoomService(RideRoomRepository(self.db_path), self.taxi,
                                     clock=lambda: self.now, message_limit=2)
        new_client = self.make_app(new_service).test_client()
        new_client.set_cookie(COOKIE, self.host.get_cookie(COOKIE).value, domain="localhost")
        self.tokens[new_client] = self.tokens[self.host]
        self.assert_error(self.send(new_client, room_id), 429, "ROOM_RATE_LIMIT")
        self.now += timedelta(seconds=60)
        self.assertEqual(self.send(new_client, room_id).status_code, 201)

    def test_fixed_secret_cookie_survives_new_app_and_another_tab_is_same_member(self):
        room_id, created = self.create(capacity=2)
        old_cookie = self.host.get_cookie(COOKIE)
        another_service = RideRoomService(RideRoomRepository(self.db_path), self.taxi, clock=lambda: self.now)
        another_app = self.make_app(another_service)
        second_tab = another_app.test_client()
        second_tab.set_cookie(COOKIE, old_cookie.value, domain="localhost")
        response = second_tab.get(f"/api/ride-rooms/{room_id}")
        self.assertTrue(response.json["self"]["is_host"])
        self.assertEqual(response.json["self"]["member_id"], created["self"]["member_id"])
        self.tokens[second_tab] = self.tokens[self.host]
        repeated = self.join(second_tab, room_id, "다른 탭의 요청")
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json["room"]["participant_count"], 1)
        self.assertEqual(self.send(second_tab, room_id, "다른 탭의 대화").status_code, 201)

    def test_polling_reallocates_fresh_quote_for_actual_members_without_external_calls(self):
        room_id, _ = self.create(capacity=4)
        initial = self.host.get(f"/api/ride-rooms/{room_id}/estimate")
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json["participant_count"], 1)
        self.assertEqual(initial.json["current_split"][0]["amount_krw"], 10001)
        self.assertEqual(initial.json["capacity_preview"], {"count": 4, "amounts_krw": [2501, 2500, 2500, 2500]})
        self.assertEqual(self.remote.call_count, 1)
        guest = self.new_client()
        self.join(guest, room_id)
        polled = self.host.get(f"/api/ride-rooms/{room_id}/messages?after_id=0")
        quote = polled.json["cached_estimate"]
        self.assertEqual(polled.json["room"]["participant_count"], 2)
        self.assertEqual(quote["participant_count"], 2)
        self.assertEqual([member["amount_krw"] for member in quote["current_split"]], [5001, 5000])
        self.assertEqual(sum(row["amount_krw"] for row in quote["current_split"]), 10001)
        self.assertEqual(len(quote["capacity_preview"]["amounts_krw"]), 4)
        self.assertEqual(quote["estimate"]["fetched_at"], initial.json["estimate"]["fetched_at"])
        self.assertGreater(polled.json["room"]["room_version"], initial.json["room_version"])
        for _ in range(3):
            self.host.get(f"/api/ride-rooms/{room_id}/messages?after_id=0")
            self.host.get("/api/ride-rooms")
        self.assertEqual(self.remote.call_count, 1)
        self.assertEqual(self.taxi.estimate.call_count, 1)
        self.assertGreater(self.taxi.peek.call_count, 1)
        self.now += timedelta(seconds=60)
        expired = self.host.get(f"/api/ride-rooms/{room_id}/messages")
        self.assertIsNone(expired.json["cached_estimate"])
        self.assertEqual(self.remote.call_count, 1)

    def test_quote_failure_or_null_fare_never_prevents_creation_or_chat(self):
        self.remote.return_value.json.return_value = {"features": [{"properties": {
            "totalTime": 601, "totalDistance": 3210,
        }}]}
        room_id, _ = self.create()
        quote = self.host.get(f"/api/ride-rooms/{room_id}/estimate")
        self.assertEqual(quote.status_code, 200)
        self.assertIsNone(quote.json["current_split"][0]["amount_krw"])
        self.assertEqual(quote.json["estimate"]["ride_duration_seconds"], 601)
        self.assertEqual(self.send(self.host, room_id, "요금 없이도 대화").status_code, 201)
        self.now += timedelta(seconds=60)
        self.remote.return_value.status_code = 403
        self.assert_error(self.host.get(f"/api/ride-rooms/{room_id}/estimate"), 502, "TAXI_PROVIDER_AUTH")
        self.assertEqual(self.send(self.host, room_id, "공급자 오류 뒤에도 대화").status_code, 201)

    def test_message_first_page_and_cursor_catchup_have_no_gaps(self):
        self.service.message_limit = 120
        room_id, _ = self.create()
        sent = []
        for index in range(55):
            result = self.send(self.host, room_id, f"검증 메시지 {index}")
            self.assertEqual(result.status_code, 201)
            sent.append(result.json["message"]["id"])
        recent = self.host.get(f"/api/ride-rooms/{room_id}/messages").json
        self.assertEqual([row["id"] for row in recent["messages"]], sent[-50:])
        cursor, received = 0, []
        while True:
            result = self.host.get(f"/api/ride-rooms/{room_id}/messages?after_id={cursor}&limit=17")
            self.assertEqual(result.status_code, 200)
            page = result.json
            received.extend(row["id"] for row in page["messages"])
            cursor = page["next_after_id"]
            if not page["has_more"]:
                break
        self.assertEqual(received, sent)
        self.assertEqual(cursor, sent[-1])
        for query in ("limit=101", "limit=0", "after_id=-1", "after_id=nan"):
            self.assert_error(self.host.get(f"/api/ride-rooms/{room_id}/messages?{query}"), 400)
        self.remote.assert_not_called()

    def test_public_list_filters_and_pagination_do_not_fetch_quotes(self):
        self.service.create_limit = 10
        ids = [self.create(departure_at=(self.now + timedelta(hours=index + 1)).isoformat())[0]
               for index in range(3)]
        first = self.host.get("/api/ride-rooms?status=all&limit=2").json
        self.assertEqual([row["room"]["id"] for row in first["rooms"]], ids[:2])
        self.assertTrue(first["has_more"])
        second = self.host.get("/api/ride-rooms", query_string={"status": "all", "limit": 2,
                                                              "cursor": first["next_cursor"]}).json
        self.assertEqual([row["room"]["id"] for row in second["rooms"]], ids[2:])
        self.assertFalse(second["has_more"])
        filtered = self.host.get("/api/ride-rooms", query_string={
            "q": "가상 목적지", "from": (self.now + timedelta(hours=2)).isoformat(),
            "to": (self.now + timedelta(hours=3)).isoformat(),
        }).json
        self.assertEqual([row["room"]["id"] for row in filtered["rooms"]], ids[1:])
        self.assertEqual(self.host.get("/api/ride-rooms?q=없는목적지").json["rooms"], [])
        for query in ({"limit": 51}, {"cursor": "!invalid"}, {"status": "unknown"},
                      {"from": "2026-10-20T10:00:00"}):
            self.assert_error(self.host.get("/api/ride-rooms", query_string=query), 400)
        self.remote.assert_not_called()

    def test_missing_room_is_json_404_and_database_failure_is_sanitized_503(self):
        self.assert_error(self.host.get(f"/api/ride-rooms/{uuid4()}"), 404, "ROOM_NOT_FOUND")
        blocked = self.directory / "private-db-location"
        blocked.write_text("parent is a file", encoding="utf-8")
        service = RideRoomService(RideRoomRepository(blocked / "secret.sqlite3"), self.taxi,
                                  clock=lambda: self.now)
        client = self.new_client(self.make_app(service))
        failed = client.get("/api/ride-rooms")
        self.assert_error(failed, 503, "ROOM_DB_UNAVAILABLE")
        for secret in (str(self.directory), "secret.sqlite3", "private-db-location", "Traceback"):
            self.assertNotIn(secret, failed.get_data(as_text=True))

    def test_missing_chat_configuration_does_not_disable_existing_app_health_transit_or_taxi(self):
        import requests
        import services.settings as settings_module

        project_root = Path(__file__).resolve().parents[1]
        original_values = settings_module.dotenv_values

        def isolated_values(path, *args, **kwargs):
            # Match the supplied project path before resolving the .env itself:
            # a symlink to a personal settings file must also stay unread.
            if Path(os.path.abspath(path)) == project_root / ".env":
                return {}
            # Other tests' temporary .env fixtures retain their normal behavior.
            return original_values(path, *args, **kwargs)

        fake_environment = {
            "TMAP_APP_KEY": "fake-tmap", "PUBLIC_DATA_KEY": "", "NAVER_MAP_CLIENT_ID": "",
            "FLASK_SECRET_KEY": "", "APP_DEBUG": "false", "PORT": "5000",
            "SESSION_COOKIE_SECURE": "false", "RIDE_ROOM_DB_PATH": str(self.db_path),
            "TAXI_CACHE_TTL_SECONDS": "60", "TAXI_CACHE_MAX_ENTRIES": "512",
            "RIDE_ROOM_MESSAGE_RATE_PER_MINUTE": "15", "RIDE_ROOM_CREATE_RATE_PER_HOUR": "3",
        }
        with (patch.dict(os.environ, fake_environment),
              patch.object(settings_module, "dotenv_values", side_effect=isolated_values),
              patch.object(requests.sessions.Session, "request", side_effect=AssertionError("No external HTTP in tests"))):
            import app as application
            # app may already have been imported by other unittest modules. Override
            # its captured dependencies as well as the environment, without reload.
            with (patch.dict(application.app.config, {"SECRET_KEY": None}),
                  patch.object(application, "settings", Settings(tmap_app_key="fake-tmap")),
                  patch.object(application.transit_service, "nearby", return_value=[{"station_name": "검증 정류장"}]),
                  patch.object(application.taxi_service, "estimate", return_value={"status": "partial", "fare": {"amount_krw": None}})):
                client = application.app.test_client()
                self.assert_error(client.get("/api/ride-rooms/session"), 503, "CHAT_NOT_CONFIGURED")
                self.assert_error(client.get("/api/ride-rooms/bad/route"), 404, "NOT_FOUND")
                self.assert_error(client.put("/api/ride-rooms/session"), 405, "METHOD_NOT_ALLOWED")
                legacy_missing = client.get("/missing")
                self.assertEqual(legacy_missing.status_code, 404)
                self.assertEqual(legacy_missing.mimetype, "text/html")
                self.assertIsNone(legacy_missing.json)
                self.assertEqual(client.get("/health").status_code, 200)
                nearby = client.post("/get_route_info", json={"lat": 37.88, "lon": 127.73})
                self.assertEqual(nearby.status_code, 200)
                self.assertIsInstance(nearby.json, list)
                taxi = client.post("/api/taxi/estimate", json={"lat": 37.88, "lon": 127.73,
                                                               "dest_lat": 37.86, "dest_lon": 127.72})
                self.assertEqual(taxi.status_code, 200)
                self.assertEqual(taxi.json["status"], "partial")


if __name__ == "__main__":
    unittest.main()
