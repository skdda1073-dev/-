"""Export the project's published rail and metro schedules as a static GTFS feed."""

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import TextIOWrapper
from itertools import pairwise
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.national_transit_repository import KST, LOCAL_MODE, TRAIN_MODE, NationalTransitRepository


MAX_DAYS = 31
AGENCY_ID = "capstone-rail"
PUBLISHER = "춘천통학마스터 공개 철도 시간표"
PUBLISHER_URL = "https://github.com/skdda1073-dev/-"
ROUTE_TYPES = {LOCAL_MODE: 1, TRAIN_MODE: 2}


@dataclass
class _Run:
    route: tuple[str, str]
    service_name: str
    stops: tuple
    days: int


def _clock(seconds):
    hours, remaining = divmod(seconds, 3600)
    minutes, seconds = divmod(remaining, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def _known_segments(stops, stations):
    """Use the same known adjacent connections as the railway timetable."""
    run = []
    for first, second in pairwise(stops):
        departure = first.departure if first.departure is not None else first.arrival
        arrival = second.arrival if second.arrival is not None else second.departure
        usable = (first.station in stations and second.station in stations and first.station != second.station
                  and departure is not None and arrival is not None and 0 <= departure < arrival)
        if usable:
            if not run:
                run.append(first)
            run.append(second)
        elif run:
            yield tuple(run)
            run = []
    if run:
        yield tuple(run)


def _runs_on_dates(timetable, dates, route_types):
    masks = [0] * len(timetable.trips)
    for offset, day in enumerate(dates):
        bit = 1 << offset
        for index, active in enumerate(timetable._valid_trips(day)):
            if active:
                masks[index] |= bit

    runs = {}
    for trip, mask in zip(timetable.trips, masks):
        if not mask or trip.mode not in route_types:
            continue
        route = (trip.mode, trip.route_name.strip() or "철도")
        for stops in _known_segments(trip.stops, timetable.stations):
            key = (route, trip.service_name, stops)
            if key in runs:
                runs[key].days |= mask
            else:
                runs[key] = _Run(route, trip.service_name, stops, mask)
    return list(runs.values())


def _write_table(archive, name, columns, rows):
    with TextIOWrapper(archive.open(name + ".txt", "w"), encoding="utf-8", newline="") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(rows)


def _service_id(mask):
    return f"service-{mask:x}"


def _stop_times(runs):
    for trip_id, run in enumerate(runs, start=1):
        for sequence, stop in enumerate(run.stops, start=1):
            # Certain published tables give one clock time for a scheduled stop.
            # GTFS uses that same recorded time for the two fields in this case.
            arrival = stop.arrival if stop.arrival is not None else stop.departure
            departure = stop.departure if stop.departure is not None else stop.arrival
            yield (f"trip-{trip_id}", _clock(arrival), _clock(departure), stop.station, sequence,
                   int(sequence == len(run.stops)), int(sequence == 1))


def _declared_transfers(timetable, stops):
    for source in sorted(stops):
        from_stop = timetable.stations[source]
        for target, _ in sorted(timetable.transfers.get(source, ())):
            if target not in stops:
                continue
            to_stop = timetable.stations[target]
            if to_stop.line in from_stop.links or from_stop.line in to_stop.links:
                # The sources identify linked stations and do not publish an exact transfer duration.
                yield source, target, 0


def export_gtfs(timetable, output, start_date, days, *, include_intercity_rail=True):
    """Write an atomic GTFS ZIP and return its trip, route and stop counts."""
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_DAYS}")
    dates = tuple(start_date + timedelta(days=offset) for offset in range(days))
    route_types = ROUTE_TYPES if include_intercity_rail else {LOCAL_MODE: ROUTE_TYPES[LOCAL_MODE]}
    runs = _runs_on_dates(timetable, dates, route_types)
    if not runs:
        raise ValueError("No rail or metro service is available for the requested dates.")

    routes = {route: f"route-{i}" for i, route in enumerate(sorted({run.route for run in runs}), start=1)}
    stops = {stop.station for run in runs for stop in run.stops}
    station = timetable.stations.__getitem__
    calendar = sorted({run.days for run in runs})
    service_dates = [day.strftime("%Y%m%d") for day in dates]

    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False) as temp:
        temporary = Path(temp.name)
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            _write_table(archive, "agency", ("agency_id", "agency_name", "agency_url", "agency_timezone", "agency_lang"),
                         [(AGENCY_ID, PUBLISHER, PUBLISHER_URL, "Asia/Seoul", "ko")])
            _write_table(archive, "stops", ("stop_id", "stop_name", "stop_lat", "stop_lon"),
                         ((key, station(key).name, station(key).lat, station(key).lon) for key in sorted(stops)))
            _write_table(archive, "routes", ("route_id", "agency_id", "route_short_name", "route_type"),
                         ((identifier, AGENCY_ID, name, route_types[mode]) for (mode, name), identifier in routes.items()))
            _write_table(archive, "trips", ("route_id", "service_id", "trip_id", "trip_headsign", "trip_short_name"),
                         ((routes[run.route], _service_id(run.days), f"trip-{trip_id}",
                           station(run.stops[-1].station).name, run.service_name)
                          for trip_id, run in enumerate(runs, start=1)))
            _write_table(archive, "stop_times", ("trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence",
                         "pickup_type", "drop_off_type"), _stop_times(runs))
            _write_table(archive, "calendar_dates", ("service_id", "date", "exception_type"),
                         ((_service_id(mask), service_day, 1) for mask in calendar
                          for offset, service_day in enumerate(service_dates) if mask & (1 << offset)))
            _write_table(archive, "transfers", ("from_stop_id", "to_stop_id", "transfer_type"),
                         _declared_transfers(timetable, stops))
            _write_table(archive, "feed_info", ("feed_publisher_name", "feed_publisher_url", "feed_lang",
                         "feed_start_date", "feed_end_date"),
                         [(PUBLISHER, PUBLISHER_URL, "ko", service_dates[0], service_dates[-1])])
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return len(runs), len(routes), len(stops)


def main(argv=None):
    parser = argparse.ArgumentParser(description="검증된 철도·전철 정적 시간표를 MOTIS용 GTFS ZIP으로 내보냅니다.")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-date", type=date.fromisoformat, default=datetime.now(KST).date(), metavar="YYYY-MM-DD")
    parser.add_argument("--days", type=int, choices=range(1, MAX_DAYS + 1), default=7)
    parser.add_argument("--without-intercity-rail", action="store_true",
                        help="다른 일반·고속 철도 GTFS와 함께 쓸 때 전철만 내보냅니다.")
    args = parser.parse_args(argv)
    try:
        plan = NationalTransitRepository(args.data_dir)._timetable()
        trips, routes, stops = export_gtfs(plan, args.output, args.start_date, args.days,
                                         include_intercity_rail=not args.without_intercity_rail)
    except (OSError, OverflowError, ValueError) as error:
        parser.exit(1, f"GTFS 내보내기 실패: {error}\n")
    print(f"GTFS {args.output} · 열차 {trips:,}편 · 노선 {routes:,}개 · 역 {stops:,}개")


if __name__ == "__main__":
    main()
