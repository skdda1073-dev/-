import unittest

from services.route_planner import plan_journeys


def stop(node_id, order, lon=127.73, lat=37.88, direction=0):
    return {"nodeid": node_id, "nodenm": node_id, "nodeord": order,
            "gpslati": lat, "gpslong": lon, "updowncd": direction}


def route(number, stops):
    return {"route_no": number, "end_node": stops[-1]["nodenm"], "stops": stops}


class RoutePlannerTests(unittest.TestCase):
    def setUp(self):
        self.a = stop("A", 1)
        self.b = stop("B", 2, lon=127.735)
        self.c = stop("C", 3, lon=127.74)
        self.d = stop("D", 4, lon=127.745)
        self.origin = {"name": "출발지", "lat": 37.879, "lon": 127.73}
        self.destination = {"name": "목적지", "lat": 37.881, "lon": 127.745}

    def plan(self, routes, origins=None, destinations=None, arrivals=None, walking=None, **kwargs):
        return plan_journeys(self.origin, self.destination, origins or [self.a],
                             destinations or [self.d], routes, arrivals if arrivals is not None else {},
                             walking or (lambda *args: (2, 100)), **kwargs)

    def test_direct_uses_ordered_full_stop_path_and_live_wait(self):
        journeys = self.plan({"R": route("10", [self.d, self.b, self.a, self.c])},
                             arrivals={"A": [{"routeid": "R", "arrtime": 300}]})
        self.assertEqual(len(journeys), 1)
        journey = journeys[0]
        self.assertEqual(journey["legs"][0]["stop_count"], 3)
        self.assertEqual(len(journey["legs"][0]["path"]), 4)
        self.assertEqual(journey["wait_min"], 3)
        self.assertTrue(journey["has_live_departure"])
        self.assertAlmostEqual(journey["total_min"], journey["ride_min"] + 7)
        self.assertTrue(journey["is_estimate"])

    def test_reverse_direction_is_rejected(self):
        self.assertEqual(self.plan({"R": route("10", [self.a, self.b, self.c, self.d])},
                                   origins=[self.d], destinations=[self.a]), [])

    def test_same_route_number_does_not_connect_different_route_ids(self):
        routes = {"outbound": route("10", [self.a, self.b]),
                  "inbound": route("10", [self.c, self.d])}
        self.assertEqual(self.plan(routes), [])

    def test_same_number_arrival_for_another_id_is_not_used(self):
        journeys = self.plan({"R": route("10", [self.a, self.d])}, arrivals={
            "A": [{"routeid": "OTHER", "routeno": "10", "arrtime": 300}]})
        self.assertIsNone(journeys[0]["total_min"])
        self.assertFalse(journeys[0]["has_live_departure"])

    def test_direction_boundary_is_not_a_through_service(self):
        self.c["updowncd"] = 1
        self.d["updowncd"] = 1
        self.assertEqual(self.plan({"R": route("10", [self.a, self.b, self.c, self.d])}), [])

    def test_missing_direction_cannot_verify_a_journey(self):
        del self.b["updowncd"]
        self.assertEqual(self.plan({"R": route("10", [self.a, self.b, self.d])}), [])

    def test_duplicate_orders_are_rejected(self):
        self.b["nodeord"] = self.a["nodeord"]
        self.assertEqual(self.plan({"R": route("10", [self.a, self.b, self.d])}), [])

    def test_missing_order_is_rejected(self):
        del self.b["nodeord"]
        self.assertEqual(self.plan({"R": route("10", [self.a, self.b, self.d])}), [])

    def test_invalid_coordinate_breaks_topology_instead_of_shortcut(self):
        self.b["gpslati"] = "nan"
        self.assertEqual(self.plan({"R": route("10", [self.a, self.b, self.d])}), [])

    def test_repeated_stop_occurrences_are_forward_and_deduplicated(self):
        repeated = stop("A", 3, lon=127.73)
        journeys = self.plan({"R": route("10", [self.a, self.b, repeated, self.d])},
                             arrivals={"A": [{"routeid": "R", "arrtime": 300}]})
        self.assertEqual(len(journeys), 1)
        self.assertEqual(journeys[0]["legs"][0]["stop_count"], 1)
        self.assertIsNone(journeys[0]["wait_min"])
        self.assertFalse(journeys[0]["has_live_departure"])

    def test_valid_transfer_uses_exact_shared_node_and_different_routes(self):
        routes = {"R1": route("10", [self.a, self.b]),
                  "R2": route("20", [self.b, self.c, self.d])}
        journeys = self.plan(routes, arrivals={
            "A": [{"routeid": "R1", "arrtime": 300}],
            "B": [{"routeid": "R2", "arrtime": 1800}],
        })
        self.assertEqual(len(journeys), 1)
        self.assertEqual(journeys[0]["transfer_count"], 1)
        self.assertEqual(journeys[0]["transfer_buffer_min"], 2)
        self.assertEqual(journeys[0]["legs"][0]["alight"]["id"], "B")
        self.assertEqual(journeys[0]["legs"][1]["board"]["id"], "B")
        self.assertIsNotNone(journeys[0]["total_min"])

    def test_same_number_transfer_is_not_inferred(self):
        routes = {"R1": route("10", [self.a, self.b]),
                  "R2": route("10", [self.b, self.d])}
        self.assertEqual(self.plan(routes), [])

    def test_nearby_differently_identified_transfer_stops_are_not_joined(self):
        other_b = stop("OTHER_B", 1, lon=self.b["gpslong"])
        routes = {"R1": route("10", [self.a, self.b]),
                  "R2": route("20", [other_b, self.d])}
        self.assertEqual(self.plan(routes), [])

    def test_transfer_limit_zero_disables_transfers(self):
        routes = {"R1": route("10", [self.a, self.b]),
                  "R2": route("20", [self.b, self.d])}
        self.assertEqual(self.plan(routes, max_transfers=0), [])

    def test_transfer_arrival_already_missed_is_not_assumed_catchable(self):
        routes = {"R1": route("10", [self.a, self.b]),
                  "R2": route("20", [self.b, self.d])}
        journeys = self.plan(routes, arrivals={
            "A": [{"routeid": "R1", "arrtime": 300}],
            "B": [{"routeid": "R2", "arrtime": 360}],
        })
        self.assertTrue(journeys[0]["has_live_departure"])
        self.assertIsNone(journeys[0]["total_min"])
        self.assertIsNone(journeys[0]["legs"][1]["wait_min"])
        self.assertGreater(journeys[0]["known_min"], journeys[0]["walk_min"])

    def test_walk_to_stop_and_boarding_margin_determine_catchability(self):
        journeys = self.plan({"R": route("10", [self.a, self.d])}, arrivals={
            "A": [{"routeid": "R", "arrtime": 120},
                  {"routeid": "R", "arrtime": 170},
                  {"routeid": "R", "arrtime": 300}],
        })
        self.assertEqual(journeys[0]["legs"][0]["arrival_min"], 5)
        self.assertEqual(journeys[0]["wait_min"], 3)

    def test_missing_or_missed_arrival_does_not_invent_zero_wait(self):
        for arrivals in ({}, {"A": [{"routeid": "R", "arrtime": 30}]}):
            with self.subTest(arrivals=arrivals):
                journey = self.plan({"R": route("10", [self.a, self.d])}, arrivals=arrivals)[0]
                self.assertIsNone(journey["total_min"])
                self.assertIsNone(journey["wait_min"])
                self.assertEqual(journey["known_min"], journey["walk_min"] + journey["ride_min"])

    def test_unknown_first_wait_prevents_future_transfer_wait_prediction(self):
        routes = {"R1": route("10", [self.a, self.b]),
                  "R2": route("20", [self.b, self.d])}
        journey = self.plan(routes, arrivals={"B": [{"routeid": "R2", "arrtime": 1800}]})[0]
        self.assertIsNone(journey["legs"][1]["arrival_min"])
        self.assertIsNone(journey["legs"][1]["wait_min"])

    def test_egress_walk_changes_final_ranking(self):
        near = stop("NEAR", 2, lon=127.744)
        far = stop("FAR", 2, lon=127.732)
        def walking(slat, slon, sname, elat, elon, ename):
            return (30 if sname == "FAR" else 2, 100)
        journeys = self.plan({"RF": route("10", [self.a, far]),
                              "RN": route("20", [self.a, near])},
                             destinations=[near, far], walking=walking)
        self.assertEqual(journeys[0]["legs"][0]["route_id"], "RN")

    def test_ride_estimate_uses_intermediate_geometry(self):
        direct = self.plan({"R": route("10", [self.a, self.d])})[0]["ride_min"]
        detour = stop("DETOUR", 2, lon=127.8, lat=37.95)
        indirect = self.plan({"R": route("10", [self.a, detour, self.d])})[0]["ride_min"]
        self.assertGreater(indirect, direct * 3)

    def test_fully_timed_catchable_journey_ranks_ahead_of_unknown_wait(self):
        routes = {"UNKNOWN": route("10", [self.a, self.d]),
                  "KNOWN": route("20", [self.a, self.b, self.c, self.d])}
        journeys = self.plan(routes, arrivals={"A": [{"routeid": "KNOWN", "arrtime": 1200}]})
        self.assertEqual(journeys[0]["legs"][0]["route_id"], "KNOWN")

    def test_failed_access_or_egress_walk_removes_candidate(self):
        for fail_name in ("출발지", "D"):
            with self.subTest(fail_name=fail_name):
                def walking(slat, slon, sname, elat, elon, ename):
                    return (-1, -1) if sname == fail_name else (2, 100)
                self.assertEqual(self.plan({"R": route("10", [self.a, self.d])}, walking=walking), [])

    def test_walking_exception_is_not_a_zero_walk(self):
        def walking(*args):
            raise TimeoutError("walking provider timeout")
        self.assertEqual(self.plan({"R": route("10", [self.a, self.d])}, walking=walking), [])

    def test_service_can_disable_unreliable_future_transfer_arrivals(self):
        routes = {"R1": route("10", [self.a, self.b]), "R2": route("20", [self.b, self.d])}
        journey = self.plan(routes, arrivals={
            "A": [{"routeid": "R1", "arrtime": 600}],
            "B": [{"routeid": "R2", "arrtime": 1800}],
        }, use_transfer_arrivals=False)[0]
        self.assertTrue(journey["has_live_departure"])
        self.assertIsNone(journey["legs"][1]["arrival_min"])
        self.assertIsNone(journey["total_min"])

    def test_invalid_origin_coordinate_is_rejected(self):
        self.origin["lat"] = float("inf")
        self.assertEqual(self.plan({"R": route("10", [self.a, self.d])}), [])

    def test_walking_is_cached_by_endpoint_pair(self):
        calls = []
        def walking(*args):
            calls.append(args)
            return 2, 100
        self.plan({"R1": route("10", [self.a, self.d]),
                   "R2": route("20", [self.a, self.d])}, walking=walking)
        self.assertEqual(len(calls), 2)

    def test_lazy_arrivals_mapping_is_supported_and_cached(self):
        class LazyArrivals(dict):
            calls = 0
            def get(self, node_id, default=None):
                self.calls += 1
                return [{"routeid": "R", "arrtime": 300}] if node_id == "A" else []
        arrivals = LazyArrivals()
        journey = self.plan({"R": route("10", [self.a, self.d]),
                             "OTHER": route("20", [self.a, self.d])}, arrivals=arrivals)[0]
        self.assertTrue(journey["has_live_departure"])
        self.assertEqual(arrivals.calls, 1)


if __name__ == "__main__":
    unittest.main()
