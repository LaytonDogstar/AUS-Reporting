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
"""

from __future__ import annotations

import sqlite3

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
    affiliate_name TEXT NOT NULL,
    received INTEGER, accepted_tc INTEGER, bs_refreshed INTEGER, duplicate INTEGER,
    bs_extracted INTEGER, submitted INTEGER, sold INTEGER, decline INTEGER,
    redirect INTEGER, funded INTEGER,
    revenue REAL, commission REAL, bs_cost REAL, final_commission REAL,
    profit REAL, final_profit REAL, volume_comparison_pct REAL,
    collected_at TEXT NOT NULL,
    PRIMARY KEY (business_date, affiliate_name)
);
CREATE INDEX IF NOT EXISTS ad_date ON affiliate_day(business_date);

CREATE TABLE IF NOT EXISTS affiliate_totals_day (
    business_date TEXT PRIMARY KEY,
    received INTEGER, accepted_tc INTEGER, bs_refreshed INTEGER, duplicate INTEGER,
    bs_extracted INTEGER, submitted INTEGER, sold INTEGER, decline INTEGER,
    redirect INTEGER, funded INTEGER,
    revenue REAL, commission REAL, bs_cost REAL, final_commission REAL,
    profit REAL, final_profit REAL,
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
    conn.executescript(SCHEMA)
    return conn


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


def save_affiliates(conn, business_date, rows, totals, collected_at):
    with conn:
        conn.execute("DELETE FROM affiliate_day WHERE business_date=?", (business_date,))
        conn.executemany(
            "INSERT INTO affiliate_day (business_date, affiliate_name, received, accepted_tc, bs_refreshed, duplicate, bs_extracted, submitted, sold, decline, redirect, funded, revenue, commission, bs_cost, final_commission, profit, final_profit,"
            " volume_comparison_pct, collected_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(business_date, r["name"], r["received"], r["accepted_tc"], r["bs_refreshed"], r["duplicate"], r["bs_extracted"], r["submitted"], r["sold"], r["decline"], r["redirect"], r["funded"], r["revenue"], r["commission"], r["bs_cost"], r["final_commission"], r["profit"], r["final_profit"],
              r["volume_comparison_pct"], collected_at) for r in rows])
        if totals:
            conn.execute(
                "INSERT OR REPLACE INTO affiliate_totals_day (business_date, received, accepted_tc, bs_refreshed, duplicate, bs_extracted, submitted, sold, decline, redirect, funded, revenue, commission, bs_cost, final_commission, profit, final_profit,"
                " collected_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (business_date, totals["received"], totals["accepted_tc"], totals["bs_refreshed"], totals["duplicate"], totals["bs_extracted"], totals["submitted"], totals["sold"], totals["decline"], totals["redirect"], totals["funded"], totals["revenue"], totals["commission"], totals["bs_cost"], totals["final_commission"], totals["profit"], totals["final_profit"], collected_at))


def affiliate_league(conn, days=30):
    return conn.execute(
        "SELECT affiliate_name, SUM(received) r, SUM(accepted_tc) tc, SUM(bs_extracted) bs,"
        " SUM(submitted) sub, SUM(sold) sold, SUM(decline) dec, SUM(revenue) rev,"
        " SUM(commission) com, SUM(bs_cost) bsc, SUM(profit) p FROM affiliate_day"
        " WHERE business_date >= date('now', ?) GROUP BY affiliate_name"
        " HAVING r > 0 ORDER BY p DESC LIMIT 60", (f"-{days} days",)).fetchall()


def affiliate_funnel(conn, days=30):
    return conn.execute(
        "SELECT SUM(received), SUM(accepted_tc), SUM(bs_extracted), SUM(submitted),"
        " SUM(sold), SUM(decline), SUM(redirect), SUM(duplicate), SUM(funded)"
        " FROM affiliate_totals_day WHERE business_date >= date('now', ?)",
        (f"-{days} days",)).fetchone()


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
        " WHERE business_date >= date('now', ?) GROUP BY lender_tier_id, lender_tier_name"
        " HAVING s > 0 ORDER BY p DESC LIMIT 40", (f"-{days} days",)).fetchall()


def quality_distribution(conn, metric, days=30):
    return conn.execute(
        "SELECT band, band_order, result, SUM(n) FROM quality_day"
        " WHERE metric=? AND business_date >= date('now', ?)"
        " GROUP BY band, band_order, result ORDER BY band_order, result",
        (metric, f"-{days} days")).fetchall()


def quality_metrics(conn):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT metric FROM quality_day ORDER BY metric")]
