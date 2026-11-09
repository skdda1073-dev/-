"""등록 도착지 전체 목록·교차 식별·HTTP 계약을 합성 데이터로 검증한다.

실제 키, .env, 버스타고 서버 또는 실제 운행 정보는 사용하지 않는다.
공개 화면에서 확인된 ID 형식만 재현하며 배차 유무를 추정하지 않는다.
"""

from copy import deepcopy
from datetime import datetime
import unittest
from unittest.mock import Mock
import unicodedata
from urllib.parse import urlsplit

from flask import Flask
import requests

from services.intercity_client import IntercityClient, IntercityError, KST
from services.intercity_destinations import BustagoDestinationsClient
from services.intercity_routes import create_intercity_blueprint
from services.intercity_service import IntercityService


NOW = datetime(2030, 10, 21, 12, tzinfo=KST)
TAGO_TERMINALS = "https://apis.data.go.kr/1613000/SuburbsBusInfo/GetSuberbsBusTrminlList"
SOURCE_TERMINALS = "https://www.bustago.or.kr/newweb/kr/common/terminalListAjax.do"
SOURCE_DESTINATIONS = "https://www.bustago.or.kr/newweb/kr/common/terminalEndListAjax.do"


def tago_row(identifier, name, city="강원도"):
    return {"terminalId": identifier, "terminalNm": name, "cityName": city}


def source_row(identifier, name, city="강원", **overrides):
    row = {"TERMINAL_ID": identifier, "TERMINAL_NM": name, "TERMINAL_AREA": city,
           "RESERVATION_CHK": 1, "ONE_WAY_CD": "0"}
    row.update(overrides)
    return row


def source_response(field, rows):
    return Mock(status_code=200, json=Mock(return_value={field: deepcopy(rows)}))


def source_client(requester, clock=None):
    return BustagoDestinationsClient(requester=requester, clock=clock or (lambda: 0), now=lambda: NOW)


def service_with(tago=None, origins=None, destinations=None, key="fake-intercity-key"):
    tago = deepcopy(tago if tago is not None else [
        tago_row("TEST-CTN", "춘천"), tago_row("TEST-SEO", "서울남부", "서울특별시"),
    ])
    origins = deepcopy(origins if origins is not None else [source_row("0001", "춘천")])
    destinations = deepcopy(destinations if destinations is not None else {
        "0001": [source_row("0002", "서울남부", "서울")],
    })

    def tago_request(url, *, params, **kwargs):
        if url != TAGO_TERMINALS:
            raise AssertionError("Destination discovery must not probe timetable pairs")
        start = (params["pageNo"] - 1) * params["numOfRows"]
        rows = tago[start:start + params["numOfRows"]]
        return Mock(status_code=200, json=Mock(return_value={"response": {
            "header": {"resultCode": "00"},
            "body": {"items": {"item": rows}, "totalCount": len(tago)},
        }}))

    def source_request(url, *, params, **kwargs):
        if url == SOURCE_TERMINALS:
            return source_response("terminalList", origins)
        if url == SOURCE_DESTINATIONS:
            return source_response("terminalEndList", destinations[params["terCode"]])
        raise AssertionError("Unexpected public-provider operation")

    tago_requester = Mock(side_effect=tago_request)
    source_requester = Mock(side_effect=source_request)
    client = IntercityClient(key, requester=tago_requester, clock=lambda: 0, now=lambda: NOW)
    service = IntercityService(key, client=client, destination_client=source_client(source_requester), now=lambda: NOW)
    return service, tago_requester, source_requester


def http_client(service):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(create_intercity_blueprint(service))
    return app.test_client()


class BustagoDestinationsClientTests(unittest.TestCase):
    def test_observed_post_operations_keep_ids_and_send_no_api_credentials(self):
        requester = Mock(side_effect=[
            source_response("terminalList", [source_row("0001", "춘천")]),
            source_response("terminalEndList", [source_row("0002", "서울남부", "서울")]),
        ])
        client = source_client(requester)
        origins = client.terminals()
        result = client.destinations("0001")
        self.assertEqual(origins["terminals"], [{"source_id": "0001", "name": "춘천", "city": "강원"}])
        self.assertEqual(result["destinations"][0]["source_id"], "0002")
        self.assertEqual(result["destinations"][0]["booking_mode"], "one_way")
        self.assertEqual(result["fetched_at"], NOW.isoformat())
        for call, url, params in zip(requester.call_args_list,
                                    (SOURCE_TERMINALS, SOURCE_DESTINATIONS),
                                    ({"area": "", "searchTerminalNm": ""},
                                     {"area": "", "country": "", "terCode": "0001"})):
            self.assertEqual(call.args, (url,))
            self.assertEqual(call.kwargs, {"params": params, "data": {},
                                          "timeout": (10, 20), "allow_redirects": False})
            self.assertNotIn("serviceKey", call.kwargs["params"])

    def test_alphanumeric_source_ids_are_valid_and_preserve_case_and_leading_zeroes(self):
        rows = [source_row(identifier, "테스트 " + identifier) for identifier in ("b107", "g384", "A001", "0001")]
        requester = Mock(side_effect=[source_response("terminalList", rows),
                                      source_response("terminalEndList", rows)])
        client = source_client(requester)
        self.assertEqual([item["source_id"] for item in client.terminals()["terminals"]],
                         ["b107", "g384", "A001", "0001"])
        result = client.destinations("b107")
        self.assertEqual([item["source_id"] for item in result["destinations"]], ["b107", "g384", "A001", "0001"])
        self.assertEqual(requester.call_args.kwargs["params"]["terCode"], "b107")

    def test_invalid_source_ids_are_rejected_before_any_request(self):
        requester = Mock(side_effect=AssertionError("Unexpected outbound request"))
        client = source_client(requester)
        for identifier in (None, True, 1, "", "a" * 13, "bad/id", "a&other=1", " leading", "한글", "１２", "a_b"):
            with self.subTest(identifier=identifier):
                with self.assertRaises(IntercityError) as raised:
                    client.destinations(identifier)
                self.assertEqual(raised.exception.code, "INVALID_INTERCITY_TERMINAL")
                self.assertEqual(raised.exception.status_code, 400)
        requester.assert_not_called()

    def test_booking_metadata_describes_booking_mode_without_inventing_departures(self):
        cases = [(1, "0", "one_way"), (2, "1", "round_trip"), (0, "0", "on_site"),
                 (-1, "1", "on_site"), (True, "0", "unknown"), ("1", "0", "unknown"),
                 (1, "other", "unknown"), (1, None, "unknown"), (1, {}, "unknown"),
                 (1, [], "unknown")]
        rows = [source_row(str(index + 1), "합성 도착지", RESERVATION_CHK=check, ONE_WAY_CD=one)
                for index, (check, one, _) in enumerate(cases)]
        result = source_client(Mock(return_value=source_response("terminalEndList", rows))).destinations("0001")
        self.assertEqual([item["booking_mode"] for item in result["destinations"]], [expected for _, _, expected in cases])
        self.assertTrue(all(set(item) == {"source_id", "name", "city", "booking_mode"} for item in result["destinations"]))

    def test_distinct_source_ids_survive_duplicate_names_but_conflicting_ids_fail(self):
        first, second = source_row("0001", "동일 이름"), source_row("0002", "동일 이름")
        client = source_client(Mock(return_value=source_response("terminalEndList", [first, second, first])))
        self.assertEqual([item["source_id"] for item in client.destinations("1000")["destinations"]], ["0001", "0002"])
        conflict = source_response("terminalEndList", [first, dict(first, TERMINAL_NM="다른 이름")])
        with self.assertRaises(IntercityError) as raised:
            source_client(Mock(return_value=conflict)).destinations("1000")
        self.assertEqual(raised.exception.code, "INTERCITY_DESTINATIONS_RESPONSE")

    def test_malformed_envelopes_and_rows_are_errors_while_empty_array_is_valid(self):
        payloads = [None, [], {}, {"terminalList": None}, {"terminalList": {}}, {"terminalList": [False]}]
        for field, value in (("TERMINAL_ID", None), ("TERMINAL_ID", 123), ("TERMINAL_ID", "bad/id"),
                             ("TERMINAL_NM", "  "), ("TERMINAL_NM", "x" * 201),
                             ("TERMINAL_AREA", None), ("TERMINAL_AREA", "")):
            payloads.append({"terminalList": [dict(source_row("1000", "테스트"), **{field: value})]})
        for payload in payloads:
            with self.subTest(payload=payload):
                response = Mock(status_code=200, json=Mock(return_value=payload))
                with self.assertRaises(IntercityError) as raised:
                    source_client(Mock(return_value=response)).terminals()
                self.assertEqual(raised.exception.code, "INTERCITY_DESTINATIONS_RESPONSE")
        result = source_client(Mock(return_value=source_response("terminalEndList", []))).destinations("1000")
        self.assertEqual(result["destinations"], [])

    def test_transport_and_parse_failures_have_safe_distinct_error_codes(self):
        secret = "fake-sensitive-provider-detail"
        cases = [
            (requests.Timeout(secret), "INTERCITY_DESTINATIONS_TIMEOUT", 504),
            (requests.ConnectionError(secret), "INTERCITY_DESTINATIONS_NETWORK", 502),
            (Mock(status_code=403, text=secret), "INTERCITY_DESTINATIONS_UNAVAILABLE", 502),
            (Mock(status_code=302, text=secret), "INTERCITY_DESTINATIONS_UNAVAILABLE", 502),
            (Mock(status_code=200, json=Mock(side_effect=ValueError(secret))), "INTERCITY_DESTINATIONS_RESPONSE", 502),
        ]
        for outcome, code, status in cases:
            with self.subTest(code=code):
                requester = Mock(side_effect=outcome) if isinstance(outcome, Exception) else Mock(return_value=outcome)
                with self.assertRaises(IntercityError) as raised:
                    source_client(requester).terminals()
                self.assertEqual((raised.exception.code, raised.exception.status_code), (code, status))
                self.assertNotIn(secret, str(raised.exception))

    def test_six_hour_cache_is_separate_per_origin_and_returns_independent_values(self):
        elapsed = [0]
        requester = Mock(side_effect=[
            source_response("terminalEndList", [source_row("1001", "첫 출발지의 도착지")]),
            source_response("terminalEndList", [source_row("1002", "다른 출발지의 도착지")]),
            source_response("terminalEndList", [source_row("1003", "갱신된 도착지")]),
        ])
        client = source_client(requester, clock=lambda: elapsed[0])
        first = client.destinations("0001")
        first["destinations"][0]["name"] = "caller mutation"
        second = client.destinations("0002")
        self.assertEqual(second["destinations"][0]["source_id"], "1002")
        elapsed[0] = 6 * 3600 - 1
        cached = client.destinations("0001")
        self.assertTrue(cached["from_cache"])
        self.assertEqual(cached["destinations"][0]["name"], "첫 출발지의 도착지")
        cached["destinations"].clear()
        self.assertEqual(len(client.destinations("0001")["destinations"]), 1)
        elapsed[0] = 6 * 3600
        refreshed = client.destinations("0001")
        self.assertFalse(refreshed["from_cache"])
        self.assertEqual(refreshed["destinations"][0]["source_id"], "1003")
        self.assertEqual([call.kwargs["params"]["terCode"] for call in requester.call_args_list], ["0001", "0002", "0001"])

    def test_cache_is_bounded_and_recently_used_origin_is_retained(self):
        requester = Mock(return_value=source_response("terminalEndList", []))
        client = source_client(requester)
        for identifier in range(1, 65):
            client.destinations(str(identifier))
        self.assertTrue(client.destinations("1")["from_cache"])
        client.destinations("65")
        self.assertTrue(client.destinations("1")["from_cache"])
        self.assertFalse(client.destinations("2")["from_cache"])
        self.assertEqual(requester.call_count, 66)


class IntercityDestinationsServiceTests(unittest.TestCase):
    def test_all_118_registered_destinations_are_returned_without_pairwise_timetable_probes(self):
        names = [f"합성 도착지 {index:03}" for index in range(118)]
        catalogue = [tago_row("TEST-CTN", "춘천")] + [tago_row(f"TEST-{i}", name, "서울특별시") for i, name in enumerate(names)]
        destinations = [source_row(str(10000 + i), name, "서울", RESERVATION_CHK=0 if i % 2 else 1)
                        for i, name in enumerate(names)]
        service, tago_requester, requester = service_with(tago=catalogue, destinations={"0001": destinations})
        result = service.destinations("TEST-CTN")
        self.assertEqual(result["status"], "ok")
        self.assertEqual((result["total_count"], result["matched_count"]), (118, 118))
        self.assertEqual(len(result["destinations"]), 118)
        self.assertEqual({item["id"] for item in result["destinations"]}, {f"TEST-{i}" for i in range(118)})
        self.assertEqual({item["booking_mode"] for item in result["destinations"]}, {"one_way", "on_site"})
        self.assertEqual(result["source"]["name"], "버스타고 등록 도착지")
        self.assertEqual(urlsplit(result["source"]["url"]).hostname, "www.bustago.or.kr")
        self.assertTrue(any("등록" in warning and "오늘" in warning for warning in result["warnings"]))
        self.assertEqual(tago_requester.call_count, 2, 'Only the 119-item catalogue is paginated')
        self.assertEqual([call.args[0] for call in requester.call_args_list], [SOURCE_TERMINALS, SOURCE_DESTINATIONS])
        self.assertNotIn("fake-intercity-key", repr(requester.call_args_list))

    def test_nfc_whitespace_case_and_recognized_province_aliases_map_exact_identities(self):
        catalogue = [tago_row("TEST-CTN", "춘천", "강원특별자치도"),
                     tago_row("TEST-SEO", "서울 Terminal", "서울특별시"),
                     tago_row("TEST-JB", "합성 전북", "전북특별자치도")]
        origins = [source_row("b107", unicodedata.normalize("NFD", "춘천"), "강원")]
        destinations = {"b107": [
            source_row("0002", "  " + unicodedata.normalize("NFD", "서울") + "   TERMINAL  ", "서울"),
            source_row("0003", "합성 전북", "전라북도"),
        ]}
        service, _, requester = service_with(tago=catalogue, origins=origins, destinations=destinations)
        result = service.destinations("TEST-CTN")
        self.assertEqual(result["status"], "ok")
        self.assertEqual({item["id"] for item in result["destinations"]}, {"TEST-SEO", "TEST-JB"})
        self.assertEqual(requester.call_args.kwargs["params"]["terCode"], "b107")

    def test_unmatched_qualifiers_digits_suffixes_and_regions_are_not_guessed(self):
        catalogue = [tago_row("TEST-CTN", "춘천"), tago_row("TEST-SEO", "서울남부", "서울특별시"),
                     tago_row("TEST-AIR", "인천공항1터미널", "인천광역시"),
                     tago_row("TEST-WON", "원주"), tago_row("TEST-GWJ", "광주", "광주광역시")]
        destinations = [source_row("1", "서울남부터미널", "서울"), source_row("2", "인천공항2터미널", "인천"),
                        source_row("3", "원주(고속)", "강원"), source_row("4", "광주", "경기")]
        service, _, _ = service_with(tago=catalogue, destinations={"0001": destinations})
        result = service.destinations("TEST-CTN")
        self.assertEqual(result["status"], "partial")
        self.assertEqual((result["total_count"], result["matched_count"]), (4, 0))
        self.assertEqual({item["name"] for item in result["destinations"]}, {row["TERMINAL_NM"] for row in destinations})
        self.assertTrue(all(item["id"] is None and item["match_status"] == "unmapped" for item in result["destinations"]))

    def test_duplicate_tago_or_source_identities_remain_ambiguous_and_are_not_dropped(self):
        catalogue = [tago_row("TEST-CTN", "춘천"), tago_row("TEST-A1", "중복 도착지", "서울특별시"),
                     tago_row("TEST-A2", "중복 도착지", "서울특별시"),
                     tago_row("TEST-B", "소스 중복", "경기도"), tago_row("TEST-C", "유일 도착지", "충청북도")]
        destinations = [source_row("1", "중복 도착지", "서울"), source_row("2", "소스 중복", "경기"),
                        source_row("3", "소스  중복", "경기도"), source_row("4", "유일 도착지", "충북"),
                        source_row("5", "목록에 없는 도착지", "강원")]
        service, _, _ = service_with(tago=catalogue, destinations={"0001": destinations})
        result = service.destinations("TEST-CTN")
        by_source = {item["source_id"]: item for item in result["destinations"]}
        self.assertEqual((result["total_count"], result["matched_count"]), (5, 1))
        self.assertEqual(result["status"], "partial")
        for identifier in ("1", "2", "3"):
            self.assertEqual(by_source[identifier]["match_status"], "ambiguous")
            self.assertIsNone(by_source[identifier]["id"])
        self.assertEqual(by_source["4"]["id"], "TEST-C")
        self.assertEqual(by_source["5"]["match_status"], "unmapped")
        self.assertIsNone(by_source["5"]["id"])

    def test_same_name_in_distinct_known_regions_can_be_matched_without_ambiguity(self):
        catalogue = [tago_row("TEST-CTN", "춘천"), tago_row("TEST-SEO", "공통 이름", "서울특별시"),
                     tago_row("TEST-GGI", "공통 이름", "경기도")]
        destinations = [source_row("1", "공통 이름", "서울"), source_row("2", "공통 이름", "경기")]
        service, _, _ = service_with(tago=catalogue, destinations={"0001": destinations})
        result = service.destinations("TEST-CTN")
        self.assertEqual(result["status"], "ok")
        self.assertEqual({item["source_id"]: item["id"] for item in result["destinations"]}, {"1": "TEST-SEO", "2": "TEST-GGI"})

    def test_unmatched_or_ambiguous_origin_never_guesses_a_provider_origin(self):
        base = [tago_row("TEST-CTN", "춘천"), tago_row("TEST-SEO", "서울남부", "서울특별시")]
        cases = [
            (base, [source_row("1", "남춘천")]),
            (base, [source_row("1", "춘천", "경기")]),
            (base, [source_row("1", "춘천"), source_row("2", "춘천", "강원특별자치도")]),
            (base + [tago_row("TEST-OTHER", "춘천")], [source_row("1", "춘천")]),
        ]
        for catalogue, origins in cases:
            with self.subTest(origins=origins):
                service, _, requester = service_with(tago=catalogue, origins=origins)
                with self.assertRaises(IntercityError) as raised:
                    service.destinations("TEST-CTN")
                self.assertEqual((raised.exception.code, raised.exception.status_code), ("INTERCITY_DEPARTURE_UNMAPPED", 503))
                self.assertEqual([call.args[0] for call in requester.call_args_list], [SOURCE_TERMINALS])

    def test_invalid_or_unknown_tago_origin_is_rejected_before_bustago_request(self):
        service, tago_requester, requester = service_with()
        for identifier in (None, True, "", "../origin", "x" * 65):
            with self.subTest(identifier=identifier):
                with self.assertRaises(IntercityError) as raised:
                    service.destinations(identifier)
                self.assertEqual(raised.exception.status_code, 400)
        tago_requester.assert_not_called()
        with self.assertRaises(IntercityError) as raised:
            service.destinations("TEST-UNKNOWN")
        self.assertEqual(raised.exception.code, "UNKNOWN_INTERCITY_TERMINAL")
        requester.assert_not_called()

    def test_missing_tago_key_fetches_neither_catalogue_nor_public_destinations(self):
        service, tago_requester, requester = service_with(key="")
        with self.assertRaises(IntercityError) as raised:
            service.destinations("TEST-CTN")
        self.assertEqual((raised.exception.code, raised.exception.status_code), ("INTERCITY_KEY_MISSING", 503))
        tago_requester.assert_not_called()
        requester.assert_not_called()

    def test_empty_registered_list_is_distinct_from_provider_failure(self):
        service, _, _ = service_with(destinations={"0001": []})
        result = service.destinations("TEST-CTN")
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["destinations"], [])
        self.assertEqual((result["total_count"], result["matched_count"]), (0, 0))
        failed, _, requester = service_with()
        requester.side_effect = [source_response("terminalList", [source_row("0001", "춘천")]),
                                 requests.Timeout("fake-private-provider-url")]
        with self.assertRaises(IntercityError) as raised:
            failed.destinations("TEST-CTN")
        self.assertEqual(raised.exception.code, "INTERCITY_DESTINATIONS_TIMEOUT")

    def test_origin_specific_lists_and_cache_do_not_infer_reverse_routes(self):
        catalogue = [tago_row("TEST-A", "춘천"), tago_row("TEST-B", "합성 출발지", "서울특별시")]
        origins = [source_row("0001", "춘천"), source_row("b107", "합성 출발지", "서울")]
        destinations = {"0001": [source_row("b107", "합성 출발지", "서울")], "b107": []}
        service, _, requester = service_with(tago=catalogue, origins=origins, destinations=destinations)
        forward = service.destinations("TEST-A")
        backward = service.destinations("TEST-B")
        again = service.destinations("TEST-A")
        self.assertEqual(forward["destinations"][0]["id"], "TEST-B")
        self.assertEqual(backward["status"], "empty")
        self.assertEqual(backward["departure"]["id"], "TEST-B")
        self.assertTrue(again["from_cache"])
        self.assertEqual(again["departure"]["id"], "TEST-A")
        calls = [call.kwargs["params"]["terCode"] for call in requester.call_args_list if call.args[0] == SOURCE_DESTINATIONS]
        self.assertEqual(calls, ["0001", "b107"])


class IntercityDestinationsRoutesTests(unittest.TestCase):
    def test_registered_list_json_preserves_unmapped_destinations_and_source_scope(self):
        service, _, _ = service_with(destinations={"0001": [source_row("0002", "서울남부", "서울"),
                                                               source_row("g384", "미연결 도착지", "강원")]})
        response = http_client(service).get("/api/intercity/destinations?departure_id=TEST-CTN")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.is_json)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.json["status"], "partial")
        self.assertEqual(response.json["departure"]["id"], "TEST-CTN")
        self.assertEqual(response.json["today"], "2030-10-21")
        self.assertEqual(response.json["total_count"], 2)
        unmapped = next(item for item in response.json["destinations"] if item["source_id"] == "g384")
        self.assertIsNone(unmapped["id"])
        self.assertEqual(unmapped["match_status"], "unmapped")
        self.assertEqual(response.json["source"]["name"], "버스타고 등록 도착지")

    def test_missing_repeated_and_unknown_arguments_are_json_400_without_fetching(self):
        service, tago_requester, requester = service_with()
        client = http_client(service)
        for suffix in ("", "?departure_id=", "?departure_id=../bad", "?departure_id=TEST-CTN&departure_id=TEST-CTN",
                       "?departure_id=TEST-CTN&arrival_id=TEST-SEO", "?departure_id=TEST-CTN&q=서울"):
            with self.subTest(suffix=suffix):
                response = client.get("/api/intercity/destinations" + suffix)
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.is_json)
                self.assertEqual(response.json["status"], "error")
                self.assertEqual(response.headers["Cache-Control"], "no-store")
        tago_requester.assert_not_called()
        requester.assert_not_called()

    def test_missing_key_unmapped_origin_and_provider_failures_keep_json_status_codes(self):
        missing, _, _ = service_with(key="")
        unmapped, _, _ = service_with(origins=[source_row("0001", "춘천", "경기도")])
        network, _, network_requester = service_with()
        network_requester.side_effect = requests.ConnectionError("fake-private-url")
        timeout, _, timeout_requester = service_with()
        timeout_requester.side_effect = requests.Timeout("fake-private-url")
        for service, status, code in ((missing, 503, "INTERCITY_KEY_MISSING"),
                                      (unmapped, 503, "INTERCITY_DEPARTURE_UNMAPPED"),
                                      (network, 502, "INTERCITY_DESTINATIONS_NETWORK"),
                                      (timeout, 504, "INTERCITY_DESTINATIONS_TIMEOUT")):
            with self.subTest(code=code):
                response = http_client(service).get("/api/intercity/destinations?departure_id=TEST-CTN")
                self.assertEqual(response.status_code, status)
                self.assertTrue(response.is_json)
                self.assertEqual(response.json["status"], "error")
                self.assertEqual(response.json["code"], code)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertNotIn("fake-private-url", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
