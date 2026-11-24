"""가짜 공공데이터 응답으로 통학 안내를 검증한다. 실제 키·네트워크는 쓰지 않는다."""

from copy import deepcopy
from datetime import datetime, timedelta
import json
import threading
import unittest
from unittest.mock import Mock, patch

from flask import Flask
import requests

from services.air_quality_client import AirQualityClient, observation_time
from services.commute_advice_routes import create_commute_advice_blueprint
from services.commute_advice_service import CommuteAdviceService
from services.environment_common import EnvironmentProviderError, KST
from services.weather_client import WeatherClient, forecast_grid, latest_issue


NOW = datetime(2026, 11, 23, 14, 20, tzinfo=KST)


def response(items, total=None, code="00"):
    return Mock(status_code=200, text="", json=Mock(return_value={"response": {
        "header": {"resultCode": code, "resultMsg": "untrusted-provider-message"},
        "body": {"items": items, "totalCount": len(items) if total is None else total},
    }}))


def forecast_rows(at=NOW, overrides=None, hours=6):
    issue = latest_issue(at)
    first = at.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    nx, ny = forecast_grid(37.8863, 127.7381)
    rows = []
    for offset in range(hours):
        target = first + timedelta(hours=offset)
        values = {"TMP": "12", "WSD": "2", "POP": "10", "PTY": "0"}
        values.update((overrides or {}).get(offset, {}))
        rows.extend({"baseDate": issue.strftime("%Y%m%d"), "baseTime": issue.strftime("%H%M"),
                     "fcstDate": target.strftime("%Y%m%d"), "fcstTime": target.strftime("%H%M"),
                     "category": category, "fcstValue": value, "nx": nx, "ny": ny}
                    for category, value in values.items())
    return rows


def air_row(at=NOW, **overrides):
    row = {"dataTime": at.replace(minute=0).strftime("%Y-%m-%d %H:%M"),
           "pm10Value": "25", "pm25Value": "12", "pm10Grade1h": "1", "pm25Grade1h": "1",
           "pm10Grade": "4", "pm25Grade": "4", "pm10Flag": None, "pm25Flag": None}
    row.update(overrides)
    return row


class CommuteAdviceTests(unittest.TestCase):
    def setUp(self):
        self.network = patch("requests.get", side_effect=AssertionError("External network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.services = []
        self.addCleanup(lambda: [service.close() for service in self.services])

    def service(self, *, weather=None, air=None, at=NOW, clock=None, weather_key="fixture-only-weather",
                air_key="fixture-only-air", weather_failure=None, air_failure=None):
        weather = deepcopy(forecast_rows(at) if weather is None else weather)
        air = deepcopy([air_row(at)] if air is None else air)
        calls = []
        lock = threading.Lock()

        def requester(url, *, params, **options):
            with lock:
                calls.append((url, deepcopy(params), options))
            if url == WeatherClient.URL:
                if weather_failure:
                    return weather_failure()
                start = (params["pageNo"] - 1) * params["numOfRows"]
                selected = deepcopy(weather[start:start + params["numOfRows"]])
                for row in selected:
                    row.update(nx=params["nx"], ny=params["ny"])
                return response(selected, len(weather))
            if url == AirQualityClient.URL:
                if air_failure:
                    return air_failure()
                return response(air)
            raise AssertionError("Unexpected endpoint")

        service = CommuteAdviceService(weather_key, air_key, requester=requester,
                                       now=lambda: at, clock=clock)
        self.services.append(service)
        return service, calls

    def test_forecast_grid_and_base_time_rollover(self):
        self.assertEqual(forecast_grid(37.5665, 126.978), (60, 127))
        self.assertEqual(forecast_grid(37.8863, 127.7381), (73, 134))
        before = datetime(2026, 11, 23, 2, 9, tzinfo=KST)
        self.assertEqual(latest_issue(before), datetime(2026, 11, 22, 23, tzinfo=KST))
        self.assertEqual(latest_issue(before + timedelta(minutes=1)), datetime(2026, 11, 23, 2, tzinfo=KST))
        self.assertEqual(latest_issue(datetime(2026, 1, 1, 0, 5, tzinfo=KST)),
                         datetime(2025, 12, 31, 23, tzinfo=KST))

    def test_three_independent_alerts_keep_forecast_and_observation_timestamps(self):
        service, _ = self.service(weather=forecast_rows(overrides={0: {"TMP": "2", "WSD": "5", "PTY": "1"}}),
                                  air=[air_row(pm25Value="50", pm25Grade1h="3")])
        result = service.advice()
        self.assertEqual(result["status"], "ok")
        self.assertEqual([item["id"] for item in result["alerts"]], ["rain", "cold_wind", "dust"])
        self.assertEqual(result["weather"]["forecast_at"], "2026-11-23T15:00:00+09:00")
        self.assertEqual(result["air"]["observed_at"], "2026-11-23T14:00:00+09:00")
        self.assertIn("중앙로", result["alerts"][-1]["message"])
        self.assertEqual(result["period"]["end"], "2026-11-23T21:00:00+09:00")
        self.assertNotIn("fixture-only", json.dumps(result, ensure_ascii=False))

    def test_cold_and_wind_must_match_the_same_forecast_hour(self):
        service, _ = self.service(weather=forecast_rows(overrides={0: {"TMP": "2", "WSD": "1"},
                                                                 1: {"TMP": "15", "WSD": "5"}}))
        ids = [item["id"] for item in service.advice()["alerts"]]
        self.assertIn("cold", ids)
        self.assertNotIn("cold_wind", ids)

    def test_snow_is_not_announced_as_rain_and_probability_is_not_current_rain(self):
        service, _ = self.service(weather=forecast_rows(overrides={0: {"PTY": "3", "POP": "90"}}))
        self.assertEqual([item["id"] for item in service.advice()["alerts"]], ["snow"])
        service2, _ = self.service(weather=forecast_rows(overrides={0: {"POP": "60"}}))
        alert = service2.advice()["alerts"][0]
        self.assertIn("강수확률", alert["message"])
        self.assertNotIn("비가 와", alert["message"])

    def test_midnight_uses_next_date_and_does_not_reuse_past_rain(self):
        late = datetime(2026, 11, 23, 23, 40, tzinfo=KST)
        rows = forecast_rows(late)
        past = deepcopy(rows[0])
        past.update(fcstDate="20261123", fcstTime="2300", category="PTY", fcstValue="1")
        service, _ = self.service(weather=rows + [past], at=late)
        result = service.advice()
        self.assertEqual(result["period"]["start"], "2026-11-24T00:00:00+09:00")
        self.assertNotIn("rain", [item["id"] for item in result["alerts"]])

    def test_air_uses_one_hour_grade_and_quality_flags(self):
        service, _ = self.service()
        result = service.advice()
        self.assertEqual(result["air"]["pm25"]["grade"], "좋음")
        self.assertNotIn("dust", [item["id"] for item in result["alerts"]])
        service2, _ = self.service(air=[air_row(pm25Value="95", pm25Grade1h="4", pm25Flag="장비점검")])
        result = service2.advice()
        self.assertEqual(result["air"]["status"], "partial")
        self.assertEqual(result["air"]["pm25"], {"value": None, "grade": None})
        self.assertNotIn("dust", [item["id"] for item in result["alerts"]])

    def test_missing_air_values_are_not_zero_or_good(self):
        service, _ = self.service(air=[air_row(pm10Value="-", pm25Value="", pm25Grade1h="4")])
        result = service.advice()
        self.assertEqual(result["air"]["status"], "unavailable")
        self.assertIsNone(result["air"]["pm10"]["value"])
        self.assertIsNone(result["air"]["pm25"]["grade"])
        self.assertEqual(result["status"], "partial")

    def test_stale_air_is_labelled_and_does_not_generate_current_warning(self):
        old = NOW - timedelta(hours=4)
        service, _ = self.service(air=[air_row(old, pm25Value="90", pm25Grade1h="4")])
        result = service.advice()
        self.assertEqual(result["air"]["status"], "stale")
        self.assertNotIn("dust", [item["id"] for item in result["alerts"]])

    def test_future_observation_is_rejected(self):
        service, _ = self.service(air=[air_row(NOW + timedelta(hours=1), pm25Value="90", pm25Grade1h="4")])
        result = service.advice()
        self.assertEqual(result["air"]["code"], "AIR_QUALITY_PROVIDER_RESPONSE")
        self.assertIsNone(result["air"]["observed_at"])
        self.assertNotIn("dust", [item["id"] for item in result["alerts"]])

    def test_weather_sentinels_and_partial_hours_are_explicit(self):
        rows = forecast_rows(hours=3, overrides={0: {"TMP": "-999", "WSD": "NaN", "POP": "999"}})
        service, _ = self.service(weather=rows)
        result = service.advice()
        self.assertEqual(result["weather"]["status"], "partial")
        self.assertIsNone(result["weather"]["temperature_c"])
        self.assertNotIn("cold_wind", [item["id"] for item in result["alerts"]])

    def test_missing_keys_are_safe_and_do_not_request(self):
        service, calls = self.service(weather_key="", air_key="")
        result = service.advice()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["weather"]["code"], "WEATHER_KEY_MISSING")
        self.assertEqual(result["air"]["code"], "AIR_QUALITY_KEY_MISSING")
        self.assertEqual(result["alerts"], [])
        self.assertEqual(calls, [])

    def test_one_failed_provider_preserves_other_data(self):
        service, _ = self.service(weather_failure=lambda: Mock(status_code=403))
        result = service.advice()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["weather"]["code"], "WEATHER_PROVIDER_AUTH")
        self.assertEqual(result["air"]["status"], "ok")

    def test_timeout_and_xml_gateway_errors_do_not_expose_key_or_provider_text(self):
        def timeout():
            raise requests.Timeout("url?serviceKey=fixture-secret")

        body = "<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode><errMsg>fixture-secret</errMsg></cmmMsgHeader></OpenAPI_ServiceResponse>"
        service, _ = self.service(weather_failure=timeout,
                                  air_failure=lambda: Mock(status_code=200, text=body, json=Mock(side_effect=ValueError("fixture-secret"))))
        result = service.advice()
        self.assertEqual(result["weather"]["code"], "WEATHER_PROVIDER_TIMEOUT")
        self.assertEqual(result["air"]["code"], "AIR_QUALITY_PROVIDER_AUTH")
        self.assertNotIn("fixture-secret", json.dumps(result, ensure_ascii=False))

    def test_encoded_key_is_decoded_once_and_native_plus_is_preserved(self):
        service, calls = self.service(weather_key="fixture%2Bkey%2Fpart%3D%252B", air_key="fixture+raw/=")
        service.advice()
        keyed = {url: params["serviceKey"] for url, params, _ in calls}
        self.assertEqual(keyed[WeatherClient.URL], "fixture+key/part=%2B")
        self.assertEqual(keyed[AirQualityClient.URL], "fixture+raw/=")
        self.assertNotIn("fixture+", repr(service.weather_client))

    def test_cache_is_shared_between_campuses_and_expires(self):
        tick = [0.0]
        service, calls = self.service(clock=lambda: tick[0])
        service.advice("hallym")
        service.advice("hallym")
        self.assertEqual(len(calls), 2)
        service.advice("knu")
        self.assertEqual(len(calls), 3, "서로 다른 날씨 격자는 따로, 같은 대기질 측정소는 공유")
        tick[0] = 901
        service.advice()
        self.assertEqual(len(calls), 5)

    def test_failures_are_cached_briefly(self):
        tick = [0.0]
        service, calls = self.service(clock=lambda: tick[0], weather_failure=lambda: Mock(status_code=429))
        service.advice()
        service.advice()
        self.assertEqual(len(calls), 2)
        tick[0] = 61
        service.advice()
        self.assertEqual(len(calls), 3)

    def test_invalid_and_duplicate_arguments_never_reach_provider(self):
        service, calls = self.service()
        app = Flask(__name__)
        app.register_blueprint(create_commute_advice_blueprint(service))
        client = app.test_client()
        for suffix in ("?campus=bad", "?campus=", "?campus=hallym&campus=knu", "?lat=37"):
            result = client.get("/api/commute-advice" + suffix)
            self.assertEqual(result.status_code, 400)
            self.assertEqual(result.headers["Cache-Control"], "no-store")
        self.assertEqual(calls, [])
        result = client.get("/api/commute-advice?campus=knu")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["campus"]["id"], "knu")

    def test_duplicate_forecast_page_is_not_treated_as_complete(self):
        rows = forecast_rows()
        requester = Mock(return_value=response(rows, total=50))
        client = WeatherClient("fixture-key", requester=requester, now=lambda: NOW)
        with self.assertRaises(EnvironmentProviderError) as caught:
            client.forecast(37.8863, 127.7381)
        self.assertEqual(caught.exception.code, "WEATHER_INCOMPLETE")
        self.assertEqual(requester.call_count, 2)

    def test_air_24_hour_timestamp_rolls_into_next_day(self):
        self.assertEqual(observation_time("2026-11-23 24:00"), datetime(2026, 11, 24, tzinfo=KST))

    def test_publication_delay_falls_back_once_and_caches_previous_issue(self):
        issue = latest_issue(NOW)
        older = issue - timedelta(hours=3)
        rows = forecast_rows()
        for row in rows:
            row.update(baseDate=older.strftime("%Y%m%d"), baseTime=older.strftime("%H%M"))

        def requester(url, *, params, **kwargs):
            if params["base_time"] == issue.strftime("%H%M"):
                return response([], code="03")
            self.assertEqual(params["base_time"], older.strftime("%H%M"))
            return response(rows)

        mocked = Mock(side_effect=requester)
        client = WeatherClient("fixture-only-key", requester=mocked, now=lambda: NOW)
        self.assertEqual(client.forecast(37.8863, 127.7381)["issued_at"], older)
        client.forecast(37.8863, 127.7381)
        self.assertEqual(mocked.call_count, 2)

    def test_stale_weather_does_not_make_fresh_alerts(self):
        service, _ = self.service()
        rows = [{"at": NOW.replace(minute=0) + timedelta(hours=index + 1),
                 "temperature_c": 0, "wind_speed_ms": 10, "precipitation_type": 1,
                 "rain_probability": 90} for index in range(6)]
        service.weather_client.forecast = Mock(return_value={
            "issued_at": NOW - timedelta(hours=7), "fetched_at": NOW, "periods": rows})
        result = service.advice()
        self.assertEqual(result["weather"]["status"], "stale")
        self.assertEqual(result["alerts"], [])

    def test_bad_station_setting_only_disables_air_information(self):
        service = CommuteAdviceService("", "fixture-only-key", station_name="x" * 51)
        self.services.append(service)
        result = service.advice()
        self.assertEqual(result["air"]["code"], "AIR_QUALITY_STATION_INVALID")
        self.assertEqual(result["air"]["station_name"], "측정소 미설정")

    def test_concurrent_refresh_reuses_provider_calls(self):
        from concurrent.futures import ThreadPoolExecutor
        service, calls = self.service()
        with ThreadPoolExecutor(max_workers=8) as workers:
            results = list(workers.map(lambda _: service.advice(), range(8)))
        self.assertTrue(all(item["status"] == "ok" for item in results))
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
