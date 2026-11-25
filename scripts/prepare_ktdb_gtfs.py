"""Convert the March 2025 KTDB snapshot into a MOTIS reference feed."""

import argparse
import csv
from datetime import date
from io import TextIOWrapper
from pathlib import Path
from shutil import copyfileobj
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile


# A representative weekday is used as an internal search date. The snapshot
# does not identify a specific day or describe weekly service.
KTDB_REFERENCE_DATE = date(2025, 3, 19)

# KTDB route types describe different modes than the GTFS Schedule codes.
# MOTIS also supports extended GTFS types for coaches, high-speed rail and air.
ROUTE_TYPES = {
    ("BR", "0"): "3",  # City and village buses
    ("RR", "1"): "1",  # Urban rail
    ("SR", "2"): "4",  # Ferry
    ("BR", "3"): "200",  # Intercity buses
    ("RR", "4"): "2",  # Conventional rail
    ("BR", "5"): "3",  # Airport buses
    ("RR", "6"): "101",  # High-speed rail
    ("AR", "7"): "1100",  # Domestic flights
}
COPIED_TABLES = ("agency.txt", "stops.txt", "trips.txt", "stop_times.txt", "transfers.txt")
COPY_BUFFER_SIZE = 1024 * 1024


def _write_routes(archive, path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        routes = csv.DictReader(source)
        if not {"route_id", "route_type"}.issubset(routes.fieldnames or ()):
            raise ValueError("KTDB routes.txt에 노선 ID 또는 교통수단 열이 없습니다.")
        with TextIOWrapper(archive.open("routes.txt", "w"), encoding="utf-8", newline="") as target:
            writer = csv.DictWriter(target, routes.fieldnames, lineterminator="\n")
            writer.writeheader()
            for route in routes:
                prefix = route["route_id"].split("_", 1)[0]
                mode = ROUTE_TYPES.get((prefix, route["route_type"]))
                if mode is None:
                    raise ValueError(f"KTDB 교통수단 코드를 확인할 수 없습니다. {route['route_id']}")
                route["route_type"] = mode
                writer.writerow(route)


def _write_calendar(archive, path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        calendar = csv.DictReader(source)
        if "service_id" not in (calendar.fieldnames or ()):
            raise ValueError("KTDB calendar.txt에 운행 ID 열이 없습니다.")
        services = sorted({row["service_id"] for row in calendar if row["service_id"]})
    if not services:
        raise ValueError("KTDB calendar.txt에 운행 ID가 없습니다.")
    with TextIOWrapper(archive.open("calendar_dates.txt", "w"), encoding="utf-8", newline="") as target:
        writer = csv.writer(target, lineterminator="\n")
        writer.writerow(("service_id", "date", "exception_type"))
        service_date = KTDB_REFERENCE_DATE.strftime("%Y%m%d")
        writer.writerows((service_id, service_date, 1) for service_id in services)


def prepare_ktdb_gtfs(source, output):
    """Write a local reference ZIP atomically, streaming the stop-time table."""
    source = Path(source)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False) as file:
        temporary = Path(file.name)
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=1) as archive:
            _write_routes(archive, source / "routes.txt")
            _write_calendar(archive, source / "calendar.txt")
            for name in COPIED_TABLES:
                with (source / name).open("rb") as input_table, archive.open(name, "w", force_zip64=True) as output_table:
                    if input_table.read(3) != b"\xef\xbb\xbf":
                        input_table.seek(0)
                    copyfileobj(input_table, output_table, length=COPY_BUFFER_SIZE)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="2025년 3월 평일 KTDB 원본을 참고 경로용 GTFS ZIP으로 변환합니다.")
    parser.add_argument("--source", type=Path, required=True, help="KTDB 원본 GTFS 텍스트 파일이 들어 있는 디렉터리")
    parser.add_argument("--output", type=Path, required=True, help="생성할 GTFS ZIP 파일")
    args = parser.parse_args(argv)
    try:
        prepare_ktdb_gtfs(args.source, args.output)
    except (OSError, ValueError, csv.Error) as error:
        parser.exit(1, f"KTDB GTFS 변환 실패. {error}\n")
    print(f"KTDB GTFS 준비 완료 {args.output} · 검색 기준일 {KTDB_REFERENCE_DATE.isoformat()}")


if __name__ == "__main__":
    main()
