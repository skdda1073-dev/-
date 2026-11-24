"""가짜 환경·HTTP로 ITX 조회와 수집기의 열차 인증키 분리를 검증한다."""

from contextlib import redirect_stderr, redirect_stdout
import importlib
from io import StringIO
import os
import sys
import unittest
from unittest.mock import MagicMock, Mock, patch

import requests


# 두 모듈의 import 시 .env 로딩을 막아 개인 인증키를 읽거나 환경에 넣지 않는다.
with patch("dotenv.load_dotenv", return_value=False):
    itx_service = importlib.import_module("services.itx_pair_service")
    collector = importlib.import_module("scripts.collect_itx")


KEY_CASES = (
    ({"TRAIN_API_KEY": "  fake-train-key  ", "TAGO_API_KEY": "fake-bus-key"}, "fake-train-key"),
    ({"TAGO_API_KEY": "  fake-legacy-key  "}, "fake-legacy-key"),
    ({"TRAIN_API_KEY": " \t ", "TAGO_API_KEY": "  fake-legacy-key  "}, "fake-legacy-key"),
)


class ItxKeySettingsTests(unittest.TestCase):
    def test_service_uses_train_key_and_preserves_stripped_legacy_fallback(self):
        body = {"items": {}, "totalCount": 0}
        for environment, expected in KEY_CASES:
            response = Mock(status_code=200)
            response.json.return_value = {"response": {"header": {"resultCode": "00"}, "body": body}}
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True), \
                    patch.object(itx_service.requests, "get", return_value=response) as requester:
                self.assertEqual(itx_service._request_body("GetCtyCodeList", {}), body)
                self.assertEqual(requester.call_args.kwargs["params"]["serviceKey"], expected)
                self.assertEqual(requester.call_args.args[0], f"{itx_service.BASE_URL}/GetCtyCodeList")

    def test_service_does_not_request_when_both_keys_are_missing_or_blank(self):
        for environment in ({}, {"TRAIN_API_KEY": " ", "TAGO_API_KEY": "\t"}):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True), \
                    patch.object(itx_service.requests, "get") as requester:
                with self.assertRaises(itx_service.TagoError) as error:
                    itx_service._request_body("GetCtyCodeList", {})
                self.assertEqual(error.exception.code, "MISSING_KEY")
                requester.assert_not_called()

    def test_service_network_errors_do_not_expose_selected_key_or_request_url(self):
        private = "fake-private-train-key"
        with patch.dict(os.environ, {"TRAIN_API_KEY": private, "TAGO_API_KEY": "fake-bus-key"}, clear=True), \
                patch.object(itx_service.requests, "get", side_effect=requests.ConnectionError(
                    f"https://example.invalid/?serviceKey={private}")):
            with self.assertRaises(itx_service.TagoError) as error:
                itx_service._request_body("GetCtyCodeList", {})
        self.assertEqual(error.exception.code, "TAGO_NETWORK")
        self.assertNotIn(private, str(error.exception))
        self.assertNotIn("serviceKey", str(error.exception))

    def test_collector_passes_the_same_selected_key_without_collecting_or_writing(self):
        class StopBeforeCollection(Exception):
            pass

        data_dir = MagicMock()
        data_dir.__truediv__.return_value.exists.return_value = False
        for environment, expected in KEY_CASES:
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True), \
                    patch.object(sys, "argv", ["collect_itx.py", "--date", "20300101", "--only-yongsan"]), \
                    patch.object(collector, "DATA_DIR", data_dir), \
                    patch.object(collector, "TagoClient", side_effect=StopBeforeCollection) as client_factory, \
                    patch.object(collector, "save") as save, redirect_stdout(StringIO()):
                with self.assertRaises(StopBeforeCollection):
                    collector.main()
                client_factory.assert_called_once_with(expected)
                save.assert_not_called()

    def test_collector_rejects_missing_or_blank_keys_before_client_or_file_access(self):
        for environment in ({}, {"TRAIN_API_KEY": " ", "TAGO_API_KEY": "\t"}):
            data_dir = MagicMock()
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True), \
                    patch.object(sys, "argv", ["collect_itx.py", "--date", "20300101"]), \
                    patch.object(collector, "DATA_DIR", data_dir), patch.object(collector, "TagoClient") as client_factory, \
                    redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as error:
                    collector.main()
                self.assertEqual(error.exception.code, 2)
                client_factory.assert_not_called()
                data_dir.__truediv__.assert_not_called()


if __name__ == "__main__":
    unittest.main()
