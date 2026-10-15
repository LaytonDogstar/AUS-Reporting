"""SQLite storage for collected reports.

What is stored, and what deliberately is not.

Lender tier figures are stored whole: a tier name, counts, revenue,
commission and profit describe a commercial relationship, not a person.

Lead quality is stored only as counts per band. A row of the quality
report is one applicant's affordability, and that does not belong
outside Azure, so the applicant never lands here - the distribution
does. Age becomes a band, gambling becomes a range, and what is kept is
how many fell in each, split by the lender's result so acceptance rate
by band is answerable.

Categorical fields keep their value because a label shared by thousands
is not personal data. A cardinality cap guards the case where a field
turns out to hold free text after all.

Affiliate figures carry the report they came from. There are two - the
normal affiliate report and the internal one - and the dev team confirmed
on 13 Oct 2026 that the two populations cannot be combined into a single
affiliate lead count. They are stored side by side and never added.

Dates are business dates in the API's own timezone, which is Sydney.
Every rolling window is cut on that date rather than on SQLite's UTC
`date('now')`, so a window means the same thing at any hour of the day.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    REPORT_TZ = ZoneInfo("Australia/Sydney")
except Exception:                               # noqa: BLE001 - no tzdata
    # Fixed AEST. Half the year this reads an hour behind Sydney, which
    # only ever delays calling a day complete - it never mislabels one.
    REPORT_TZ = timezone(timedelta(hours=10))

# The two affiliate reports. Kept apart, never summed: FF and non-FF
# affiliates are different populations and the dev team confirmed they
# can no longer be combined into one count. Which endpoint holds which
# population is not confirmed, so they are named after the report.
SOURCES = ("external", "internal")

# The affiliate reports return no AffiliateId, but the name ends in one:
# "Borrowly(59)". Lives here rather than in the collector because the
# migration below needs it too.
AFFILIATE_ID_IN_NAME = re.compile(r"\((\d+)\)\s*$")


def affiliate_id_from(name):
    m = AFFILIATE_ID_IN_NAME.search(name or "")
    return int(m.group(1)) if m else None


# Test traffic that the reports return alongside real affiliates. It
# sells at rates real traffic does not - DummyJourney sold 111 of 128
# leads at zero revenue over 30 days - so it flatters every rate it is
# counted in. Excluded from the pages, not from collection: the stored
# days still reconcile against the API's own totals.
TEST_AFFILIATE_IDS = (13, 144, 145, 162)        # OverflowTest, DummyJourney,
                                                # DummyImmediate, ClearScore Test

# The buying side's test traffic: "Dummy", tier "Dummy Buyer". Bought 111
# of 112 leads over 30 days at $0, so it would read as the best lender
# on the platform. Agreed with Layton 15 Oct 2026 to exclude it.
TEST_LENDER_IDS = (24,)

# Lenders left out of reporting for now. DCS (22) is an offline lender:
# not in the lender report, but in the ping log, buying every lead it is
# offered from internal sources at $10. Layton, 15 Oct 2026: leave out.
OFFLINE_LENDER_IDS = (22,)

# Everything the lender figures leave out. Collection keeps them all, so
# the stored days still reconcile against the API.
EXCLUDED_LENDER_IDS = TEST_LENDER_IDS + OFFLINE_LENDER_IDS


def _not_excluded_lender(column="lender_id"):
    ids = ",".join(str(i) for i in EXCLUDED_LENDER_IDS)
    return f" AND ({column} IS NULL OR {column} NOT IN ({ids}))"


def business_today():
    """Today as the API dates it. The reports run on Sydney time, so a
    UTC date is up to a day out when deciding what is collectable."""
    return datetime.now(REPORT_TZ).date()


def business_cutoff(days: int) -> str:
    """ISO date `days` back from today, for a rolling window."""
    return (business_today() - timedelta(days=days)).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS call (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    method TEXT NOT NULL,
    url TEXT NOT NULL,
    status INTEGER,
    bytes INTEGER,
    ms INTEGER,
    error TEXT
);

CREATE TABLE IF NOT EXISTS shape (
    call_id INTEGER NOT NULL REFERENCES call(id),
    ordinal INTEGER NOT NULL,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS shape_call ON shape(call_id, ordinal);

-- One row per lender tier per day. No personal data.
CREATE TABLE IF NOT EXISTS lender_tier_day (
    business_date TEXT NOT NULL,
    lender_tier_id INTEGER NOT NULL,
    lender_tier_name TEXT,
    ignored INTEGER, processed INTEGER, sent INTEGER,
    accepted INTEGER, blacklist INTEGER, redirect INTEGER,
    conversion_rate REAL, earning_per_lead REAL, extra_income REAL,
    revenue REAL, commission REAL, profit REAL, blacklist_price REAL,
    collected_at TEXT NOT NULL,
    PRIMARY KEY (business_date, lender_tier_id)
);
CREATE INDEX IF NOT EXISTS ltd_date ON lender_tier_day(business_date);

-- The report's own totals block, kept as reported rather than summed
-- from the tiers, so a disagreement between the two stays visible.
CREATE TABLE IF NOT EXISTS totals_day (
    business_date TEXT PRIMARY KEY,
    ignored INTEGER, processed INTEGER, sent INTEGER,
    accepted INTEGER, blacklist INTEGER, redirect INTEGER,
    conversion_rate REAL, extra_income REAL,
    revenue REAL, commission REAL, profit REAL, blacklist_price REAL,
    collected_at TEXT NOT NULL
);

-- Counts only. One row per (day, metric, band, lender result).
CREATE TABLE IF NOT EXISTS quality_day (
    business_date TEXT NOT NULL,
    metric TEXT NOT NULL,
    band TEXT NOT NULL,
    band_order INTEGER NOT NULL,
    result TEXT NOT NULL,
    n INTEGER NOT NULL,
    PRIMARY KEY (business_date, metric, band, result)
);
CREATE INDEX IF NOT EXISTS qd_date ON quality_day(business_date, metric, band_order);

CREATE TABLE IF NOT EXISTS quality_day_meta (
    business_date TEXT PRIMARY KEY,
    rows INTEGER NOT NULL,
    collected_at TEXT NOT NULL
);

-- One row per affiliate per day. AffiliateName is a trading name, not a
-- person. The API returns no AffiliateId, so the name is the key.
CREATE TABLE IF NOT EXISTS affiliate_day (
    business_date TEXT NOT NULL,
    source TEXT NOT NULL,
    affiliate_name TEXT NOT NULL,
    received INTEGER, accepted_tc INTEGER, bs_refreshed INTEGER, duplicate INTEGER,
    bs_extracted INTEGER, submitted INTEGER, sold INTEGER, decline INTEGER,
    redirect INTEGER, funded INTEGER,
    revenue REAL, commission REAL, bs_cost REAL, final_commission REAL,
    profit REAL, final_profit REAL, volume_comparison_pct REAL,
    affiliate_id INTEGER,
    collected_at TEXT NOT NULL,
    adword_spent REAL,
    PRIMARY KEY (business_date, source, affiliate_name)
);
CREATE INDEX IF NOT EXISTS ad_date ON affiliate_day(business_date);

CREATE TABLE IF NOT EXISTS affiliate_totals_day (
    business_date TEXT NOT NULL,
    source TEXT NOT NULL,
    received INTEGER, accepted_tc INTEGER, bs_refreshed INTEGER, duplicate INTEGER,
    bs_extracted INTEGER, submitted INTEGER, sold INTEGER, decline INTEGER,
    redirect INTEGER, funded INTEGER,
    revenue REAL, commission REAL, bs_cost REAL, final_commission REAL,
    profit REAL, final_profit REAL,
    collected_at TEXT NOT NULL,
    adword_spent REAL,
    PRIMARY KEY (business_date, source)
);

-- The ping log, reduced. One row per day, lender, tier and status.
-- LenderApplicationResults returns a row per lead and lender carrying
-- LeadId and LeadApplicationId; neither lands here, because a lead
-- identifier outside Azure is the thing this design exists to avoid.
-- What is kept is how many pings fell in each status, how many distinct
-- applications they covered, and what they cost.
CREATE TABLE IF NOT EXISTS lender_result_day (
    business_date TEXT NOT NULL,
    lender_id INTEGER NOT NULL,
    lender_tier_id INTEGER NOT NULL,
    status_id INTEGER NOT NULL,
    pings INTEGER NOT NULL,
    applications INTEGER NOT NULL,
    cost REAL,
    collected_at TEXT NOT NULL,
    PRIMARY KEY (business_date, lender_id, lender_tier_id, status_id)
);
CREATE INDEX IF NOT EXISTS lrd_date ON lender_result_day(business_date);

-- Day-level counts the per-status rows cannot be summed into: a lead or
-- an application appears under several lenders, so distinct counts have
-- to be taken once over the whole day.
CREATE TABLE IF NOT EXISTS lender_result_day_meta (
    business_date TEXT PRIMARY KEY,
    pings INTEGER NOT NULL,
    leads INTEGER NOT NULL,
    applications INTEGER NOT NULL,
    collected_at TEXT NOT NULL
);

-- Lead quality per affiliate: the same bands, counted for one source
-- at a time. LeadQualityReport takes an affiliateId filter, so this is
-- one call per active affiliate per day rather than a dev ask.
--
-- The lender result is kept, so a band's sold rate can be taken within
-- one population. Against everyone, every external source scores badly
-- and every internal one well - internal traffic sells at 49% against
-- 27% - which says nothing about the leads.
CREATE TABLE IF NOT EXISTS quality_affiliate_day (
    business_date TEXT NOT NULL,
    affiliate_id INTEGER NOT NULL,
    metric TEXT NOT NULL,
    band TEXT NOT NULL,
    band_order INTEGER NOT NULL,
    result TEXT NOT NULL,
    n INTEGER NOT NULL,
    PRIMARY KEY (business_date, affiliate_id, metric, band, result)
);
CREATE INDEX IF NOT EXISTS qad_date ON quality_affiliate_day(business_date, metric);

-- Lead quality per lender tier: what each tier was offered, let through
-- its filters, and bought, by band. Results are the tier's own:
-- FilterFailed, Ignored, Decline, Error, Sold. Leads the tier was never
-- offered ("Not Processed") are not stored - see the collector.
CREATE TABLE IF NOT EXISTS quality_tier_day (
    business_date TEXT NOT NULL,
    lender_tier_id INTEGER NOT NULL,
    metric TEXT NOT NULL,
    band TEXT NOT NULL,
    band_order INTEGER NOT NULL,
    result TEXT NOT NULL,
    n INTEGER NOT NULL,
    PRIMARY KEY (business_date, lender_tier_id, metric, band, result)
);
CREATE INDEX IF NOT EXISTS qtd_metric ON quality_tier_day(metric, business_date);

-- The affiliate lookup, from Affiliates. A snapshot, replaced whole.
-- That endpoint also returns each affiliate's portal username and
-- password and its Credfin secret key. None of those is read, let alone
-- stored: see reduce_affiliate_lookup, and the note in PUBLIC-API.md.
CREATE TABLE IF NOT EXISTS affiliate (
    affiliate_id INTEGER PRIMARY KEY,
    name TEXT,
    group_name TEXT,
    is_internal INTEGER,
    is_active INTEGER,
    commission_type TEXT,
    commission_value TEXT,
    collected_at TEXT NOT NULL
);

-- The ping log aggregated the other way: per affiliate and status, so
-- "what happens to this source's leads once they reach a lender" is
-- answerable. Separate from lender_result_day because crossing the two
-- would multiply 110 affiliates by 56 tiers for no gain.
CREATE TABLE IF NOT EXISTS affiliate_result_day (
    business_date TEXT NOT NULL,
    affiliate_id INTEGER NOT NULL,
    status_id INTEGER NOT NULL,
    pings INTEGER NOT NULL,
    applications INTEGER NOT NULL,
    cost REAL,
    collected_at TEXT NOT NULL,
    PRIMARY KEY (business_date, affiliate_id, status_id)
);
CREATE INDEX IF NOT EXISTS ard_date ON affiliate_result_day(business_date);

-- The ping log a third way: per affiliate, lender and status. Which
-- lenders buy a source's leads, and which sources a lender buys from.
CREATE TABLE IF NOT EXISTS affiliate_lender_day (
    business_date TEXT NOT NULL,
    affiliate_id INTEGER NOT NULL,
    lender_id INTEGER NOT NULL,
    status_id INTEGER NOT NULL,
    pings INTEGER NOT NULL,
    cost REAL,
    collected_at TEXT NOT NULL,
    PRIMARY KEY (business_date, affiliate_id, lender_id, status_id)
);
CREATE INDEX IF NOT EXISTS ald_date ON affiliate_lender_day(business_date);

-- The tier lookup, from LenderTiers. A snapshot, replaced whole on each
-- collection: it is what puts names on the ping log, whose rows carry
-- ids alone. The endpoint also returns a lender contact email, a logo
-- url and marketing copy; none of that is kept.
CREATE TABLE IF NOT EXISTS lender_tier (
    lender_tier_id INTEGER PRIMARY KEY,
    lender_tier_name TEXT,
    lender_id INTEGER,
    lender_name TEXT,
    code TEXT,
    price REAL,
    is_active INTEGER,
    collected_at TEXT NOT NULL
);

-- The ping log by the hour, for the Today and Yesterday pages. Hours
-- are Sydney hours: DateCreated is local time, the same clock the
-- reports date their days by. Leads are counted once, in the hour of
-- their first ping, against the affiliate that sent them; no lead
-- identifier is kept. `sold_leads` is how many of them sold at least
-- once: a lead can be bought by more than one lender, so sales can
-- outnumber leads and are no denominator for a buy-rate.
CREATE TABLE IF NOT EXISTS hour_leads (
    business_date TEXT NOT NULL,
    hour INTEGER NOT NULL,
    affiliate_id INTEGER NOT NULL,
    leads INTEGER NOT NULL,
    sold_leads INTEGER NOT NULL,
    PRIMARY KEY (business_date, hour, affiliate_id)
);

-- What reached a lender and what it bought, per hour, affiliate and
-- tier: enough to show one population at a time and every lender in
-- it. Only pings that reached a lender (Sold, Decline, Error) are kept;
-- the filtered and ignored 95% say nothing an hour-by-hour page needs.
CREATE TABLE IF NOT EXISTS hour_sales (
    business_date TEXT NOT NULL,
    hour INTEGER NOT NULL,
    affiliate_id INTEGER NOT NULL,
    lender_id INTEGER NOT NULL,
    lender_tier_id INTEGER NOT NULL,
    reached INTEGER NOT NULL,
    sold INTEGER NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (business_date, hour, affiliate_id, lender_id, lender_tier_id)
);
CREATE INDEX IF NOT EXISTS hs_date ON hour_sales(business_date);

-- Pings a lender's filters turned down, per hour, affiliate and lender:
-- with what reached it, how many it evaluated, so filter pass is
-- passed / (passed + failed). Per lender, not per tier - per tier this
-- would be the biggest table in the database for a question asked
-- about lenders. Ignored pings, which the lender never evaluated, are
-- left out.
CREATE TABLE IF NOT EXISTS hour_filtered (
    business_date TEXT NOT NULL,
    hour INTEGER NOT NULL,
    affiliate_id INTEGER NOT NULL,
    lender_id INTEGER NOT NULL,
    filtered INTEGER NOT NULL,
    PRIMARY KEY (business_date, hour, affiliate_id, lender_id)
);
CREATE INDEX IF NOT EXISTS hf_date ON hour_filtered(business_date);

-- When a day's hours were read and how far they reach. `complete` is 1
-- once the finished day has been collected; until then the rows are the
-- latest live read, and `as_of` is the newest ping in it.
CREATE TABLE IF NOT EXISTS hour_meta (
    business_date TEXT PRIMARY KEY,
    as_of TEXT,
    complete INTEGER NOT NULL,
    collected_at TEXT NOT NULL
);

-- Today's FF figures from the internal affiliate report, read live with
-- the ping log, so the Today page can put Google Ads spend beside what
-- it bought. Finished days take theirs from affiliate_day instead.
CREATE TABLE IF NOT EXISTS live_internal (
    business_date TEXT PRIMARY KEY,
    received INTEGER, sold INTEGER,
    revenue REAL, bs_cost REAL, adword_spent REAL, profit REAL,
    collected_at TEXT NOT NULL
);

-- Why a day is missing, so a gap is explained rather than silent.
CREATE TABLE IF NOT EXISTS collection (
    business_date TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    ms INTEGER,
    ran_at TEXT NOT NULL,
    PRIMARY KEY (business_date, kind)
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    _migrate_affiliate_source(conn)
    _migrate_quality_affiliate_result(conn)
    pairs_new = not _has_table(conn, "affiliate_lender_day")
    hours_new = _has_table(conn, "lender_result_day") and not _has_table(conn, "hour_filtered")
    conn.executescript(SCHEMA)
    if pairs_new:
        _recollect_ping_log(conn)
    _recollect_tier_quality(conn)
    _migrate_metric_names(conn)
    _migrate_affiliate_id(conn)
    _migrate_adword_spent(conn)
    if hours_new:
        _recollect_hours(conn)
    return conn


def _has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


def _recollect_ping_log(conn):
    """The affiliate-by-lender counts come from the ping log, so days
    collected before they existed have none. Marking the ping log
    uncollected for those days gets them refetched by the next pass -
    and nothing else, because collection skips what is already done."""
    with conn:
        conn.execute("DELETE FROM collection WHERE kind='lender_results'")


def _recollect_hours(conn):
    """Hours stored before the filtered counts existed have none, and
    would read as every lead passing every filter - into the typical
    day, too. So they are dropped, not kept, and the ping log refetched:
    a day comes back into the norm only with its filtered counts."""
    with conn:
        for table in ("hour_leads", "hour_sales", "hour_meta"):
            conn.execute(f"DELETE FROM {table}")
    _recollect_ping_log(conn)


def _recollect_tier_quality(conn):
    """Employment status and time at employer joined the per-tier counts
    on 15 Oct 2026. Days stored before then have neither, so they are
    marked uncollected and fetched again. Checked once per connect: a
    database that has the new rows, or none at all, is left alone."""
    stored = conn.execute("SELECT 1 FROM quality_tier_day LIMIT 1").fetchone()
    if not stored:
        return
    has = conn.execute("SELECT 1 FROM quality_tier_day WHERE metric='EmploymentStatus'"
                       " LIMIT 1").fetchone()
    if has:
        return
    with conn:
        conn.execute("DELETE FROM collection WHERE kind='quality_by_tier'")


def _migrate_affiliate_id(conn):
    """Add affiliate_day.affiliate_id, and fill it for rows already
    stored.

    Filling matters: a day already collected is never collected again,
    so without this those rows keep a null id for good - and everything
    that joins on the id, the dashboard included, silently shows
    nothing. The id is in the name they already carry, so no refetch is
    needed.
    """
    columns = {r[1] for r in conn.execute("PRAGMA table_info(affiliate_day)")}
    if "affiliate_id" not in columns:
        with conn:
            conn.execute("ALTER TABLE affiliate_day ADD COLUMN affiliate_id INTEGER")
    names = [r[0] for r in conn.execute(
        "SELECT DISTINCT affiliate_name FROM affiliate_day WHERE affiliate_id IS NULL")]
    filled = [(affiliate_id_from(n), n) for n in names]
    filled = [(i, n) for i, n in filled if i is not None]
    if filled:
        with conn:
            conn.executemany(
                "UPDATE affiliate_day SET affiliate_id=? WHERE affiliate_name=?"
                " AND affiliate_id IS NULL", filled)


def _migrate_adword_spent(conn):
    """Add adword_spent to both affiliate tables. The internal report
    always carried it and nothing read it, so days stored before are
    marked uncollected for that report alone, and refetch in about
    fifteen seconds each."""
    columns = {r[1] for r in conn.execute("PRAGMA table_info(affiliate_day)")}
    if "adword_spent" in columns:
        return
    with conn:
        conn.execute("ALTER TABLE affiliate_day ADD COLUMN adword_spent REAL")
        conn.execute("ALTER TABLE affiliate_totals_day ADD COLUMN adword_spent REAL")
        conn.execute("DELETE FROM collection WHERE kind='internal_affiliates'")


def _migrate_affiliate_source(conn):
    """Drop affiliate tables written before the two reports were split.

    Those rows are the normal report alone, with no column saying so, and
    the primary key cannot be changed in place. Nothing here is a system
    of record - the API regenerates it - so they are dropped and the days
    are marked uncollected, which costs a backfill and nothing else.
    """
    have = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "affiliate_day" not in have:
        return
    columns = {r[1] for r in conn.execute("PRAGMA table_info(affiliate_day)")}
    if "source" in columns:
        return
    with conn:
        conn.execute("DROP TABLE IF EXISTS affiliate_day")
        conn.execute("DROP TABLE IF EXISTS affiliate_totals_day")
        if "collection" in have:
            conn.execute("DELETE FROM collection WHERE kind IN ('affiliates',"
                         " 'internal_affiliates')")


# Metrics stored under a name the collector no longer uses. The API
# renamed its misnamed Centrelink field on 14 Oct 2026; days collected
# before that are the same measure and belong in the same series, so
# they are renamed in place rather than left as a second metric.
RENAMED_METRICS = {"CenterlinkMonthly": "WagesAndCentrelinkMonthly"}


def _migrate_quality_affiliate_result(conn):
    """Drop a per-affiliate quality table written before the result was
    kept. Those rows cannot be split by outcome after the fact, and the
    days refetch in about twenty seconds each."""
    have = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "quality_affiliate_day" not in have:
        return
    columns = {r[1] for r in conn.execute("PRAGMA table_info(quality_affiliate_day)")}
    if "result" in columns:
        return
    with conn:
        conn.execute("DROP TABLE quality_affiliate_day")
        if "collection" in have:
            conn.execute("DELETE FROM collection WHERE kind='quality_by_affiliate'")


def _migrate_metric_names(conn):
    for old, new in RENAMED_METRICS.items():
        rows = conn.execute("SELECT COUNT(*) FROM quality_day WHERE metric=?",
                            (old,)).fetchone()[0]
        if not rows:
            continue
        with conn:
            # A day could hold both names only if it were collected twice
            # across the rename; the counts are per (day, band, result),
            # so the old rows are dropped rather than doubled in.
            conn.execute(
                "DELETE FROM quality_day WHERE metric=? AND EXISTS ("
                "  SELECT 1 FROM quality_day AS k WHERE k.metric=?"
                "    AND k.business_date=quality_day.business_date)", (old, new))
            conn.execute("UPDATE quality_day SET metric=? WHERE metric=?", (new, old))


# ------------------------------------------------------------------ writes

def save_tiers(conn, business_date, rows, totals, collected_at):
    with conn:
        conn.execute("DELETE FROM lender_tier_day WHERE business_date=?", (business_date,))
        conn.executemany(
            "INSERT INTO lender_tier_day (business_date, lender_tier_id, lender_tier_name,"
            " ignored, processed, sent, accepted, blacklist, redirect, conversion_rate,"
            " earning_per_lead, extra_income, revenue, commission, profit, blacklist_price,"
            " collected_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(business_date, r["id"], r["name"], r["ignored"], r["processed"], r["sent"],
              r["accepted"], r["blacklist"], r["redirect"], r["conversion_rate"],
              r["earning_per_lead"], r["extra_income"], r["revenue"], r["commission"],
              r["profit"], r["blacklist_price"], collected_at) for r in rows])
        if totals:
            conn.execute(
                "INSERT OR REPLACE INTO totals_day (business_date, ignored, processed, sent,"
                " accepted, blacklist, redirect, conversion_rate, extra_income, revenue,"
                " commission, profit, blacklist_price, collected_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (business_date, totals["ignored"], totals["processed"], totals["sent"],
                 totals["accepted"], totals["blacklist"], totals["redirect"],
                 totals["conversion_rate"], totals["extra_income"], totals["revenue"],
                 totals["commission"], totals["profit"], totals["blacklist_price"],
                 collected_at))


def save_quality(conn, business_date, counts, row_count, collected_at):
    """counts: {(metric, band, band_order, result): n}"""
    with conn:
        conn.execute("DELETE FROM quality_day WHERE business_date=?", (business_date,))
        conn.executemany(
            "INSERT INTO quality_day (business_date, metric, band, band_order, result, n)"
            " VALUES (?,?,?,?,?,?)",
            [(business_date, m, b, o, r, n) for (m, b, o, r), n in sorted(counts.items())])
        conn.execute("INSERT OR REPLACE INTO quality_day_meta (business_date, rows, collected_at)"
                     " VALUES (?,?,?)", (business_date, row_count, collected_at))


def save_affiliates(conn, business_date, source, rows, totals, collected_at):
    """One report's rows for one day. `source` is which report they came
    from; rows from the two are never mixed or added together."""
    if source not in SOURCES:
        raise ValueError(f"unknown affiliate source: {source!r}")
    with conn:
        conn.execute("DELETE FROM affiliate_day WHERE business_date=? AND source=?",
                     (business_date, source))
        conn.executemany(
            "INSERT INTO affiliate_day (business_date, source, affiliate_name, received, accepted_tc, bs_refreshed, duplicate, bs_extracted, submitted, sold, decline, redirect, funded, revenue, commission, bs_cost, final_commission, profit, final_profit,"
            " volume_comparison_pct, affiliate_id, collected_at, adword_spent)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(business_date, source, r["name"], r["received"], r["accepted_tc"], r["bs_refreshed"], r["duplicate"], r["bs_extracted"], r["submitted"], r["sold"], r["decline"], r["redirect"], r["funded"], r["revenue"], r["commission"], r["bs_cost"], r["final_commission"], r["profit"], r["final_profit"],
              r["volume_comparison_pct"], r.get("affiliate_id"), collected_at,
              r.get("adword_spent")) for r in rows])
        if totals:
            conn.execute(
                "INSERT OR REPLACE INTO affiliate_totals_day (business_date, source, received, accepted_tc, bs_refreshed, duplicate, bs_extracted, submitted, sold, decline, redirect, funded, revenue, commission, bs_cost, final_commission, profit, final_profit,"
                " collected_at, adword_spent) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (business_date, source, totals["received"], totals["accepted_tc"], totals["bs_refreshed"], totals["duplicate"], totals["bs_extracted"], totals["submitted"], totals["sold"], totals["decline"], totals["redirect"], totals["funded"], totals["revenue"], totals["commission"], totals["bs_cost"], totals["final_commission"], totals["profit"], totals["final_profit"], collected_at,
                 totals.get("adword_spent")))


def save_quality_by_affiliate(conn, business_date, counts, collected_at):
    """counts: {(affiliate_id, metric, band, band_order, result): n}"""
    with conn:
        conn.execute("DELETE FROM quality_affiliate_day WHERE business_date=?",
                     (business_date,))
        conn.executemany(
            "INSERT INTO quality_affiliate_day (business_date, affiliate_id, metric,"
            " band, band_order, result, n) VALUES (?,?,?,?,?,?,?)",
            [(business_date, aid, metric, band, order, result, n)
             for (aid, metric, band, order, result), n in sorted(counts.items())])


def save_quality_by_tier(conn, business_date, counts, collected_at):
    """counts: {(lender_tier_id, metric, band, band_order, result): n}"""
    with conn:
        conn.execute("DELETE FROM quality_tier_day WHERE business_date=?",
                     (business_date,))
        conn.executemany(
            "INSERT INTO quality_tier_day (business_date, lender_tier_id, metric,"
            " band, band_order, result, n) VALUES (?,?,?,?,?,?,?)",
            [(business_date, tid, metric, band, order, result, n)
             for (tid, metric, band, order, result), n in sorted(counts.items())])


def band_sold_rates(conn, metric, days=30, source=None):
    """{band: (leads, sold rate)} - what a band is worth.

    Within one population when `source` is given, which is the only
    comparison that means anything: internal traffic sells at 49%
    against external's 27%, so scoring an external affiliate against
    everyone marks it down for the company it keeps.
    """
    cutoff = business_cutoff(days)
    if source is None:
        rows = conn.execute(
            "SELECT band, result, SUM(n) FROM quality_day"
            " WHERE metric=? AND business_date >= ? GROUP BY band, result",
            (metric, cutoff)).fetchall()
    else:
        rows = conn.execute(
            "SELECT q.band, q.result, SUM(q.n) FROM quality_affiliate_day q"
            " WHERE q.metric=? AND q.business_date >= ?" + _not_test("q.affiliate_id") +
            " AND q.affiliate_id IN (SELECT DISTINCT affiliate_id FROM affiliate_day"
            "   WHERE source=? AND affiliate_id IS NOT NULL)"
            " GROUP BY q.band, q.result", (metric, cutoff, source)).fetchall()
    totals: dict[str, list] = {}
    for band, result, n in rows:
        slot = totals.setdefault(band, [0, 0])
        slot[0] += n
        if result == "Sold":
            slot[1] += n
    return {band: (total, (sold / total) if total else 0)
            for band, (total, sold) in totals.items()}


def affiliate_quality(conn, metric, days=30, source=None):
    """Per affiliate: the band mix it sends, and what that mix is worth.

    `expected` is the sold rate this affiliate's mix would produce at
    population rates - a quality score that owes nothing to how well
    the leads were sold. Comparing it with the actual sold rate says
    which of the two a source is good at.
    """
    cutoff = business_cutoff(days)
    rates = band_sold_rates(conn, metric, days, source)
    rows = conn.execute(
        "SELECT affiliate_id, band, SUM(n) FROM quality_affiliate_day"
        " WHERE metric=? AND business_date >= ?" + _not_test() +
        " GROUP BY affiliate_id, band", (metric, cutoff)).fetchall()
    by_affiliate: dict[int, dict[str, int]] = {}
    for aid, band, n in rows:
        by_affiliate.setdefault(aid, {})[band] = n

    out = {}
    for aid, bands in by_affiliate.items():
        leads = sum(bands.values())
        if not leads:
            continue
        expected = sum((bands.get(band, 0) / leads) * rate
                       for band, (_n, rate) in rates.items())
        out[aid] = {"leads": leads, "expected": expected, "bands": bands}
    return out


def save_lender_results(conn, business_date, counts, meta, collected_at):
    """counts: {(lender_id, tier_id, status_id): (pings, applications, cost)}"""
    with conn:
        conn.execute("DELETE FROM lender_result_day WHERE business_date=?", (business_date,))
        conn.executemany(
            "INSERT INTO lender_result_day (business_date, lender_id, lender_tier_id,"
            " status_id, pings, applications, cost, collected_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [(business_date, lender, tier, status, pings, apps, cost, collected_at)
             for (lender, tier, status), (pings, apps, cost) in sorted(counts.items())])
        conn.execute(
            "INSERT OR REPLACE INTO lender_result_day_meta (business_date, pings, leads,"
            " applications, collected_at) VALUES (?,?,?,?,?)",
            (business_date, meta["pings"], meta["leads"], meta["applications"], collected_at))


def save_affiliate_lender_results(conn, business_date, counts, collected_at):
    """counts: {(affiliate_id, lender_id, status_id): (pings, cost)}"""
    with conn:
        conn.execute("DELETE FROM affiliate_lender_day WHERE business_date=?",
                     (business_date,))
        conn.executemany(
            "INSERT INTO affiliate_lender_day (business_date, affiliate_id, lender_id,"
            " status_id, pings, cost, collected_at) VALUES (?,?,?,?,?,?,?)",
            [(business_date, aff, lender, status, pings, cost, collected_at)
             for (aff, lender, status), (pings, cost) in sorted(counts.items())])


def save_affiliate_lookup(conn, rows, collected_at):
    """The affiliate lookup, replaced whole."""
    with conn:
        conn.execute("DELETE FROM affiliate")
        conn.executemany(
            "INSERT INTO affiliate (affiliate_id, name, group_name, is_internal,"
            " is_active, commission_type, commission_value, collected_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [(r["id"], r["name"], r["group_name"], 1 if r["is_internal"] else 0,
              1 if r["is_active"] else 0, r["commission_type"], r["commission_value"],
              collected_at) for r in rows])


def save_affiliate_results(conn, business_date, counts, collected_at):
    """counts: {(affiliate_id, status_id): (pings, applications, cost)}"""
    with conn:
        conn.execute("DELETE FROM affiliate_result_day WHERE business_date=?",
                     (business_date,))
        conn.executemany(
            "INSERT INTO affiliate_result_day (business_date, affiliate_id, status_id,"
            " pings, applications, cost, collected_at) VALUES (?,?,?,?,?,?,?)",
            [(business_date, affiliate, status, pings, apps, cost, collected_at)
             for (affiliate, status), (pings, apps, cost) in sorted(counts.items())])


def save_hours(conn, business_date, leads, sales, as_of, complete, collected_at,
               filtered=None):
    """One day of the ping log by the hour, replaced whole.

    leads: {(hour, affiliate_id): (leads, sold_leads)}
    sales: {(hour, affiliate_id, lender_id, tier_id): (reached, sold, value)}
    filtered: {(hour, affiliate_id, lender_id): pings that failed its filters}

    A live read of today is replaced by the next one; a finished day,
    `complete`, by nothing - a live read never overwrites it.
    """
    with conn:
        done = conn.execute("SELECT complete FROM hour_meta WHERE business_date=?",
                            (business_date,)).fetchone()
        if done and done[0] and not complete:
            return False
        conn.execute("DELETE FROM hour_leads WHERE business_date=?", (business_date,))
        conn.execute("DELETE FROM hour_sales WHERE business_date=?", (business_date,))
        conn.execute("DELETE FROM hour_filtered WHERE business_date=?", (business_date,))
        conn.executemany(
            "INSERT INTO hour_filtered (business_date, hour, affiliate_id, lender_id, filtered)"
            " VALUES (?,?,?,?,?)",
            [(business_date, h, a, l, n) for (h, a, l), n in sorted((filtered or {}).items())])
        conn.executemany(
            "INSERT INTO hour_leads (business_date, hour, affiliate_id, leads, sold_leads)"
            " VALUES (?,?,?,?,?)",
            [(business_date, h, a, n, sold) for (h, a), (n, sold) in sorted(leads.items())])
        conn.executemany(
            "INSERT INTO hour_sales (business_date, hour, affiliate_id, lender_id,"
            " lender_tier_id, reached, sold, value) VALUES (?,?,?,?,?,?,?,?)",
            [(business_date, h, a, l, t, r, s_, round(v, 2))
             for (h, a, l, t), (r, s_, v) in sorted(sales.items())])
        conn.execute(
            "INSERT OR REPLACE INTO hour_meta (business_date, as_of, complete, collected_at)"
            " VALUES (?,?,?,?)", (business_date, as_of, 1 if complete else 0, collected_at))
    return True


def save_live_internal(conn, business_date, totals, collected_at):
    """Today's FF figures, from the internal report read live."""
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO live_internal (business_date, received, sold, revenue,"
            " bs_cost, adword_spent, profit, collected_at) VALUES (?,?,?,?,?,?,?,?)",
            (business_date, totals["received"], totals["sold"], totals["revenue"],
             totals["bs_cost"], totals["adword_spent"], totals["profit"], collected_at))


def is_internal_map(conn):
    """affiliate id -> True for an internal (FF) source."""
    return {r[0]: bool(r[1]) for r in conn.execute(
        "SELECT affiliate_id, is_internal FROM affiliate")}


def _in_source(conn, source):
    """SQL keeping one population's affiliates, test traffic left out.
    An affiliate missing from a loaded lookup counts as external: every
    internal source is in it, because the lookup is where 'internal'
    comes from. With no lookup at all, nothing is kept.

    `all` keeps both. That is sound here and only here: the hour tables
    come from the ping log, which counts an FF lead and an external one
    the same way. The two affiliate reports do not - the dev team said
    on 13 Oct 2026 that FF counts carry their own computation - so the
    pages built on those reports still show one population at a time."""
    test = ",".join(str(i) for i in TEST_AFFILIATE_IDS)
    if source == "all":
        return f" AND affiliate_id NOT IN ({test})"
    known = is_internal_map(conn)
    if not known:
        # Without the lookup nothing says which sources are FF, and
        # calling them all external would add the two populations
        # together. Show nothing until it is in.
        return " AND 0"
    internal = [a for a, i in known.items() if i]
    ids = ",".join(str(i) for i in internal) or "NULL"
    op = "IN" if source == "internal" else "NOT IN"
    return f" AND affiliate_id {op} ({ids}) AND affiliate_id NOT IN ({test})"


def hour_days(conn, before, limit=60):
    """Finished days with hours stored, newest first, before `before`."""
    return [r[0] for r in conn.execute(
        "SELECT business_date FROM hour_meta WHERE complete=1 AND business_date < ?"
        " ORDER BY business_date DESC LIMIT ?", (before, limit))]


def hour_meta(conn, business_date):
    r = conn.execute("SELECT as_of, complete, collected_at FROM hour_meta"
                     " WHERE business_date=?", (business_date,)).fetchone()
    return {"as_of": r[0], "complete": bool(r[1]), "collected_at": r[2]} if r else None


def hour_rows(conn, dates, source):
    """(leads, sales, filtered rows) for the given days and one population,
    or both (`all`).

    leads: (date, hour, affiliate_id, leads, sold_leads)
    filtered: (date, hour, affiliate_id, lender_id, filtered)
    sales: (date, hour, affiliate_id, lender_id, tier_id, reached, sold, value)
    """
    if not dates:
        return [], [], []
    marks = ",".join("?" * len(dates))
    where = _in_source(conn, source)
    leads = conn.execute(
        f"SELECT business_date, hour, affiliate_id, leads, sold_leads FROM hour_leads"
        f" WHERE business_date IN ({marks})" + where, dates).fetchall()
    sales = conn.execute(
        f"SELECT business_date, hour, affiliate_id, lender_id, lender_tier_id, reached,"
        f" sold, value FROM hour_sales WHERE business_date IN ({marks})" + where +
        _not_excluded_lender(), dates).fetchall()
    filtered = conn.execute(
        f"SELECT business_date, hour, affiliate_id, lender_id, filtered FROM hour_filtered"
        f" WHERE business_date IN ({marks})" + where + _not_excluded_lender(), dates).fetchall()
    return leads, sales, filtered


def ff_money(conn, business_date):
    """FF revenue, BS cost, Ads spend and profit for one day: the stored
    internal report for a finished day, the live read for today.

    The report's own totals block carries 0 for Ads spend on finished
    days while its rows carry the real figure - all of it on one source -
    so the rows are summed rather than the totals read.
    """
    r = conn.execute(
        "SELECT SUM(received), SUM(sold), SUM(revenue), SUM(bs_cost), SUM(adword_spent),"
        " SUM(profit), MAX(collected_at), COUNT(*) FROM affiliate_day"
        " WHERE business_date=? AND source='internal'", (business_date,)).fetchone()
    if r and r[7]:
        return {"received": r[0], "sold": r[1], "revenue": r[2], "bs_cost": r[3],
                "adword_spent": r[4], "profit": r[5], "collected_at": r[6], "live": False}
    r = conn.execute(
        "SELECT received, sold, revenue, bs_cost, adword_spent, profit, collected_at"
        " FROM live_internal WHERE business_date=?", (business_date,)).fetchone()
    if r:
        return {"received": r[0], "sold": r[1], "revenue": r[2], "bs_cost": r[3],
                "adword_spent": r[4], "profit": r[5], "collected_at": r[6], "live": True}
    return None


def ff_money_daily(conn, days=30):
    """Per day: FF revenue, BS cost, Ads spend and profit, from the
    internal report's rows."""
    return conn.execute(
        "SELECT business_date, SUM(revenue), SUM(bs_cost), SUM(COALESCE(adword_spent, 0)),"
        " SUM(profit), SUM(received), SUM(sold) FROM affiliate_day"
        " WHERE business_date >= ? AND source='internal'"
        " GROUP BY business_date ORDER BY business_date",
        (business_cutoff(days),)).fetchall()


# The metric the quality score is taken over. Monotonic across every
# band on thirty days - 15.1% sold under a dollar a month, 47.7% above
# three thousand - and populated for every lead, which the gambling and
# dishonour fields are not.
QUALITY_METRIC = "WagesAndCentrelinkMonthly"

# Below this, an affiliate's own sold rate is noise and the index it
# produces is worse than nothing: one lead that sold reads as 2.62x,
# which is a number people act on. The expected rate is still shown -
# that is a property of the mix, not of the sample size.
MIN_SCORED_LEADS = 100


def scorecard(conn, days=30, limit=80, metric=QUALITY_METRIC):
    """One row per affiliate: what it sends, what it earns, what it is worth.

    The affiliate reports are keyed by name and the ping log by id, so
    the id parsed out of the report name is what joins them. Volume and
    money come from the reports; reach and acceptance from the ping log.

    Grouped by name rather than by id, deliberately. The name is on
    every row; the id is not, on days collected before it was parsed
    out. Grouping by id split those affiliates into two rows - one with
    an id and one without - which is a reporting error, not a gap.
    """
    cutoff = business_cutoff(days)
    rows = conn.execute(
        "SELECT MAX(a.affiliate_id), a.affiliate_name, a.source,"
        " SUM(a.received) leads, SUM(a.sold) sold, SUM(a.revenue) revenue,"
        " SUM(a.commission) commission, SUM(a.bs_cost) bs_cost, SUM(a.profit) profit"
        " FROM affiliate_day a WHERE a.business_date >= ?" + _not_test("a.affiliate_id") +
        " GROUP BY a.affiliate_name, a.source HAVING leads > 0",
        (cutoff,)).fetchall()
    known = {r[0]: r[1] for r in conn.execute("SELECT affiliate_id, name FROM affiliate")}
    quality = {source: affiliate_quality(conn, metric, days, source)
               for source in SOURCES}
    pings = {r[0]: r[1:] for r in conn.execute(
        "SELECT affiliate_id,"
        " SUM(CASE WHEN status_id IN (1,2,3) THEN pings ELSE 0 END) reached,"
        " SUM(CASE WHEN status_id=1 THEN pings ELSE 0 END) ping_sold"
        # Per affiliate and lender, so excluded lenders can be left out.
        " FROM affiliate_lender_day WHERE business_date >= ?" + _not_excluded_lender() +
        " GROUP BY affiliate_id",
        (cutoff,))}
    out = []
    for aid, name, source, leads, sold, revenue, commission, bs_cost, profit in rows:
        reached, ping_sold = pings.get(aid, (0, 0))
        q = quality.get(source, {}).get(aid)
        sold_rate = (sold / leads) if leads else 0
        expected = q["expected"] if q else None
        out.append({
            # What this source's lead mix is worth at population rates,
            # and how its actual sold rate compares. Above 1.00 means it
            # sells better than its leads alone would predict.
            "expected": expected,
            "quality_index": ((sold_rate / expected)
                              if expected and leads >= MIN_SCORED_LEADS else None),
            # The lookup's name where the id resolved, else the report's
            # own, which ends in the id: "Borrowly(59)".
            "id": aid, "name": known.get(aid) or name, "source": source, "leads": leads, "sold": sold,
            "sold_rate": sold_rate,
            "revenue": revenue or 0, "commission": commission or 0,
            "bs_cost": bs_cost or 0, "profit": profit or 0,
            "revenue_per_lead": (revenue or 0) / leads if leads else 0,
            "profit_per_lead": (profit or 0) / leads if leads else 0,
            "reached": reached, "acceptance": (ping_sold / reached) if reached else None,
        })
    out.sort(key=lambda r: -r["profit_per_lead"])
    return out[:limit]


def save_lender_lookup(conn, rows, collected_at):
    """The tier lookup, replaced whole. Ids are stable; names are not."""
    with conn:
        conn.execute("DELETE FROM lender_tier")
        conn.executemany(
            "INSERT INTO lender_tier (lender_tier_id, lender_tier_name, lender_id,"
            " lender_name, code, price, is_active, collected_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [(r["id"], r["name"], r["lender_id"], r["lender_name"], r["code"],
              r["price"], 1 if r["is_active"] else 0, collected_at) for r in rows])


def lender_lookup(conn):
    """{tier_id: (tier_name, lender_id, lender_name)} - what names the ping log."""
    return {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        "SELECT lender_tier_id, lender_tier_name, lender_id, lender_name FROM lender_tier")}


def lender_result_status(conn, days=30):
    """Pings and cost per status across the window."""
    return conn.execute(
        "SELECT status_id, SUM(pings), SUM(cost) FROM lender_result_day"
        " WHERE business_date >= ?" + _not_excluded_lender() + " GROUP BY status_id ORDER BY SUM(pings) DESC",
        (business_cutoff(days),)).fetchall()


# Statuses where the ping actually got to a lender. FilterFailed (0) and
# Ignored (4) are stopped before that, and measured 14 Oct 2026 they are
# 95% of all rows - so a rate taken over every ping is not an acceptance
# rate. Sold + Decline + Error reproduces the lender report's `sent` to
# within 0.2%, which is what fixes the denominator.
REACHED_STATUSES = (1, 2, 3)


def lender_result_league(conn, days=30, limit=40):
    """Per lender and tier: what reached it, what it bought, what it paid.

    Acceptance is sold over pings that reached the lender, not over every
    ping: the pingtree filters most of them out first, and a rate over
    all of them answers a question nobody asked.

    `cost` on a ping is the tier's list price, carried on every row
    whatever the outcome, so only the sold rows are money. That column
    is summed for sold alone.
    """
    return conn.execute(
        "SELECT lender_id, lender_tier_id, SUM(pings) p,"
        " SUM(CASE WHEN status_id IN (1,2,3) THEN pings ELSE 0 END) reached,"
        " SUM(CASE WHEN status_id=1 THEN pings ELSE 0 END) sold,"
        " SUM(CASE WHEN status_id=2 THEN pings ELSE 0 END) declined,"
        " SUM(CASE WHEN status_id=1 THEN cost ELSE 0 END) sold_value"
        " FROM lender_result_day WHERE business_date >= ?" + _not_excluded_lender() +
        " GROUP BY lender_id, lender_tier_id HAVING reached > 0"
        " ORDER BY reached DESC LIMIT ?", (business_cutoff(days), limit)).fetchall()


def lender_result_reach(conn, days=30):
    """(all pings, reached a lender, sold, value of the sold)."""
    return conn.execute(
        "SELECT SUM(pings),"
        " SUM(CASE WHEN status_id IN (1,2,3) THEN pings ELSE 0 END),"
        " SUM(CASE WHEN status_id=1 THEN pings ELSE 0 END),"
        " SUM(CASE WHEN status_id=1 THEN cost ELSE 0 END)"
        " FROM lender_result_day WHERE business_date >= ?" + _not_excluded_lender(),
        (business_cutoff(days),)).fetchone()


def tier_names(conn, days=30):
    """{lender_tier_id: name} from the lender report, which carries both.

    The ping log returns ids alone, so this is what puts names on it.
    Whether the two id spaces agree is not assumed - the caller reports
    how many resolved, so a mismatch shows rather than hides.
    """
    return {r[0]: r[1] for r in conn.execute(
        "SELECT lender_tier_id, lender_tier_name FROM lender_tier_day"
        " WHERE business_date >= ? AND lender_tier_name IS NOT NULL"
        " AND lender_tier_name <> '' GROUP BY lender_tier_id",
        (business_cutoff(days),))}


def lender_result_totals(conn, days=30):
    """Day-level distinct counts, which per-status rows cannot give."""
    return conn.execute(
        "SELECT SUM(pings), SUM(leads), SUM(applications) FROM lender_result_day_meta"
        " WHERE business_date >= ?", (business_cutoff(days),)).fetchone()


def _not_test(column="affiliate_id"):
    """SQL excluding test affiliates. A null id is kept: it means the
    row predates the id being parsed, not that it is test traffic."""
    ids = ",".join(str(i) for i in TEST_AFFILIATE_IDS)
    return f" AND ({column} IS NULL OR {column} NOT IN ({ids}))"


def affiliate_league(conn, days=30, source="external"):
    return conn.execute(
        "SELECT affiliate_name, SUM(received) r, SUM(accepted_tc) tc, SUM(bs_extracted) bs,"
        " SUM(submitted) sub, SUM(sold) sold, SUM(decline) dec, SUM(revenue) rev,"
        " SUM(commission) com, SUM(bs_cost) bsc, SUM(profit) p FROM affiliate_day"
        " WHERE business_date >= ? AND source=?" + _not_test() +
        " GROUP BY affiliate_name HAVING r > 0 ORDER BY p DESC LIMIT 60",
        (business_cutoff(days), source)).fetchall()


def affiliate_funnel(conn, days=30, source="external"):
    """One report's funnel. There is deliberately no call that sums the
    two: the populations cannot be combined into one count."""
    return conn.execute(
        "SELECT SUM(received), SUM(accepted_tc), SUM(bs_extracted), SUM(submitted),"
        " SUM(sold), SUM(decline), SUM(redirect), SUM(duplicate), SUM(funded)"
        " FROM affiliate_totals_day WHERE business_date >= ? AND source=?",
        (business_cutoff(days), source)).fetchone()


def mark(conn, business_date, kind, status, detail=None, ms=None, ran_at=""):
    with conn:
        conn.execute("INSERT OR REPLACE INTO collection (business_date, kind, status, detail, ms, ran_at)"
                     " VALUES (?,?,?,?,?,?)", (business_date, kind, status, detail, ms, ran_at))


# ------------------------------------------------------------------- reads

def collected_days(conn, kind):
    return {r[0] for r in conn.execute(
        "SELECT business_date FROM collection WHERE kind=? AND status='ok'", (kind,))}


def totals_series(conn, limit=90):
    return conn.execute(
        "SELECT business_date, sent, accepted, conversion_rate, revenue, commission, profit,"
        " processed, ignored, redirect FROM totals_day ORDER BY business_date DESC LIMIT ?",
        (limit,)).fetchall()


def tier_league(conn, days=30):
    return conn.execute(
        "SELECT lender_tier_name, SUM(sent) s, SUM(accepted) a, SUM(revenue) rev,"
        " SUM(commission) com, SUM(profit) p FROM lender_tier_day"
        " WHERE business_date >= ? GROUP BY lender_tier_id, lender_tier_name"
        " HAVING s > 0 ORDER BY p DESC LIMIT 40", (business_cutoff(days),)).fetchall()


def quality_distribution(conn, metric, days=30):
    return conn.execute(
        "SELECT band, band_order, result, SUM(n) FROM quality_day"
        " WHERE metric=? AND business_date >= ?"
        " GROUP BY band, band_order, result ORDER BY band_order, result",
        (metric, business_cutoff(days))).fetchall()


def quality_metrics(conn):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT metric FROM quality_day ORDER BY metric")]


# ----------------------------------------------------------------- lenders
#
# A lender is the sum of its tiers. Tiers are where filters and prices
# live, but the question asked is about lenders - is this lender buying
# a particular customer - and Moneyspot alone runs 28 tiers.

# The lender report's names for the three stages a lead goes through at
# a tier. `sent` is a lead that passed every one of the tier's filters
# (Layton, 15 Oct 2026); `redirect` is a sale that made it to the
# lender's own page.
TIER_RESULTS_PASSED = ("Sold", "Decline", "Error")


def _not_test_lender(column="t.lender_id"):
    return _not_excluded_lender(column)


# A tier missing from the lookup still belongs somewhere: it stands as
# its own lender, keyed by the negative tier id so it cannot collide.
_LENDER_ID = "COALESCE(t.lender_id, -d.lender_tier_id)"
_LENDER_NAME = "COALESCE(NULLIF(TRIM(t.lender_name), ''), d.lender_tier_name)"


def lender_daily(conn, days=30):
    """[(lender_id, lender_name, day, processed, sent, accepted,
    redirect, revenue, commission, profit)] from the lender report,
    tiers summed."""
    return conn.execute(
        f"SELECT {_LENDER_ID}, MAX({_LENDER_NAME}), d.business_date,"
        " SUM(d.processed), SUM(d.sent), SUM(d.accepted), SUM(d.redirect),"
        " SUM(d.revenue), SUM(d.commission), SUM(d.profit) FROM lender_tier_day d"
        " LEFT JOIN lender_tier t ON t.lender_tier_id = d.lender_tier_id"
        " WHERE d.business_date >= ?" + _not_test_lender() +
        f" GROUP BY {_LENDER_ID}, d.business_date",
        (business_cutoff(days),)).fetchall()


def lender_tier_totals(conn, days=30):
    """Per tier over the window, with its lender and list price."""
    return conn.execute(
        f"SELECT {_LENDER_ID}, d.lender_tier_id, MAX(d.lender_tier_name), MAX(t.price),"
        " SUM(d.processed), SUM(d.sent), SUM(d.accepted), SUM(d.redirect),"
        " SUM(d.revenue), SUM(d.commission), SUM(d.profit) FROM lender_tier_day d"
        " LEFT JOIN lender_tier t ON t.lender_tier_id = d.lender_tier_id"
        " WHERE d.business_date >= ?" + _not_test_lender() +
        f" GROUP BY {_LENDER_ID}, d.lender_tier_id HAVING SUM(d.processed) > 0",
        (business_cutoff(days),)).fetchall()


def lender_demographics(conn, metric, days=30):
    """Per lender and band: offered, passed the filters, bought.

    {lender_id: {band: [offered, passed, sold]}}, plus the bands in
    order. Offered is every lead the lender's tiers were offered;
    passed is what got through the filters (sold, declined, or an
    error from the lender); sold is what it bought.

    A lead offered to two tiers of one lender counts twice, as it does
    in the lender report's own sums.
    """
    passed = ",".join(f"'{r}'" for r in TIER_RESULTS_PASSED)
    rows = conn.execute(
        f"SELECT COALESCE(t.lender_id, -q.lender_tier_id), q.band, q.band_order,"
        f" SUM(q.n), SUM(CASE WHEN q.result IN ({passed}) THEN q.n ELSE 0 END),"
        " SUM(CASE WHEN q.result='Sold' THEN q.n ELSE 0 END)"
        " FROM quality_tier_day q"
        " LEFT JOIN lender_tier t ON t.lender_tier_id = q.lender_tier_id"
        " WHERE q.metric=? AND q.business_date >= ?" + _not_test_lender() +
        " GROUP BY 1, q.band, q.band_order",
        (metric, business_cutoff(days))).fetchall()
    by_lender: dict[int, dict[str, list]] = {}
    order: dict[str, int] = {}
    for lid, band, band_order, offered, ok, sold in rows:
        by_lender.setdefault(lid, {})[band] = [offered, ok, sold]
        order[band] = band_order
    return by_lender, [b for b, _ in sorted(order.items(), key=lambda bo: (bo[1], bo[0]))]


def lender_demographic_metrics(conn):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT metric FROM quality_tier_day ORDER BY metric")]


def affiliate_lender_daily(conn, days=30):
    """[(day, affiliate_id, lender_id, offered, passed, sold, value)].

    Offered is every ping; passed the filters is sold, declined or
    errored - the lender report's `sent`; value is the list price of
    what sold, which is what the lender paid. Test affiliates and
    excluded lenders are left out.
    """
    return conn.execute(
        "SELECT business_date, affiliate_id, lender_id, SUM(pings),"
        " SUM(CASE WHEN status_id IN (1,2,3) THEN pings ELSE 0 END),"
        " SUM(CASE WHEN status_id=1 THEN pings ELSE 0 END),"
        " SUM(CASE WHEN status_id=1 THEN cost ELSE 0 END)"
        " FROM affiliate_lender_day WHERE business_date >= ?" + _not_test() +
        _not_excluded_lender() +
        " GROUP BY business_date, affiliate_id, lender_id",
        (business_cutoff(days),)).fetchall()


def lender_names(conn):
    """{lender_id: name} from the tier lookup."""
    return {r[0]: (r[1] or "").strip() or f"Lender {r[0]}" for r in conn.execute(
        "SELECT lender_id, MAX(lender_name) FROM lender_tier GROUP BY lender_id")}


def lender_demographics_daily(conn, metric, days=30):
    """[(day, lender_id, band, band_order, offered, sold)] - the mix a
    lender was offered and bought, a day at a time, tiers summed."""
    return conn.execute(
        "SELECT q.business_date, COALESCE(t.lender_id, -q.lender_tier_id), q.band,"
        " q.band_order, SUM(q.n), SUM(CASE WHEN q.result='Sold' THEN q.n ELSE 0 END)"
        " FROM quality_tier_day q"
        " LEFT JOIN lender_tier t ON t.lender_tier_id = q.lender_tier_id"
        " WHERE q.metric=? AND q.business_date >= ?" + _not_test_lender() +
        " GROUP BY 1, 2, q.band, q.band_order",
        (metric, business_cutoff(days))).fetchall()


def tier_demographics_daily(conn, metric, days=30):
    """[(day, lender_id, lender_tier_id, band, band_order, offered, sold)]
    - the same as lender_demographics_daily, kept tier by tier so a
    lender's profile can be opened for one of its tiers."""
    return conn.execute(
        "SELECT q.business_date, COALESCE(t.lender_id, -q.lender_tier_id), q.lender_tier_id,"
        " q.band, q.band_order, SUM(q.n), SUM(CASE WHEN q.result='Sold' THEN q.n ELSE 0 END)"
        " FROM quality_tier_day q"
        " LEFT JOIN lender_tier t ON t.lender_tier_id = q.lender_tier_id"
        " WHERE q.metric=? AND q.business_date >= ?" + _not_test_lender() +
        " GROUP BY 1, 2, 3, q.band, q.band_order",
        (metric, business_cutoff(days))).fetchall()
