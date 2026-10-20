"""Room permissions, read cursors, expiration and exact fare allocation."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import uuid

from services.mobility_validation import MobilityInputError
from services.ride_room_repository import RideRoomRepository, RoomError
from services.ride_room_service import RideRoomService


class RideRoomServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "rooms.sqlite3"
        self.now = datetime(2026, 10, 20, 5, 0, tzinfo=timezone.utc)
        self.taxi = Mock()
        self.estimate = {"fare": {"amount_krw": 10001}, "fetched_at": self.now.isoformat(),
                         "expires_at": (self.now + timedelta(seconds=60)).isoformat()}
        self.taxi.estimate.return_value = self.estimate
        self.taxi.peek.return_value = self.estimate
        self.service = RideRoomService(RideRoomRepository(self.path), self.taxi, clock=lambda: self.now,
                                       message_limit=100, create_limit=100)

    def payload(self, **changes):
        return {"client_room_id": str(uuid.uuid4()), "title": "같이 타요", "nickname": "방장",
                "meeting": {"name": "공개 장소", "lat": 37.88, "lon": 127.73},
                "destination": {"name": "목적지", "lat": 37.86, "lon": 127.72},
                "departure_at": (self.now + timedelta(hours=1)).isoformat(), "capacity": 4, **changes}

    def create(self, **changes):
        return self.service.create_room("host", self.payload(**changes))[0]["room"]["id"]

    def message(self, body="안녕하세요"):
        return {"client_message_id": str(uuid.uuid4()), "body": body}

    def test_create_retry_after_departure_and_changed_body_conflict(self):
        payload = self.payload()
        first, status = self.service.create_room("host", payload)
        self.now += timedelta(hours=2)
        retry, retry_status = self.service.create_room("host", payload)
        self.assertEqual((status, retry_status), (201, 200))
        self.assertEqual(first["room"]["id"], retry["room"]["id"])
        self.assertEqual(retry["room"]["status"], "expired")
        with self.assertRaises(RoomError) as caught:
            self.service.create_room("host", {**payload, "title": "바뀐 제목"})
        self.assertEqual(caught.exception.status_code, 409)

    def test_create_rejects_bad_types_coordinates_times_and_client_authority(self):
        cases = [None, [], self.payload(capacity=True), self.payload(capacity=3.0),
                 self.payload(nickname=7), self.payload(title="x" * 61),
                 self.payload(meeting={"name": "장소", "lat": True, "lon": 127}),
                 self.payload(meeting={"name": "장소", "lat": float("nan"), "lon": 127}),
                 self.payload(meeting={"name": "장소", "lat": 37}),
                 self.payload(departure_at="2026-10-20T14:00:00"),
                 self.payload(departure_at=self.now.isoformat()),
                 self.payload(departure_at=(self.now + timedelta(hours=24, seconds=1)).isoformat()),
                 self.payload(owner_id="host"), self.payload(fare=10), self.payload(participant_count=1)]
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises((RoomError, MobilityInputError)):
                    self.service.create_room("host", payload)
        self.taxi.estimate.assert_not_called()
        self.taxi.peek.assert_not_called()

    def test_24_hour_boundary_is_allowed_and_seoul_display_is_explicit(self):
        room_id = self.create(departure_at=(self.now + timedelta(hours=24)).isoformat())
        room = self.service.get_room("host", room_id)["room"]
        self.assertTrue(room["departure_at"].endswith("+09:00"))
        self.assertEqual(room["participant_count"], 1)

    def test_full_reopens_after_leave_and_rejoin_retains_member_identity(self):
        room_id = self.create(capacity=2)
        joined = self.service.join_room("guest", room_id, "참가자")
        self.assertEqual(joined["room"]["status"], "full")
        member_id = joined["self"]["member_id"]
        left = self.service.leave_room("guest", room_id)
        self.assertEqual((left["room"]["status"], left["room"]["participant_count"]), ("open", 1))
        version = left["room"]["room_version"]
        self.assertEqual(self.service.leave_room("guest", room_id)["room"]["room_version"], version)
        rejoined = self.service.join_room("guest", room_id, "새 닉네임")
        self.assertEqual(rejoined["self"]["member_id"], member_id)
        self.assertEqual(rejoined["room"]["participant_count"], 2)

    def test_host_close_repeats_and_readonly_permissions(self):
        room_id = self.create(capacity=2)
        self.service.join_room("guest", room_id, "참가자")
        self.service.send_message("guest", room_id, self.message())  # full permits chat
        with self.assertRaises(RoomError) as caught:
            self.service.close_room("guest", room_id)
        self.assertEqual(caught.exception.status_code, 403)
        with self.assertRaises(RoomError):
            self.service.leave_room("host", room_id)
        closed = self.service.close_room("host", room_id)
        version = closed["room"]["room_version"]
        self.assertEqual(self.service.close_room("host", room_id)["room"]["room_version"], version)
        self.now += timedelta(hours=2)
        self.assertEqual(self.service.get_room("host", room_id)["room"]["status"], "closed")
        self.assertEqual(len(self.service.get_messages("guest", room_id)["messages"]), 1)
        with self.assertRaises(RoomError) as caught:
            self.service.send_message("guest", room_id, self.message())
        self.assertEqual(caught.exception.code, "ROOM_CLOSED")

    def test_outsiders_and_departed_members_cannot_read_send_or_estimate(self):
        room_id = self.create()
        detail = self.service.get_room("outsider", room_id)
        self.assertFalse(detail["self"]["joined"])
        self.assertNotIn("participants", detail)
        self.service.join_room("guest", room_id, "참가자")
        self.service.leave_room("guest", room_id)
        for actor in ("outsider", "guest"):
            for action in (lambda: self.service.get_messages(actor, room_id),
                           lambda: self.service.send_message(actor, room_id, self.message()),
                           lambda: self.service.get_estimate(actor, room_id)):
                with self.assertRaises(RoomError) as caught:
                    action()
                self.assertEqual(caught.exception.status_code, 403)
        self.taxi.estimate.assert_not_called()

    def test_exact_departure_boundary_blocks_new_join_and_send_but_allows_read(self):
        room_id = self.create()
        self.service.join_room("guest", room_id, "참가자")
        self.now += timedelta(hours=1)
        self.assertEqual(self.service.get_room("guest", room_id)["room"]["status"], "expired")
        for action in (lambda: self.service.join_room("new", room_id, "새 참가자"),
                       lambda: self.service.send_message("guest", room_id, self.message())):
            with self.assertRaises(RoomError) as caught:
                action()
            self.assertEqual(caught.exception.code, "ROOM_EXPIRED")
        self.assertEqual(self.service.get_messages("guest", room_id)["messages"], [])

    def test_initial_recent_messages_and_forward_cursor_do_not_skip_new_rows(self):
        room_id = self.create()
        ids = [self.service.send_message("host", room_id, self.message(str(index)))[0]["message"]["id"]
               for index in range(55)]
        initial = self.service.get_messages("host", room_id)
        self.assertEqual([message["id"] for message in initial["messages"]], ids[-50:])
        self.assertEqual(initial["next_after_id"], ids[-1])
        self.assertFalse(initial["has_more"])
        page1 = self.service.get_messages("host", room_id, after_id=ids[0], limit=10)
        self.assertTrue(page1["has_more"])
        accumulated = list(page1["messages"])
        page = page1
        while page["has_more"]:
            page = self.service.get_messages("host", room_id, after_id=page["next_after_id"], limit=10)
            accumulated += page["messages"]
        self.assertEqual([message["id"] for message in accumulated], ids[1:])
        empty = self.service.get_messages("host", room_id, after_id=ids[-1])
        self.assertEqual(empty["next_after_id"], ids[-1])

    def test_messages_preserve_text_and_sender_name_snapshot(self):
        room_id = self.create()
        self.service.join_room("guest", room_id, "원래 이름")
        body = '<img src=x onerror="alert(1)">\n두 번째 줄'
        self.service.send_message("guest", room_id, self.message(body))
        self.service.leave_room("guest", room_id)
        self.service.join_room("guest", room_id, "새 이름")
        message = self.service.get_messages("guest", room_id)["messages"][0]
        self.assertEqual(message["body"], body)
        self.assertEqual(message["nickname"], "원래 이름")
        for bad in ("", " " * 3, "a" * 501, True, "abc\x00"):
            with self.assertRaises((RoomError, MobilityInputError)):
                self.service.send_message("host", room_id, self.message(bad))

    def test_list_keyset_filters_and_public_response_have_no_actor_secrets(self):
        for index in range(5):
            self.create(title=str(index), departure_at=(self.now + timedelta(minutes=10 + index)).isoformat())
        result = self.service.list_rooms("outsider", {"q": "목적지", "limit": "2"})
        rooms = list(result["rooms"])
        while result["has_more"]:
            result = self.service.list_rooms("outsider", {"q": "목적지", "limit": 2, "cursor": result["next_cursor"]})
            rooms.extend(result["rooms"])
        self.assertEqual(len({item["room"]["id"] for item in rooms}), 5)
        self.assertNotIn("actor_hash", str(rooms))
        self.assertNotIn("participants", str(rooms))
        self.taxi.estimate.assert_not_called()
        self.taxi.peek.assert_not_called()
        for query in ({"status": []}, {"limit": True}, {"cursor": "!"}, {"limit": "101"},
                      {"from": (self.now + timedelta(hours=1)).isoformat(), "to": self.now.isoformat()}):
            with self.assertRaises((RoomError, MobilityInputError)):
                self.service.list_rooms("outsider", query)

    def test_exact_current_split_is_distinct_from_capacity_and_uses_cached_poll(self):
        room_id = self.create()
        self.service.join_room("guest1", room_id, "참가자1")
        self.service.join_room("guest2", room_id, "참가자2")
        estimate = self.service.get_estimate("host", room_id)
        self.assertEqual([item["amount_krw"] for item in estimate["current_split"]], [3334, 3334, 3333])
        self.assertEqual(sum(item["amount_krw"] for item in estimate["current_split"]), 10001)
        self.assertEqual(estimate["capacity_preview"], {"count": 4, "amounts_krw": [2501, 2500, 2500, 2500]})
        self.service.leave_room("guest1", room_id)
        polled = self.service.get_messages("host", room_id)
        self.assertEqual([item["amount_krw"] for item in polled["cached_estimate"]["current_split"]], [5001, 5000])
        self.assertEqual(self.taxi.estimate.call_count, 1)
        self.taxi.peek.return_value = None
        self.assertIsNone(self.service.get_messages("host", room_id)["cached_estimate"])

    def test_missing_fare_produces_null_splits(self):
        room_id = self.create()
        self.taxi.estimate.return_value = {"fare": {"amount_krw": None}}
        result = self.service.get_estimate("host", room_id)
        self.assertIsNone(result["current_split"][0]["amount_krw"])
        self.assertIsNone(result["capacity_preview"]["amounts_krw"])

    def test_estimate_rechecks_membership_after_provider_call(self):
        room_id = self.create()
        self.service.join_room("guest", room_id, "참가자")

        def leave_during_request(*args, **kwargs):
            self.service.leave_room("guest", room_id)
            return self.estimate

        self.taxi.estimate.side_effect = leave_during_request
        with self.assertRaises(RoomError) as caught:
            self.service.get_estimate("guest", room_id)
        self.assertEqual(caught.exception.status_code, 403)

    def test_estimate_finishes_without_write_lock_and_uses_latest_participants(self):
        room_id = self.create()

        def join_during_request(*args, **kwargs):
            self.service.join_room("guest", room_id, "참가자")
            return self.estimate

        self.taxi.estimate.side_effect = join_during_request
        result = self.service.get_estimate("host", room_id)
        self.assertEqual(result["participant_count"], 2)
        self.assertEqual(result["room_version"], 2)
        self.assertEqual([member["amount_krw"] for member in result["current_split"]], [5001, 5000])


if __name__ == "__main__":
    unittest.main()
