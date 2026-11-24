"""공개 시간표 요청 계약과 실패·캐시 처리를 합성 응답으로 검증한다.

실제 서버, API 키, .env 또는 실제 배차를 사용하지 않는다.
"""

from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import Mock, patch

import requests

from services.intercity_bustago import BustagoTimetableClient
from services.intercity_client import IntercityError, KST


QUERY_DATE = "20301021"
NOW = datetime(2030, 10, 21, 4, 30, tzinfo=timezone.utc)
URL = "https://www.bustago.or.kr/newweb/kr/ticket/ticketListJson3.do"
PAGE_URL = "https://www.bustago.or.kr/newweb/kr/ticket/ticket.do"


def row(departure="2037", arrival="0001", day=QUERY_DATE, **changes):
    item = {"sterCode": departure, "eterCode": arrival, "DEP_DATE": day,
            "BUS_ORDER_DT": day, "DEP_TIME": "0600", "FARE0": 12300,
            "DIST_TIME": 75, "BUS_TYPE_NM": "합성 등급", "TRANSP_BIZR_ABBR_NM": "합성 회사",
            "BUS_ROUTE_ID": "TEST-ROUTE", "TOT_SEAT_CNT": "40"}
    item.update(changes)
    return item


def response(rows=None, *, payload=None, status=200):
    body = payload if payload is not None else {
        "ticketSingleList": deepcopy(rows if rows is not None else [row()]),
        "ticketDoubleList": [], "language": "kr",
    }
    return Mock(status_code=status, json=Mock(return_value=body))


def client(requester, clock=None):
    return BustagoTimetableClient(requester=requester, clock=clock or (lambda: 0), now=lambda: NOW)


class BustagoTimetableClientTests(unittest.TestCase):
    def test_observed_single_post_contract_has_no_credentials_or_session_bootstrap(self):
        requester = Mock(return_value=response())
        result = client(requester).read("2037", "0001", QUERY_DATE)
        requester.assert_called_once_with(
            URL,
            data={"startType": "S", "orderDate": QUERY_DATE, "orderBackDate": "",
                  "depTerId": "2037", "arrTerId": "0001", "depTime": "0000", "arrTime": "0000",
                  "goBusGrade": "", "goBackBusGrade": ""},
            headers={"Referer": PAGE_URL, "X-Requested-With": "XMLHttpRequest"},
            timeout=(10, 20), allow_redirects=False,
        )
        self.assertEqual(result, {"rows": [row()], "fetched_at": NOW.astimezone(KST).isoformat(),
                                  "from_cache": False})

    def test_default_requester_uses_post_without_reading_any_key(self):
        with patch("services.intercity_bustago.requests.post", return_value=response()) as post:
            result = BustagoTimetableClient(now=lambda: NOW).read("2037", "0001", QUERY_DATE)
        self.assertEqual(len(result["rows"]), 1)
        post.assert_called_once()
        self.assertNotIn("params", post.call_args.kwargs)
        self.assertNotIn("cookies", post.call_args.kwargs)
        self.assertNotIn("Authorization", post.call_args.kwargs["headers"])

    def test_alphanumeric_ids_keep_case_and_leading_zeroes(self):
        requester = Mock(return_value=response([row("b107", "A001")]))
        result = client(requester).read("b107", "A001", QUERY_DATE)
        self.assertEqual(result["rows"][0]["sterCode"], "b107")
        self.assertEqual(result["rows"][0]["eterCode"], "A001")
        self.assertEqual(requester.call_args.kwargs["data"]["arrTerId"], "A001")

    def test_invalid_ids_are_rejected_in_either_position_without_requests(self):
        requester = Mock(side_effect=AssertionError("Unexpected network request"))
        source = client(requester)
        invalid = (None, True, 1, "", "a" * 13, "../2037", "a&b=1", " 2037", "2037\n", "１２", "a_b")
        for identifier in invalid:
            for departure, arrival in ((identifier, "0001"), ("2037", identifier)):
                with self.subTest(departure=departure, arrival=arrival):
                    with self.assertRaises(IntercityError) as caught:
                        source.read(departure, arrival, QUERY_DATE)
                    self.assertEqual((caught.exception.code, caught.exception.status_code),
                                     ("INVALID_INTERCITY_TERMINAL", 400))
        requester.assert_not_called()

    def test_same_terminal_is_rejected_before_request(self):
        requester = Mock()
        with self.assertRaises(IntercityError) as caught:
            client(requester).read("0001", "0001", QUERY_DATE)
        self.assertEqual((caught.exception.code, caught.exception.status_code),
                         ("SAME_INTERCITY_TERMINAL", 400))
        requester.assert_not_called()

    def test_invalid_dates_are_rejected_without_request(self):
        requester = Mock()
        source = client(requester)
        for day in (None, True, 20301021, "", "2030-10-21", "2030102", "203010210", "２０３０１０２１",
                    "20301021\n", "00000101", "20300229", "20301131", "20301301"):
            with self.subTest(day=day):
                with self.assertRaises(IntercityError) as caught:
                    source.read("2037", "0001", day)
                self.assertEqual((caught.exception.code, caught.exception.status_code),
                                 ("INVALID_INTERCITY_DATE", 400))
        requester.assert_not_called()

    def test_valid_leap_date_is_not_restricted_to_client_clock_today(self):
        requester = Mock(return_value=response([row(day="20320229")]))
        result = client(requester).read("2037", "0001", "20320229")
        self.assertEqual(result["rows"][0]["DEP_DATE"], "20320229")

    def test_raw_rows_remain_unfiltered_for_service_time_and_fare_interpretation(self):
        rows = [row(DEP_TIME="2359", TOT_SEAT_CNT="0", FARE0=None, DIST_TIME=0),
                row(DEP_TIME="0700", TRANSP_BIZR_ABBR_NM="회사 A"),
                row(DEP_TIME="0700", TRANSP_BIZR_ABBR_NM="회사 B")]
        result = client(Mock(return_value=response(rows))).read("2037", "0001", QUERY_DATE)
        self.assertEqual(result["rows"], rows)

    def test_empty_success_is_cached_and_distinct_from_failure(self):
        requester = Mock(return_value=response([]))
        source = client(requester)
        first = source.read("2037", "0001", QUERY_DATE)
        second = source.read("2037", "0001", QUERY_DATE)
        self.assertEqual(first["rows"], [])
        self.assertFalse(first["from_cache"])
        self.assertEqual(second["rows"], [])
        self.assertTrue(second["from_cache"])
        requester.assert_called_once()

    def test_invalid_envelopes_and_wrong_list_types_are_not_empty_results(self):
        malformed = [None, [], "provider failure", {}, {"ticketSingleList": None},
                     {"ticketSingleList": {}}, {"ticketSingleList": ""}, {"ticketSingleList": False},
                     {"ticketSingleList": 0}, {"ticketSingleList": [None]},
                     {"ticketSingleList": [], "ticketDoubleList": {}},
                     {"ticketSingleList": [], "language": {}}]
        for payload in malformed:
            with self.subTest(payload=payload):
                requester = Mock(return_value=Mock(status_code=200, json=Mock(return_value=payload)))
                with self.assertRaises(IntercityError) as caught:
                    client(requester).read("2037", "0001", QUERY_DATE)
                self.assertEqual((caught.exception.code, caught.exception.status_code),
                                 ("INTERCITY_BUSTAGO_RESPONSE", 502))

    def test_error_envelopes_cannot_disguise_failure_as_an_empty_timetable(self):
        for error in ({"error": "private provider details"}, {"message": "provider URL"},
                      {"resultCode": "500"}, {"success": False}):
            with self.subTest(field=next(iter(error))):
                requester = Mock(return_value=response(payload={"ticketSingleList": [], **error}))
                with self.assertRaises(IntercityError) as caught:
                    client(requester).read("2037", "0001", QUERY_DATE)
                self.assertEqual(caught.exception.code, "INTERCITY_BUSTAGO_RESPONSE")
                self.assertNotIn(next(iter(error.values())).__str__(), str(caught.exception))

    def test_any_wrong_or_missing_route_identity_rejects_the_entire_response(self):
        invalid = [row(sterCode="0001"), row(eterCode="2037"), row(eterCode=1), row(sterCode=None),
                   row(eterCode="001"), row(DEP_DATE="20301022"), row(DEP_DATE=20301021),
                   row(BUS_ORDER_DT="20301020")]
        for field in ("sterCode", "eterCode", "DEP_DATE"):
            missing = row()
            del missing[field]
            invalid.append(missing)
        for bad in invalid:
            with self.subTest(row=bad):
                requester = Mock(return_value=response([row(), bad]))
                with self.assertRaises(IntercityError) as caught:
                    client(requester).read("2037", "0001", QUERY_DATE)
                self.assertEqual(caught.exception.code, "INTERCITY_BUSTAGO_RESPONSE")

    def test_optional_return_list_and_booking_date_may_be_absent(self):
        item = row()
        del item["BUS_ORDER_DT"]
        for payload in ({"ticketSingleList": [item]},
                        {"ticketSingleList": [item], "ticketDoubleList": None}):
            with self.subTest(payload=payload):
                result = client(Mock(return_value=response(payload=payload))).read("2037", "0001", QUERY_DATE)
                self.assertEqual(result["rows"], [item])

    def test_oversized_response_is_rejected_instead_of_truncated(self):
        requester = Mock(return_value=response([row()] * (BustagoTimetableClient.MAX_ROWS + 1)))
        with self.assertRaises(IntercityError) as caught:
            client(requester).read("2037", "0001", QUERY_DATE)
        self.assertEqual(caught.exception.code, "INTERCITY_BUSTAGO_RESPONSE")

    def test_network_errors_are_controlled_and_do_not_expose_provider_text(self):
        private = "https://provider.invalid/private?token=secret-value"
        for failure, code, status in [
            (requests.Timeout(private), "INTERCITY_BUSTAGO_TIMEOUT", 504),
            (requests.ConnectionError(private), "INTERCITY_BUSTAGO_NETWORK", 502),
        ]:
            with self.subTest(code=code):
                with self.assertRaises(IntercityError) as caught:
                    client(Mock(side_effect=failure)).read("2037", "0001", QUERY_DATE)
                self.assertEqual((caught.exception.code, caught.exception.status_code), (code, status))
                self.assertNotIn("secret-value", str(caught.exception))
                self.assertNotIn("provider.invalid", str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)

    def test_http_errors_and_redirects_are_not_parsed_or_cached(self):
        for status in (301, 302, 401, 403, 429, 500, 503):
            with self.subTest(status=status):
                failed = response(status=status)
                requester = Mock(side_effect=[failed, response()])
                source = client(requester)
                with self.assertRaises(IntercityError) as caught:
                    source.read("2037", "0001", QUERY_DATE)
                self.assertEqual((caught.exception.code, caught.exception.status_code),
                                 ("INTERCITY_BUSTAGO_UNAVAILABLE", 502))
                failed.json.assert_not_called()
                self.assertFalse(source.read("2037", "0001", QUERY_DATE)["from_cache"])
                self.assertEqual(requester.call_count, 2)

    def test_json_decode_errors_are_sanitized_and_not_cached(self):
        failed = Mock(status_code=200, json=Mock(side_effect=ValueError("secret-value in raw provider body")))
        requester = Mock(side_effect=[failed, response()])
        source = client(requester)
        with self.assertRaises(IntercityError) as caught:
            source.read("2037", "0001", QUERY_DATE)
        self.assertEqual(caught.exception.code, "INTERCITY_BUSTAGO_RESPONSE")
        self.assertNotIn("secret-value", str(caught.exception))
        self.assertFalse(source.read("2037", "0001", QUERY_DATE)["from_cache"])
        self.assertEqual(requester.call_count, 2)

    def test_route_validation_failure_is_not_cached(self):
        requester = Mock(side_effect=[response([row(eterCode="WRONG")]), response()])
        source = client(requester)
        with self.assertRaises(IntercityError):
            source.read("2037", "0001", QUERY_DATE)
        self.assertFalse(source.read("2037", "0001", QUERY_DATE)["from_cache"])
        self.assertEqual(requester.call_count, 2)

    def test_cached_rows_and_initial_rows_are_deep_copies(self):
        raw = row(extra={"items": ["original"]})
        payload = {"ticketSingleList": [raw]}
        requester = Mock(return_value=response(payload=payload))
        source = client(requester)
        first = source.read("2037", "0001", QUERY_DATE)
        first["rows"][0]["extra"]["items"].append("caller edit")
        raw["extra"]["items"].append("response edit")
        second = source.read("2037", "0001", QUERY_DATE)
        self.assertEqual(second["rows"][0]["extra"]["items"], ["original"])
        second["rows"].clear()
        self.assertEqual(len(source.read("2037", "0001", QUERY_DATE)["rows"]), 1)
        requester.assert_called_once()

    def test_cache_expires_at_sixty_seconds_and_refreshes_timestamp(self):
        elapsed = [0]
        requester = Mock(return_value=response())
        source = client(requester, clock=lambda: elapsed[0])
        original = source.read("2037", "0001", QUERY_DATE)
        elapsed[0] = 59.9
        self.assertTrue(source.read("2037", "0001", QUERY_DATE)["from_cache"])
        source.now = lambda: datetime(2030, 10, 21, 4, 31, tzinfo=timezone.utc)
        elapsed[0] = 60
        refreshed = source.read("2037", "0001", QUERY_DATE)
        self.assertFalse(refreshed["from_cache"])
        self.assertNotEqual(original["fetched_at"], refreshed["fetched_at"])
        self.assertEqual(requester.call_count, 2)

    def test_cache_is_separated_by_both_direction_and_date(self):
        def answer(url, *, data, **kwargs):
            return response([row(data["depTerId"], data["arrTerId"], data["orderDate"])])
        requester = Mock(side_effect=answer)
        source = client(requester)
        pairs = [("2037", "0001", QUERY_DATE), ("0001", "2037", QUERY_DATE),
                 ("2037", "0001", "20301022"), ("2037", "2027", QUERY_DATE)]
        for departure, arrival, day in pairs:
            self.assertFalse(source.read(departure, arrival, day)["from_cache"])
        for departure, arrival, day in pairs:
            cached = source.read(departure, arrival, day)
            self.assertTrue(cached["from_cache"])
            self.assertEqual((cached["rows"][0]["sterCode"], cached["rows"][0]["eterCode"],
                              cached["rows"][0]["DEP_DATE"]), (departure, arrival, day))
        self.assertEqual(requester.call_count, 4)

    def test_cache_is_bounded_to_128_and_evicts_the_least_recently_used_pair(self):
        def answer(url, *, data, **kwargs):
            return response([row(data["depTerId"], data["arrTerId"], data["orderDate"])])
        requester = Mock(side_effect=answer)
        source = client(requester)
        for index in range(128):
            source.read("2037", f"T{index}", QUERY_DATE)
        self.assertTrue(source.read("2037", "T0", QUERY_DATE)["from_cache"])
        source.read("2037", "T128", QUERY_DATE)
        self.assertFalse(source.read("2037", "T1", QUERY_DATE)["from_cache"])
        self.assertTrue(source.read("2037", "T0", QUERY_DATE)["from_cache"])
        self.assertEqual(requester.call_count, 130)


if __name__ == "__main__":
    unittest.main()
