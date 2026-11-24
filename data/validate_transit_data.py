"""Validate the prepared transit snapshots without starting the application."""

from collections import Counter
from contextlib import contextmanager
import csv
import gzip
from hashlib import sha256
import json
from pathlib import Path
import sys


DATA = Path(__file__).resolve().parent
ROOT = DATA.parent


@contextmanager
def open_csv(path):
    opener = gzip.open if path.suffix == ".gz" else Path.open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as file:
        yield file


def check(condition, message):
    if not condition:
        raise ValueError(message)


def count_rows(path):
    with open_csv(path) as file:
        rows = csv.reader(file, strict=True)
        header = next(rows)
        check(header and len(set(header)) == len(header), f"{path.name} has an invalid header")
        count = 0
        for count, row in enumerate(rows, 1):
            check(len(row) == len(header), f"{path.name} row {count + 1} width differs from the header")
        return count


def read_records(path):
    with open_csv(path) as file:
        return list(csv.DictReader(file))


def checksum(path):
    digest = sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate():
    manifest = read_records(DATA / "sources.csv")
    hashes, expected_counts = {}, {}
    for record in manifest:
        check(record["dataset"] and record["status"], "source record is missing its dataset or status")
        hashes.update(json.loads(record["raw_sha256"] or "{}"))
        expected_counts.update(json.loads(record["normalized_file_rows"] or "{}"))
    for name, digest in hashes.items():
        check(checksum(ROOT / name) == digest, f"{name} differs from its recorded SHA-256")
    present = {
        str(path.relative_to(ROOT))
        for path in (DATA / "normalized").rglob("*")
        if path.is_file() and (path.name.endswith(".csv") or path.name.endswith(".csv.gz"))
    }
    check(present == set(expected_counts), "normalized CSV files and sources.csv are out of sync")
    for name, expected in expected_counts.items():
        check(count_rows(ROOT / name) == expected, f"{name} differs from its recorded row count")

    raw_files = {str(path.relative_to(ROOT)) for path in (DATA / "raw").rglob("*") if path.is_file() and not path.name.startswith(".")}
    check(raw_files <= set(hashes), f"raw files missing from sources.csv: {sorted(raw_files - set(hashes))}")

    required = {
        "subway/kric_routes_20260630.csv": 47,
        "subway/kric_stations_20260630.csv": 1099,
        "subway/kric_stop_times_20260630.csv.gz": 223425,
        "subway/korail_gyeongchun_weekday_20260901.csv": 2390,
        "subway/korail_gyeongchun_holiday_20260901.csv": 1843,
        "bus/tago_stops_20261109.csv": 227102,
        "bus/ts_bis_service_summary_20260716.csv": 10563,
        "campus/buildings.csv": 137,
    }
    for name, expected in required.items():
        check(expected_counts.get("data/normalized/" + name) == expected, f"{name} snapshot is incomplete")

    base = DATA / "normalized"
    campus_rail = read_records(base / "rail/chuncheon_rail_facility_crosswalk.csv")
    check({(row["facility_name"], row["tago_train_station_id"], row["kric_metro_station_id"])
           for row in campus_rail} == {("춘천역", "NAT140873", "1329"), ("남춘천역", "NAT140840", "1328")},
          "Chuncheon station identifiers do not match the verified crosswalk")
    campus_bus = read_records(base / "campus/city_boarding_stop_crosswalk.csv")
    check({(row["ars_code"], row["tago_stop_id"]) for row in campus_bus}
          == {("2056", "CCB250002079"), ("2310", "CCB250026574")}, "campus bus stop IDs differ")
    shuttle = read_records(base / "campus/hallym_shuttle_stop_times.csv")
    check(Counter(row["time_kind"] for row in shuttle)
          == {"departure": 16, "published_stop_time": 180, "not_published": 20}, "shuttle event counts differ")
    print(f"Validated {len(manifest)} sources, {len(hashes)} source files, and {len(expected_counts)} CSVs.")


if __name__ == "__main__":
    try:
        validate()
    except (OSError, ValueError, KeyError, csv.Error, StopIteration) as error:
        sys.exit(str(error))
