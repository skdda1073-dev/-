"""실제 운임과 무관한 정수 배분 검증."""

import unittest

from services.fare_split import preview, split_amount


class FareSplitTests(unittest.TestCase):
    def test_remainder_goes_to_stable_first_members_and_preserves_total(self):
        self.assertEqual(split_amount(10001, 3), [3334, 3334, 3333])
        for amount in (1, 7, 10000, 10001, 123456):
            for count in range(1, 5):
                with self.subTest(amount=amount, count=count):
                    amounts = split_amount(amount, count)
                    self.assertEqual(sum(amounts), amount)
                    self.assertEqual(len(amounts), count)
                    self.assertLessEqual(max(amounts) - min(amounts), 1)

    def test_unavailable_or_invalid_fare_never_becomes_free_trip(self):
        for amount in (None, True, False, 0, -1, 1.1, "10000", float("nan"), float("inf")):
            with self.subTest(amount=amount):
                self.assertIsNone(split_amount(amount, 2))
        for count in (0, -1, 5, True, "2", 2.0, None):
            with self.subTest(count=count):
                self.assertIsNone(split_amount(10000, count))

    def test_preview_is_explicit_hypothetical_counts(self):
        values = preview(10001)
        self.assertEqual([row["count"] for row in values], [1, 2, 3, 4])
        self.assertEqual(values[2]["amounts_krw"], [3334, 3334, 3333])
        self.assertTrue(all(row["amounts_krw"] is None for row in preview(None)))


if __name__ == "__main__":
    unittest.main()
