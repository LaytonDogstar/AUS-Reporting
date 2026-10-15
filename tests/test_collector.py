"""The two affiliate reports, and which days are collectable.

Both answers came from the dev team on 13 Oct 2026: FF and non-FF
affiliate counts cannot be combined, and the API reports on Sydney time.
Neither is visible in the numbers themselves, so each is held by a test.
"""

import datetime as dt
import json
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
    """As the collector builds one: the id parsed out of the name, the
    way the affiliate reports deliver it."""
    return {"name": name, "affiliate_id": store.affiliate_id_from(name),
            "received": received, "accepted_tc": 1, "bs_refreshed": 0,
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
        # every kind is collected by one route or the other
        self.assertEqual(set(collector.KINDS),
                         set(kinds) | set(collector.MULTI_CALL_KINDS))
        self.assertFalse(set(kinds) & set(collector.MULTI_CALL_KINDS))

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


class RenamedFields(unittest.TestCase):
    """A renamed payload field is invisible in the data: the field stops
    appearing and its metric quietly empties. Both spellings count."""

    METRIC = "WagesAndCentrelinkMonthly"

    def bands(self, *items):
        counts = collector.reduce_quality(list(items))
        return {band: n for (metric, band, _o, _r), n in counts.items()
                if metric == self.METRIC}

    def test_the_old_name_still_counts(self):
        self.assertEqual(self.bands({"CenterlinkMonthly": 2500, "Result": "Sold"}),
                         {"2000-3000": 1})

    def test_the_new_name_counts(self):
        self.assertEqual(self.bands({"WagesAndCentrelinkMonthly": 2500, "Result": "Sold"}),
                         {"2000-3000": 1})

    def test_both_spellings_land_in_one_series(self):
        self.assertEqual(
            self.bands({"CenterlinkMonthly": 2500, "Result": "Sold"},
                       {"WagesAndCentrelinkMonthly": 2500, "Result": "Sold"}),
            {"2000-3000": 2})

    def test_a_missing_field_counts_as_nothing(self):
        """Not as unknown: the metric was not reported for that lead."""
        self.assertEqual(self.bands({"WagesMonthly": 2500, "Result": "Sold"}), {})

    def test_a_categorical_field_is_resolved_the_same_way(self):
        counts = collector.reduce_quality([{"EmploymentStatus": "FullTime",
                                            "Result": "Sold"}])
        self.assertIn(("EmploymentStatus", "FullTime", 0, "Sold"), counts)


class MetricRename(unittest.TestCase):
    """Days collected under the old field name are the same measure."""

    def setUp(self):
        self.path = str(Path(self.enterContext(tempfile.TemporaryDirectory())) / "q.db")
        conn = store.connect(self.path)
        conn.execute("INSERT INTO quality_day VALUES"
                     " ('2026-10-01','CenterlinkMonthly','2000-3000',3,'Sold',7)")
        conn.commit()
        conn.close()

    def test_old_rows_join_the_new_series(self):
        conn = store.connect(self.path)
        self.addCleanup(conn.close)

        rows = conn.execute("SELECT metric, n FROM quality_day").fetchall()
        self.assertEqual(rows, [("WagesAndCentrelinkMonthly", 7)])

    def test_a_day_collected_under_both_names_is_not_doubled(self):
        conn = store.connect(self.path)
        conn.execute("INSERT INTO quality_day VALUES"
                     " ('2026-10-01','CenterlinkMonthly','1000-2000',2,'Sold',5)")
        conn.commit()
        conn.close()

        conn = store.connect(self.path)
        self.addCleanup(conn.close)
        total = conn.execute("SELECT SUM(n) FROM quality_day WHERE business_date=?"
                             " AND metric=?", ("2026-10-01",
                                               "WagesAndCentrelinkMonthly")).fetchone()[0]
        self.assertEqual(total, 7)              # the new-name rows win


class PingLog(unittest.TestCase):
    """LenderApplicationResults, measured 14 Oct 2026: a bare top-level
    array of about 99,000 rows a day, each carrying LeadId and
    LeadApplicationId."""

    def rows(self):
        return [
            {"LeadId": "lead-1", "LeadApplicationId": "app-1", "AffiliateId": 189,
             "LenderId": 12, "LenderTierId": 10162, "StatusId": 0, "Cost": 82.0,
             "DateCreated": "2026-10-13T01:00:00", "Id": 1},
            {"LeadId": "lead-1", "LeadApplicationId": "app-1", "AffiliateId": 189,
             "LenderId": 13, "LenderTierId": 10163, "StatusId": 1, "Cost": 40.0,
             "DateCreated": "2026-10-13T01:00:05", "Id": 2},
            {"LeadId": "lead-2", "LeadApplicationId": "app-2", "AffiliateId": 7,
             "LenderId": 12, "LenderTierId": 10162, "StatusId": 0, "Cost": 1.5,
             "DateCreated": "2026-10-13T02:00:00", "Id": 3},
        ]

    def test_no_identifier_survives_the_reduction(self):
        counts, meta = collector.reduce_lender_results(self.rows())

        flat = repr(counts) + repr(meta)
        for identifier in ("lead-1", "lead-2", "app-1", "app-2"):
            self.assertNotIn(identifier, flat, identifier)

    def test_pings_group_by_lender_tier_and_status(self):
        counts, _ = collector.reduce_lender_results(self.rows())

        self.assertEqual(counts[(12, 10162, 0)], (2, 2, 83.5))
        self.assertEqual(counts[(13, 10163, 1)], (1, 1, 40.0))

    def test_distinct_counts_are_taken_over_the_whole_day(self):
        """One lead is offered to several lenders, so summing the
        per-status rows would count it once per ping."""
        _, meta = collector.reduce_lender_results(self.rows())

        self.assertEqual(meta, {"pings": 3, "leads": 2, "applications": 2})

    def test_a_bare_array_and_an_items_envelope_both_read(self):
        bare, _ = collector.reduce_lender_results(self.rows())
        wrapped, _ = collector.reduce_lender_results({"Items": self.rows()})

        self.assertEqual(bare, wrapped)

    def test_an_unknown_status_is_still_counted(self):
        counts, _ = collector.reduce_lender_results(
            [{"LenderId": 12, "LenderTierId": 1, "StatusId": 9, "Cost": 0}])

        self.assertEqual(counts[(12, 1, 9)], (1, 0, 0.0))
        self.assertNotIn(9, collector.STATUS_LABELS)

    def test_the_handler_stores_what_the_page_reads(self):
        conn = store.connect(":memory:")
        self.addCleanup(conn.close)
        day = store.business_today().isoformat()
        payload = json.dumps(self.rows()).encode()

        detail = collector._store_lender_results(conn, day, payload, 1, STAMP)

        self.assertIn("3 ping(s)", detail)
        self.assertEqual(store.lender_result_totals(conn, 30), (3, 2, 2))
        self.assertEqual(store.lender_result_status(conn, 30),
                         [(0, 2, 83.5), (1, 1, 40.0)])
        league = store.lender_result_league(conn, 30)
        # lender 13 leads: its ping reached a lender, lender 12's did not
        self.assertEqual(league[0], (13, 10163, 1, 1, 1, 0, 40.0))
        self.assertEqual(store.lender_result_reach(conn, 30), (3, 1, 1, 40.0))

    def test_the_lookup_carries_names_and_not_contact_details(self):
        rows = collector.reduce_lender_lookup([{
            "Id": 13, "Name": "Speedy Money", "DisplayName": "Speedy Money",
            "LenderId": 16, "LenderName": "Speedy Money", "Code": "SPM-T1",
            "Price": 45.0, "IsActive": True, "Email": "ops@example.com.au",
            "LogoUrl": "/images/buyer/x.jpg", "InterstitialMessage": "..."}])

        self.assertEqual(rows[0]["id"], 13)
        self.assertEqual(rows[0]["lender_name"], "Speedy Money")
        self.assertNotIn("ops@example.com.au", repr(rows))
        self.assertEqual(set(rows[0]), {"id", "name", "lender_id", "lender_name",
                                        "code", "price", "is_active"})

    def test_the_ping_log_is_one_of_the_collected_kinds(self):
        kinds = [k for k, *_ in collector._requests(dt.date(2026, 10, 13))]
        self.assertIn("lender_results", kinds)
        urls = {k: u for k, _m, u, _b, _h in collector._requests(dt.date(2026, 10, 13))}
        self.assertIn("/LenderApplicationResults?startDate=", urls["lender_results"])


class AffiliateIdentity(unittest.TestCase):
    """The affiliate reports carry no AffiliateId, but their names end
    in one - measured 15 Oct 2026, all 116 of them."""

    def test_the_id_is_parsed_out_of_the_report_name(self):
        self.assertEqual(collector.affiliate_id_from("Borrowly(59)"), 59)
        self.assertEqual(collector.affiliate_id_from("Simple Finance (195)"), 195)
        self.assertEqual(collector.affiliate_id_from("FF SEO(194)"), 194)

    def test_a_name_without_one_gives_none_rather_than_guessing(self):
        self.assertIsNone(collector.affiliate_id_from("Borrowly"))
        self.assertIsNone(collector.affiliate_id_from(""))
        self.assertIsNone(collector.affiliate_id_from(None))

    def test_a_number_in_the_middle_is_not_an_id(self):
        self.assertIsNone(collector.affiliate_id_from("Breezy (115) promo"))

    def test_the_rows_carry_it(self):
        rows, _ = collector.reduce_affiliates(
            {"Items": [{"AffiliateName": "Borrowly(59)", "TotalReceived": 10}]})
        self.assertEqual(rows[0]["affiliate_id"], 59)

    def test_the_lookup_drops_the_credentials_the_endpoint_returns(self):
        """Affiliates returns each affiliate's portal password and its
        Credfin secret key. Nothing can store what is not read."""
        rows = collector.reduce_affiliate_lookup([{
            "Id": 59, "Name": "Borrowly", "GroupName": "Leadbrain",
            "IsInternalSource": False, "IsActive": True, "IsDeleted": False,
            "CommissionType": "Percentage", "CommissionValue": "80.00%",
            "Username": "borrowly", "Password": "hunter2",
            "CredfinSecretKey": "sk-live-xyz", "CredfinIdentifier": "cid-9"}])

        self.assertEqual(set(rows[0]), {"id", "name", "group_name", "is_internal",
                                        "is_active", "commission_type",
                                        "commission_value"})
        for secret in ("hunter2", "sk-live-xyz", "cid-9", "borrowly"):
            self.assertNotIn(secret, repr(rows), secret)

    def test_a_deleted_affiliate_is_not_active(self):
        rows = collector.reduce_affiliate_lookup(
            [{"Id": 1, "Name": "Gone", "IsActive": True, "IsDeleted": True}])
        self.assertFalse(rows[0]["is_active"])

    def test_pings_are_also_counted_per_affiliate(self):
        counts = collector.reduce_affiliate_results([
            {"AffiliateId": 59, "StatusId": 1, "Cost": 40.0, "LeadApplicationId": "a"},
            {"AffiliateId": 59, "StatusId": 1, "Cost": 40.0, "LeadApplicationId": "a"},
            {"AffiliateId": 59, "StatusId": 0, "Cost": 10.0, "LeadApplicationId": "b"},
            {"AffiliateId": 12, "StatusId": 1, "Cost": 15.0, "LeadApplicationId": "c"}])

        self.assertEqual(counts[(59, 1)], (2, 1, 80.0))   # 2 pings, 1 application
        self.assertEqual(counts[(59, 0)], (1, 1, 10.0))
        self.assertEqual(counts[(12, 1)], (1, 1, 15.0))

    def test_no_identifier_survives_the_per_affiliate_reduction(self):
        counts = collector.reduce_affiliate_results(
            [{"AffiliateId": 59, "StatusId": 1, "Cost": 1.0,
              "LeadApplicationId": "app-1", "LeadId": "lead-1"}])
        self.assertNotIn("app-1", repr(counts))
        self.assertNotIn("lead-1", repr(counts))


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


class QualityByAffiliate(unittest.TestCase):
    """LeadQualityReport takes an affiliateId, so the mix a source sends
    is one call per active affiliate rather than a dev ask. Verified
    against the live API on 15 Oct 2026: the per-affiliate rows for a
    day summed exactly to the unfiltered day."""

    def setUp(self):
        self.conn = store.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.day = "2026-10-12"

    def test_bands_are_counted_with_the_lender_result(self):
        """The result is kept so a band's sold rate can be taken within
        one population rather than against everyone."""
        counts = collector.reduce_quality_bands([
            {"WageAndCenterlinkMonthly": 2500, "Result": "Sold"},
            {"WageAndCenterlinkMonthly": 2600, "Result": "Decline"},
        ])
        self.assertEqual(counts[("WagesAndCentrelinkMonthly", "2000-3000", 3, "Sold")], 1)
        self.assertEqual(
            counts[("WagesAndCentrelinkMonthly", "2000-3000", 3, "Decline")], 1)

    def test_a_day_with_no_affiliate_rows_is_refused_not_stored_empty(self):
        """Collected out of order, it would otherwise store nothing and
        mark itself done."""
        with self.assertRaises(RuntimeError):
            collector.collect_quality_by_affiliate(self.conn, self.day, STAMP)

    def test_what_a_band_is_worth_comes_from_the_population(self):
        store.save_quality(self.conn, self.day, {
            ("WagesAndCentrelinkMonthly", "3000+", 4, "Sold"): 48,
            ("WagesAndCentrelinkMonthly", "3000+", 4, "Decline"): 52,
            ("WagesAndCentrelinkMonthly", "<1", 0, "Sold"): 15,
            ("WagesAndCentrelinkMonthly", "<1", 0, "Decline"): 85,
        }, 200, STAMP)

        rates = store.band_sold_rates(self.conn, "WagesAndCentrelinkMonthly", 30)
        self.assertAlmostEqual(rates["3000+"][1], 0.48)
        self.assertAlmostEqual(rates["<1"][1], 0.15)

    def test_a_source_sending_better_leads_scores_higher(self):
        store.save_quality(self.conn, self.day, {
            ("WagesAndCentrelinkMonthly", "3000+", 4, "Sold"): 48,
            ("WagesAndCentrelinkMonthly", "3000+", 4, "Decline"): 52,
            ("WagesAndCentrelinkMonthly", "<1", 0, "Sold"): 15,
            ("WagesAndCentrelinkMonthly", "<1", 0, "Decline"): 85,
        }, 200, STAMP)
        store.save_quality_by_affiliate(self.conn, self.day, {
            (1, "WagesAndCentrelinkMonthly", "3000+", 4, "Sold"): 100,   # high income
            (2, "WagesAndCentrelinkMonthly", "<1", 0, "Sold"): 100,      # low
        }, STAMP)

        quality = store.affiliate_quality(self.conn, "WagesAndCentrelinkMonthly", 30)
        self.assertAlmostEqual(quality[1]["expected"], 0.48)
        self.assertAlmostEqual(quality[2]["expected"], 0.15)

    def test_a_source_is_scored_against_its_own_population(self):
        """Internal traffic sells at 49% against external's 27%, so
        scoring an external source against everyone marks it down for
        the company it keeps."""
        store.save_affiliates(self.conn, self.day, "external",
                              [affiliate_row("Outside(1)")], None, STAMP)
        store.save_affiliates(self.conn, self.day, "internal",
                              [affiliate_row("Inside(2)")], None, STAMP)
        store.save_quality_by_affiliate(self.conn, self.day, {
            (1, "WagesAndCentrelinkMonthly", "3000+", 4, "Sold"): 20,
            (1, "WagesAndCentrelinkMonthly", "3000+", 4, "Decline"): 80,
            (2, "WagesAndCentrelinkMonthly", "3000+", 4, "Sold"): 60,
            (2, "WagesAndCentrelinkMonthly", "3000+", 4, "Decline"): 40,
        }, STAMP)

        external = store.band_sold_rates(self.conn, "WagesAndCentrelinkMonthly",
                                         30, "external")
        internal = store.band_sold_rates(self.conn, "WagesAndCentrelinkMonthly",
                                         30, "internal")
        self.assertAlmostEqual(external["3000+"][1], 0.20)
        self.assertAlmostEqual(internal["3000+"][1], 0.60)
        # and the external affiliate is judged against 20%, not 40%
        q = store.affiliate_quality(self.conn, "WagesAndCentrelinkMonthly", 30, "external")
        self.assertAlmostEqual(q[1]["expected"], 0.20)

    def test_test_affiliates_are_left_out_of_the_quality_figures_too(self):
        store.save_quality_by_affiliate(self.conn, self.day, {
            (144, "WagesAndCentrelinkMonthly", "3000+", 4, "Sold"): 128,
        }, STAMP)
        self.assertEqual(
            store.affiliate_quality(self.conn, "WagesAndCentrelinkMonthly", 30), {})
