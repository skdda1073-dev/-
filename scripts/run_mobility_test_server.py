"""Disposable loopback UI verification server: synthetic quotes, no upstream HTTP."""

import argparse
from contextlib import nullcontext
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description="실제 키·실제 방을 사용하지 않는 브라우저 검증 서버")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reuse-temp-dir", type=Path, help="재시작 검증용 capstone-browser- 임시 디렉터리")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("포트는 1024~65535 범위여야 합니다.")
    if args.reuse_temp_dir is None:
        directory = tempfile.TemporaryDirectory(prefix="capstone-browser-")
    else:
        temporary = args.reuse_temp_dir.resolve()
        if (temporary.parent != Path(tempfile.gettempdir()).resolve()
                or not temporary.name.startswith("capstone-browser-")
                or not temporary.is_dir() or (temporary / "rooms.sqlite3").is_symlink()):
            parser.error("운영 DB와 분리된 capstone-browser- 임시 디렉터리를 지정하세요.")
        directory = nullcontext(temporary)
    with directory as temporary:
        os.environ.update({
            "TMAP_APP_KEY": "fake-verification-key", "PUBLIC_DATA_KEY": "", "TAGO_API_KEY": "", "TRAIN_API_KEY": "", "NAVER_MAP_CLIENT_ID": "fake-map-id",
            "FLASK_SECRET_KEY": "fixed-non-secret-verification-key-0123456789-abcdef",
            "SESSION_COOKIE_SECURE": "false", "APP_DEBUG": "false", "PORT": str(args.port),
            "RIDE_ROOM_DB_PATH": str(Path(temporary) / "rooms.sqlite3"),
            "TAXI_CACHE_TTL_SECONDS": "60", "TAXI_CACHE_MAX_ENTRIES": "512",
            "RIDE_ROOM_MESSAGE_RATE_PER_MINUTE": "120", "RIDE_ROOM_CREATE_RATE_PER_HOUR": "30",
        })
        import requests
        import services.settings

        # Tests for temporary .env files live in unittest, not this UI server.
        services.settings.dotenv_values = lambda *args, **kwargs: {}

        def blocked(*args, **kwargs):
            raise requests.ConnectionError("Verification blocks external HTTP")

        requests.sessions.Session.request = blocked
        import app

        class FakeTaxiResponse:
            status_code = 200

            def json(self):
                return {"features": [{"properties": {"totalTime": 600, "totalDistance": 2500,
                                                        "taxiFare": 10001, "totalFare": 0}}]}

        app.taxi_service.requester = lambda *args, **kwargs: FakeTaxiResponse()

        @app.app.after_request
        def test_response(response):
            response.headers["X-Capstone-Test-Server"] = "synthetic-data"
            return response

        print("임시 검증 서버: 가짜 택시 견적·임시 방 DB·외부 API 차단", flush=True)
        app.app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False, load_dotenv=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
