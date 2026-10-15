"""The Today and Yesterday pages, and Google Ads spend.

The pages set one day, hour by hour, against the norm, one population
at a time - the US dashboard's Today and Yesterday views, fitted to the
Overflow ping log. Ads spend is the one figure the internal report
always carried and nothing read.
"""

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent / "service"
if str(SERVICE) not in sys.path:
    sys.path.insert(0, str(SERVICE))

import collector                                           # noqa: E402
import dash                                                # noqa: E402
import main                                                # noqa: E402
import store                                               # noqa: E402

STAMP = "2026-10-15T00:00:00+00:00"
FF, EXT, TEST = 24, 59, 144                  # Friendly Finance, Borrowly, DummyJourney
LENDER, DUMMY = 5, 24


def ping(lead, at, affiliate=EXT, lender=LENDER, tier=50, status=1, cost=30.0):
    return {"LeadId": lead, "LeadApplicationId": "app-" + lead, "AffiliateId": affiliate,
            "LenderId": lender, "LenderTierId": tier, "StatusId": status, "Cost": cost,
            "DateCreated": at, "Id": 1}


class ReduceHours(unittest.TestCase):
    def setUp(self):
        self.leads, self.sales, self.as_of, self.filtered = collector.reduce_hours([
            ping("L1", "2026-10-15T09:10:00.1", status=0),     # filtered at 9
            ping("L1", "2026-10-15T10:05:00.0", status=1),     # sold at 10
            ping("L1", "2026-10-15T10:06:00.0", tier=51, status=1, cost=0.0),  # and again
            ping("L2", "2026-10-15T10:30:00.0", status=2),     # declined
            ping("L3", "2026-10-15T22:26:41.5", status=4),     # ignored
        ])

    def test_a_lead_counts_once_in_the_hour_of_its_first_ping(self):
        self.assertEqual(self.leads[(9, EXT)][0], 1)
        self.assertEqual(self.leads[(10, EXT)][0], 1)
        self.assertEqual(sum(n for n, _ in self.leads.values()), 3)

    def test_a_lead_sold_twice_is_one_sold_lead(self):
        self.assertEqual(sum(s for _, s in self.leads.values()), 1)
        self.assertEqual(self.leads[(9, EXT)][1], 1)

    def test_only_pings_that_reached_a_lender_are_kept(self):
        reached = sum(r for r, _, _ in self.sales.values())
        sold = sum(s for _, s, _ in self.sales.values())
        self.assertEqual((reached, sold), (3, 2))
        self.assertEqual(sum(v for _, _, v in self.sales.values()), 30.0)

    def test_filter_failures_are_counted_per_lender_and_hour(self):
        self.assertEqual(self.filtered, {(9, EXT, LENDER): 1})

    def test_ignored_pings_are_neither_filtered_nor_reached(self):
        reached = sum(r for r, _, _ in self.sales.values())
        self.assertEqual(reached + sum(self.filtered.values()), 4)    # of 5 pings, 1 ignored

    def test_as_of_is_the_newest_ping_to_the_minute(self):
        self.assertEqual(self.as_of, "2026-10-15T22:26")

    def test_no_lead_identifier_survives(self):
        out = json.dumps([list(self.leads.items()), list(self.sales.items())], default=str)
        for ident in ("L1", "L2", "L3", "app-"):
            self.assertNotIn(ident, out)


class SaveHours(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(":memory:")
        self.addCleanup(self.conn.close)

    def test_a_live_read_never_overwrites_a_finished_day(self):
        day = "2026-10-14"
        store.save_hours(self.conn, day, {(1, EXT): (10, 4)}, {}, day + "T23:59", True, STAMP)
        wrote = store.save_hours(self.conn, day, {(1, EXT): (3, 1)}, {}, day + "T10:00",
                                 False, STAMP)
        self.assertFalse(wrote)
        self.assertEqual(self.conn.execute("SELECT leads FROM hour_leads").fetchone()[0], 10)

    def test_the_finished_day_replaces_the_live_read(self):
        day = "2026-10-14"
        store.save_hours(self.conn, day, {(1, EXT): (3, 1)}, {}, day + "T10:00", False, STAMP)
        store.save_hours(self.conn, day, {(1, EXT): (10, 4)}, {}, day + "T23:59", True, STAMP)
        self.assertEqual(self.conn.execute("SELECT SUM(leads) FROM hour_leads").fetchone()[0], 10)
        self.assertTrue(store.hour_meta(self.conn, day)["complete"])


def seed(conn, day, hour=10, ext=(10, 4, 4, 120.0), ff=(6, 3, 3, 60.0), complete=True):
    """One day: external and FF leads and sales at one hour, plus test
    traffic and a sale to the Dummy lender, which must not show."""
    leads = {(hour, EXT): ext[:2], (hour, FF): ff[:2], (hour, TEST): (50, 50)}
    sales = {(hour, EXT, LENDER, 50): (ext[2], ext[2], ext[3]),
             (hour, FF, LENDER, 50): (ff[2], ff[2], ff[3]),
             (hour, EXT, DUMMY, 99): (9, 9, 0.0),
             (hour, TEST, LENDER, 50): (5, 5, 999.0)}
    store.save_hours(conn, day, leads, sales, day + "T23:59", complete, STAMP)


class FilteredMigration(unittest.TestCase):
    def test_hours_stored_before_filtered_counts_are_dropped_and_refetched(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory())) / "old.db"
        conn = store.connect(str(tmp))
        seed(conn, "2026-10-14")
        store.mark(conn, "2026-10-14", "lender_results", "ok", "", 1, STAMP)
        conn.execute("DROP TABLE hour_filtered")
        conn.commit(); conn.close()
        conn = store.connect(str(tmp))
        self.addCleanup(conn.close)
        self.assertIsNone(store.hour_meta(conn, "2026-10-14"))
        self.assertEqual(store.collected_days(conn, "lender_results"), set())


class Payload(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(":memory:")
        self.addCleanup(self.conn.close)
        store.save_affiliate_lookup(self.conn, [
            {"id": FF, "name": "Friendly Finance", "group_name": "FF", "is_internal": True,
             "is_active": True, "commission_type": None, "commission_value": None},
            {"id": EXT, "name": "Borrowly", "group_name": "B", "is_internal": False,
             "is_active": True, "commission_type": None, "commission_value": None}], STAMP)
        self.today = store.business_today()
        for i in range(1, 22):
            seed(self.conn, (self.today - dt.timedelta(days=i)).isoformat())
        seed(self.conn, self.today.isoformat(), ext=(20, 12, 14, 280.0), complete=False)

    def test_one_population_at_a_time(self):
        ext = dash.today_payload(self.conn, "today", "external")
        ff = dash.today_payload(self.conn, "today", "internal")
        self.assertEqual(sum(ext["total"]["day"]["leads"]), 20)
        self.assertEqual(sum(ff["total"]["day"]["leads"]), 6)
        self.assertEqual([a["id"] for a in ext["affiliates"]], [EXT])
        self.assertEqual([a["id"] for a in ff["affiliates"]], [FF])

    def test_all_sources_adds_both_populations_from_the_ping_log(self):
        p = dash.today_payload(self.conn, "today", "all")
        self.assertEqual(sum(p["total"]["day"]["leads"]), 26)        # 20 + 6, no test traffic
        self.assertEqual(sum(p["total"]["day"]["sold"]), 17)         # 14 + 3
        self.assertEqual({a["id"]: a["internal"] for a in p["affiliates"]}, {EXT: False, FF: True})
        self.assertIsNotNone(p["ff"])

    def test_without_the_lookup_neither_population_shows_anything(self):
        self.conn.execute("DELETE FROM affiliate")
        self.conn.commit()
        for source in ("external", "internal"):
            p = dash.today_payload(self.conn, "today", source)
            self.assertEqual(sum(p["total"]["day"]["leads"]), 0)

    def test_test_traffic_and_excluded_lenders_are_left_out(self):
        ext = dash.today_payload(self.conn, "today", "external")
        self.assertEqual(sum(ext["total"]["day"]["sold"]), 14)
        self.assertEqual(sum(ext["total"]["day"]["value"]), 280.0)
        self.assertNotIn(DUMMY, [r["id"] for r in ext["lenders"]])

    def test_the_norm_is_the_same_weekday_when_there_are_enough(self):
        p = dash.today_payload(self.conn, "today", "external")
        self.assertEqual(p["normLabel"], "a typical " + self.today.strftime("%A"))
        self.assertEqual(len(p["normDays"]), 3)
        for d in p["normDays"]:
            self.assertEqual(dt.date.fromisoformat(d).weekday(), self.today.weekday())
        self.assertEqual(sum(p["total"]["norm"]["leads"]), 10)     # an average day, not a sum

    def test_yesterday_is_the_day_before(self):
        p = dash.today_payload(self.conn, "yesterday", "external")
        self.assertEqual(p["date"], (self.today - dt.timedelta(days=1)).isoformat())
        self.assertTrue(p["meta"]["complete"])

    def test_filter_pass_is_reached_over_reached_plus_filtered(self):
        day = (self.today - dt.timedelta(days=1)).isoformat()
        seed(self.conn, day)
        self.conn.execute("INSERT INTO hour_filtered VALUES (?,?,?,?,?)", (day, 10, EXT, LENDER, 36))
        self.conn.execute("INSERT INTO hour_filtered VALUES (?,?,?,?,?)", (day, 10, EXT, DUMMY, 99))
        self.conn.commit()
        p = dash.today_payload(self.conn, "yesterday", "external")
        lender = next(r for r in p["lenders"] if r["id"] == LENDER)
        # 4 reached it, 36 failed its filters: 4 of 40 evaluated passed.
        self.assertEqual((sum(lender["day"]["reached"]), sum(lender["day"]["offered"])), (4, 40))
        self.assertEqual(sum(p["total"]["day"]["offered"]), 40)      # the Dummy lender left out

    def test_a_lender_buying_twice_its_usual_share_is_a_mover(self):
        p = dash.today_payload(self.conn, "today", "external")
        # 4 sales per 10 leads usually; 20 leads today: 8 expected, 14 bought.
        m = p["movers"][0]
        self.assertEqual((m["expected"], m["gap"]), (8.0, 6.0))

    def test_a_gap_within_chance_is_not_a_mover(self):
        seed(self.conn, self.today.isoformat(), ext=(20, 10, 10, 200.0), complete=False)
        self.assertEqual(dash.today_payload(self.conn, "today", "external")["movers"], [])


class EplBreakdown(unittest.TestCase):
    def lender(self, lid, sold_day, value_day, sold_norm, value_norm):
        return {"id": lid, "name": f"L{lid}",
                "sum": {"day": {"sold": sold_day, "value": value_day},
                        "norm": {"sold": sold_norm, "value": value_norm}}}

    def test_mix_and_price_add_up_to_the_gap(self):
        rows = [self.lender(1, 50, 2000, 30, 1200), self.lender(2, 40, 400, 60, 480),
                self.lender(3, 10, 0, 0, 0), self.lender(4, 0, 0, 10, 300)]
        e = dash.epl_breakdown(rows)
        self.assertAlmostEqual(e["mix"] + e["price"], e["gap"])
        self.assertAlmostEqual(sum(r["impact"] for r in e["rows"]), e["gap"])

    def test_a_lender_only_paying_more_is_all_price(self):
        rows = [self.lender(1, 50, 2000, 50, 1500), self.lender(2, 50, 500, 50, 500)]
        e = dash.epl_breakdown(rows)
        self.assertAlmostEqual(e["mix"], 0)
        self.assertAlmostEqual(e["price"], 5.0)


class AdsSpend(unittest.TestCase):
    """Measured 15 Oct 2026: the internal report's totals block reads 0
    for Ads spend on a finished day, while one row carries all of it."""

    payload = {"TotalAdwordSpent": 0.0, "TotalRevenue": 5433.0, "Items": [
        {"AffiliateName": "Friendly Finance(24)", "TotalRevenue": 637.0, "TotalBSCost": 30.96,
         "TotalAdwordSpent": 0.0, "TotalProfit": 606.04, "TotalReceived": 100, "TotalSold": 40},
        {"AffiliateName": "Friendly Finance Min Loan(193)", "TotalRevenue": 1193.0,
         "TotalBSCost": 63.36, "TotalAdwordSpent": 4281.99687, "TotalProfit": -3152.36,
         "TotalReceived": 200, "TotalSold": 90}]}

    def setUp(self):
        self.conn = store.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.day = (store.business_today() - dt.timedelta(days=1)).isoformat()
        rows, totals = collector.reduce_affiliates(self.payload)
        store.save_affiliates(self.conn, self.day, "internal", rows, totals, STAMP)

    def test_the_spend_is_read_off_the_rows(self):
        m = store.ff_money(self.conn, self.day)
        self.assertAlmostEqual(m["adword_spent"], 4281.99687)
        self.assertAlmostEqual(m["revenue"], 1830.0)
        self.assertFalse(m["live"])

    def test_the_affiliates_page_carries_it_for_ff_only(self):
        self.assertIsNotNone(dash.payload(self.conn, 30, "internal")["paid"])
        self.assertIsNone(dash.payload(self.conn, 30, "external")["paid"])

    def test_return_on_ad_spend_is_revenue_over_spend(self):
        paid = dash.paid_acquisition(self.conn, 30)
        self.assertAlmostEqual(paid["roas"]["current"], 1830.0 / 4281.99687)

    def test_a_database_without_the_column_gets_it_and_refetches(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory())) / "old.db"
        conn = store.connect(str(tmp))
        conn.execute("ALTER TABLE affiliate_day DROP COLUMN adword_spent")
        conn.execute("ALTER TABLE affiliate_totals_day DROP COLUMN adword_spent")
        store.mark(conn, self.day, "internal_affiliates", "ok", "", 1, STAMP)
        store.mark(conn, self.day, "affiliates", "ok", "", 1, STAMP)
        conn.commit(); conn.close()
        conn = store.connect(str(tmp))
        self.addCleanup(conn.close)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(affiliate_day)")}
        self.assertIn("adword_spent", cols)
        self.assertEqual(store.collected_days(conn, "internal_affiliates"), set())
        self.assertEqual(store.collected_days(conn, "affiliates"), {self.day})


class Pages(unittest.TestCase):
    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory())) / "t.db"
        main.DB_PATH = str(tmp)
        conn = store.connect(main.DB_PATH)
        today = store.business_today()
        for i in range(0, 8):
            seed(conn, (today - dt.timedelta(days=i)).isoformat(), complete=i > 0)
        conn.close()

    def test_both_pages_render_with_the_pop_up(self):
        for which in ("today", "yesterday"):
            for source in ("all", "external", "internal"):
                with self.subTest(which=which, source=source):
                    html = main.today_page(which, source).decode()
                    self.assertIn('id="chartPop"', html)
                    self.assertIn('"page":"' + which + '"', html)
                    self.assertNotIn("/*__DASHBOARD_DATA__*/", html)

    def test_the_combined_view_is_the_default(self):
        self.assertIn('"source":"all"', main.today_page("today").decode())

    def test_an_empty_database_still_renders(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory())) / "e.db"
        main.DB_PATH = str(tmp)
        html = main.today_page("today", "external").decode()
        self.assertIn('"meta":null', html)

    def test_every_page_links_to_today_and_yesterday(self):
        for name in ("template.html", "lenders.html", "customers.html", "today.html"):
            with self.subTest(page=name):
                src = (SERVICE / name).read_text()
                self.assertIn('href="/today"', src)
                self.assertIn('href="/yesterday"', src)


if __name__ == "__main__":
    unittest.main()
