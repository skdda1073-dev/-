"""Manually delete a bounded batch of rooms older than departure + 24 hours."""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.ride_room_repository import RideRoomRepository, RoomError
from services.settings import ConfigurationError, load_settings


def main(argv=None):
    parser = argparse.ArgumentParser(description="출발 예정 시각 이후 24시간이 지난 모집방 정리 (기본: 미리보기)")
    parser.add_argument("--apply", action="store_true", help="대상 방·참가자·메시지를 실제 삭제")
    parser.add_argument("--limit", type=int, default=200, help="이번 처리 상한 (1~1000, 기본 200)")
    parser.add_argument("--db", help="관리할 DB 경로. 생략하면 프로젝트 설정을 사용")
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.limit <= 1000:
        parser.error("--limit은 1~1000이어야 합니다.")
    try:
        db_path = arguments.db or load_settings(ROOT).ride_room_db_path
        path = Path(db_path)
        if not path.is_absolute():
            path = ROOT / path
        result = RideRoomRepository(path).cleanup(
            datetime.now(timezone.utc) - timedelta(hours=24), limit=arguments.limit, apply=arguments.apply,
        )
    except (ConfigurationError, RoomError, OSError):
        print("모집방 저장소 또는 설정을 확인하세요. 데이터는 출력하지 않습니다.", file=sys.stderr)
        return 1
    print("정리 적용" if arguments.apply else "미리보기 (삭제 없음)")
    print("전체 대상: {eligible_count}, 이번 배치: {selected_count}, 삭제: {deleted_count}".format(**result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
