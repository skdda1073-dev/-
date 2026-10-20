"""Retention cleanup only touches an isolated temporary database."""

from datetime import datetime, timedelta, timezone
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock
import uuid

from services.ride_room_repository import RideRoomRepository, RoomError
from services.ride_room_service import RideRoomService


class RideRoomCleanupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "rooms.sqlite3"
        self.now = datetime(2026, 10, 20, 5, tzinfo=timezone.utc)
        self.repository = RideRoomRepository(self.path)
        self.service = RideRoomService(self.repository, Mock(), clock=lambda: self.now)

    def create(self, hours):
        payload = {"client_room_id": str(uuid.uuid4()), "title": "보관정책 테스트", "nickname": "방장",
                   "meeting": {"name": "공개 집결지", "lat": 37.88, "lon": 127.73},
                   "destination": {"name": "목적지", "lat": 37.86, "lon": 127.72},
                   "departure_at": (self.now + timedelta(hours=hours)).isoformat(), "capacity": 2}
        room_id = self.service.create_room("host", payload)[0]["room"]["id"]
        self.service.send_message("host", room_id, {"client_message_id": str(uuid.uuid4()), "body": "기록"})
        return room_id

    def test_no_database_dry_run_does_not_create_files(self):
        self.assertEqual(self.repository.cleanup(self.now),
                         {"eligible_count": 0, "selected_count": 0, "deleted_count": 0})
        self.assertFalse(self.path.exists())

    def test_expiration_does_not_delete_and_retention_cutoff_is_strict(self):
        old_id = self.create(1)
        boundary_id = self.create(2)
        self.now += timedelta(hours=26)
        self.assertEqual(self.service.get_room("host", old_id)["room"]["status"], "expired")
        dry_run = self.repository.cleanup(self.now - timedelta(hours=24))
        self.assertEqual(dry_run, {"eligible_count": 1, "selected_count": 1, "deleted_count": 0})
        self.assertEqual(len(self.service.get_messages("host", old_id)["messages"]), 1)
        applied = self.repository.cleanup(self.now - timedelta(hours=24), apply=True)
        self.assertEqual(applied["deleted_count"], 1)
        with self.assertRaises(RoomError) as caught:
            self.service.get_room("host", old_id)
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(self.service.get_room("host", boundary_id)["room"]["status"], "expired")
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM participants WHERE room_id=?", (old_id,)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages WHERE room_id=?", (old_id,)).fetchone()[0], 0)

    def test_cleanup_has_a_batch_limit_and_can_repeat(self):
        self.create(1)
        self.create(2)
        self.create(3)
        self.now += timedelta(hours=30)
        first = self.repository.cleanup(self.now - timedelta(hours=24), limit=1, apply=True)
        self.assertEqual(first, {"eligible_count": 3, "selected_count": 1, "deleted_count": 1})
        second = self.repository.cleanup(self.now - timedelta(hours=24), limit=2, apply=True)
        self.assertEqual(second["deleted_count"], 2)
        self.assertEqual(self.repository.cleanup(self.now - timedelta(hours=24), apply=True)["eligible_count"], 0)

    def test_cleanup_rejects_unbounded_or_naive_inputs(self):
        for limit in (0, 1001, True, 1.5):
            with self.assertRaises(ValueError):
                self.repository.cleanup(self.now, limit=limit)
        with self.assertRaises(ValueError):
            self.repository.cleanup(self.now.replace(tzinfo=None))

    def test_dry_run_opens_existing_database_without_schema_or_content_changes(self):
        self.create(1)
        before = self.path.read_bytes()
        # A fresh repository has not initialized schema: dry-run must still be
        # read-only rather than issuing CREATE TABLE IF NOT EXISTS statements.
        fresh = RideRoomRepository(self.path)
        fresh.cleanup(self.now + timedelta(hours=2))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(Path(str(self.path) + "-journal").exists())


if __name__ == "__main__":
    unittest.main()
