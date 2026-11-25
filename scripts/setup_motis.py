#!/usr/bin/env python3
"""Prepare and run a local MOTIS server with Korean transit data."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
from datetime import date
from pathlib import Path
from zipfile import BadZipFile, ZipFile


PROJECT_DIR = Path(__file__).resolve().parents[1]
MOTIS_VERSION = "v2.11.4"
MOTIS_RELEASE = f"https://github.com/motis-project/motis/releases/download/{MOTIS_VERSION}"
MOTIS_ARCHIVES = {
    ("Darwin", "arm64"): (
        "motis-macos-arm64.tar.bz2",
        "02753c7e6487beaede33b7ea1b37e6349cb5387de8be8c35be7137d55c7da9d7",
    ),
    ("Linux", "aarch64"): (
        "motis-linux-arm64.tar.bz2",
        "76f41df5bb4db14ccd163dad0a23dd85bebbb9f21a9736834e9b3d4201080f3b",
    ),
    ("Linux", "x86_64"): (
        "motis-linux-amd64.tar.bz2",
        "b521bb29c961a3b994ff4dac663778ba64fee7b4cfaf3f2b20ff1d5e1d15e0dc",
    ),
}
OSM_FILENAME = "south-korea-261101.osm.pbf"
OSM_URL = f"https://download.geofabrik.de/asia/south-korea/{OSM_FILENAME}"
# SHA-256 for the extract verified against Geofabrik's published MD5.
OSM_SHA256 = "055f00dea8bdc13b1805d305b4bd2e2b0c419c4be0a93f0e7b751bd965db5009"
KORAIL_GTFS_URL = "https://mkuran.pl/gtfs/korail.zip"
KORAIL_RT_URL = "https://mkuran.pl/gtfs/korail.pb"


def validate_gtfs(path):
    required_files = {"agency.txt", "routes.txt", "stops.txt", "trips.txt", "stop_times.txt"}
    with ZipFile(path) as feed:
        contents = set(feed.namelist())
    missing = required_files - contents
    if missing or not ({"calendar.txt", "calendar_dates.txt"} & contents):
        raise ValueError(f"GTFS ZIP 최상위에 시간표 파일이 누락되었습니다. {path}")


def download(url, destination, validate):
    if destination.is_file():
        validate(destination)
        return

    curl = shutil.which("curl")
    if curl is None:
        raise RuntimeError("교통 데이터와 MOTIS 바이너리를 받으려면 curl이 필요합니다.")
    with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
        staged_file = Path(directory) / destination.name
        print(f"다운로드 중 {destination.name}", flush=True)
        subprocess.run(
            [curl, "--disable", "--fail", "--location", "--silent", "--show-error", "--retry", "2",
             "--connect-timeout", "15", "--max-time", "900", "--output", str(staged_file), url],
            check=True,
        )
        validate(staged_file)
        staged_file.replace(destination)


def verify_checksum(path, expected):
    with path.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != expected:
        raise ValueError(f"체크섬이 일치하지 않습니다. {path}")


def prepare_binary(runtime_dir, archive_name, checksum):
    install_dir = runtime_dir / f"{MOTIS_VERSION}-{archive_name.removesuffix('.tar.bz2')}"
    executable = install_dir / "motis"
    if executable.is_file():
        return executable

    archive_path = runtime_dir / archive_name
    download(
        f"{MOTIS_RELEASE}/{archive_name}",
        archive_path,
        lambda path: verify_checksum(path, checksum),
    )
    with tempfile.TemporaryDirectory(dir=runtime_dir) as directory:
        staged_install = Path(directory) / "motis-release"
        with tarfile.open(archive_path) as archive:
            archive.extractall(staged_install, filter="data")
        staged_binary = staged_install / "motis"
        staged_binary.chmod(staged_binary.stat().st_mode | 0o100)
        staged_install.rename(install_dir)
    return executable


def prepare_config(runtime_dir, osm_path, gtfs_paths, korail_path, port, *, start_date=None, days=None):
    datasets = {}
    for number, gtfs_path in enumerate(gtfs_paths, start=1):
        datasets[f"hallym{number}"] = {"path": str(gtfs_path)}
    if korail_path:
        datasets["korail"] = {
            "path": str(korail_path),
            "rt": [{"url": KORAIL_RT_URL, "protocol": "gtfsrt"}],
        }
    server = {"host": "127.0.0.1", "port": port}
    if korail_path:
        server["data_attribution_link"] = "https://mkuran.pl/gtfs/"
    timetable = {"first_day": start_date.isoformat() if start_date else "TODAY",
                 "link_stop_distance": 250, "datasets": datasets}
    if days is not None:
        timetable["num_days"] = days
    config = {
        "server": server,
        "osm": str(osm_path),
        "street_routing": True,
        "osr_footpath": True,
        "logging": {"log_level": "info"},
        "timetable": timetable,
    }
    # MOTIS accepts JSON as a subset of its YAML configuration format.
    config_text = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    with tempfile.TemporaryDirectory(dir=runtime_dir) as directory:
        staged = Path(directory) / "config.yml"
        staged.write_text(config_text, encoding="utf-8")
        staged.replace(runtime_dir / "config.yml")


def main():
    parser = argparse.ArgumentParser(description="전국 대중교통 경로에 사용할 로컬 MOTIS를 준비합니다.")
    parser.add_argument("--gtfs", action="append", type=Path, default=[], help="가져올 GTFS ZIP. 여러 번 지정 가능.")
    parser.add_argument("--korail", action="store_true", help="M. Kuranowski의 코레일 GTFS와 GTFS-RT를 추가합니다.")
    parser.add_argument("--osm", type=Path, help="직접 준비한 OSM PBF. 기본값은 검증된 대한민국 지도입니다.")
    parser.add_argument("--binary", type=Path, help="이미 준비된 MOTIS 실행 파일을 사용합니다.")
    parser.add_argument("--runtime-dir", type=Path, default=PROJECT_DIR / ".motis", help="실행 파일과 색인 저장 경로.")
    parser.add_argument("--port", type=int, default=8889, help="루프백 HTTP 포트. 기본값은 8889.")
    parser.add_argument("--start-date", type=date.fromisoformat, help="불러올 시간표의 시작일. 기본값은 오늘입니다.")
    parser.add_argument("--days", type=int, help="불러올 시간표의 일수. 기본값은 MOTIS 설정을 따릅니다.")
    parser.add_argument("--serve", action="store_true", help="데이터를 가져온 후 서버를 실행합니다.")
    args = parser.parse_args()
    if not args.gtfs and not args.korail:
        parser.error("--gtfs 또는 --korail을 지정하세요.")
    if not 1 <= args.port <= 65535:
        parser.error("--port는 1부터 65535까지 사용할 수 있습니다.")
    if args.days is not None and args.days < 1:
        parser.error("--days는 1 이상이어야 합니다.")
    architecture = (platform.system(), platform.machine())
    if architecture not in MOTIS_ARCHIVES:
        parser.error(f"이 운영체제와 아키텍처 조합의 네이티브 MOTIS를 제공하지 않습니다. {architecture}")

    import fcntl

    try:
        gtfs_paths = [path.resolve(strict=True) for path in args.gtfs]
        for path in gtfs_paths:
            validate_gtfs(path)
        runtime_dir = args.runtime_dir.resolve()
        osm_path = args.osm.resolve(strict=True) if args.osm else runtime_dir / OSM_FILENAME
        if args.osm and not osm_path.is_file():
            raise ValueError(f"OSM 파일을 찾을 수 없습니다. {osm_path}")

        runtime_dir.mkdir(parents=True, exist_ok=True)
        with (runtime_dir / "runtime.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError(f"이미 MOTIS가 사용 중인 실행 경로입니다. {runtime_dir}") from error

            executable = (args.binary.resolve(strict=True) if args.binary
                          else prepare_binary(runtime_dir, *MOTIS_ARCHIVES[architecture]))
            if not args.osm:
                download(OSM_URL, osm_path, lambda path: verify_checksum(path, OSM_SHA256))
            korail_path = runtime_dir / "korail.zip" if args.korail else None
            if korail_path:
                download(KORAIL_GTFS_URL, korail_path, validate_gtfs)
            prepare_config(runtime_dir, osm_path, gtfs_paths, korail_path, args.port,
                           start_date=args.start_date, days=args.days)
            subprocess.run([str(executable), "import"], cwd=runtime_dir, check=True)

            if args.serve:
                print(f"MOTIS 경로 서버 실행 http://127.0.0.1:{args.port}", flush=True)
                os.chdir(runtime_dir)
                # Keep concurrent imports from changing the live server's index.
                os.set_inheritable(lock.fileno(), True)
                os.execv(executable, [str(executable), "server"])
        print(f"MOTIS 색인 준비 완료 {runtime_dir}")
    except (OSError, ValueError, RuntimeError, BadZipFile, subprocess.SubprocessError, tarfile.TarError) as error:
        parser.exit(1, f"MOTIS 준비 실패. {error}\n")


if __name__ == "__main__":
    main()
