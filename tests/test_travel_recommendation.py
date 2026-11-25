"""외부 조회 없이 수단 추천의 시간 비교·보행 하한·미확인 값 처리를 검증한다."""

from copy import deepcopy
import unittest

from services.travel_recommendation import build_recommendation


ORIGIN = {"lat": 37.88, "lon": 127.73}
DESTINATION = {"lat": 37.89, "lon": 127.74}


def walk(minutes=7, distance=600):
    return {"status": "ok", "minutes": minutes, "distance_m": distance, "source": "tmap"}


def bus(total=None, access=8, egress=0, wait=None, ride=3):
    return {
        "access_walk": {"minutes": access, "distance_m": access * 60},
        "egress_walk": {"minutes": egress, "distance_m": egress * 60},
        "walk_min": access + egress, "ride_min": ride, "wait_min": wait,
        "total_min": total, "has_live_departure": total is not None,
        "legs": [{"route_no": "300", "route_number_verified": True, "direction_verified": True,
                  "arrival_reference_only": False, "wait_min": wait}],
    }


class TravelRecommendationTests(unittest.TestCase):
    def recommend(self, walking=None, journeys=None):
        return build_recommendation(ORIGIN, DESTINATION, walk() if walking is None else walking, journeys or [])

    def test_seven_minute_walk_beats_nineteen_minute_bus_with_eight_minute_access(self):
        result = self.recommend(journeys=[bus(19, access=8, wait=8)])
        self.assertEqual((result["preferred_mode"], result["reason"], result["time_saved_min"]), ("walk", "walk_faster", 12))
        self.assertEqual(result["transit"]["access_walk_min"], 8)
        self.assertEqual(result["criteria"], {"short_walk_max_min": 15, "short_walk_max_m": 1000, "comparison_margin_min": 2})

    def test_fastest_complete_candidate_is_selected_instead_of_first(self):
        result = self.recommend(journeys=[bus(30, access=1, egress=1, wait=8, ride=20), bus(19, wait=8)])
        self.assertEqual(result["transit"]["candidate_index"], 1)
        self.assertEqual(result["transit"]["access_walk_min"], 8)
        self.assertEqual(result["min_bus_access_min"], 1)
        self.assertEqual(result["min_bus_walk_min"], 2)
        self.assertEqual(result["time_saved_min"], 12)

    def test_unknown_wait_uses_whole_candidate_walk_sum_without_mixing_paths(self):
        result = self.recommend(journeys=[bus(access=1, egress=8), bus(access=8, egress=1)])
        self.assertEqual((result["preferred_mode"], result["reason"]), ("walk", "stop_walk_overhead"))
        self.assertEqual(result["min_bus_access_min"], 1)
        self.assertEqual(result["min_bus_walk_min"], 9)
        self.assertEqual((result["transit"]["access_walk_min"], result["transit"]["egress_walk_min"]), (1, 8))
        self.assertIsNone(result["time_saved_min"])

    def test_unknown_wait_does_not_use_large_riding_or_known_minimum_as_a_lower_bound(self):
        candidate = bus(access=1, egress=1, ride=2000)
        candidate["known_min"] = 2002
        result = self.recommend(walk(17, 1200), [candidate])
        self.assertEqual((result["preferred_mode"], result["reason"]), ("undetermined", "comparison_unknown"))
        self.assertIsNone(result["time_saved_min"])

    def test_short_trip_with_unknown_wait_does_not_claim_time_savings(self):
        result = self.recommend(journeys=[bus(access=8)])
        self.assertEqual((result["preferred_mode"], result["reason"]), ("walk", "short_trip"))
        self.assertIsNone(result["transit"]["total_min"])
        self.assertIsNone(result["time_saved_min"])

    def test_one_low_walk_candidate_prevents_a_universal_stop_walk_claim(self):
        result = self.recommend(walk(17, 1200), [bus(access=20), bus(access=1, egress=1)])
        self.assertEqual(result["min_bus_walk_min"], 2)
        self.assertEqual(result["preferred_mode"], "undetermined")

    def test_unknown_wait_stop_walk_comparison_respects_reasonable_walking_limits(self):
        for minutes, distance, expected in ((25, 2000, "walk"), (25.01, 2000, "undetermined"),
                                            (25, 2000.01, "undetermined"), (100, 8000, "undetermined")):
            with self.subTest(minutes=minutes, distance=distance):
                result = self.recommend(walk(minutes, distance), [bus(access=minutes + 2)])
                self.assertEqual(result["preferred_mode"], expected)
                self.assertIsNone(result["time_saved_min"])

    def test_faster_bus_wins_even_for_short_walk_at_the_margin(self):
        result = self.recommend(journeys=[bus(5, access=1, egress=1, wait=1, ride=2)])
        self.assertEqual((result["preferred_mode"], result["reason"], result["time_saved_min"]), ("transit", "transit_faster", 2))
        self.assertEqual(result["scooter"]["status"], "not_suggested")

    def test_shorter_walk_stays_available_and_long_walk_prefers_timed_transit(self):
        at_margin = self.recommend(journeys=[bus(9, access=1, egress=1, wait=3, ride=4)])
        self.assertEqual(at_margin["time_saved_min"], 2)
        for minutes, distance in ((25, 2000), (25.01, 2000), (25, 2000.01), (26, 2100)):
            with self.subTest(minutes=minutes, distance=distance):
                result = self.recommend(walk(minutes, distance), [bus(40, access=1, egress=1, wait=8, ride=30)])
                self.assertEqual((result["preferred_mode"], result["reason"]), ("transit", "less_walking"))
                self.assertIsNone(result["time_saved_min"])
        result = self.recommend(walk(25, 2000), [bus(40, access=20, egress=1, wait=8, ride=11)])
        self.assertEqual((result["preferred_mode"], result["reason"]), ("walk", "walk_faster"))

    def test_one_minute_faster_transit_beats_short_walk(self):
        result = self.recommend(journeys=[bus(6, access=1, egress=1, wait=1, ride=3)])
        self.assertEqual((result["preferred_mode"], result["reason"], result["time_saved_min"]),
                         ("transit", "transit_faster", 1))

    def test_equal_arrival_prefers_fewer_transfers_then_shorter_walk(self):
        transfer = bus(7, access=1, egress=0, wait=2, ride=4)
        transfer["legs"].append(dict(transfer["legs"][0], route_no="2", wait_min=1))
        self.assertEqual(self.recommend(journeys=[transfer])["preferred_mode"], "walk")
        result = self.recommend(journeys=[transfer, bus(7, access=3, wait=1), bus(7, access=2, wait=1, ride=4)])
        self.assertEqual(result["transit"]["candidate_index"], 2)
        self.assertEqual((result["preferred_mode"], result["reason"], result["time_saved_min"]),
                         ("transit", "equal_total", None))

    def test_short_trip_boundaries_and_no_confirmed_transit(self):
        for minutes, distance, expected in ((15, 1000, "walk"), (15.01, 1000, "undetermined"), (15, 1000.01, "undetermined")):
            with self.subTest(minutes=minutes, distance=distance):
                result = self.recommend(walk(minutes, distance))
                self.assertEqual(result["preferred_mode"], expected)
                self.assertEqual(result["reason"], "walk_only" if expected == "walk" else "comparison_unknown")

    def test_unverified_bus_and_unconfirmed_shuttle_are_excluded(self):
        for flag, value in (("route_number_verified", False), ("direction_verified", False), ("arrival_reference_only", True)):
            candidate = bus(5, access=1, wait=1)
            candidate["legs"][0][flag] = value
            with self.subTest(flag=flag):
                self.assertIsNone(self.recommend(journeys=[candidate])["transit"])
        shuttle = bus(5, access=1, wait=1)
        shuttle.update(transport_type="shuttle", availability_confirmed=False)
        self.assertIsNone(self.recommend(journeys=[shuttle])["transit"])

    def test_confirmed_shuttle_compares_schedule_without_claiming_live_departure(self):
        shuttle = bus(5, access=1, wait=1)
        shuttle.update(transport_type="shuttle", availability_confirmed=True, has_live_departure=False)
        shuttle["legs"][0]["direction_verified"] = False
        self.assertEqual(self.recommend(journeys=[shuttle])["preferred_mode"], "transit")

    def test_invalid_complete_fields_preserve_only_unknown_candidate_metrics(self):
        for field, value in (("total_min", True), ("total_min", float("nan")), ("wait_min", -1),
                             ("ride_min", float("inf")), ("walk_min", False), ("has_live_departure", False)):
            candidate = bus(5, access=1, wait=1)
            candidate[field] = value
            with self.subTest(field=field, value=value):
                result = self.recommend(journeys=[candidate])
                self.assertIsNone(result["transit"]["total_min"])
                self.assertIsNone(result["time_saved_min"])
        candidate = bus(5, access=1, wait=1)
        candidate["legs"][0]["wait_min"] = None
        self.assertIsNone(self.recommend(journeys=[candidate])["transit"]["total_min"])

    def test_invalid_direct_walk_prefers_transit_without_invented_savings(self):
        for value in (None, True, -1, float("nan"), float("inf"), "7", 0, 10 ** 400):
            for field in ("minutes", "distance_m"):
                direct = walk()
                direct[field] = value
                with self.subTest(field=field, value=value):
                    result = self.recommend(direct, [bus(access=1)])
                    self.assertEqual((result["status"], result["preferred_mode"], result["reason"]),
                                     ("unavailable", "transit", "walk_unavailable"))
                    self.assertIsNone(result["walk"])
                    self.assertIsNone(result["time_saved_min"])

    def test_invalid_or_overflowing_stop_walks_cannot_enter_minimums(self):
        for access, egress in ((True, 1), (-1, 1), (float("nan"), 1), (1, float("inf")), (1e308, 1e308)):
            candidate = bus()
            candidate["access_walk"]["minutes"] = access
            candidate["egress_walk"]["minutes"] = egress
            with self.subTest(access=access, egress=egress):
                result = self.recommend(journeys=[candidate])
                self.assertIsNone(result["transit"])
                self.assertIsNone(result["min_bus_walk_min"])

    def test_same_point_requires_valid_coordinates_and_never_suggests_scooter(self):
        result = build_recommendation(ORIGIN, dict(ORIGIN), walk(0, 0), [])
        self.assertEqual((result["preferred_mode"], result["reason"]), ("none", "same_point"))
        self.assertEqual(result["walk"], {"minutes": 0, "distance_m": 0})
        self.assertEqual(result["scooter"]["status"], "not_suggested")
        invalid = {"lat": True, "lon": 127.73}
        self.assertEqual(build_recommendation(invalid, invalid, walk(0, 0), [])["status"], "unavailable")

    def test_scooter_is_only_a_conditional_option_with_no_time_or_live_claim(self):
        for minutes, distance, status in ((7, 300, "conditional"), (25, 2000, "conditional"),
                                          (7, 299, "not_suggested"), (25.01, 1800, "not_suggested"),
                                          (15, 2000.01, "not_suggested")):
            with self.subTest(minutes=minutes, distance=distance):
                scooter = self.recommend(walk(minutes, distance))["scooter"]
                self.assertEqual(scooter, {"status": status, "total_min": None,
                                           "availability_confirmed": False, "route_confirmed": False})

    def test_calculation_does_not_mutate_inputs(self):
        direct, candidates = walk(), [bus(19, wait=8)]
        before = deepcopy((direct, candidates))
        self.recommend(direct, candidates)
        self.assertEqual((direct, candidates), before)


if __name__ == "__main__":
    unittest.main()
