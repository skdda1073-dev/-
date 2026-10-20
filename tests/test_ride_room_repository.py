"""Real temporary SQLite connections exercise atomic room invariants."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock
import uuid

from services.ride_room_repository import RideRoomRepository, RoomError
from services.ride_room_service import RideRoomService


class RideRoomRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "nested" / "rooms.sqlite3"
        self.now = datetime(2026, 10, 20, 5, 0, tzinfo=timezone.utc)
        self.taxi = Mock()
        self.taxi.peek.return_value = None
        self.repository = RideRoomRepository(self.path)
        self.service = self.make_service()

    def make_service(self, **kwargs):
        return RideRoomService(RideRoomRepository(self.path), self.taxi, clock=lambda: self.now, **kwargs)

    def payload(self, capacity=2):
        return {"client_room_id": str(uuid.uuid4()), "title": "함께 이동", "nickname": "방장",
                "meeting": {"name": "공개 집결지", "lat": 37.88, "lon": 127.73},
                "destination": {"name": "목적지", "lat": 37.86, "lon": 127.72},
                "departure_at": (self.now + timedelta(hours=1)).isoformat(), "capacity": capacity}

    def create(self, capacity=2):
        return self.service.create_room("host-hash", self.payload(capacity))[0]["room"]["id"]

    def test_lazy_initialization_and_creation_host_are_atomic(self):
        self.assertFalse(self.path.exists())
        room_id = self.create()
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM rooms").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM participants WHERE active=1").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        self.assertEqual(self.service.get_room("host-hash", room_id)["room"]["participant_count"], 1)

    def test_independent_connections_cannot_overfill_last_seat(self):
        room_id = self.create()
        barrier = threading.Barrier(8)

        def attempt(index):
            service = self.make_service()
            barrier.wait()
            try:
                service.join_room("guest-%s" % index, room_id, "참가자")
                return "joined"
            except RoomError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(8)))
        self.assertEqual(results.count("joined"), 1)
        self.assertEqual(results.count("ROOM_FULL"), 7)
        self.assertEqual(self.service.get_room("host-hash", room_id)["room"]["participant_count"], 2)

    def test_same_actor_two_tabs_join_once_even_when_full(self):
        room_id = self.create()
        barrier = threading.Barrier(2)

        def attempt(_):
            service = self.make_service()
            barrier.wait()
            return service.join_room("same-guest", room_id, "참가자")

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual([result["room"]["participant_count"] for result in results], [2, 2])
        self.assertEqual(results[0]["self"]["member_id"], results[1]["self"]["member_id"])
        self.assertEqual(results[0]["room"]["room_version"], 2)

    def test_concurrent_create_retry_has_one_room_and_one_host(self):
        payload = self.payload()
        barrier = threading.Barrier(2)

        def attempt(_):
            service = self.make_service()
            barrier.wait()
            return service.create_room("host-hash", payload)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sorted(result[1] for result in results), [200, 201])
        self.assertEqual(results[0][0]["room"]["id"], results[1][0]["room"]["id"])
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM participants").fetchone()[0], 1)

    def test_message_retry_is_per_actor_and_conflicting_content_is_rejected(self):
        room_id = self.create()
        self.service.join_room("guest", room_id, "참가자")
        message = {"client_message_id": str(uuid.uuid4()), "body": "만날게요"}
        first, status = self.service.send_message("host-hash", room_id, message)
        retry, retry_status = self.service.send_message("host-hash", room_id, message)
        other, other_status = self.service.send_message("guest", room_id, message)
        self.assertEqual((status, retry_status, other_status), (201, 200, 201))
        self.assertEqual(first["message"]["id"], retry["message"]["id"])
        self.assertNotEqual(first["message"]["id"], other["message"]["id"])
        with self.assertRaises(RoomError) as caught:
            self.service.send_message("host-hash", room_id, {**message, "body": "다른 본문"})
        self.assertEqual(caught.exception.code, "MESSAGE_RETRY_CONFLICT")

    def test_rate_limits_persist_across_repository_instances_and_ignore_retries(self):
        service = self.make_service(message_limit=1, create_limit=1)
        payload = self.payload()
        room_id = service.create_room("host-hash", payload)[0]["room"]["id"]
        service.create_room("host-hash", payload)
        other_service = self.make_service(message_limit=1, create_limit=1)
        with self.assertRaises(RoomError) as caught:
            other_service.create_room("host-hash", self.payload())
        self.assertEqual(caught.exception.status_code, 429)
        self.assertGreater(caught.exception.retry_after, 0)
        message = {"client_message_id": str(uuid.uuid4()), "body": "첫 메시지"}
        service.send_message("host-hash", room_id, message)
        other_service.send_message("host-hash", room_id, message)
        with self.assertRaises(RoomError) as caught:
            other_service.send_message("host-hash", room_id, {**message, "client_message_id": str(uuid.uuid4())})
        self.assertEqual(caught.exception.status_code, 429)
        self.now += timedelta(seconds=61)
        self.assertEqual(other_service.send_message("host-hash", room_id,
                         {**message, "client_message_id": str(uuid.uuid4())})[1], 201)

    def test_failed_insert_rolls_back_room_host_and_rate_limit(self):
        self.repository._initialize()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TRIGGER fail_host BEFORE INSERT ON participants BEGIN SELECT RAISE(ABORT,'private-detail'); END")
        with self.assertRaises(RoomError) as caught:
            self.service.create_room("host-hash", self.payload())
        self.assertEqual(caught.exception.status_code, 503)
        self.assertNotIn("private-detail", str(caught.exception))
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM rooms").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM rate_limit_events").fetchone()[0], 0)

    def test_busy_database_has_safe_503_and_connection_recovers(self):
        room_id = self.create()
        locked = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(locked.close)
        locked.execute("BEGIN IMMEDIATE")
        service = RideRoomService(RideRoomRepository(self.path, busy_timeout_ms=20), self.taxi, clock=lambda: self.now)
        with self.assertRaises(RoomError) as caught:
            service.join_room("guest", room_id, "참가자")
        self.assertEqual(caught.exception.code, "ROOM_DB_UNAVAILABLE")
        self.assertNotIn(str(self.path), str(caught.exception))
        locked.rollback()
        self.assertEqual(service.join_room("guest", room_id, "참가자")["room"]["participant_count"], 2)

    def test_clock_is_sampled_after_write_lock_acquisition(self):
        room_id = self.create()
        service = self.make_service()
        service.repository._initialize()
        locked = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(locked.close)
        locked.execute("BEGIN IMMEDIATE")
        started, sampled = threading.Event(), threading.Event()

        def clock():
            value = self.now
            sampled.set()
            return value

        service.clock = clock

        def join():
            started.set()
            return service.join_room("guest", room_id, "참가자")

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(join)
            self.assertTrue(started.wait(1))
            # An implementation sampling before BEGIN IMMEDIATE would already
            # capture the old value here; correct code is still waiting for SQL.
            sampled.wait(0.05)
            self.now += timedelta(hours=1)
            locked.rollback()
            with self.assertRaises(RoomError) as caught:
                future.result(timeout=2)
        self.assertEqual(caught.exception.code, "ROOM_EXPIRED")


if __name__ == "__main__":
    unittest.main()
