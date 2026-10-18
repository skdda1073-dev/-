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
            ["tmap_app_key", "public_data_key", "naver_map_client_id"], False))
        self.assertFalse(settings.debug)
        self.assertEqual(settings.port, 5000)

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


if __name__ == "__main__":
    unittest.main()
