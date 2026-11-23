"""진단 명령의 외부 호출 조건과 비밀값 비노출을 검증한다."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib.util
from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_commute_advice.py"
SPEC = importlib.util.spec_from_file_location("commute_advice_probe", SCRIPT)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def example_response():
    """검사용 합성 값이며 실제 날씨·대기질이 아니다."""
    return {
        "status": "ok",
        "weather": {
            "status": "ok", "code": None,
            "issued_at": "2030-01-02T14:00:00+09:00",
            "forecast_at": "2030-01-02T15:00:00+09:00",
            "temperature_c": 3, "wind_speed_ms": 4.2, "rain_probability": 70,
        },
        "air": {
            "status": "ok", "code": None, "observed_at": "2030-01-02T14:00:00+09:00",
            "pm10": {"value": 45, "grade": "보통"},
            "pm25": {"value": 40, "grade": "나쁨"},
        },
        "alerts": [{"id": "rain"}, {"id": "cold_wind"}, {"id": "dust"}],
    }


class CommuteAdviceProbeTests(unittest.TestCase):
    def setUp(self):
        self.secret = "PRIVATE-KEY-DO-NOT-PRINT"
        self.settings = SimpleNamespace(
            weather_data_key=self.secret, airkorea_data_key=self.secret,
            airkorea_station_name="중앙로",
        )

    def run_probe(self, args, *, settings_loader=None, response=None, factory=None):
        lines, stray = [], StringIO()
        service = Mock(advice=Mock(return_value=response if response is not None else example_response()))
        factory = factory if factory is not None else Mock(return_value=service)
        with redirect_stdout(stray), redirect_stderr(stray):
            code = probe.main(
                args, settings_loader=settings_loader or (lambda _: self.settings),
                service_factory=factory, output=lines.append,
            )
        text = "\n".join(lines) + stray.getvalue()
        self.assertNotIn(self.secret, text)
        return code, json.loads(lines[0]), factory, service, stray.getvalue()

    def test_default_checks_presence_without_constructing_service(self):
        code, output, factory, _, stray = self.run_probe([])
        self.assertEqual(code, 0)
        self.assertEqual(output, {"weather_data_key_configured": True, "airkorea_data_key_configured": True})
        factory.assert_not_called()
        self.assertEqual(stray, "")

    def test_missing_key_is_a_configuration_result_without_network(self):
        self.settings.weather_data_key = ""
        code, output, factory, _, _ = self.run_probe([])
        self.assertEqual(code, 2)
        self.assertFalse(output["weather_data_key_configured"])
        factory.assert_not_called()

    def test_invalid_campus_never_echoes_argument_or_loads_settings(self):
        loader = Mock(side_effect=AssertionError("must not load"))
        code, output, factory, _, stray = self.run_probe(["--campus", self.secret], settings_loader=loader)
        self.assertEqual(code, 2)
        self.assertEqual(output["code"], "COMMUTE_ADVICE_ARGUMENTS_INVALID")
        loader.assert_not_called()
        factory.assert_not_called()
        self.assertEqual(stray, "")

    def test_noisy_settings_exception_is_sanitized(self):
        def loader(_):
            print(self.secret)
            raise RuntimeError(self.secret)
        code, output, factory, _, stray = self.run_probe([], settings_loader=loader)
        self.assertEqual(code, 2)
        self.assertEqual(output["code"], "COMMUTE_ADVICE_CONFIGURATION_INVALID")
        factory.assert_not_called()
        self.assertEqual(stray, "")

    def test_live_calls_selected_campus_and_only_returns_safe_fields(self):
        response = example_response()
        response["url"] = self.secret
        response["weather"]["message"] = self.secret
        response["air"]["station_name"] = self.secret
        response["alerts"][0]["title"] = self.secret
        code, output, factory, service, _ = self.run_probe(["--live", "--campus", "knu"], response=response)
        self.assertEqual(code, 0)
        factory.assert_called_once_with(self.secret, self.secret, station_name="중앙로")
        service.advice.assert_called_once_with(campus="knu")
        self.assertEqual(output["campus"], "knu")
        self.assertEqual(output["alert_types"], ["rain", "cold_wind", "dust"])
        self.assertEqual(output["air"]["pm25"], {"value": 40, "grade": "나쁨"})

    def test_partial_result_preserves_provider_error_without_claiming_success(self):
        response = example_response()
        response["status"] = "partial"
        response["weather"] = {"status": "unavailable", "code": "WEATHER_PROVIDER_AUTH"}
        response["alerts"] = [{"id": "dust"}]
        code, output, _, _, _ = self.run_probe(["--live"], response=response)
        self.assertEqual(code, 4)
        self.assertEqual(output["weather"]["code"], "WEATHER_PROVIDER_AUTH")
        self.assertEqual(output["air"]["status"], "ok")

    def test_provider_exception_text_is_never_printed(self):
        service = Mock(advice=Mock(side_effect=RuntimeError("URL?serviceKey=" + self.secret)))
        code, output, _, _, stray = self.run_probe(["--live"], factory=Mock(return_value=service))
        self.assertEqual(code, 3)
        self.assertEqual(output["code"], "COMMUTE_ADVICE_CHECK_FAILED")
        self.assertEqual(stray, "")

    def test_unknown_error_code_does_not_echo_untrusted_provider_value(self):
        response = example_response()
        response["weather"]["code"] = self.secret
        code, output, _, _, _ = self.run_probe(["--live"], response=response)
        self.assertEqual(code, 0)
        self.assertEqual(output["weather"]["code"], "UNKNOWN_PROVIDER_ERROR")

    def test_invalid_metrics_or_timestamps_are_not_reported_as_success(self):
        for invalid in (float("nan"), float("inf"), True, self.secret):
            with self.subTest(metric=type(invalid).__name__):
                response = deepcopy(example_response())
                response["weather"]["temperature_c"] = invalid
                code, output, _, _, _ = self.run_probe(["--live"], response=response)
                self.assertEqual(code, 3)
                self.assertEqual(output["code"], "COMMUTE_ADVICE_RESPONSE_INVALID")
        response = example_response()
        response["air"]["observed_at"] = self.secret
        code, output, _, _, _ = self.run_probe(["--live"], response=response)
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
