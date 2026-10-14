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
    collected_at TEXT NOT NULL,
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
    conn.executescript(SCHEMA)
    _migrate_metric_names(conn)
    return conn


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
            " volume_comparison_pct, collected_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(business_date, source, r["name"], r["received"], r["accepted_tc"], r["bs_refreshed"], r["duplicate"], r["bs_extracted"], r["submitted"], r["sold"], r["decline"], r["redirect"], r["funded"], r["revenue"], r["commission"], r["bs_cost"], r["final_commission"], r["profit"], r["final_profit"],
              r["volume_comparison_pct"], collected_at) for r in rows])
        if totals:
            conn.execute(
                "INSERT OR REPLACE INTO affiliate_totals_day (business_date, source, received, accepted_tc, bs_refreshed, duplicate, bs_extracted, submitted, sold, decline, redirect, funded, revenue, commission, bs_cost, final_commission, profit, final_profit,"
                " collected_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (business_date, source, totals["received"], totals["accepted_tc"], totals["bs_refreshed"], totals["duplicate"], totals["bs_extracted"], totals["submitted"], totals["sold"], totals["decline"], totals["redirect"], totals["funded"], totals["revenue"], totals["commission"], totals["bs_cost"], totals["final_commission"], totals["profit"], totals["final_profit"], collected_at))


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


def lender_result_status(conn, days=30):
    """Pings and cost per status across the window."""
    return conn.execute(
        "SELECT status_id, SUM(pings), SUM(cost) FROM lender_result_day"
        " WHERE business_date >= ? GROUP BY status_id ORDER BY SUM(pings) DESC",
        (business_cutoff(days),)).fetchall()


def lender_result_league(conn, days=30, limit=40):
    """Per lender and tier: pings, how many were sold, and what they cost.

    Sold is status 1. Acceptance is sold over pings for that tier, which
    is the pingtree's own question: of the times we offered this lead to
    this tier, how often did it buy.
    """
    return conn.execute(
        "SELECT lender_id, lender_tier_id, SUM(pings) p,"
        " SUM(CASE WHEN status_id=1 THEN pings ELSE 0 END) sold,"
        " SUM(CASE WHEN status_id=2 THEN pings ELSE 0 END) declined,"
        " SUM(cost) cost FROM lender_result_day WHERE business_date >= ?"
        " GROUP BY lender_id, lender_tier_id HAVING p > 0"
        " ORDER BY p DESC LIMIT ?", (business_cutoff(days), limit)).fetchall()


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


def affiliate_league(conn, days=30, source="external"):
    return conn.execute(
        "SELECT affiliate_name, SUM(received) r, SUM(accepted_tc) tc, SUM(bs_extracted) bs,"
        " SUM(submitted) sub, SUM(sold) sold, SUM(decline) dec, SUM(revenue) rev,"
        " SUM(commission) com, SUM(bs_cost) bsc, SUM(profit) p FROM affiliate_day"
        " WHERE business_date >= ? AND source=? GROUP BY affiliate_name"
        " HAVING r > 0 ORDER BY p DESC LIMIT 60",
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
