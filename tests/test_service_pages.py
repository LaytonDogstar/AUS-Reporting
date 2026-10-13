"""What the pages must say about fields that read as something else.

Three of the quality fields mean something other than their name, and
the affiliate figures are two populations rather than one. None of that
is visible in the numbers, so each correction is held here.
"""

import sys
import tempfile
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent / "service"
if str(SERVICE) not in sys.path:
    sys.path.insert(0, str(SERVICE))

import main                                                # noqa: E402
import store                                               # noqa: E402

STAMP = "2026-10-13T00:00:00+00:00"


def text(page):
    return page.decode() if isinstance(page, bytes) else page


class ServicePage(unittest.TestCase):
    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory())) / "t.db"
        self.path = str(tmp)
        self.day = store.business_today().isoformat()
        main.DB_PATH = self.path

    def conn(self):
        conn = store.connect(self.path)
        self.addCleanup(conn.close)
        return conn


class QualityNotes(ServicePage):
    def setUp(self):
        super().setUp()
        counts = {}
        for metric, band, order in (("CenterlinkMonthly", "3000+", 4),
                                    ("HasPrimaryIncomeSource", "False", 0),
                                    ("IsAustralianResident", "None", 0),
                                    ("Age", "<18", 0)):
            counts[(metric, band, order, "Sold")] = 40
        store.save_quality(self.conn(), self.day, counts, 40, STAMP)
        self.html = text(main.quality_page(30))

    def test_the_centrelink_field_is_shown_under_what_it_holds(self):
        self.assertIn("Wages and Centrelink, monthly", self.html)
        self.assertIn("not Centrelink income", self.html)
        # and still names the field the API returns, so it can be found
        self.assertIn("<code>CenterlinkMonthly</code>", self.html)

    def test_the_subset_fields_are_not_offered_as_population_rates(self):
        self.assertEqual(self.html.count("Not a population rate"), 2)

    def test_the_under_18_band_is_called_test_data(self):
        self.assertIn("test data, not real applicants", self.html)


class AffiliateSections(ServicePage):
    def rows(self, received):
        return {"name": "Alpha", "received": received, "accepted_tc": 1, "bs_refreshed": 0,
                "duplicate": 0, "bs_extracted": 1, "submitted": 2, "sold": 1, "decline": 1,
                "redirect": 0, "funded": 0, "revenue": 100.0, "commission": 60.0,
                "bs_cost": 1.0, "final_commission": 59.0, "profit": 40.0,
                "final_profit": 40.0, "volume_comparison_pct": 0.0}

    def test_both_reports_are_shown_and_neither_total_is_their_sum(self):
        conn = self.conn()
        store.save_affiliates(conn, self.day, "external", [self.rows(100)],
                              self.rows(100), STAMP)
        store.save_affiliates(conn, self.day, "internal", [self.rows(20)],
                              self.rows(20), STAMP)

        html = text(main.affiliates_page(30))
        self.assertIn("Affiliate report", html)
        self.assertIn("Internal affiliate report", html)
        self.assertIn("never added together", html.lower().replace("</strong>", ""))
        # each report's own received count, rendered in its own section
        self.assertIn('<td class="n">100</td>', html)
        self.assertIn('<td class="n">20</td>', html)

    def test_an_uncollected_report_says_so_rather_than_reading_as_zero(self):
        store.save_affiliates(self.conn(), self.day, "external", [self.rows(100)],
                              self.rows(100), STAMP)

        html = text(main.affiliates_page(30))
        self.assertEqual(html.count("Nothing collected yet."), 1)


if __name__ == "__main__":
    unittest.main()
