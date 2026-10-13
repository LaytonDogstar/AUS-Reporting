"""The two affiliate reports, and which days are collectable.

Both answers came from the dev team on 13 Oct 2026: FF and non-FF
affiliate counts cannot be combined, and the API reports on Sydney time.
Neither is visible in the numbers themselves, so each is held by a test.
"""

import datetime as dt
import sqlite3
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent / "service"
if str(SERVICE) not in sys.path:
    sys.path.insert(0, str(SERVICE))

import collector                                           # noqa: E402
import store                                               # noqa: E402

STAMP = "2026-10-13T00:00:00+00:00"


def affiliate_row(name, received=10, revenue=100.0, profit=40.0):
    return {"name": name, "received": received, "accepted_tc": 1, "bs_refreshed": 0,
            "duplicate": 0, "bs_extracted": 2, "submitted": 3, "sold": 1, "decline": 2,
            "redirect": 0, "funded": 0, "revenue": revenue, "commission": 60.0,
            "bs_cost": 1.0, "final_commission": 59.0, "profit": profit,
            "final_profit": profit, "volume_comparison_pct": 0.0}


class AffiliateSources(unittest.TestCase):
    """Two populations stored side by side, and no way to add them."""

    def setUp(self):
        self.conn = store.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.day = store.business_today().isoformat()

    def save(self, source, name, **kw):
        rows = [affiliate_row(name, **kw)]
        store.save_affiliates(self.conn, self.day, source, rows,
                              affiliate_row(None, **kw), STAMP)

    def test_each_report_keeps_its_own_rows(self):
        self.save("external", "Alpha", received=10)
        self.save("internal", "Beta", received=5)

        external = store.affiliate_league(self.conn, 30, "external")
        internal = store.affiliate_league(self.conn, 30, "internal")
        self.assertEqual([r[0] for r in external], ["Alpha"])
        self.assertEqual([r[0] for r in internal], ["Beta"])

    def test_one_report_does_not_overwrite_the_other(self):
        """Same day, same primary key but for the source column."""
        self.save("external", "Alpha", received=10)
        self.save("internal", "Alpha", received=5)

        self.assertEqual(store.affiliate_funnel(self.conn, 30, "external")[0], 10)
        self.assertEqual(store.affiliate_funnel(self.conn, 30, "internal")[0], 5)

    def test_recollecting_a_day_replaces_only_that_source(self):
        self.save("external", "Alpha", received=10)
        self.save("internal", "Beta", received=5)
        self.save("external", "Alpha", received=11)

        self.assertEqual(store.affiliate_funnel(self.conn, 30, "external")[0], 11)
        self.assertEqual(store.affiliate_funnel(self.conn, 30, "internal")[0], 5)

    def test_an_unknown_source_is_refused(self):
        with self.assertRaises(ValueError):
            store.save_affiliates(self.conn, self.day, "combined",
                                  [affiliate_row("Alpha")], None, STAMP)

    def test_both_reports_are_collected(self):
        kinds = [k for k, *_ in collector._requests(dt.date(2026, 10, 13))]
        self.assertIn("affiliates", kinds)
        self.assertIn("internal_affiliates", kinds)
        self.assertEqual(set(collector.KINDS), set(kinds))

    def test_the_internal_report_is_a_different_endpoint(self):
        urls = {k: u for k, _m, u, _b, _h in collector._requests(dt.date(2026, 10, 13))}
        self.assertTrue(urls["affiliates"].endswith("/AffiliateLeadCountReport"))
        self.assertTrue(urls["internal_affiliates"].endswith(
            "/InternalAffiliateLeadCountReport"))

    def test_the_internal_handler_stores_under_internal(self):
        payload = b'{"Items":[{"AffiliateName":"Beta","TotalReceived":5}]}'
        collector._store_internal_affiliates(self.conn, self.day, payload, 1, STAMP)

        self.assertEqual([(r[0], r[1]) for r in
                          store.affiliate_league(self.conn, 30, "internal")],
                         [("Beta", 5)])
        self.assertEqual(store.affiliate_league(self.conn, 30, "external"), [])


class Migration(unittest.TestCase):
    """A database written before the split has no column saying which
    report its rows came from, so those rows are dropped and refetched."""

    def setUp(self):
        self.path = str(Path(self.enterContext(tempfile.TemporaryDirectory())) / "old.db")
        old = sqlite3.connect(self.path)
        with old:
            old.execute("CREATE TABLE affiliate_day (business_date TEXT NOT NULL,"
                        " affiliate_name TEXT NOT NULL, received INTEGER,"
                        " PRIMARY KEY (business_date, affiliate_name))")
            old.execute("CREATE TABLE affiliate_totals_day (business_date TEXT PRIMARY KEY,"
                        " received INTEGER)")
            old.execute("CREATE TABLE collection (business_date TEXT NOT NULL,"
                        " kind TEXT NOT NULL, status TEXT NOT NULL, detail TEXT,"
                        " ms INTEGER, ran_at TEXT NOT NULL,"
                        " PRIMARY KEY (business_date, kind))")
            old.execute("INSERT INTO affiliate_day VALUES ('2026-10-01', 'Alpha', 10)")
            old.execute("INSERT INTO collection VALUES ('2026-10-01', 'affiliates',"
                        " 'ok', '1 affiliate(s)', 10, ?)", (STAMP,))
            old.execute("INSERT INTO collection VALUES ('2026-10-01', 'tiers',"
                        " 'ok', '3 tier(s)', 10, ?)", (STAMP,))
        old.close()

    def test_the_old_rows_are_dropped_and_the_column_arrives(self):
        conn = store.connect(self.path)
        self.addCleanup(conn.close)

        columns = {r[1] for r in conn.execute("PRAGMA table_info(affiliate_day)")}
        self.assertIn("source", columns)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM affiliate_day").fetchone()[0], 0)

    def test_the_affiliate_days_are_marked_for_recollection(self):
        conn = store.connect(self.path)
        self.addCleanup(conn.close)

        kinds = {r[0] for r in conn.execute("SELECT kind FROM collection")}
        self.assertEqual(kinds, {"tiers"})          # the other reports are untouched

    def test_migrating_twice_is_harmless(self):
        store.connect(self.path).close()
        conn = store.connect(self.path)
        self.addCleanup(conn.close)
        store.save_affiliates(conn, "2026-10-02", "external",
                              [affiliate_row("Alpha")], affiliate_row(None), STAMP)

        self.assertEqual(conn.execute("SELECT COUNT(*) FROM affiliate_day").fetchone()[0], 1)


class BusinessDates(unittest.TestCase):
    """Which day is finished is a Sydney question, not a UTC one."""

    def test_sydney_is_ahead_of_utc(self):
        utc_today = dt.datetime.now(dt.timezone.utc).date()
        self.assertIn(store.business_today() - utc_today,
                      (dt.timedelta(0), dt.timedelta(days=1)))

    def test_the_newest_collectable_day_is_sydney_yesterday(self):
        self.enterContext(unittest.mock.patch.object(
            store, "business_today", lambda: dt.date(2026, 10, 14)))
        conn = store.connect(":memory:")
        self.addCleanup(conn.close)

        days = collector.missing_days(conn, days=3)
        self.assertEqual([d.isoformat() for d in days],
                         ["2026-10-13", "2026-10-12", "2026-10-11"])

    def test_a_collected_day_is_not_asked_for_again(self):
        self.enterContext(unittest.mock.patch.object(
            store, "business_today", lambda: dt.date(2026, 10, 14)))
        conn = store.connect(":memory:")
        self.addCleanup(conn.close)
        for kind in collector.KINDS:
            store.mark(conn, "2026-10-13", kind, "ok", "", 1, STAMP)

        days = collector.missing_days(conn, days=3)
        self.assertNotIn(dt.date(2026, 10, 13), days)

    def test_a_window_is_cut_on_the_business_date(self):
        """A 30-day window must not move with UTC's clock."""
        self.enterContext(unittest.mock.patch.object(
            store, "business_today", lambda: dt.date(2026, 10, 14)))
        self.assertEqual(store.business_cutoff(30), "2026-09-14")


if __name__ == "__main__":
    unittest.main()
