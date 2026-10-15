"""The dashboard page, built from collected counts.

The look comes from `dashboard/template.html`, which was written
against the source database. The data comes from the service's own
store. These tests hold the join between them.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent / "service"
if str(SERVICE) not in sys.path:
    sys.path.insert(0, str(SERVICE))

import dash                                                # noqa: E402
import store                                               # noqa: E402

STAMP = "2026-10-15T00:00:00+00:00"


def affiliate_row(name, received, sold, decline=0, tc=0, bs=0, submitted=0):
    return {"name": name, "received": received, "accepted_tc": tc, "bs_refreshed": 0,
            "duplicate": 0, "bs_extracted": bs, "submitted": submitted, "sold": sold,
            "decline": decline, "redirect": 0, "funded": 0, "revenue": 10.0 * sold,
            "commission": 0.0, "bs_cost": 0.0, "final_commission": 0.0,
            "profit": 10.0 * sold, "final_profit": 10.0 * sold,
            "volume_comparison_pct": 0.0,
            "affiliate_id": int(name.split("(")[1].rstrip(")"))}


class Payload(unittest.TestCase):
    def setUp(self):
        self.path = str(Path(self.enterContext(tempfile.TemporaryDirectory())) / "d.db")
        self.conn = store.connect(self.path)
        self.addCleanup(self.conn.close)
        self.day = store.business_today().isoformat()
        store.save_affiliates(self.conn, self.day, "external",
                              [affiliate_row("Borrowly(59)", 100, 40, 60, 90, 80, 95)],
                              None, STAMP)
        store.save_affiliates(self.conn, self.day, "internal",
                              [affiliate_row("FF SEO(194)", 50, 25, 25)], None, STAMP)
        store.save_affiliate_lookup(self.conn, [
            {"id": 59, "name": "Borrowly", "group_name": "G", "is_internal": False,
             "is_active": True, "commission_type": "Percentage",
             "commission_value": "80.00%"},
            {"id": 194, "name": "FF SEO", "group_name": "FF", "is_internal": True,
             "is_active": True, "commission_type": None, "commission_value": None},
        ], STAMP)

    def test_one_population_at_a_time(self):
        """FF and non-FF lead counts cannot be added, so a page showing
        both would open on a headline that means nothing."""
        external = dash.payload(self.conn, 30, "external")
        internal = dash.payload(self.conn, 30, "internal")

        self.assertEqual([a["Id"] for a in external["affiliates"]], [59])
        self.assertEqual([a["Id"] for a in internal["affiliates"]], [194])
        self.assertEqual(sum(r["Applications"] for r in external["applications"]), 100)
        self.assertEqual(sum(r["Applications"] for r in internal["applications"]), 50)

    def test_the_page_says_which_population_it_is(self):
        self.assertIn("Internal", dash.payload(self.conn, 30, "internal")["sourceLabel"])
        self.assertIn("External", dash.payload(self.conn, 30, "external")["sourceLabel"])

    def test_the_funnel_carries_only_steps_this_data_has(self):
        """A funnel padded with stages that never fire reads as
        attrition that did not happen."""
        stages = dash.payload(self.conn, 30, "external")["stages"]
        self.assertEqual(set(stages), {"1", "3", "7", "8", "10", "13"})
        self.assertEqual(stages["10"]["outcome"], "good")
        self.assertEqual(stages["13"]["outcome"], "bad")

    def test_a_step_with_no_count_is_left_out_rather_than_shown_as_zero(self):
        rows = dash.payload(self.conn, 30, "internal")["stage_counts"]
        self.assertNotIn(3, {r["StageId"] for r in rows})      # no accepted T&Cs
        self.assertIn(1, {r["StageId"] for r in rows})

    def test_names_come_from_the_lookup_not_the_report_suffix(self):
        affiliates = dash.payload(self.conn, 30, "external")["affiliates"]
        self.assertEqual(affiliates[0]["Name"], "Borrowly")    # not "Borrowly(59)"

    def test_rows_without_an_id_are_left_out_of_the_page(self):
        """They cannot be attributed to an affiliate, and a total that
        silently includes them would not match the table."""
        store.save_affiliates(self.conn, "2026-09-20", "external",
                              [{**affiliate_row("Mystery(7)", 500, 100),
                                "affiliate_id": None}], None, STAMP)
        total = sum(r["Applications"]
                    for r in dash.payload(self.conn, 30, "external")["applications"])
        self.assertEqual(total, 100)


class Render(unittest.TestCase):
    def test_the_payload_is_embedded_as_valid_json(self):
        html = dash.render({"generatedUtc": "2026-10-15T00:00:00+00:00", "windowDays": 30,
                            "source": "external", "sourceLabel": "External affiliates",
                            "stages": dash.stages(), "affiliates": [], "applications": [],
                            "accepts": [], "stage_counts": []})
        embedded = html.split("const DATA = ", 1)[1].split(";\n", 1)[0]
        json.loads(embedded.replace("<\\/", "</"))

    def test_a_closing_script_tag_in_the_data_cannot_end_the_script(self):
        html = dash.render({"generatedUtc": "x", "windowDays": 30, "stages": {},
                            "affiliates": [{"Id": 1, "Name": "</script><b>x",
                                            "DisplayName": "</script><b>x"}],
                            "applications": [], "accepts": [], "stage_counts": []})
        self.assertNotIn("</script><b>", html)

    def test_the_template_still_has_its_placeholder(self):
        self.assertIn(dash.PLACEHOLDER, dash.TEMPLATE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


class TestTraffic(unittest.TestCase):
    """DummyJourney sold 111 of 128 leads at zero revenue over thirty
    days. Counted, it flatters every rate it appears in."""

    def setUp(self):
        self.path = str(Path(self.enterContext(tempfile.TemporaryDirectory())) / "t.db")
        self.conn = store.connect(self.path)
        self.addCleanup(self.conn.close)
        self.day = store.business_today().isoformat()
        store.save_affiliates(self.conn, self.day, "external", [
            affiliate_row("Borrowly(59)", 100, 40),
            affiliate_row("DummyJourney(144)", 128, 111),
        ], None, STAMP)

    def test_it_is_left_off_the_dashboard(self):
        data = dash.payload(self.conn, 30, "external")
        self.assertEqual([a["Id"] for a in data["affiliates"]], [59])
        self.assertEqual(sum(r["Applications"] for r in data["applications"]), 100)

    def test_it_is_left_off_the_scorecard_and_the_league(self):
        names = {r["name"] for r in store.scorecard(self.conn, 30)}
        self.assertNotIn("DummyJourney(144)", names)
        league = {r[0] for r in store.affiliate_league(self.conn, 30)}
        self.assertNotIn("DummyJourney(144)", league)

    def test_it_is_still_collected_so_the_day_reconciles(self):
        """Excluded from the pages, not from the data: the stored day
        still matches what the API reported."""
        total = self.conn.execute(
            "SELECT SUM(received) FROM affiliate_day WHERE business_date=?",
            (self.day,)).fetchone()[0]
        self.assertEqual(total, 228)


class BackfilledIds(unittest.TestCase):
    """A day already collected is never collected again, so rows stored
    before the id was parsed would keep a null one for good - and the
    dashboard, which joins on it, would show nothing at all."""

    def test_the_id_is_filled_from_the_name_already_stored(self):
        path = str(Path(self.enterContext(tempfile.TemporaryDirectory())) / "old.db")
        conn = store.connect(path)
        day = store.business_today().isoformat()
        store.save_affiliates(conn, day, "external",
                              [affiliate_row("Borrowly(59)", 100, 40)], None, STAMP)
        conn.execute("UPDATE affiliate_day SET affiliate_id=NULL")   # as if older
        conn.commit()
        conn.close()

        conn = store.connect(path)                                   # migration runs
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute(
            "SELECT affiliate_id FROM affiliate_day").fetchone()[0], 59)
        self.assertEqual(len(dash.payload(conn, 30, "external")["applications"]), 1)
