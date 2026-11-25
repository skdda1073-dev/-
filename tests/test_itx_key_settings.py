"""가짜 환경·HTTP로 ITX 조회와 수집기의 열차 인증키 분리를 검증한다."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime
import importlib
from io import StringIO
import os
import sys
from threading import Event
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

    def test_invalid_provider_result_code_does_not_expose_raw_details(self):
        response = Mock(status_code=200)
        response.json.return_value = {"response": {"header": {"resultCode": "fake-private-train-key"}}}
        with patch.dict(os.environ, {"TRAIN_API_KEY": "fake-private-train-key"}, clear=True), \
                patch.object(itx_service.requests, "get", return_value=response):
            with self.assertRaises(itx_service.TagoError) as error:
                itx_service._request_body("GetCtyCodeList", {})
        self.assertEqual(error.exception.code, "BAD_RESPONSE")
        self.assertNotIn("fake-private-train-key", str(error.exception))

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


class PlannedItxTests(unittest.TestCase):
    origin = "NAT140873"
    destination = "NAT130126"
    service_date = date(2026, 11, 26)

    def setUp(self):
        planned_cache = patch.dict(itx_service._planned_cache, clear=True)
        pending_cache = patch.dict(itx_service._planned_inflight, clear=True)
        backoff = patch.object(itx_service, "_planned_throttled_until", 0.0)
        for item in (planned_cache, pending_cache, backoff):
            item.start()
            self.addCleanup(item.stop)

    @staticmethod
    def row(**overrides):
        return {"trainno": "02002", "traingradename": "ITX-청춘", "depplacename": "춘천", "arrplacename": "청량리",
                "depplandtime": "20261126060700", "arrplandtime": "20261126071200", "adultcharge": "8600", **overrides}

    def test_filters_date_train_grade_stop_names_and_invalid_chronology(self):
        rows = [self.row(traingradename="KTX"), self.row(traingradename=""), self.row(depplacename="남춘천"),
                self.row(arrplacename="상봉"), self.row(depplandtime="20261125060700", arrplandtime="20261125071200"),
                self.row(arrplandtime="20261126060500"), self.row(arrplandtime="20261129071200"),
                self.row(depplandtime="2026-11-26T06:07:00"), self.row(trainno="unknown"), self.row(trainno="00000"),
                self.row(), self.row(adultcharge=-1),
                self.row(trainno=2099, depplandtime="20261126235600", arrplandtime="20261127001700", adultcharge=True)]
        with patch.object(itx_service, "_all_pages", return_value=rows) as provider:
            first, overnight = itx_service.planned_itx_trips(self.origin, self.destination, "2026-11-26")
        provider.assert_called_once_with("GetStrtpntAlocFndTrainInfo", timeout=itx_service.PLANNED_ITX_TIMEOUT,
                                         depPlaceId=self.origin, arrPlaceId=self.destination,
                                         depPlandTime="20261126", trainGradeCode="09")
        self.assertEqual(first, {"train_number": "02002", "departure_at": datetime.fromisoformat("2026-11-26T06:07:00+09:00"),
                                 "arrival_at": datetime.fromisoformat("2026-11-26T07:12:00+09:00"), "fare_won": 8600})
        self.assertEqual(overnight["train_number"], "02099")
        self.assertEqual(overnight["arrival_at"].date(), date(2026, 11, 27))
        self.assertIsNone(overnight["fare_won"])

    def test_invalid_lookup_does_not_fetch_and_success_is_cached_by_requested_date(self):
        with patch.object(itx_service, "_all_pages", side_effect=[[self.row()], [self.row(
            depplandtime="20261128060600", arrplandtime="20261128070800", trainno="02052")]]) as provider:
            for malformed in ("2026-02-29", "2026-W48-4", "", datetime(2026, 11, 26)):
                self.assertEqual(itx_service.planned_itx_trips(self.origin, self.destination, malformed), ())
            self.assertEqual(itx_service.planned_itx_trips(self.origin, self.origin, self.service_date), ())
            self.assertEqual(itx_service.planned_itx_trips("not-a-station", self.destination, self.service_date), ())
            provider.assert_not_called()
            first, = itx_service.planned_itx_trips(self.origin, self.destination, self.service_date)
            first["fare_won"] = 0
            again, = itx_service.planned_itx_trips(self.origin, self.destination, "20261126")
            saturday, = itx_service.planned_itx_trips(self.origin, self.destination, date(2026, 11, 28))
            self.assertEqual((again["fare_won"], saturday["train_number"]), (8600, "02052"))
        self.assertEqual(provider.call_count, 2)

    def test_concurrent_same_pair_and_day_share_one_network_request(self):
        started, waiting, release = Event(), Event(), Event()
        key = (self.origin, self.destination, self.service_date)

        def response(*_args, **_kwargs):
            started.set()
            if not release.wait(3):
                raise AssertionError("concurrent fixture timed out")
            return [self.row()]

        with patch.object(itx_service, "_all_pages", side_effect=response) as provider, ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(itx_service.planned_itx_trips, *key)
            self.assertTrue(started.wait(1))
            pending = itx_service._planned_inflight[key]
            original_wait = pending.wait

            def observe_wait(timeout):
                waiting.set()
                return original_wait(timeout)

            with patch.object(pending, "wait", side_effect=observe_wait):
                second = pool.submit(itx_service.planned_itx_trips, *key)
                try:
                    self.assertTrue(waiting.wait(1))
                finally:
                    release.set()
                results = (first.result(timeout=2), second.result(timeout=2))
        self.assertEqual(results[0], results[1])
        self.assertIsNot(results[0][0], results[1][0])
        provider.assert_called_once()

    def test_quota_uses_cached_success_and_temporarily_suppresses_new_pairs(self):
        with patch.object(itx_service, "_all_pages", side_effect=[
            [self.row()], itx_service.TagoError("mock secret never returned", "TAGO_RATE_LIMIT"),
        ]) as provider:
            saved = itx_service.planned_itx_trips(self.origin, self.destination, self.service_date)
            self.assertEqual(itx_service.planned_itx_trips(self.origin, self.destination, "2026-11-27"), ())
            self.assertEqual(itx_service.planned_itx_trips(self.destination, self.origin, self.service_date), ())
            self.assertEqual(itx_service.planned_itx_trips(self.origin, self.destination, self.service_date), saved)
        self.assertEqual(provider.call_count, 2)


if __name__ == "__main__":
    unittest.main()
