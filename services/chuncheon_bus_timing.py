"""Load dated Chuncheon bus riding-time estimates for specific stop sequences."""

import csv
from dataclasses import dataclass
from datetime import date, time
from functools import cached_property
from hashlib import sha256
import json
from pathlib import Path
import re


_TAGO_ID = re.compile(r"CCB[0-9]{9}")
_DEPARTURES_FILE = re.compile(r"chuncheon_published_departures_[0-9]{8}\.csv")


@dataclass(frozen=True)
class PublishedBusTiming:
    approach_stop_ids: tuple[str, ...]
    departure_times: tuple[time, ...]
    board_offset: int
    alight_offset: int
    source: str


class ChuncheonBusTiming:
    def __init__(self, path):
        self._path = Path(path)

    @cached_property
    def _entries(self):
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return ()
        rows = data.get("estimates") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return ()

        entries = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            route_id, direction = row.get("route_id"), row.get("direction")
            stop_ids, minutes, source = row.get("stop_ids"), row.get("minutes"), row.get("source_label")
            if (not isinstance(route_id, str) or not _TAGO_ID.fullmatch(route_id) or direction not in ("0", "1")
                    or not isinstance(stop_ids, list) or len(stop_ids) < 2
                    or not all(isinstance(stop, str) and _TAGO_ID.fullmatch(stop) for stop in stop_ids)
                    or len(set(stop_ids)) != len(stop_ids)
                    or type(minutes) not in (int, float) or not 0 < minutes <= 1440
                    or not isinstance(source, str) or not source.strip()):
                continue
            try:
                first = date.fromisoformat(row.get("valid_from"))
                last = date.fromisoformat(row.get("valid_through"))
            except (TypeError, ValueError):
                continue
            if first <= last:
                source = source.strip()
                estimate = (float(minutes), source)
                schedule = self._published_timing(row.get("route_number"), row.get("published_departures"), estimate)
                entries.append(((route_id, direction, tuple(stop_ids)), estimate, first, last, schedule))
        return tuple(entries)

    def _published_timing(self, route_number, details, estimate):
        if not isinstance(route_number, str) or not isinstance(details, dict):
            return None
        filename, count = details.get("file"), details.get("matched_departure_count")
        inline_clocks = details.get("source_departure_times")
        approach = details.get("approach_stop_ids")
        board, alight = details.get("boarding_offset_minutes"), details.get("alighting_offset_minutes")
        if ((filename is None) == (inline_clocks is None)
                or (filename is not None and (not isinstance(filename, str) or not _DEPARTURES_FILE.fullmatch(filename)))
                or type(count) is not int or count <= 0
                or not isinstance(approach, list)
                or not all(isinstance(stop, str) and _TAGO_ID.fullmatch(stop) for stop in approach)
                or type(board) is not int or type(alight) is not int or not 0 <= board < alight <= 1440
                or alight - board != estimate[0]):
            return None
        try:
            if filename is not None:
                with self._path.with_name(filename).open(encoding="utf-8-sig", newline="") as stream:
                    rows = sorted((int(row["sequence_in_table"]), row["local_departure_time"])
                                  for row in csv.DictReader(stream)
                                  if row.get("route_label") == route_number
                                  and row.get("day_type") == "source_unspecified"
                                  and row.get("departure_role") == "published_times_departure_point_unlabelled"
                                  and row.get("departure_stop_label") == "" and row.get("variant_label") == ""
                                  and row.get("effective_from") == details.get("effective_from"))
                if any(order != i for i, (order, _) in enumerate(rows, 1)):
                    return None
                clocks = [clock for _, clock in rows]
            else:
                clocks = inline_clocks
            if not isinstance(clocks, list) or len(clocks) != count:
                return None
            # Keep the dated origin sequence tied to the independently checked stop times.
            if sha256("\n".join(clocks).encode("ascii")).hexdigest() != details.get("clock_sequence_sha256"):
                return None
            departures = tuple(time.fromisoformat(clock) for clock in clocks)
            if any(first >= second for first, second in zip(departures, departures[1:])):
                return None
        except (OSError, UnicodeError, KeyError, TypeError, ValueError, csv.Error):
            return None
        return PublishedBusTiming(tuple(approach), departures, board, alight, estimate[1])

    def _for_date(self, service_date, *, published):
        matches = {}
        conflicts = set()
        for key, estimate, first, last, schedule in self._entries:
            if not first <= service_date <= last:
                continue
            value = (estimate, schedule) if published else estimate
            if key in matches and matches[key] != value:
                conflicts.add(key)
            matches[key] = value
        return {key: value for key, value in matches.items() if key not in conflicts}

    def for_date(self, service_date):
        """Return estimates keyed by route ID, direction and all stops in travel order."""
        return self._for_date(service_date, published=False)

    def published_for_date(self, service_date):
        return {key: timing for key, (_, timing) in self._for_date(service_date, published=True).items()
                if timing is not None}
