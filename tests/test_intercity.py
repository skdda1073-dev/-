"""공식 TAGO 계약과 사용자에게 보이는 결과를 가짜 응답·시계로 검증한다.

실제 인증키, 프로젝트 .env, 외부 네트워크는 사용하지 않는다. 터미널·운행
fixture는 모두 테스트용이며 실제 춘천 배차나 현재 요금을 의미하지 않는다.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from flask import Flask
import requests

from services.intercity_client import IntercityClient, IntercityError, KST
from services.intercity_routes import create_intercity_blueprint
from services.intercity_service import IntercityService
from services.settings import load_settings


NOW = datetime(2026, 11, 9, 12, 0, tzinfo=KST)
TERMINAL_URL = "https://apis.data.go.kr/1613000/SuburbsBusInfo/GetSuberbsBusTrminlList"
TIMETABLE_URL = "https://apis.data.go.kr/1613000/SuburbsBusInfo/GetStrtpntAlocFndSuberbsBusInfo"
CATALOGUE = [
    {"terminalId": "TEST-DEP", "terminalNm": "테스트 춘천", "cityName": "강원특별자치도"},
    {"terminalId": "TEST-ARR", "terminalNm": "테스트 서울", "cityName": "서울특별시"},
]


def trip(**overrides):
    record = {
        "routeId": "TEST-ROUTE", "depPlandTime": "202611091300",
        "arrPlandTime": "202611091430", "gradeNm": "일반", "charge": "9800",
        "depPlaceNm": "테스트 춘천", "arrPlaceNm": "테스트 서울",
    }
    record.update(overrides)
    return record


def json_response(rows, total=None, code="00"):
    payload = {"response": {
        "header": {"resultCode": code, "resultMsg": "NORMAL SERVICE."},
        "body": {"items": {"item": deepcopy(rows)},
                 "totalCount": len(rows) if total is None else total},
    }}
    return Mock(status_code=200, text="", json=Mock(return_value=payload))


def xml_error(code="30", secret="fake-provider-secret"):
    text = (
        "<OpenAPI_ServiceResponse><cmmMsgHeader>"
        f"<errMsg>{secret}</errMsg>"
        f"<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR {secret}</returnAuthMsg>"
        f"<returnReasonCode>{code}</returnReasonCode>"
        "</cmmMsgHeader></OpenAPI_ServiceResponse>"
    )
    return Mock(status_code=200, text=text, json=Mock(side_effect=ValueError(secret)))


def client_with(requester, key="fake-only-key", now=None, clock=None):
    return IntercityClient(key, requester=requester, now=now or (lambda: NOW),
                          clock=clock or (lambda: 0.0))


def service_with(rows=None, terminals=None, now=None):
    catalogue = deepcopy(CATALOGUE if terminals is None else terminals)
    records = deepcopy([trip()] if rows is None else rows)

    def respond(url, *, params, **kwargs):
        if url == TERMINAL_URL:
            data = catalogue
        elif url == TIMETABLE_URL:
            data = records
        else:
            raise AssertionError(f"Unexpected provider operation: {url}")
        start = (params["pageNo"] - 1) * params["numOfRows"]
        return json_response(data[start:start + params["numOfRows"]], total=len(data))

    requester = Mock(side_effect=respond)
    time = now or (lambda: NOW)
    client = client_with(requester, now=time)
    return IntercityService("fake-only-key", client=client, now=time), requester


def http_client(service):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(create_intercity_blueprint(service))
    return app.test_client()


class IntercityClientTests(unittest.TestCase):
    def test_documented_operation_parameters_and_key_encoding_once(self):
        requester = Mock(return_value=json_response([]))
        client = client_with(requester, key="fake%2Bkey%2Fpart%3D%252B")
        parameters = {"depTerminalId": "TEST-DEP", "arrTerminalId": "TEST-ARR",
                      "depPlandTime": "20261109"}
        client.read("GetStrtpntAlocFndSuberbsBusInfo", parameters, 60)
        args, kwargs = requester.call_args
        self.assertEqual(args, (TIMETABLE_URL,))
        self.assertEqual(kwargs["params"], {
            "serviceKey": "fake+key/part=%2B", "_type": "json", "pageNo": 1,
            "numOfRows": 100, **parameters,
        })
        self.assertFalse(kwargs["allow_redirects"])
        self.assertTrue(all(0 < value <= 10 for value in kwargs["timeout"]))
        prepared = requests.Request("GET", args[0], params=kwargs["params"]).prepare()
        self.assertEqual(parse_qs(urlsplit(prepared.url).query)["serviceKey"], ["fake+key/part=%2B"])

    def test_json_list_singleton_empty_and_documented_success_code(self):
        cases = [({"item": [{"terminalId": "TEST"}]}, 1, [{"terminalId": "TEST"}]),
                 ({"item": {"terminalId": "TEST"}}, 1, [{"terminalId": "TEST"}]),
                 ({"item": []}, 0, []), ("", 0, []), (None, 0, [])]
        for items, total, expected in cases:
            with self.subTest(items=items):
                response = json_response([])
                response.json.return_value["response"]["body"] = {"items": items, "totalCount": str(total)}
                client = client_with(Mock(return_value=response))
                result = client.read("GetSuberbsBusTrminlList", {}, 3600)
                self.assertEqual(result["rows"], expected)
                self.assertFalse(result["from_cache"])
                self.assertEqual(result["fetched_at"], NOW.isoformat())

    def test_auth_xml_and_http_failures_hide_provider_text_and_key(self):
        secret = "fake-sensitive-key+%2B"
        for response in (xml_error(secret=secret), Mock(status_code=403)):
            with self.subTest(status=response.status_code):
                client = client_with(Mock(return_value=response), key=secret)
                with self.assertRaises(IntercityError) as raised:
                    client.read("GetSuberbsBusTrminlList", {}, 3600)
                self.assertEqual(raised.exception.code, "INTERCITY_PROVIDER_AUTH")
                self.assertEqual(raised.exception.status_code, 503)
                self.assertNotIn(secret, str(raised.exception))
                self.assertNotIn("SERVICE_KEY_IS_NOT_REGISTERED_ERROR", str(raised.exception))

    def test_daily_per_second_and_http_limits_are_quota_failures(self):
        for response in (json_response([], code="22"), json_response([], code="23"),
                         xml_error("23"), Mock(status_code=429)):
            with self.subTest(response=response):
                client = client_with(Mock(return_value=response))
                with self.assertRaises(IntercityError) as raised:
                    client.read("GetSuberbsBusTrminlList", {}, 3600)
                self.assertEqual(raised.exception.code, "INTERCITY_PROVIDER_QUOTA")
                self.assertEqual(raised.exception.status_code, 503)

    def test_network_failures_and_invalid_json_never_become_empty_data(self):
        secret = "fake-secret-in-provider-url"
        for failure, expected in ((requests.Timeout(secret), "INTERCITY_PROVIDER_TIMEOUT"),
                                  (requests.ConnectionError(secret), "INTERCITY_PROVIDER_NETWORK")):
            with self.subTest(expected=expected):
                client = client_with(Mock(side_effect=failure))
                with self.assertRaises(IntercityError) as raised:
                    client.read("GetSuberbsBusTrminlList", {}, 3600)
                self.assertEqual(raised.exception.code, expected)
                self.assertNotIn(secret, str(raised.exception))
        malformed = [[], {}, {"response": {"header": {"resultCode": "00"}, "body": {"items": []}}}]
        for item in (False, 0, "not-an-item"):
            malformed.append({"response": {"header": {"resultCode": "00"},
                                           "body": {"items": {"item": item}, "totalCount": 0}}})
        malformed.append({"response": {"header": {"resultCode": "00"},
                                       "body": {"items": "", "totalCount": "²"}}})
        for payload in malformed:
            with self.subTest(payload=payload):
                response = Mock(status_code=200, json=Mock(return_value=payload))
                with self.assertRaises(IntercityError) as raised:
                    client_with(Mock(return_value=response)).read("GetSuberbsBusTrminlList", {}, 60)
                self.assertEqual(raised.exception.code, "INTERCITY_PROVIDER_RESPONSE")

    def test_all_pages_are_read_using_provider_total_count(self):
        records = [{"terminalId": f"TEST-{i}"} for i in range(101)]
        requester = Mock(side_effect=[json_response(records[:100], total="101"),
                                      json_response(records[100:], total="101")])
        result = client_with(requester).read("GetSuberbsBusTrminlList", {}, 3600)
        self.assertEqual(result["rows"], records)
        self.assertEqual([call.kwargs["params"]["pageNo"] for call in requester.call_args_list], [1, 2])
        self.assertTrue(all(call.args[0] == TERMINAL_URL for call in requester.call_args_list))

    def test_repeated_truncated_or_changing_pages_fail_without_caching_partial_list(self):
        batch = [{"terminalId": f"TEST-{i}"} for i in range(100)]
        sequences = [
            [json_response(batch, total=200), json_response(batch, total=200)],
            [json_response(batch, total=101), json_response([], total=101)],
            [json_response(batch, total=101), json_response([{"terminalId": "LAST"}], total=102)],
        ]
        for responses in sequences:
            with self.subTest(responses=responses):
                requester = Mock(side_effect=responses + [json_response([{"terminalId": "RECOVERED"}])])
                client = client_with(requester)
                with self.assertRaises(IntercityError) as raised:
                    client.read("GetSuberbsBusTrminlList", {}, 3600)
                self.assertEqual(raised.exception.code, "INTERCITY_INCOMPLETE")
                recovered = client.read("GetSuberbsBusTrminlList", {}, 3600)
                self.assertEqual(recovered["rows"], [{"terminalId": "RECOVERED"}])
                self.assertFalse(recovered["from_cache"])

    def test_missing_key_never_makes_provider_request(self):
        requester = Mock(side_effect=AssertionError("Unexpected outbound request"))
        client = client_with(requester, key="   ")
        self.assertFalse(client.configured)
        for operation in ("GetSuberbsBusTrminlList", "GetStrtpntAlocFndSuberbsBusInfo"):
            with self.assertRaises(IntercityError) as raised:
                client.read(operation, {}, 60)
            self.assertEqual(raised.exception.code, "INTERCITY_KEY_MISSING")
        requester.assert_not_called()

    def test_cache_is_isolated_by_operation_arguments_and_client(self):
        requester = Mock(side_effect=[json_response([{"value": label}])
                                     for label in ("terminals", "route-a", "route-b", "other-key")])
        first = client_with(requester, key="fake-key-a")
        second = client_with(requester, key="fake-key-b")
        calls = [(first, "GetSuberbsBusTrminlList", {}, "terminals"),
                 (first, "GetStrtpntAlocFndSuberbsBusInfo", {"arrTerminalId": "A"}, "route-a"),
                 (first, "GetStrtpntAlocFndSuberbsBusInfo", {"arrTerminalId": "B"}, "route-b"),
                 (second, "GetSuberbsBusTrminlList", {}, "other-key")]
        for client, operation, params, label in calls:
            self.assertEqual(client.read(operation, params, 60)["rows"], [{"value": label}])
        for client, operation, params, label in calls:
            cached = client.read(operation, params, 60)
            self.assertEqual(cached["rows"], [{"value": label}])
            self.assertTrue(cached["from_cache"])
        self.assertEqual(requester.call_count, 4)

    def test_cached_results_are_copied_and_expire(self):
        elapsed = [10.0]
        requester = Mock(side_effect=[json_response([{"terminalNm": "original"}]),
                                      json_response([{"terminalNm": "updated"}])])
        client = client_with(requester, clock=lambda: elapsed[0])
        first = client.read("GetSuberbsBusTrminlList", {}, 60)
        first["rows"][0]["terminalNm"] = "caller mutation"
        second = client.read("GetSuberbsBusTrminlList", {}, 60)
        self.assertEqual(second["rows"][0]["terminalNm"], "original")
        second["rows"].clear()
        self.assertEqual(len(client.read("GetSuberbsBusTrminlList", {}, 60)["rows"]), 1)
        elapsed[0] = 71.0
        refreshed = client.read("GetSuberbsBusTrminlList", {}, 60)
        self.assertEqual(refreshed["rows"][0]["terminalNm"], "updated")
        self.assertFalse(refreshed["from_cache"])
        self.assertEqual(requester.call_count, 2)


class IntercityServiceTests(unittest.TestCase):
    def test_today_and_provider_query_use_korean_date_at_utc_boundary(self):
        utc_now = datetime(2026, 11, 8, 15, 5, tzinfo=timezone.utc)
        service, requester = service_with(now=lambda: utc_now)
        self.assertEqual(service.configuration()["today"], "2026-11-09")
        self.assertEqual(service.configuration()["date_policy"], "today_only")
        requester.assert_not_called()
        result = service.timetable("TEST-DEP", "TEST-ARR")
        self.assertEqual(result["date"], "2026-11-09")
        self.assertEqual(requester.call_args.kwargs["params"]["depPlandTime"], "20261109")

    def test_other_days_and_invalid_dates_are_rejected_before_provider_access(self):
        service, requester = service_with()
        for day in ("2026-11-08", "2026-11-10", "20261109", "2026-02-30", "", True, []):
            with self.subTest(day=day):
                with self.assertRaises(IntercityError) as raised:
                    service.timetable("TEST-DEP", "TEST-ARR", day)
                self.assertEqual(raised.exception.status_code, 400)
        requester.assert_not_called()

    def test_same_malformed_and_unknown_terminal_ids_cannot_request_timetable(self):
        service, requester = service_with()
        for departure, arrival in (("TEST-DEP", "TEST-DEP"), (None, "TEST-ARR"),
                                   (True, "TEST-ARR"), ("../forged", "TEST-ARR"),
                                   ("TEST-DEP", "x" * 65)):
            with self.subTest(departure=departure, arrival=arrival):
                with self.assertRaises(IntercityError) as raised:
                    service.timetable(departure, arrival)
                self.assertEqual(raised.exception.status_code, 400)
        requester.assert_not_called()
        with self.assertRaises(IntercityError) as raised:
            service.timetable("TEST-UNKNOWN", "TEST-ARR")
        self.assertEqual(raised.exception.code, "UNKNOWN_INTERCITY_TERMINAL")
        self.assertEqual([call.args[0] for call in requester.call_args_list], [TERMINAL_URL])

    def test_catalogue_filter_deduplication_and_malformed_identity_rejection(self):
        service, _ = service_with(terminals=[CATALOGUE[0], CATALOGUE[1], CATALOGUE[0]])
        self.assertEqual(len(service.terminals()["terminals"]), 2)
        self.assertEqual(service.terminals("강원")["terminals"], [
            {"id": "TEST-DEP", "name": "테스트 춘천", "city": "강원특별자치도"},
        ])
        for malformed in (None, True, 12, "bad/id", ""):
            with self.subTest(terminal_id=malformed):
                bad, _ = service_with(terminals=[dict(CATALOGUE[0], terminalId=malformed)])
                with self.assertRaises(IntercityError) as raised:
                    bad.terminals()
                self.assertEqual(raised.exception.code, "INTERCITY_TERMINAL_RESPONSE")
        conflict, _ = service_with(terminals=[CATALOGUE[0], dict(CATALOGUE[0], terminalNm="다른 터미널")])
        with self.assertRaises(IntercityError):
            conflict.terminals()

    def test_displayed_terminal_names_come_from_validated_catalogue(self):
        service, requester = service_with(rows=[trip(depPlaceNm="forged origin", arrPlaceNm="forged destination")])
        result = service.timetable("TEST-DEP", "TEST-ARR")
        self.assertEqual(result["departure"]["name"], "테스트 춘천")
        self.assertEqual(result["arrival"]["name"], "테스트 서울")
        params = requester.call_args.kwargs["params"]
        self.assertEqual(params["depTerminalId"], "TEST-DEP")
        self.assertEqual(params["arrTerminalId"], "TEST-ARR")
        self.assertNotIn("depPlaceNm", params)

    def test_documented_12_digit_overnight_and_14_digit_second_times(self):
        service, _ = service_with(rows=[
            trip(routeId="overnight", depPlandTime="202611092350", arrPlandTime="202611100020"),
            trip(routeId="seconds", depPlandTime="20261109120001", arrPlandTime="20261109120102"),
        ])
        result = service.timetable("TEST-DEP", "TEST-ARR")
        by_id = {item["route_id"]: item for item in result["trips"]}
        self.assertEqual(result["status"], "ok")
        self.assertEqual(by_id["overnight"]["duration_minutes"], 30)
        self.assertEqual(by_id["overnight"]["arrival_at"], "2026-11-10T00:20:00+09:00")
        self.assertEqual(by_id["seconds"]["duration_minutes"], 2)
        self.assertEqual(by_id["seconds"]["departure_at"], "2026-11-09T12:00:01+09:00")

    def test_unknown_or_invalid_fares_are_none_instead_of_zero(self):
        invalid = [None, "", "unknown", "NaN", "Infinity", "-5", True, "4.5", 0]
        rows = [trip(routeId=f"unknown-{i}", charge=value) for i, value in enumerate(invalid)]
        rows.extend([trip(routeId="known-string", charge="9800"), trip(routeId="known-number", charge=9000)])
        service, _ = service_with(rows=rows)
        result = service.timetable("TEST-DEP", "TEST-ARR")
        fares = {item["route_id"]: item["fare_krw"] for item in result["trips"]}
        for i in range(len(invalid)):
            self.assertIsNone(fares[f"unknown-{i}"])
        self.assertEqual(fares["known-string"], 9800)
        self.assertEqual(fares["known-number"], 9000)
        self.assertEqual(result["status"], "partial")

    def test_missing_optional_values_and_invalid_arrivals_remain_partial_trips(self):
        service, _ = service_with(rows=[
            {"routeId": "missing", "depPlandTime": "202611091300"},
            trip(routeId="invalid", arrPlandTime="not-a-time"),
            trip(routeId="backwards", arrPlandTime="202611091259"),
        ])
        result = service.timetable("TEST-DEP", "TEST-ARR")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["trips"]), 3)
        for item in result["trips"]:
            self.assertIsNone(item["arrival_at"])
            self.assertIsNone(item["duration_minutes"])
        missing = next(item for item in result["trips"] if item["route_id"] == "missing")
        self.assertIsNone(missing["fare_krw"])
        self.assertTrue(missing["grade"])

    def test_stale_departures_are_excluded_and_all_invalid_is_not_fake_empty(self):
        invalid = [trip(depPlandTime="202611081300"), trip(depPlandTime="202611101300"),
                   trip(depPlandTime="202613091300"), trip(depPlandTime="")]
        service, _ = service_with(rows=invalid + [trip(routeId="today")])
        result = service.timetable("TEST-DEP", "TEST-ARR")
        self.assertEqual(result["status"], "partial")
        self.assertEqual([item["route_id"] for item in result["trips"]], ["today"])
        service, _ = service_with(rows=invalid)
        with self.assertRaises(IntercityError) as raised:
            service.timetable("TEST-DEP", "TEST-ARR")
        self.assertEqual(raised.exception.code, "INTERCITY_SCHEDULE_RESPONSE")

    def test_empty_feed_has_booking_fallback_and_does_not_claim_no_bus(self):
        service, _ = service_with(rows=[])
        result = service.timetable("TEST-DEP", "TEST-ARR")
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["trips"], [])
        self.assertTrue(any("없다는 뜻은 아닙니다" in message for message in result["warnings"]))
        hosts = {urlsplit(link["url"]).hostname for link in result["booking_links"]}
        self.assertEqual(hosts, {"www.bustago.or.kr", "txbus.t-money.co.kr"})

    def test_multi_page_schedule_is_sorted_and_exact_duplicates_removed(self):
        start = NOW.replace(hour=9)
        records = [trip(routeId=f"TEST-{i}", depPlandTime=(start + timedelta(minutes=i)).strftime("%Y%m%d%H%M"),
                        arrPlandTime=(start + timedelta(minutes=i + 90)).strftime("%Y%m%d%H%M"))
                   for i in reversed(range(100))]
        service, requester = service_with(rows=records + [records[0]])
        result = service.timetable("TEST-DEP", "TEST-ARR")
        times = [item["departure_at"] for item in result["trips"]]
        self.assertEqual(len(times), 100)
        self.assertEqual(times, sorted(times))
        timetable_calls = [call for call in requester.call_args_list if call.args[0] == TIMETABLE_URL]
        self.assertEqual([call.kwargs["params"]["pageNo"] for call in timetable_calls], [1, 2])

    def test_midnight_rollover_rejects_old_day_and_next_request_uses_new_date(self):
        current = [NOW.replace(hour=23, minute=59, second=59)]
        query_dates = []

        def respond(url, *, params, **kwargs):
            if url == TERMINAL_URL:
                return json_response(CATALOGUE)
            self.assertEqual(url, TIMETABLE_URL)
            day = params["depPlandTime"]
            query_dates.append(day)
            if day == "20261109":
                current[0] = datetime(2026, 11, 10, 0, 0, 1, tzinfo=KST)
            return json_response([trip(depPlandTime=f"{day}1300", arrPlandTime=f"{day}1430")])

        time = lambda: current[0]
        client = client_with(Mock(side_effect=respond), now=time)
        service = IntercityService("fake-only-key", client=client, now=time)
        with self.assertRaises(IntercityError) as raised:
            service.timetable("TEST-DEP", "TEST-ARR")
        self.assertEqual(raised.exception.code, "INTERCITY_TODAY_ONLY")
        result = service.timetable("TEST-DEP", "TEST-ARR")
        self.assertEqual(result["date"], "2026-11-10")
        self.assertEqual(query_dates, ["20261109", "20261110"])
        self.assertFalse(result["from_cache"])


class IntercityRoutesTests(unittest.TestCase):
    def test_configuration_is_json_without_network_or_secrets(self):
        for key in ("", "fake-route-secret"):
            with self.subTest(configured=bool(key)):
                requester = Mock(side_effect=AssertionError("Unexpected outbound request"))
                service = IntercityService(key, client=client_with(requester, key=key), now=lambda: NOW)
                response = http_client(service).get("/api/intercity/config")
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.is_json)
                self.assertEqual(response.json["configured"], bool(key))
                self.assertEqual(response.json["today"], "2026-11-09")
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertNotIn("fake-route-secret", response.get_data(as_text=True))
                requester.assert_not_called()

    def test_missing_unknown_and_repeated_query_parameters_are_json_400(self):
        service, requester = service_with()
        client = http_client(service)
        urls = [
            "/api/intercity/config?q=unexpected",
            "/api/intercity/terminals?q=a&q=b",
            "/api/intercity/timetable",
            "/api/intercity/timetable?departure_id=TEST-DEP",
            "/api/intercity/timetable?departure_id=TEST-DEP&arrival_id=TEST-ARR&date=2026-11-09&date=2026-11-09",
            "/api/intercity/timetable?departure_id=TEST-DEP&departure_id=TEST-OTHER&arrival_id=TEST-ARR",
            "/api/intercity/timetable?departure_id=TEST-DEP&arrival_id=TEST-ARR&departure_name=forged",
        ]
        for url in urls:
            with self.subTest(url=url):
                response = client.get(url)
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.is_json)
                self.assertEqual(response.json["status"], "error")
                self.assertTrue(response.json["code"])
                self.assertTrue(response.json["error"])
                self.assertEqual(response.headers["Cache-Control"], "no-store")
        requester.assert_not_called()

    def test_missing_key_auth_and_quota_errors_are_json_503(self):
        cases = [("", None, "INTERCITY_KEY_MISSING"),
                 ("fake-route-secret", xml_error(secret="fake-route-secret"), "INTERCITY_PROVIDER_AUTH"),
                 ("fake-route-secret", json_response([], code="23"), "INTERCITY_PROVIDER_QUOTA")]
        for key, provider_response, code in cases:
            with self.subTest(code=code):
                requester = Mock(return_value=provider_response)
                service = IntercityService(key, client=client_with(requester, key=key), now=lambda: NOW)
                response = http_client(service).get("/api/intercity/terminals")
                self.assertEqual(response.status_code, 503)
                self.assertTrue(response.is_json)
                self.assertEqual(response.json["status"], "error")
                self.assertEqual(response.json["code"], code)
                self.assertTrue(response.json["booking_links"])
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertNotIn("fake-route-secret", response.get_data(as_text=True))
                if not key:
                    requester.assert_not_called()

    def test_valid_timetable_returns_provider_derived_json(self):
        service, _ = service_with()
        response = http_client(service).get(
            "/api/intercity/timetable?departure_id=TEST-DEP&arrival_id=TEST-ARR&date=2026-11-09")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.is_json)
        self.assertEqual(response.json["status"], "ok")
        self.assertEqual(response.json["departure"]["id"], "TEST-DEP")
        self.assertEqual(response.json["arrival"]["id"], "TEST-ARR")
        self.assertEqual(response.json["trips"][0]["fare_krw"], 9800)
        self.assertEqual(response.json["trips"][0]["duration_minutes"], 90)


class IntercitySettingsTests(unittest.TestCase):
    def test_specific_key_or_public_fallback_stays_private_in_repr(self):
        cases = [({}, ""), ({"PUBLIC_DATA_KEY": "fake-public"}, "fake-public"),
                 ({"PUBLIC_DATA_KEY": "fake-public", "INTERCITY_DATA_KEY": "fake-intercity"}, "fake-intercity"),
                 ({"PUBLIC_DATA_KEY": "fake-public", "INTERCITY_DATA_KEY": "  "}, "fake-public")]
        with tempfile.TemporaryDirectory() as directory:
            for environment, expected in cases:
                with self.subTest(environment=environment):
                    settings = load_settings(directory, environ=environment)
                    self.assertEqual(settings.intercity_data_key, expected)
                    self.assertNotIn("fake-public", repr(settings))
                    self.assertNotIn("fake-intercity", repr(settings))

    def test_environment_overrides_dotenv_and_empty_specific_key_uses_public_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            contents = "INTERCITY_DATA_KEY=fake-file-specific\nPUBLIC_DATA_KEY=fake-file-public\n"
            path.write_text(contents, encoding="utf-8")
            settings = load_settings(directory, environ={"INTERCITY_DATA_KEY": "fake-env-specific"})
            self.assertEqual(settings.intercity_data_key, "fake-env-specific")
            fallback = load_settings(directory, environ={"INTERCITY_DATA_KEY": "", "PUBLIC_DATA_KEY": "fake-env-public"})
            self.assertEqual(fallback.intercity_data_key, "fake-env-public")
            self.assertNotIn("fake-", repr(settings))
            self.assertNotIn("fake-", repr(fallback))
            self.assertEqual(path.read_text(encoding="utf-8"), contents)


if __name__ == "__main__":
    unittest.main()
