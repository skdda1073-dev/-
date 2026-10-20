"""Run all unittest checks without loading personal settings or contacting APIs."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import requests
    import services.settings as settings_module

    original_values = settings_module.dotenv_values

    def safe_values(path, **kwargs):
        # Compare the supplied path before resolving a possibly private symlink target.
        if Path(os.path.abspath(path)) == ROOT / ".env":
            return {}
        return original_values(path, **kwargs)

    with tempfile.TemporaryDirectory(prefix="capstone-unit-") as temporary:
        fake_environment = {
            "TMAP_APP_KEY": "", "PUBLIC_DATA_KEY": "", "NAVER_MAP_CLIENT_ID": "",
            "FLASK_SECRET_KEY": "", "APP_DEBUG": "false", "PORT": "5000",
            "SESSION_COOKIE_SECURE": "false", "RIDE_ROOM_DB_PATH": str(Path(temporary) / "rooms.sqlite3"),
            "TAXI_CACHE_TTL_SECONDS": "60", "TAXI_CACHE_MAX_ENTRIES": "512",
            "RIDE_ROOM_MESSAGE_RATE_PER_MINUTE": "15", "RIDE_ROOM_CREATE_RATE_PER_HOUR": "3",
        }
        with (patch.dict(os.environ, fake_environment),
              patch.object(settings_module, "dotenv_values", side_effect=safe_values),
              patch.object(requests.sessions.Session, "request", side_effect=requests.ConnectionError("External HTTP disabled in tests"))):
            suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
            result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
