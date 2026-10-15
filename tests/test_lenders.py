"""The lender page: whether each lender's rates hold up, and which
customers it buys against those it is offered.

The definitions came from Layton on 15 Oct 2026: `sent` is a lead that
passed every one of the tier's filters, and `redirect` is a sale that
reached the lender's own page. What a tier-filtered quality report
returns was measured against the live API the same day.
"""

import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent / "service"
if str(SERVICE) not in sys.path:
    sys.path.insert(0, str(SERVICE))

import collector                                           # noqa: E402
import dash                                                # noqa: E402
import store                                               # noqa: E402
import trends                                              # noqa: E402

STAMP = "2026-10-15T00:00:00+00:00"
METRIC = "WagesAndCentrelinkMonthly"


def tier(tid, name, processed, sent, accepted, redirect=None, revenue=0.0):
    return {"id": tid, "name": name, "ignored": 0, "processed": processed, "sent": sent,
            "accepted": accepted, "blacklist": 0,
            "redirect": accepted if redirect is None else redirect,
            "conversion_rate": 0, "earning_per_lead": 0, "extra_income": 0,
            "revenue": revenue, "commission": 0, "profit": 0, "blacklist_price": 0}


def lookup(tid, name, lender_id, lender_name, price=10.0):
    return {"id": tid, "name": name, "lender_id": lender_id, "lender_name": lender_name,
            "code": None, "price": price, "is_active": True}


def day(n):
    return (store.business_today() - dt_days(n)).isoformat()


def dt_days(n):
    import datetime as dt
    return dt.timedelta(days=n)


class CollectByTier(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.day = "2026-10-12"

    def _respond(self, by_tier):
        calls = []

        def fake(url, attempts=3, pause=2.0):
            calls.append(url)
            tid = int(url.rsplit("lenderTierId=", 1)[1])
            return json.dumps({"Items": by_tier[tid]}).encode()
        self.enterContext(unittest.mock.patch.object(collector, "fetch_retrying", fake))
        return calls

    def test_each_tier_offered_anything_is_one_call_with_the_tier_filter(self):
        """The lenderId filter is ignored by the API, so it is per tier."""
        store.save_tiers(self.conn, self.day, [
            tier(1, "A", 100, 10, 2), tier(2, "B", 50, 0, 0), tier(3, "C", 0, 0, 0)],
            None, STAMP)
        calls = self._respond({1: [], 2: []})
        collector.collect_quality_by_tier(self.conn, self.day, STAMP)
        self.assertEqual(sorted(int(u.rsplit("=", 1)[1]) for u in calls), [1, 2])
        self.assertTrue(all("lenderTierId=" in u for u in calls))

    def test_leads_never_offered_to_the_tier_are_not_kept(self):
        """'Not Processed' leads sold higher up the tree or ended first.
        They say nothing about the tier."""
        store.save_tiers(self.conn, self.day, [tier(1, "A", 3, 2, 1)], None, STAMP)
        self._respond({1: [
            {"WageAndCenterlinkMonthly": 2500, "Result": "Sold"},
            {"WageAndCenterlinkMonthly": 2500, "Result": "FilterFailed"},
            {"WageAndCenterlinkMonthly": 2500, "Result": "Not Processed"},
        ]})
        collector.collect_quality_by_tier(self.conn, self.day, STAMP)
        results = {r[0]: r[1] for r in self.conn.execute(
            "SELECT result, SUM(n) FROM quality_tier_day WHERE metric=? GROUP BY result",
            (METRIC,))}
        self.assertEqual(results, {"Sold": 1, "FilterFailed": 1})

    def test_no_lead_identifier_is_stored(self):
        store.save_tiers(self.conn, self.day, [tier(1, "A", 1, 1, 1)], None, STAMP)
        self._respond({1: [{"LeadId": "abc-123", "LeadApplicationId": "def-456",
                            "WageAndCenterlinkMonthly": 2500, "Result": "Sold"}]})
        collector.collect_quality_by_tier(self.conn, self.day, STAMP)
        dump = "\n".join(self.conn.iterdump())
        self.assertNotIn("abc-123", dump)
        self.assertNotIn("def-456", dump)

    def test_a_day_without_the_lender_report_is_refused(self):
        """Collected out of order, it would otherwise store nothing and
        mark itself done."""
        with self.assertRaises(RuntimeError):
            collector.collect_quality_by_tier(self.conn, self.day, STAMP)

    def test_one_failed_call_fails_the_day(self):
        """A partial day would understate every lender with a failed tier."""
        store.save_tiers(self.conn, self.day, [tier(1, "A", 1, 1, 1), tier(2, "B", 1, 1, 1)],
                         None, STAMP)

        def flaky(url, attempts=3, pause=2.0):
            if url.endswith("=2"):
                raise RuntimeError("reset")
            return b'{"Items": []}'
        self.enterContext(unittest.mock.patch.object(collector, "fetch_retrying", flaky))
        with self.assertRaises(RuntimeError):
            collector.collect_quality_by_tier(self.conn, self.day, STAMP)

    def test_it_is_a_collected_kind(self):
        self.assertIn("quality_by_tier", collector.KINDS)
        self.assertIn("quality_by_tier", collector.MULTI_CALL_KINDS)


class Demographics(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.day = day(1)
        store.save_lender_lookup(self.conn, [
            lookup(1, "Big T1", 10, "Big"), lookup(2, "Big T2", 10, "Big"),
            lookup(85, "Dummy Buyer", 24, "Dummy")], STAMP)

    def test_offered_passed_and_bought_per_band(self):
        store.save_quality_by_tier(self.conn, self.day, {
            (1, METRIC, "3000+", 4, "Sold"): 5,
            (1, METRIC, "3000+", 4, "Decline"): 3,
            (1, METRIC, "3000+", 4, "Error"): 1,
            (1, METRIC, "3000+", 4, "FilterFailed"): 10,
            (1, METRIC, "3000+", 4, "Ignored"): 2,
        }, STAMP)
        by_lender, bands = store.lender_demographics(self.conn, METRIC)
        # offered = everything stored; passed = sold, declined, errored
        self.assertEqual(by_lender[10]["3000+"], [21, 9, 5])
        self.assertEqual(bands, ["3000+"])

    def test_a_lender_is_the_sum_of_its_tiers(self):
        store.save_quality_by_tier(self.conn, self.day, {
            (1, METRIC, "<1", 0, "Sold"): 1,
            (2, METRIC, "<1", 0, "Sold"): 2,
        }, STAMP)
        by_lender, _ = store.lender_demographics(self.conn, METRIC)
        self.assertEqual(by_lender[10]["<1"], [3, 3, 3])

    def test_the_dummy_lender_is_left_out(self):
        """It bought 111 of 112 at $0 and would read as the best lender."""
        store.save_quality_by_tier(self.conn, self.day, {
            (85, METRIC, "<1", 0, "Sold"): 100}, STAMP)
        by_lender, _ = store.lender_demographics(self.conn, METRIC)
        self.assertEqual(by_lender, {})


class LenderTrends(unittest.TestCase):
    def test_the_three_rates_are_the_agreed_ones(self):
        self.assertEqual(trends.LENDER_MEASURES, {
            "filter_pass": ("sent", "processed"),
            "acceptance": ("accepted", "sent"),
            "redirect_rate": ("redirect", "accepted"),
        })

    def test_a_falling_filter_pass_rate_is_called(self):
        days = ([{"day": f"d{i}", "processed": 1000, "sent": 200} for i in range(21)]
                + [{"day": f"e{i}", "processed": 1000, "sent": 120} for i in range(7)])
        r = trends.assess(days, "filter_pass", measures=trends.LENDER_MEASURES)
        self.assertTrue(r["flag"])
        self.assertAlmostEqual(r["current"], 0.12)

    def test_a_steady_one_is_not(self):
        days = [{"day": f"d{i}", "processed": 1000, "sent": 200} for i in range(28)]
        r = trends.assess(days, "filter_pass", measures=trends.LENDER_MEASURES)
        self.assertFalse(r["flag"])

    def test_the_affiliate_measures_still_assess_as_before(self):
        days = [{"day": f"d{i}", "received": 100, "revenue": 500.0} for i in range(28)]
        r = trends.assess(days, "earnings_per_lead")
        self.assertAlmostEqual(r["current"], 5.0)


class Page(unittest.TestCase):
    def setUp(self):
        self.path = str(Path(self.enterContext(tempfile.TemporaryDirectory())) / "d.db")
        self.conn = store.connect(self.path)
        self.addCleanup(self.conn.close)
        store.save_lender_lookup(self.conn, [
            lookup(1, "Big T1", 10, "Big"), lookup(2, "Big T2", 10, "Big"),
            lookup(3, "Small", 11, "Small"), lookup(85, "Dummy Buyer", 24, "Dummy")], STAMP)
        for i in range(1, 15):
            store.save_tiers(self.conn, day(i), [
                tier(1, "Big T1", 1000, 100, 30, 28, 300.0),
                tier(2, "Big T2", 500, 50, 10, 10, 100.0),
                tier(3, "Small", 200, 0, 0),
                tier(85, "Dummy Buyer", 5, 5, 5)], None, STAMP)
        store.save_quality_by_tier(self.conn, day(1), {
            (1, METRIC, "3000+", 4, "Sold"): 8,
            (1, METRIC, "<1", 0, "FilterFailed"): 10,
        }, STAMP)

    def test_a_lender_is_its_tiers_summed_and_the_dummy_is_gone(self):
        data = dash.lender_payload(self.conn, 30)
        names = [l["name"] for l in data["lenders"]]
        self.assertEqual(names, ["Big", "Small"])
        big = data["lenders"][0]
        self.assertEqual((big["processed"], big["sent"], big["accepted"]),
                         (1500 * 14, 150 * 14, 40 * 14))
        self.assertEqual([t["name"] for t in big["tiers"]], ["Big T1", "Big T2"])

    def test_a_lender_whose_filters_pass_nothing_still_shows(self):
        """MyLoans360 was offered 87,522 leads in 30 days and passed
        none: that is the finding, not an empty row to hide."""
        small = dash.lender_payload(self.conn, 30)["lenders"][1]
        self.assertEqual(small["sent"], 0)
        self.assertTrue(small["measures"]["filter_pass"]["enoughToTrend"])
        self.assertFalse(small["measures"]["acceptance"]["enoughToTrend"])

    def test_demographics_carry_the_offered_and_bought_mix(self):
        demo = dash.lender_payload(self.conn, 30)["demographics"]
        self.assertEqual([m["key"] for m in demo["metrics"]], [METRIC])
        self.assertEqual(demo["byLender"][METRIC]["10"]["3000+"], [8, 8, 8])
        self.assertEqual(demo["all"][METRIC]["<1"], [10, 0, 0])

    def test_the_page_renders_with_the_dashboard_stylesheet(self):
        html = dash.lender_page(self.conn, 30).decode()
        self.assertNotIn(dash.STYLE_PLACEHOLDER, html)
        self.assertNotIn(dash.PLACEHOLDER, html)
        self.assertIn("--series-1", html)
        self.assertIn('"view":"lenders"', html)

    def test_a_closing_script_tag_in_a_name_cannot_end_the_page(self):
        store.save_lender_lookup(self.conn, [lookup(3, "x", 11, "</script><b>")], STAMP)
        html = dash.lender_page(self.conn, 30).decode()
        self.assertNotIn("</script><b>", html)


if __name__ == "__main__":
    unittest.main()


class WhoBoughtFromWhom(unittest.TestCase):
    """The ping log counted per affiliate and lender, for the drill-down
    each way round: which lenders bought an affiliate's leads, and whose
    leads a lender bought."""

    def setUp(self):
        self.conn = store.connect(":memory:")
        self.addCleanup(self.conn.close)
        store.save_lender_lookup(self.conn, [lookup(1, "Big T1", 10, "Big"),
                                             lookup(85, "Dummy Buyer", 24, "Dummy")], STAMP)
        store.save_affiliate_lookup(self.conn, [
            {"id": 59, "name": "Borrowly", "group_name": "G", "is_internal": False,
             "is_active": True, "commission_type": None, "commission_value": None},
            {"id": 194, "name": "FF SEO", "group_name": "FF", "is_internal": True,
             "is_active": True, "commission_type": None, "commission_value": None}], STAMP)

    def test_the_reduction_keeps_no_lead_identifier(self):
        pairs = collector.reduce_affiliate_lender_results([
            {"AffiliateId": 59, "LenderId": 10, "StatusId": 1, "Cost": 50.0,
             "LeadId": "abc", "LeadApplicationId": "def"},
            {"AffiliateId": 59, "LenderId": 10, "StatusId": 0, "Cost": 50.0}])
        self.assertEqual(pairs, {(59, 10, 1): (1, 50.0), (59, 10, 0): (1, 50.0)})

    def test_offered_passed_bought_and_what_sold_was_worth(self):
        for i in range(1, 15):
            store.save_affiliate_lender_results(self.conn, day(i), {
                (59, 10, 0): (80, 4000.0),       # filtered out
                (59, 10, 2): (15, 750.0),        # declined
                (59, 10, 1): (5, 250.0),         # sold
                (194, 10, 1): (2, 100.0),
                (13, 10, 1): (9, 0.0),           # a test affiliate
                (59, 24, 1): (9, 0.0),           # the Dummy lender
            }, STAMP)
        by_affiliate = dash.pair_drilldown(self.conn, 30, "affiliate")
        self.assertEqual(set(by_affiliate), {"59", "194"})
        row = by_affiliate["59"][0]
        self.assertEqual((row["name"], row["offered"], row["passed"], row["sold"]),
                         ("Big", 100 * 14, 20 * 14, 5 * 14))
        self.assertAlmostEqual(row["value"], 250.0 * 14)   # only the sold rows are money
        self.assertAlmostEqual(row["acceptance"]["current"], 0.25)
        self.assertAlmostEqual(row["bought"]["points"][-1][1], 5.0)

        by_lender = dash.pair_drilldown(self.conn, 30, "lender")
        self.assertEqual(set(by_lender), {"10"})
        names = [(r["name"], r["internal"]) for r in by_lender["10"]]
        self.assertEqual(names, [("Borrowly", False), ("FF SEO", True)])
        self.assertAlmostEqual(sum(r["share"] for r in by_lender["10"]), 1.0)

    def test_days_collected_before_the_cross_existed_are_refetched(self):
        path = str(Path(self.enterContext(tempfile.TemporaryDirectory())) / "old.db")
        conn = store.connect(path)
        conn.execute("DROP TABLE affiliate_lender_day")
        store.mark(conn, "2026-10-01", "lender_results", "ok")
        store.mark(conn, "2026-10-01", "tiers", "ok")
        conn.commit(); conn.close()
        conn = store.connect(path)
        self.addCleanup(conn.close)
        self.assertNotIn("2026-10-01", store.collected_days(conn, "lender_results"))
        self.assertIn("2026-10-01", store.collected_days(conn, "tiers"))


class OfflineLenders(unittest.TestCase):
    """DCS is an offline lender. Layton, 15 Oct 2026: leave it out of
    reporting for now."""

    def setUp(self):
        self.conn = store.connect(":memory:")
        self.addCleanup(self.conn.close)
        store.save_lender_lookup(self.conn, [lookup(1, "Big T1", 10, "Big"),
                                             lookup(62, "DCS", 22, "DCS")], STAMP)

    def test_it_is_excluded(self):
        self.assertIn(22, store.EXCLUDED_LENDER_IDS)

    def test_it_is_left_out_of_the_drill_down(self):
        store.save_affiliate_lender_results(self.conn, day(1), {
            (194, 22, 1): (135, 1350.0), (194, 10, 1): (5, 250.0)}, STAMP)
        rows = dash.pair_drilldown(self.conn, 30, "affiliate")["194"]
        self.assertEqual([r["name"] for r in rows], ["Big"])

    def test_it_is_left_out_of_the_ping_log_figures(self):
        store.save_lender_results(self.conn, day(1), {
            (22, 62, 1): (135, 135, 1350.0), (10, 1, 1): (5, 5, 250.0)},
            {"pings": 140, "leads": 140, "applications": 140}, STAMP)
        self.assertEqual([r[0] for r in store.lender_result_league(self.conn)], [10])
        self.assertEqual(store.lender_result_reach(self.conn)[2], 5)


class CustomerMix(unittest.TestCase):
    """Buyer profiles, as the US dashboard lays them out: what each
    lender buys against the pool of every lead received, every measure
    at once, with each band's share of its purchases over time."""

    def setUp(self):
        self.conn = store.connect(":memory:")
        self.addCleanup(self.conn.close)
        store.save_lender_lookup(self.conn, [lookup(1, "Big T1", 10, "Big"),
                                             lookup(62, "DCS", 22, "DCS")], STAMP)
        for i in range(1, 29):
            # Big buys 30% high-income for three weeks, then 60%.
            high = 60 if i <= 7 else 30
            store.save_tiers(self.conn, day(i), [tier(1, "Big T1", 400, 200, 100, revenue=1000.0)],
                             None, STAMP)
            # the pool: every lead received, a quarter of them high-income
            store.save_quality(self.conn, day(i), {
                (METRIC, "3000+", 4, "Sold"): 100, (METRIC, "3000+", 4, "Decline"): 150,
                (METRIC, "<1", 0, "Sold"): 100, (METRIC, "<1", 0, "Decline"): 650,
                ("EmploymentStatus", "Full time", 0, "Sold"): 200,
                ("EmploymentStatus", "Centrelink", 0, "Decline"): 800,
                ("Age", "<18", 0, "Decline"): 10, ("Age", "25-34", 2, "Sold"): 990,
            }, 1000, STAMP)
            store.save_quality_by_tier(self.conn, day(i), {
                (1, METRIC, "3000+", 4, "Sold"): high,
                (1, METRIC, "<1", 0, "Sold"): 100 - high,
                (1, "EmploymentStatus", "Full time", 0, "Sold"): 100,
                (1, "Age", "<18", 0, "Sold"): 5,
                (1, "Age", "25-34", 2, "Sold"): 95,
                (62, METRIC, "3000+", 4, "Sold"): 99,
            }, STAMP)

    def level(self, data, metric, band):
        a = next(a for a in data["attrs"] if a["key"] == metric)
        return next(l for l in a["levels"] if l["band"] == band)

    def test_share_index_and_take_against_the_pool(self):
        data = dash.customer_payload(self.conn, 30)
        high = self.level(data, METRIC, "3000+")
        self.assertAlmostEqual(high["poolShare"], 0.25)
        cell = high["cells"]["10"]
        share = (60 * 7 + 30 * 21) / (100 * 28)
        self.assertAlmostEqual(cell["share"], share)
        self.assertAlmostEqual(cell["index"], 100 * share / 0.25)
        self.assertAlmostEqual(cell["take"], (60 * 7 + 30 * 21) / (250 * 28))

    def test_every_measure_at_once_categoricals_first(self):
        keys = [a["key"] for a in dash.customer_payload(self.conn, 30)["attrs"]]
        self.assertEqual(keys[0], "EmploymentStatus")
        self.assertIn(METRIC, keys)
        self.assertIn("Age", keys)

    def test_the_trend_is_its_share_over_time_with_shifts_either_way(self):
        data = dash.customer_payload(self.conn, 30)
        t = data["trends"]["10"][METRIC]
        self.assertAlmostEqual(t["3000+"]["share"][-1], 0.6)
        self.assertEqual(len(t["3000+"]["share"]), len(data["axis"]))
        self.assertEqual(t["3000+"]["shift"], 1)
        self.assertEqual(t["<1"]["shift"], -1)
        self.assertAlmostEqual(self.level(data, METRIC, "3000+")["pool"][-1], 0.25)

    def test_excluded_lenders_and_test_ages_are_left_out(self):
        data = dash.customer_payload(self.conn, 30)
        self.assertEqual([l["name"] for l in data["lenders"]], ["Big"])
        self.assertNotIn("22", data["trends"])
        age = next(a for a in data["attrs"] if a["key"] == "Age")
        self.assertEqual([l["band"] for l in age["levels"]], ["25-34"])

    def test_the_page_renders(self):
        html = dash.customer_page(self.conn, 30).decode()
        self.assertNotIn(dash.PLACEHOLDER, html)
        self.assertNotIn(dash.STYLE_PLACEHOLDER, html)
        self.assertIn('"view":"customers"', html)

    def test_employment_status_is_counted_per_tier(self):
        counts = collector.reduce_quality_bands([
            {"EmploymentStatus": "Full time", "Result": "Sold"},
            {"EmploymentStatus": "Centrelink", "Result": "FilterFailed"}])
        self.assertEqual(counts[("EmploymentStatus", "Full time", 0, "Sold")], 1)
        self.assertEqual(counts[("EmploymentStatus", "Centrelink", 0, "FilterFailed")], 1)

    def test_days_stored_before_employment_was_counted_are_refetched(self):
        path = str(Path(self.enterContext(tempfile.TemporaryDirectory())) / "old.db")
        conn = store.connect(path)
        store.save_quality_by_tier(conn, "2026-10-01", {(1, METRIC, "<1", 0, "Sold"): 1}, STAMP)
        store.mark(conn, "2026-10-01", "quality_by_tier", "ok")
        conn.close()
        conn = store.connect(path)
        self.addCleanup(conn.close)
        self.assertNotIn("2026-10-01", store.collected_days(conn, "quality_by_tier"))

    def test_a_steady_mix_is_not_called_a_shift(self):
        days = [{"day": f"d{i}", "s": 30, "S": 100} for i in range(28)]
        self.assertEqual(trends.assess_shift(days, "s", "S")["shift"], 0)
