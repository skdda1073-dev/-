"""Room policies, public response shaping and transient fare allocation."""

import base64
from datetime import datetime, timezone
import hashlib
import json
from math import isfinite
import unicodedata
from zoneinfo import ZoneInfo

from services.fare_split import split_amount
from services.mobility_validation import (
    MobilityInputError, aware_datetime, clean_text, named_point, uuid_text,
)
from services.ride_room_repository import RoomError, room_status


SEOUL = ZoneInfo("Asia/Seoul")


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(SEOUL).isoformat()


def _fingerprint(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class RideRoomService:
    def __init__(self, repository, taxi_service, clock=None, message_limit=15, create_limit=3):
        self.repository = repository
        self.taxi_service = taxi_service
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.message_limit = message_limit
        self.create_limit = create_limit

    def _now(self):
        value = self.clock()
        if isinstance(value, datetime):
            return value.timestamp()
        return float(value)

    def _object(self, data, allowed):
        if not isinstance(data, dict):
            raise RoomError("INVALID_REQUEST", "요청 본문은 JSON 객체여야 합니다.")
        if set(data) - set(allowed):
            raise RoomError("INVALID_REQUEST", "허용되지 않은 요청 필드가 있습니다.")

    def _public(self, snapshot):
        row = snapshot["room"]
        own = snapshot["own"]
        room = {
            "id": row["id"], "title": row["title"],
            "meeting": {"name": row["meeting_name"], "lat": row["meeting_lat"], "lon": row["meeting_lon"]},
            "destination": {"name": row["destination_name"], "lat": row["destination_lat"], "lon": row["destination_lon"]},
            "departure_at": _iso(row["departure_at"]), "capacity": row["capacity"],
            "participant_count": len(snapshot["members"]),
            "status": room_status(row, len(snapshot["members"]), self._now()),
            "room_version": row["room_version"], "created_at": _iso(row["created_at"]),
            "closed_at": _iso(row["closed_at"]) if row["closed_at"] is not None else None,
        }
        result = {"room": room, "self": {"joined": own is not None,
                                          "is_host": own is not None and own["actor_hash"] == row["host_actor_hash"],
                                          "member_id": own["member_id"] if own is not None else None}}
        if own is not None:
            result["participants"] = [{"member_id": member["member_id"], "nickname": member["nickname"],
                                        "is_host": member["actor_hash"] == row["host_actor_hash"]}
                                       for member in snapshot["members"]]
        return result

    def create_room(self, actor_hash, data):
        self._object(data, {"client_room_id", "title", "nickname", "meeting", "destination", "departure_at", "capacity"})
        capacity = data.get("capacity")
        if isinstance(capacity, bool) or not isinstance(capacity, int) or not 2 <= capacity <= 4:
            raise RoomError("INVALID_CAPACITY", "총 정원은 방장을 포함한 2~4명의 정수여야 합니다.")
        normalized = {
            "client_room_id": uuid_text(data.get("client_room_id"), "client_room_id"),
            "title": clean_text(data.get("title"), "title", 60),
            "nickname": clean_text(data.get("nickname"), "nickname", 20),
            "meeting": named_point(data.get("meeting"), "meeting"),
            "destination": named_point(data.get("destination"), "destination"),
            "departure_at": aware_datetime(data.get("departure_at"), "departure_at").timestamp(),
            "capacity": capacity,
        }
        normalized["body_hash"] = _fingerprint(normalized)
        snapshot, created = self.repository.create(actor_hash, normalized, self._now, self.create_limit)
        return self._public(snapshot), 201 if created else 200

    def get_room(self, actor_hash, room_id):
        room_id = uuid_text(room_id, "room_id")
        return self._public(self.repository.snapshot(room_id, actor_hash))

    def join_room(self, actor_hash, room_id, nickname):
        room_id = uuid_text(room_id, "room_id")
        nickname = clean_text(nickname, "nickname", 20)
        return self._public(self.repository.join(room_id, actor_hash, nickname, self._now))

    def leave_room(self, actor_hash, room_id):
        room_id = uuid_text(room_id, "room_id")
        return self._public(self.repository.leave(room_id, actor_hash, self._now))

    def close_room(self, actor_hash, room_id):
        room_id = uuid_text(room_id, "room_id")
        return self._public(self.repository.close(room_id, actor_hash, self._now))

    @staticmethod
    def _message(row):
        return {"id": row["id"], "member_id": row["member_id"], "nickname": row["nickname"],
                "body": row["body"], "created_at": _iso(row["created_at"])}

    @staticmethod
    def _bounded_integer(value, field, lower, upper):
        if isinstance(value, bool) or not (isinstance(value, int) or
                                          isinstance(value, str) and value.isascii() and value.isdigit()):
            raise RoomError("INVALID_" + field.upper(), "조회 숫자 조건을 확인하세요.")
        try:
            number = int(value)
        except (ValueError, OverflowError):
            raise RoomError("INVALID_" + field.upper(), "조회 숫자 조건을 확인하세요.") from None
        if not lower <= number <= upper:
            raise RoomError("INVALID_" + field.upper(), "조회 숫자 범위를 확인하세요.")
        return number

    def get_messages(self, actor_hash, room_id, after_id=None, limit=50):
        room_id = uuid_text(room_id, "room_id")
        limit = self._bounded_integer(limit, "limit", 1, 100)
        if after_id is not None:
            after_id = self._bounded_integer(after_id, "after_id", 0, 9223372036854775807)
        snapshot, rows, has_more = self.repository.messages(room_id, actor_hash, after_id, limit)
        result = self._public(snapshot)
        result.update(messages=[self._message(row) for row in rows],
                      next_after_id=rows[-1]["id"] if rows else (after_id or 0), has_more=has_more,
                      cached_estimate=self._cached_estimate(snapshot))
        return result

    def send_message(self, actor_hash, room_id, data):
        room_id = uuid_text(room_id, "room_id")
        self._object(data, {"body", "client_message_id"})
        body = data.get("body")
        if not isinstance(body, str):
            raise RoomError("INVALID_BODY", "메시지는 1~500자 문자열이어야 합니다.")
        body = unicodedata.normalize("NFC", body.replace("\r\n", "\n")).strip()
        if not 1 <= len(body) <= 500 or any(unicodedata.category(char).startswith("C") and char not in "\n\t" for char in body):
            raise RoomError("INVALID_BODY", "메시지는 제어문자 없이 1~500자로 입력하세요.")
        normalized = {"body": body, "client_message_id": uuid_text(data.get("client_message_id"), "client_message_id")}
        normalized["body_hash"] = _fingerprint(normalized)
        snapshot, row, created = self.repository.send(room_id, actor_hash, normalized, self._now, self.message_limit)
        return {"message": self._message(row), "room_version": snapshot["room"]["room_version"]}, 201 if created else 200

    def list_rooms(self, actor_hash, query_dict):
        self._object(query_dict, {"q", "from", "to", "status", "limit", "cursor"})
        query = {"limit": self._bounded_integer(query_dict.get("limit", 20), "limit", 1, 50)}
        if query_dict.get("q"):
            query["q"] = clean_text(query_dict["q"], "q", 100)
        status = query_dict.get("status", "open")
        if not isinstance(status, str) or status not in {"open", "full", "closed", "expired", "all", ""}:
            raise RoomError("INVALID_STATUS", "모집 상태 조건을 확인하세요.")
        query["status"] = status
        for name in ("from", "to"):
            if query_dict.get(name):
                query[name] = aware_datetime(query_dict[name], name).timestamp()
        if "from" in query and "to" in query and query["from"] > query["to"]:
            raise RoomError("INVALID_TIME_RANGE", "출발 시각 구간을 확인하세요.")
        if query_dict.get("cursor"):
            raw = query_dict["cursor"]
            try:
                if not isinstance(raw, str) or len(raw) > 300:
                    raise ValueError("cursor")
                timestamp, room_id = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode())
                if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not isfinite(timestamp):
                    raise ValueError("timestamp")
                query["cursor"] = (timestamp, uuid_text(room_id, "cursor"))
            except (ValueError, TypeError, UnicodeError, MobilityInputError):
                raise RoomError("INVALID_CURSOR", "목록 페이지 식별자를 확인하세요.") from None
        snapshots, has_more = self.repository.list_snapshots(actor_hash, self._now(), query)
        cursor = None
        if has_more and snapshots:
            row = snapshots[-1]["room"]
            cursor = base64.urlsafe_b64encode(json.dumps([row["departure_at"], row["id"]]).encode()).decode().rstrip("=")
        return {"rooms": [self._public(snapshot) for snapshot in snapshots], "next_cursor": cursor, "has_more": has_more}

    def _estimate_payload(self, snapshot, estimate):
        members = snapshot["members"]
        fare = estimate.get("fare", {}).get("amount_krw") if estimate else None
        amounts = split_amount(fare, len(members)) if members else None
        return {"estimate": estimate, "room_version": snapshot["room"]["room_version"],
                "participant_count": len(members),
                "current_split": [{"member_id": member["member_id"], "nickname": member["nickname"],
                                    "amount_krw": amounts[index] if amounts is not None else None}
                                   for index, member in enumerate(members)],
                "capacity_preview": {"count": snapshot["room"]["capacity"],
                                     "amounts_krw": split_amount(fare, snapshot["room"]["capacity"])}}

    def _points(self, snapshot):
        public = self._public(snapshot)["room"]
        return public["meeting"], public["destination"]

    def _cached_estimate(self, snapshot):
        origin, destination = self._points(snapshot)
        estimate = self.taxi_service.peek(origin, destination)
        return self._estimate_payload(snapshot, estimate) if estimate is not None else None

    def get_estimate(self, actor_hash, room_id):
        room_id = uuid_text(room_id, "room_id")
        snapshot = self.repository.snapshot(room_id, actor_hash, require_member=True)
        origin, destination = self._points(snapshot)
        estimate = self.taxi_service.estimate(origin, destination, split_count=1)
        # External calls never hold a DB transaction. Recheck rights and membership afterwards.
        snapshot = self.repository.snapshot(room_id, actor_hash, require_member=True)
        return self._estimate_payload(snapshot, estimate)
