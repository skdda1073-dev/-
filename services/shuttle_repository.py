"""원본 셔틀 시간표를 보존하며 행 순서와 회차 경계만 해석한다.

현재 원본은 한 물리적 행이 한 정류장 시각인 CSV다. 인용부호가 잘못된
행도 다음 정상 회차를 삼키지 않도록 각 행을 독립적으로 검증한다.
`시간`의 도착/출발 의미, 운행일, 승강장 좌표는 이 파일로 확정하지 않는다.
00:00~47:59를 서비스 날짜 자정 기준 분으로 보존한다. 24시 이상만 명시적
다음 날이며, 감소한 시각을 자정 통과로 보정하지 않는다.
"""

import csv
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
from threading import RLock


class ShuttleDataError(Exception):
    code = "SHUTTLE_DATA"


class ShuttleRepository:
    COLUMNS = ("노선명", "방면", "정류장", "시간")
    LOOP_ROUTES = frozenset(("제2노선", "제3노선", "제4노선"))
    TERMINAL = "대학교"

    def __init__(self, path):
        self.path = Path(path)
        self._snapshot = None
        self._lock = RLock()

    @staticmethod
    def _id(kind, *parts):
        value = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
        return f"shuttle-{kind}-{sha256(value.encode('utf-8')).hexdigest()}"

    @staticmethod
    def _minutes(value):
        if not re.fullmatch(r"[0-9]{2}:[0-5][0-9]", value):
            return None
        hour, minute = (int(part) for part in value.split(":"))
        return hour * 60 + minute if hour <= 47 else None

    @staticmethod
    def _parse_line(text):
        try:
            return next(csv.reader([text], strict=True))
        except (csv.Error, StopIteration):
            return None

    def _read(self):
        try:
            raw = self.path.read_bytes()
        except OSError:
            raise ShuttleDataError("셔틀 CSV 파일을 읽을 수 없습니다.") from None
        encodings = ("utf-8-sig", "cp949") if raw.startswith(b"\xef\xbb\xbf") else ("utf-8", "cp949")
        for encoding in encodings:
            try:
                content = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ShuttleDataError("셔틀 CSV 문자 인코딩을 해석하지 못했습니다.")
        lines = content.splitlines()
        if not lines:
            raise ShuttleDataError("셔틀 CSV가 비어 있습니다.")
        header = self._parse_line(lines[0])
        if header is None or len(header) != len(self.COLUMNS) or set(header) != set(self.COLUMNS):
            raise ShuttleDataError("셔틀 CSV 열 이름 또는 열 개수가 올바르지 않습니다.")
        if len(lines) == 1:
            raise ShuttleDataError("셔틀 CSV에 시간표 행이 없습니다.")
        return raw, encoding, header, lines[1:]

    def _load(self):
        raw, encoding, header, lines = self._read()
        trips, diagnostics = [], []
        route_names, stop_names = set(), set()
        active = None

        def diagnostic(line, code, message):
            diagnostics.append({"line": line, "code": code, "message": message})

        def new_trip(route, direction, line, started, invalid=False):
            return {
                "id": self._id("trip", route, direction, line),
                "route_id": self._id("route", route, direction),
                "route_name": route, "direction": direction,
                "sequence_basis": "csv_row_order", "routing_usable": False, "events": [],
                "_line": line, "_started": started, "_invalid": invalid,
            }

        def finish(trip, closed=False, single=False):
            if not single and (not closed or not trip["_started"] or len(trip["events"]) < 3):
                diagnostic(trip["_line"], "INCOMPLETE_TRIP", "대학교 시작·종료가 확인되는 완전한 회차가 아닙니다.")
                trip["_invalid"] = True
            trip["routing_usable"] = not single and not trip["_invalid"]
            for key in ("_line", "_started", "_invalid"):
                trip.pop(key)
            trips.append(trip)

        for line_number, text in enumerate(lines, 2):
            cells = self._parse_line(text)
            row = dict(zip(header, cells or []))
            values = {key: str(row.get(key, "")).strip() for key in self.COLUMNS}
            route, direction, name, time_text = (values[key] for key in self.COLUMNS)
            if route:
                route_names.add(route)
            if name:
                stop_names.add(name)
            minutes = self._minutes(time_text)
            invalid = cells is None or len(cells) != len(header) or not all(values.values()) or minutes is None

            # 노선·방면이 바뀌면 이전 회차를 이어 붙이지 않는다.
            if active is not None and route and direction and (route, direction) != (active["route_name"], active["direction"]):
                finish(active)
                active = None

            if invalid:
                if cells is None:
                    code, message = "INVALID_CSV_ROW", "CSV 인용부호 또는 행 구조가 올바르지 않습니다."
                elif len(cells) != len(header):
                    code, message = "INVALID_COLUMN_COUNT", "행의 셀 개수가 헤더와 다릅니다."
                elif not all(values.values()):
                    code, message = "MISSING_VALUE", "비어 있는 필수 셀이 있습니다."
                else:
                    code, message = "INVALID_TIME", "시각은 00:00~47:59의 HH:MM 형식이어야 합니다."
                diagnostic(line_number, code, message)
                if active is not None:
                    active["_invalid"] = True
                    # 손상된 종착 행도 경계로 사용하되 해당 회차는 사용하지 않는다.
                    same_context = (not route or route == active["route_name"]) and (not direction or direction == active["direction"])
                    if same_context and name == self.TERMINAL:
                        finish(active, closed=True)
                        active = None
                elif route in self.LOOP_ROUTES and direction:
                    active = new_trip(route, direction, line_number, name == self.TERMINAL, invalid=True)
                elif route == "제1노선" and direction:
                    finish(new_trip(route, direction, line_number, False, invalid=True), single=True)
                continue

            event = {
                "stop_id": self._id("stop", route, direction, name), "name": name,
                "time": time_text, "minutes": minutes, "line": line_number,
            }
            if route not in self.LOOP_ROUTES:
                trip = new_trip(route, direction, line_number, False)
                trip["events"].append(event)
                finish(trip, single=True)
                if route != "제1노선":
                    diagnostic(line_number, "UNSUPPORTED_ROUTE", "확인되지 않은 노선의 회차 경계는 추측하지 않습니다.")
                continue

            if active is None:
                active = new_trip(route, direction, line_number, name == self.TERMINAL)
                active["events"].append(event)
                continue
            previous = active["events"][-1] if active["events"] else None
            if previous is not None and minutes < previous["minutes"]:
                diagnostic(line_number, "DECREASING_TIME", "회차 안의 시각이 감소합니다. 자정 통과로 보정하지 않습니다.")
                active["_invalid"] = True
            active["events"].append(event)
            if name == self.TERMINAL:
                finish(active, closed=True)
                active = None

        if active is not None:
            finish(active)
        return {
            "source": {
                "filename": self.path.name, "encoding": encoding, "sha256": sha256(raw).hexdigest(),
                "row_count": len(lines), "route_count": len(route_names), "stop_count": len(stop_names),
                "time_semantics": "unspecified",
                "time_convention": "00:00-47:59; minutes from service-day midnight; no implicit rollover",
            },
            "conditions": {key: None for key in ("weekdays", "term", "excluded_dates", "eligibility", "fare")},
            "diagnostics": diagnostics, "trips": trips,
        }

    def snapshot(self):
        with self._lock:
            if self._snapshot is None:
                self._snapshot = self._load()
            return deepcopy(self._snapshot)
