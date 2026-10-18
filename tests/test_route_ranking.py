"""기존 주 점수는 유지하고 동점 후보만 번호 검증·ID 순으로 정렬한다."""

import unittest

from services.route_planner import plan_journeys


class RouteRankingTests(unittest.TestCase):
    def setUp(self):
        self.board = {"nodeid": "A", "nodenm": "출발 정류장", "nodeord": 1,
                      "gpslati": 37.88, "gpslong": 127.73, "updowncd": None}
        self.alight = {"nodeid": "B", "nodenm": "도착 정류장", "nodeord": 2,
                       "gpslati": 37.88, "gpslong": 127.74, "updowncd": None}
        self.origin = {"name": "출발", "lat": 37.879, "lon": 127.73}
        self.destination = {"name": "목적지", "lat": 37.881, "lon": 127.74}

    def route(self, verified=True, detour=False):
        stops = [self.board, self.alight]
        if detour:
            stops = [self.board,
                     {"nodeid": "C", "nodenm": "우회 정류장", "nodeord": 2,
                      "gpslati": 37.95, "gpslong": 127.80, "updowncd": None},
                     {**self.alight, "nodeord": 3}]
        return {"route_no": "12-S" if verified else "번호 미확인(ID 001)",
                "sequence_basis": "csv_order", "route_number_verified": verified,
                "stops": stops}

    def plan(self, routes):
        return plan_journeys(self.origin, self.destination, [self.board], [self.alight],
                             routes, {}, lambda *args: (2, 100), max_transfers=0)

    def test_equal_score_prefers_verified_numbers_then_stable_ids(self):
        rows = [("R0", self.route(verified=False)),
                ("R2", self.route()), ("R1", self.route())]
        expected = ["R1", "R2", "R0"]
        for entries in (rows, list(reversed(rows))):
            with self.subTest(order=[key for key, _ in entries]):
                journeys = self.plan(dict(entries))
                self.assertEqual([item["legs"][0]["route_id"] for item in journeys], expected)
                self.assertEqual(len({item["known_min"] for item in journeys}), 1)
                self.assertTrue(all(item["total_min"] is None for item in journeys))
                self.assertEqual(journeys[-1]["legs"][0]["route_no"], "번호 미확인(ID 001)")

    def test_number_verification_never_overrides_better_primary_time(self):
        journeys = self.plan({"VERIFIED": self.route(detour=True),
                              "UNKNOWN": self.route(verified=False)})
        self.assertEqual(journeys[0]["legs"][0]["route_id"], "UNKNOWN")
        self.assertLess(journeys[0]["known_min"], journeys[1]["known_min"])


if __name__ == "__main__":
    unittest.main()
