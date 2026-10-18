"""백엔드 점검 도구가 기본 실행에서 외부 API를 호출하지 않는지 확인한다."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from flask import Flask, jsonify, request


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_backend.py"
SPEC = importlib.util.spec_from_file_location("backend_probe", SCRIPT)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class BackendProbeTests(unittest.TestCase):
    def application(self, *, tmap=True, public=True, csv_ready=True, places=None, route_body=None, route_status=200):
        app = Flask(__name__)
        called = []

        @app.get("/health")
        def health():
            called.append("health")
            return jsonify({
                "status": "ok" if tmap and csv_ready else "degraded",
                "configured": {"tmap_app_key": tmap, "public_data_key": public, "naver_map_client_id": False},
                "checks": {"csv": {"status": "ok" if csv_ready else "error", "route_count": 223},
                           "campus": {"status": "ok"}},
                "external_apis_verified": False,
            })

        @app.get("/search_poi")
        def search():
            called.append("search")
            self.assertEqual(request.args["q"], "춘천역")
            return jsonify(places if places is not None else [{"name": "춘천역", "lat": 37.88, "lon": 127.72}])

        @app.post("/get_route_info")
        def route():
            called.append("route")
            self.assertEqual(request.json["campus"], "hallym")
            self.assertIn(request.json["location"], {"공학관", "본관"})
            self.assertEqual(request.json["dest_name"], "춘천역")
            return jsonify(route_body if route_body is not None else {"journeys": [{"legs": []}]}), route_status

        return SimpleNamespace(
            app=app, called=called,
            CAMPUS_BUILDINGS={"hallym": {"공학관": (37.881, 127.74), "본관": (37.8835, 127.7384)}},
            transit_client=Mock(arrivals=Mock(return_value=[])),
        )

    def check(self, application, live=False):
        lines = []
        code = probe.run_checks(application, live=live, output=lines.append)
        return code, "\n".join(lines)

    def test_default_only_checks_local_health(self):
        application = self.application()
        code, output = self.check(application)
        self.assertEqual(code, 0)
        self.assertEqual(application.called, ["health"])
        application.transit_client.arrivals.assert_not_called()
        self.assertIn("외부 API 호출 없음", output)
        self.assertIn("성공을 뜻하지 않습니다", output)

    def test_missing_tmap_is_nonzero_and_never_calls_external_services(self):
        application = self.application(tmap=False)
        code, _ = self.check(application, live=True)
        self.assertEqual(code, 2)
        self.assertEqual(application.called, ["health"])
        application.transit_client.arrivals.assert_not_called()

    def test_csv_failure_is_readiness_failure(self):
        code, output = self.check(self.application(csv_ready=False))
        self.assertEqual(code, 2)
        self.assertIn("준비 미완료", output)

    def test_live_success_does_not_claim_empty_arrivals_are_verified(self):
        application = self.application()
        code, output = self.check(application, live=True)
        self.assertEqual(code, 0)
        self.assertEqual(application.called, ["health", "search", "route"])
        application.transit_client.arrivals.assert_called_once_with("CCB250002127")
        self.assertIn("현재 도착자료 없음", output)
        self.assertIn("실제 도착값은 검증하지 못했습니다", output)

    def test_missing_public_key_is_optional_for_csv_routing(self):
        application = self.application(public=False)
        code, output = self.check(application, live=True)
        self.assertEqual(code, 0)
        application.transit_client.arrivals.assert_not_called()
        self.assertIn("CSV 경로 후보는 조회할 수 있습니다", output)

    def test_empty_search_is_not_success(self):
        application = self.application(places=[])
        code, output = self.check(application, live=True)
        self.assertEqual(code, 3)
        self.assertEqual(application.called, ["health", "search"])
        self.assertIn("SEARCH_EMPTY", output)

    def test_invalid_search_coords_do_not_reach_routing(self):
        application = self.application(places=[{"name": "춘천역", "lat": True, "lon": 127.7}])
        code, _ = self.check(application, live=True)
        self.assertEqual(code, 3)
        self.assertNotIn("route", application.called)

    def test_no_journeys_is_distinct_from_successful_route_search(self):
        code, output = self.check(self.application(route_body={"journeys": []}), live=True)
        self.assertEqual(code, 3)
        self.assertIn("NO_JOURNEYS", output)

    def test_raw_errors_and_unknown_error_codes_are_never_printed(self):
        application = self.application(route_body={"error": "secret-key-body", "code": "secret-key-code"}, route_status=502)
        application.transit_client.arrivals.side_effect = RuntimeError("secret-key-exception")
        code, output = self.check(application, live=True)
        self.assertEqual(code, 3)
        self.assertIn("HTTP_502", output)
        self.assertNotIn("secret-key", output)

    def test_logger_state_is_restored(self):
        application = self.application()
        application.app.logger.disabled = False
        self.check(application)
        self.assertFalse(application.app.logger.disabled)

    def test_configured_arrival_failure_is_not_a_successful_live_check(self):
        application = self.application()
        application.transit_client.arrivals.side_effect = RuntimeError("fake-private-key")
        code, output = self.check(application, live=True)
        self.assertEqual(code, 4)
        self.assertIn("부분 성공", output)
        self.assertNotIn("fake-private-key", output)


if __name__ == "__main__":
    unittest.main()
