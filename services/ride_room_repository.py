"""SQLite storage for anonymous ride rooms; no provider estimates are persisted."""

from contextlib import contextmanager
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
import sqlite3
import threading
import uuid


class RoomError(RuntimeError):
    def __init__(self, code, message, status_code=400, retry_after=None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retry_after = retry_after


def room_status(room, count, now):
    if room["closed_at"] is not None:
        return "closed"
    if room["departure_at"] <= now:
        return "expired"
    return "full" if count >= room["capacity"] else "open"


class RideRoomRepository:
    def __init__(self, db_path, busy_timeout_ms=3000):
        self.db_path = str(db_path)
        self.busy_timeout_ms = busy_timeout_ms
        self._initialized = False
        self._init_lock = threading.Lock()

    def _connect(self, read_only=False):
        location = Path(self.db_path).resolve().as_uri() + "?mode=ro" if read_only else self.db_path
        connection = sqlite3.connect(location, timeout=self.busy_timeout_ms / 1000,
                                     isolation_level=None, uri=read_only)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=%d" % self.busy_timeout_ms)
            return connection
        except Exception:
            connection.close()
            raise

    def _initialize(self):
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            connection = self._connect()
            try:
                connection.executescript("""
                CREATE TABLE IF NOT EXISTS rooms (
                    id TEXT PRIMARY KEY, host_actor_hash TEXT NOT NULL,
                    client_room_id TEXT NOT NULL, body_hash TEXT NOT NULL,
                    title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 60),
                    meeting_name TEXT NOT NULL, meeting_lat REAL NOT NULL, meeting_lon REAL NOT NULL,
                    destination_name TEXT NOT NULL, destination_lat REAL NOT NULL, destination_lon REAL NOT NULL,
                    departure_at REAL NOT NULL, capacity INTEGER NOT NULL CHECK(capacity BETWEEN 2 AND 4),
                    created_at REAL NOT NULL, closed_at REAL, room_version INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(host_actor_hash, client_room_id)
                );
                CREATE TABLE IF NOT EXISTS participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                    actor_hash TEXT NOT NULL, member_id TEXT NOT NULL UNIQUE,
                    nickname TEXT NOT NULL CHECK(length(nickname) BETWEEN 1 AND 20),
                    active INTEGER NOT NULL CHECK(active IN (0,1)),
                    joined_at REAL NOT NULL, left_at REAL,
                    UNIQUE(room_id, actor_hash)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                    participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
                    actor_hash TEXT NOT NULL, client_message_id TEXT NOT NULL, body_hash TEXT NOT NULL,
                    nickname TEXT NOT NULL, body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 500),
                    created_at REAL NOT NULL,
                    UNIQUE(room_id, actor_hash, client_message_id)
                );
                CREATE TABLE IF NOT EXISTS rate_limit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_hash TEXT NOT NULL, action TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS rooms_departure ON rooms(departure_at,id);
                CREATE INDEX IF NOT EXISTS participants_active ON participants(room_id,active,id);
                CREATE INDEX IF NOT EXISTS messages_room ON messages(room_id,id);
                CREATE INDEX IF NOT EXISTS rate_limit_window ON rate_limit_events(actor_hash,action,created_at);
                CREATE INDEX IF NOT EXISTS rate_limit_cleanup ON rate_limit_events(created_at);
                """)
                self._initialized = True
            finally:
                connection.close()

    @contextmanager
    def transaction(self, write=False, initialize=True):
        connection = None
        try:
            if initialize:
                self._initialize()
            connection = self._connect(read_only=not initialize and not write)
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except (sqlite3.Error, OSError):
            if connection is not None:
                connection.rollback()
            raise RoomError("ROOM_DB_UNAVAILABLE", "모집방 저장소를 이용할 수 없습니다. 잠시 후 다시 시도하세요.", 503) from None
        except Exception:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    def _snapshot(self, connection, room_id, actor_hash, require_member=False):
        row = connection.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
        if row is None:
            raise RoomError("ROOM_NOT_FOUND", "모집방을 찾을 수 없습니다.", 404)
        members = [dict(member) for member in connection.execute(
            "SELECT * FROM participants WHERE room_id=? AND active=1 ORDER BY id", (room_id,))]
        own = next((member for member in members if member["actor_hash"] == actor_hash), None)
        if require_member and own is None:
            raise RoomError("ROOM_FORBIDDEN", "참가한 사람만 이 기능을 이용할 수 있습니다.", 403)
        return {"room": dict(row), "members": members, "own": own}

    def snapshot(self, room_id, actor_hash, require_member=False):
        with self.transaction() as connection:
            return self._snapshot(connection, room_id, actor_hash, require_member)

    def _writable(self, snapshot, now, joining=False):
        state = room_status(snapshot["room"], len(snapshot["members"]), now)
        if state in {"closed", "expired"}:
            raise RoomError("ROOM_" + state.upper(), "종료되었거나 출발 시각이 지난 모집방입니다.", 409)
        if joining and state == "full":
            raise RoomError("ROOM_FULL", "모집 정원이 찼습니다.", 409)

    def _rate_limit(self, connection, actor_hash, action, now, limit, window):
        connection.execute("DELETE FROM rate_limit_events WHERE created_at<=?", (now - 3600,))
        rows = connection.execute(
            "SELECT COUNT(*) AS n, MIN(created_at) AS first FROM rate_limit_events "
            "WHERE actor_hash=? AND action=? AND created_at>?", (actor_hash, action, now - window)).fetchone()
        if rows["n"] >= limit:
            retry = max(1, int(rows["first"] + window - now) + 1)
            raise RoomError("ROOM_RATE_LIMIT", "요청이 많습니다. 잠시 후 다시 시도하세요.", 429, retry)
        connection.execute("INSERT INTO rate_limit_events(actor_hash,action,created_at) VALUES(?,?,?)",
                           (actor_hash, action, now))

    def create(self, actor_hash, data, now, create_limit):
        with self.transaction(write=True) as connection:
            now = now() if callable(now) else now
            previous = connection.execute(
                "SELECT id,body_hash FROM rooms WHERE host_actor_hash=? AND client_room_id=?",
                (actor_hash, data["client_room_id"])).fetchone()
            if previous:
                if previous["body_hash"] != data["body_hash"]:
                    raise RoomError("ROOM_RETRY_CONFLICT", "같은 생성 식별자로 다른 내용을 보낼 수 없습니다.", 409)
                return self._snapshot(connection, previous["id"], actor_hash), False
            if not now < data["departure_at"] <= now + 86400:
                raise RoomError("INVALID_DEPARTURE_AT", "출발 시각은 현재 이후 24시간 이내여야 합니다.")
            self._rate_limit(connection, actor_hash, "create", now, create_limit, 3600)
            room_id = str(uuid.uuid4())
            meeting, destination = data["meeting"], data["destination"]
            connection.execute(
                "INSERT INTO rooms(id,host_actor_hash,client_room_id,body_hash,title,"
                "meeting_name,meeting_lat,meeting_lon,destination_name,destination_lat,destination_lon,"
                "departure_at,capacity,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (room_id, actor_hash, data["client_room_id"], data["body_hash"], data["title"],
                 meeting["name"], meeting["lat"], meeting["lon"], destination["name"], destination["lat"],
                 destination["lon"], data["departure_at"], data["capacity"], now))
            connection.execute(
                "INSERT INTO participants(room_id,actor_hash,member_id,nickname,active,joined_at) VALUES(?,?,?,?,1,?)",
                (room_id, actor_hash, str(uuid.uuid4()), data["nickname"], now))
            return self._snapshot(connection, room_id, actor_hash), True

    def join(self, room_id, actor_hash, nickname, now):
        with self.transaction(write=True) as connection:
            now = now() if callable(now) else now
            snapshot = self._snapshot(connection, room_id, actor_hash)
            if snapshot["own"] is not None:
                return snapshot
            self._writable(snapshot, now, joining=True)
            previous = connection.execute("SELECT id FROM participants WHERE room_id=? AND actor_hash=?",
                                          (room_id, actor_hash)).fetchone()
            if previous:
                connection.execute("UPDATE participants SET active=1,left_at=NULL,nickname=? WHERE id=?",
                                   (nickname, previous["id"]))
            else:
                connection.execute(
                    "INSERT INTO participants(room_id,actor_hash,member_id,nickname,active,joined_at) VALUES(?,?,?,?,1,?)",
                    (room_id, actor_hash, str(uuid.uuid4()), nickname, now))
            connection.execute("UPDATE rooms SET room_version=room_version+1 WHERE id=?", (room_id,))
            return self._snapshot(connection, room_id, actor_hash)

    def leave(self, room_id, actor_hash, now):
        with self.transaction(write=True) as connection:
            now = now() if callable(now) else now
            snapshot = self._snapshot(connection, room_id, actor_hash)
            if snapshot["room"]["host_actor_hash"] == actor_hash:
                raise RoomError("HOST_MUST_CLOSE", "방장은 나가기 대신 방 종료를 이용하세요.", 409)
            if snapshot["own"] is not None:
                connection.execute("UPDATE participants SET active=0,left_at=? WHERE room_id=? AND actor_hash=?",
                                   (now, room_id, actor_hash))
                connection.execute("UPDATE rooms SET room_version=room_version+1 WHERE id=?", (room_id,))
            return self._snapshot(connection, room_id, actor_hash)

    def close(self, room_id, actor_hash, now):
        with self.transaction(write=True) as connection:
            now = now() if callable(now) else now
            snapshot = self._snapshot(connection, room_id, actor_hash)
            if snapshot["room"]["host_actor_hash"] != actor_hash:
                raise RoomError("ROOM_FORBIDDEN", "방장만 모집방을 종료할 수 있습니다.", 403)
            if snapshot["room"]["closed_at"] is None:
                connection.execute("UPDATE rooms SET closed_at=?,room_version=room_version+1 WHERE id=?",
                                   (now, room_id))
            return self._snapshot(connection, room_id, actor_hash)

    def messages(self, room_id, actor_hash, after_id, limit):
        with self.transaction() as connection:
            snapshot = self._snapshot(connection, room_id, actor_hash, True)
            if after_id is None:
                rows = list(connection.execute(
                    "SELECT m.*,p.member_id FROM messages m JOIN participants p ON p.id=m.participant_id "
                    "WHERE m.room_id=? ORDER BY m.id DESC LIMIT ?", (room_id, limit)))
                rows.reverse()
                has_more = False
            else:
                rows = list(connection.execute(
                    "SELECT m.*,p.member_id FROM messages m JOIN participants p ON p.id=m.participant_id "
                    "WHERE m.room_id=? AND m.id>? ORDER BY m.id LIMIT ?", (room_id, after_id, limit + 1)))
                has_more = len(rows) > limit
                rows = rows[:limit]
            return snapshot, [dict(row) for row in rows], has_more

    def send(self, room_id, actor_hash, data, now, message_limit):
        with self.transaction(write=True) as connection:
            now = now() if callable(now) else now
            snapshot = self._snapshot(connection, room_id, actor_hash, True)
            previous = connection.execute(
                "SELECT m.*,p.member_id FROM messages m JOIN participants p ON p.id=m.participant_id "
                "WHERE m.room_id=? AND m.actor_hash=? AND m.client_message_id=?",
                (room_id, actor_hash, data["client_message_id"])).fetchone()
            if previous:
                if previous["body_hash"] != data["body_hash"]:
                    raise RoomError("MESSAGE_RETRY_CONFLICT", "같은 전송 식별자로 다른 메시지를 보낼 수 없습니다.", 409)
                return snapshot, dict(previous), False
            self._writable(snapshot, now)
            self._rate_limit(connection, actor_hash, "message", now, message_limit, 60)
            member = snapshot["own"]
            cursor = connection.execute(
                "INSERT INTO messages(room_id,participant_id,actor_hash,client_message_id,body_hash,nickname,body,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)", (room_id, member["id"], actor_hash, data["client_message_id"],
                                           data["body_hash"], member["nickname"], data["body"], now))
            row = dict(connection.execute("SELECT * FROM messages WHERE id=?", (cursor.lastrowid,)).fetchone())
            row["member_id"] = member["member_id"]
            return snapshot, row, True

    def list_snapshots(self, actor_hash, now, query):
        conditions, parameters = [], []
        if query.get("q"):
            conditions.append("instr(destination_name,?)>0")
            parameters.append(query["q"])
        if query.get("from") is not None:
            conditions.append("departure_at>=?")
            parameters.append(query["from"])
        if query.get("to") is not None:
            conditions.append("departure_at<=?")
            parameters.append(query["to"])
        if query.get("cursor"):
            conditions.append("(departure_at,id)>(?,?)")
            parameters.extend(query["cursor"])
        status = query.get("status")
        active_count = "(SELECT COUNT(*) FROM participants p WHERE p.room_id=rooms.id AND p.active=1)"
        if status == "closed":
            conditions.append("closed_at IS NOT NULL")
        elif status == "expired":
            conditions.append("closed_at IS NULL AND departure_at<=?")
            parameters.append(now)
        elif status in {"open", "full"}:
            conditions.append("closed_at IS NULL AND departure_at>?")
            conditions.append(active_count + ("<capacity" if status == "open" else ">=capacity"))
            parameters.append(now)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.transaction() as connection:
            rows = list(connection.execute("SELECT id FROM rooms" + where + " ORDER BY departure_at,id LIMIT ?",
                                           (*parameters, query["limit"] + 1)))
            snapshots = [self._snapshot(connection, row["id"], actor_hash) for row in rows[:query["limit"]]]
            return snapshots, len(rows) > query["limit"]

    def cleanup(self, cutoff, limit=200, apply=False):
        if isinstance(cutoff, datetime):
            if cutoff.tzinfo is None or cutoff.utcoffset() is None:
                raise ValueError("cutoff must include timezone")
            cutoff = cutoff.astimezone(timezone.utc).timestamp()
        if isinstance(cutoff, bool) or not isinstance(cutoff, (int, float)) or not isfinite(cutoff):
            raise ValueError("cutoff must be a finite UTC timestamp or aware datetime")
        if not isinstance(apply, bool):
            raise ValueError("apply must be boolean")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if not Path(self.db_path).is_file():
            return {"eligible_count": 0, "selected_count": 0, "deleted_count": 0}
        with self.transaction(write=apply, initialize=apply) as connection:
            count = connection.execute("SELECT COUNT(*) FROM rooms WHERE departure_at<?", (cutoff,)).fetchone()[0]
            rows = list(connection.execute("SELECT id FROM rooms WHERE departure_at<? ORDER BY departure_at,id LIMIT ?",
                                           (cutoff, limit)))
            if apply:
                connection.executemany("DELETE FROM rooms WHERE id=?", [(row["id"],) for row in rows])
                connection.execute("DELETE FROM rate_limit_events WHERE created_at<?", (cutoff,))
            return {"eligible_count": count, "selected_count": len(rows),
                    "deleted_count": len(rows) if apply else 0}
