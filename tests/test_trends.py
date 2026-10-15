"""Calling a drop in an affiliate's conversion, and not calling noise."""

import sys
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent / "service"
if str(SERVICE) not in sys.path:
    sys.path.insert(0, str(SERVICE))

import journeys                                            # noqa: E402
import trends                                              # noqa: E402


def day(i, received=100, extracted=60, submitted=60, sold=24, revenue=1200.0):
    return {"day": f"2026-09-{i + 1:02d}", "received": received, "extracted": extracted,
            "submitted": submitted, "sold": sold, "revenue": revenue}


class Rolling(unittest.TestCase):
    def test_each_point_covers_seven_days(self):
        points = trends.rolling([day(i) for i in range(10)], "sold", "submitted")
        self.assertEqual(len(points), 4)                    # days 7..10
        self.assertAlmostEqual(points[0][1], 0.4)

    def test_volume_is_summed_not_averaged(self):
        """Three leads must not weigh as much as three hundred."""
        days = [day(i, submitted=300, sold=150) for i in range(6)]
        days.append(day(6, submitted=3, sold=0))
        (_, rate), = trends.rolling(days, "sold", "submitted")
        self.assertAlmostEqual(rate, 900 / 1803)            # not (0.5*6 + 0)/7


class Assess(unittest.TestCase):
    def test_a_steady_affiliate_is_not_flagged(self):
        result = trends.assess([day(i) for i in range(28)], "submitted_to_sold")
        self.assertFalse(result["flag"])
        self.assertAlmostEqual(result["change"], 0)

    def test_a_real_fall_is_flagged(self):
        days = [day(i) for i in range(21)] + [day(21 + i, sold=12) for i in range(7)]
        result = trends.assess(days, "submitted_to_sold")      # 40% -> 20%
        self.assertTrue(result["flag"])
        self.assertAlmostEqual(result["change"], -0.5)

    def test_a_fall_on_tiny_volume_is_not_called(self):
        days = ([day(i, received=4, submitted=4, sold=2) for i in range(21)]
                + [day(21 + i, received=4, submitted=4, sold=0) for i in range(7)])
        result = trends.assess(days, "submitted_to_sold")
        self.assertFalse(result["enough"])
        self.assertFalse(result["flag"])

    def test_a_small_wobble_on_big_volume_is_not_flagged(self):
        """Statistically real but too small to act on."""
        days = ([day(i, submitted=2000, sold=800) for i in range(21)]
                + [day(21 + i, submitted=2000, sold=770) for i in range(7)])
        result = trends.assess(days, "submitted_to_sold")      # 40% -> 38.5%
        self.assertFalse(result["flag"])

    def test_a_rise_is_never_a_drop(self):
        days = [day(i) for i in range(21)] + [day(21 + i, sold=40) for i in range(7)]
        self.assertFalse(trends.assess(days, "submitted_to_sold")["flag"])

    def test_earnings_per_lead_flags_a_wide_fall(self):
        days = ([day(i) for i in range(21)]
                + [day(21 + i, revenue=900.0) for i in range(7)])     # -25%
        self.assertTrue(trends.assess(days, "earnings_per_lead")["flag"])


class Journeys(unittest.TestCase):
    def test_statements_supplied_means_no_statement_step(self):
        """JRF1st sends statements with the lead; a zero there is not a
        drop, it is a step that does not happen."""
        self.assertFalse(journeys.has_statement_step(journeys.journey_of(63, "external")))
        self.assertTrue(journeys.has_statement_step(journeys.journey_of(1, "external")))

    def test_internal_sources_are_one_journey(self):
        self.assertEqual(journeys.journey_of(163, "internal"), journeys.INTERNAL)

    def test_an_affiliate_not_yet_described_says_so(self):
        self.assertEqual(journeys.journey_of(999, "external"), journeys.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
