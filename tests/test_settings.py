"""가짜 키·임시 파일만 사용해 설정의 우선순위와 비밀값 비노출을 검증한다."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services.settings import ConfigurationError, load_settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write_env(self, text):
        (self.root / ".env").write_text(text, encoding="utf-8-sig")

    def test_no_file_and_missing_credentials_are_safe_defaults(self):
        settings = load_settings(self.root, environ={})
        self.assertEqual(settings.configured(), dict.fromkeys(
            ["tmap_app_key", "public_data_key", "tago_api_key", "naver_map_client_id"], False))
        self.assertFalse(settings.debug)
        self.assertEqual(settings.port, 5000)
        self.assertEqual(settings.motis_base_url, "")

    def test_motis_requires_a_loopback_http_or_https_origin(self):
        for value in ("http://127.0.0.1:8889/", "http://[::1]:8889", "https://transit.example"):
            with self.subTest(value=value):
                self.assertEqual(load_settings(self.root, environ={"MOTIS_BASE_URL": value}).motis_base_url,
                                 value.rstrip("/"))
        for value in ("http://transit.example", "http://localhost:0", "https://id@example.com", "https://example.com?key=example", "https://example.com/path"):
            with self.subTest(value=value), self.assertRaisesRegex(ConfigurationError, "MOTIS_BASE_URL"):
                load_settings(self.root, environ={"MOTIS_BASE_URL": value})

    def test_project_dotenv_loaded_with_literal_secret_characters(self):
        self.write_env("TMAP_APP_KEY='fake-${NOT_EXPANDED}-$x-%%-==#literal'\nPUBLIC_DATA_KEY='fake-percent%2Bkey='\nNAVER_MAP_CLIENT_ID='fake-map'\n")
        settings = load_settings(self.root, environ={})
        self.assertEqual(settings.tmap_app_key, "fake-${NOT_EXPANDED}-$x-%%-==#literal")
        self.assertEqual(settings.public_data_key, "fake-percent%2Bkey=")
        self.assertTrue(all(settings.configured().values()))
        self.assertNotIn("fake-", repr(settings))

    def test_environment_including_explicit_empty_value_has_priority(self):
        self.write_env("TMAP_APP_KEY='file-fake'\nPUBLIC_DATA_KEY='file-public-fake'\n")
        settings = load_settings(self.root, environ={"TMAP_APP_KEY": "env-fake", "PUBLIC_DATA_KEY": ""})
        self.assertEqual(settings.tmap_app_key, "env-fake")
        self.assertFalse(settings.configured()["public_data_key"])

    def test_bus_key_is_separate_from_existing_public_and_intercity_keys(self):
        self.write_env("PUBLIC_DATA_KEY='fake-friend-public'\nTAGO_API_KEY='fake-own-bus'\n")
        before = self.root.joinpath('.env').read_bytes()
        settings = load_settings(self.root, environ={})
        self.assertEqual(settings.bus_data_key, "fake-own-bus")
        self.assertEqual(settings.public_data_key, "fake-friend-public")
        self.assertEqual(settings.intercity_data_key, "fake-friend-public")
        self.assertTrue(settings.configured()["tago_api_key"])
        self.assertNotIn("fake-", repr(settings))
        self.assertEqual(self.root.joinpath('.env').read_bytes(), before)

    def test_bus_key_preserves_legacy_fallback_and_environment_precedence(self):
        self.write_env("PUBLIC_DATA_KEY='fake-public'\nTAGO_API_KEY='fake-file-bus'\nINTERCITY_DATA_KEY='fake-intercity'\n")
        settings = load_settings(self.root, environ={"TAGO_API_KEY": "fake-env-bus"})
        self.assertEqual(settings.bus_data_key, "fake-env-bus")
        self.assertEqual(settings.intercity_data_key, "fake-intercity")
        settings = load_settings(self.root, environ={"TAGO_API_KEY": ""})
        self.assertEqual(settings.bus_data_key, "fake-public")
        self.assertEqual(settings.tago_api_key, "")

    def test_loading_does_not_mutate_process_environment(self):
        self.write_env("TMAP_APP_KEY='file-fake'\n")
        with patch.dict(os.environ, {}, clear=True):
            load_settings(self.root)
            self.assertNotIn("TMAP_APP_KEY", os.environ)

    def test_env_is_never_rewritten_and_path_does_not_use_cwd(self):
        self.write_env("TMAP_APP_KEY='file-fake'\n")
        before = (self.root / ".env").read_bytes()
        self.assertEqual(load_settings(self.root, environ={}).tmap_app_key, "file-fake")
        self.assertEqual((self.root / ".env").read_bytes(), before)

    def test_invalid_non_secret_settings_have_sanitized_errors(self):
        for name, bad in (("PORT", "fake-secret"), ("PORT", "0"), ("PORT", "65536"), ("APP_DEBUG", "fake-secret")):
            with self.subTest(name=name, bad=bad):
                with self.assertRaises(ConfigurationError) as raised:
                    load_settings(self.root, environ={name: bad})
                self.assertNotIn(bad, str(raised.exception))

    def test_debug_and_port_are_explicit(self):
        settings = load_settings(self.root, environ={"APP_DEBUG": "true", "PORT": "5050"})
        self.assertTrue(settings.debug)
        self.assertEqual(settings.port, 5050)

    def test_chat_defaults_preserve_existing_readiness_contract(self):
        settings = load_settings(self.root, environ={})
        self.assertEqual(settings.flask_secret_key, "")
        self.assertTrue(settings.session_cookie_secure)
        self.assertEqual(settings.ride_room_db_path, str(self.root / "instance" / "ride_rooms.sqlite3"))
        self.assertEqual(settings.taxi_cache_ttl_seconds, 60)
        self.assertEqual(settings.taxi_cache_max_entries, 512)
        self.assertEqual(settings.ride_room_message_rate_per_minute, 15)
        self.assertEqual(settings.ride_room_create_rate_per_hour, 3)
        self.assertEqual(len(settings.configured()), 4)
        self.assertFalse((self.root / "instance").exists())

    def test_chat_secrets_are_literal_private_and_environment_wins(self):
        self.write_env("FLASK_SECRET_KEY='fake-${UNEXPANDED}-%%-long-file-secret'\n")
        secret = "fake-${UNEXPANDED}-%%-long-env-secret"
        settings = load_settings(self.root, environ={"FLASK_SECRET_KEY": secret, "SESSION_COOKIE_SECURE": "false"})
        self.assertEqual(settings.flask_secret_key, secret)
        self.assertNotIn(secret, repr(settings))
        self.assertFalse(settings.session_cookie_secure)

    def test_mobility_setting_bounds_have_sanitized_errors(self):
        for name, bad in (("SESSION_COOKIE_SECURE", "private-value"),
                          ("TAXI_CACHE_TTL_SECONDS", "0"), ("TAXI_CACHE_MAX_ENTRIES", "4097"),
                          ("RIDE_ROOM_MESSAGE_RATE_PER_MINUTE", "-1"),
                          ("RIDE_ROOM_CREATE_RATE_PER_HOUR", "private-value")):
            with self.subTest(name=name):
                with self.assertRaises(ConfigurationError) as raised:
                    load_settings(self.root, environ={name: bad})
                self.assertNotIn(bad, str(raised.exception))

    def test_environment_advice_keys_use_public_fallback_without_rewriting_env(self):
        self.write_env("PUBLIC_DATA_KEY='fake-common-key'\n")
        before = (self.root / ".env").read_bytes()
        settings = load_settings(self.root, environ={})
        self.assertEqual(settings.weather_data_key, "fake-common-key")
        self.assertEqual(settings.airkorea_data_key, "fake-common-key")
        self.assertEqual(settings.airkorea_station_name, "중앙로")
        self.assertEqual((self.root / ".env").read_bytes(), before)
        self.assertNotIn("fake-common-key", repr(settings))
        self.assertEqual(settings.configured(), {
            "tmap_app_key": False, "public_data_key": True, "tago_api_key": True, "naver_map_client_id": False,
        })

    def test_environment_advice_keys_can_be_overridden_independently(self):
        self.write_env("PUBLIC_DATA_KEY='fake-common'\nWEATHER_DATA_KEY='fake-file-weather'\n")
        settings = load_settings(self.root, environ={
            "WEATHER_DATA_KEY": "fake-env-weather", "AIRKOREA_DATA_KEY": "fake-air",
            "AIRKOREA_STATION_NAME": "퇴계동",
        })
        self.assertEqual(settings.weather_data_key, "fake-env-weather")
        self.assertEqual(settings.airkorea_data_key, "fake-air")
        self.assertEqual(settings.airkorea_station_name, "퇴계동")
        self.assertNotIn("fake-env-weather", repr(settings))
        self.assertNotIn("fake-air", repr(settings))


if __name__ == "__main__":
    unittest.main()
