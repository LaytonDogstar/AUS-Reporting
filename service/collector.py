"""Fetch the reports, reduce them to things worth keeping, and store.

Runs one day at a time. The reports take fifteen to seventeen seconds
each, so nothing here can happen while a user waits; a background thread
fills in whatever days are missing and the pages read what it stored.
"""

from __future__ import annotations

import base64
import json
import re
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import store

API_BASE = os.environ.get("OVERFLOW_API_BASE", "https://admin.overflowhub.com.au").rstrip("/")
API_USER = os.environ.get("OVERFLOW_API_USER", "")
API_PASSWORD = os.environ.get("OVERFLOW_API_PASSWORD", "")
TIMEOUT = int(os.environ.get("API_TIMEOUT", "120"))
BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS", "30"))
DAYS_PER_WAKE = int(os.environ.get("DAYS_PER_WAKE", "4"))
WAKE_SECONDS = int(os.environ.get("WAKE_SECONDS", "1800"))
# On an empty database - a first deploy, or a restart without a volume -
# fill the history in one unattended pass rather than four days every
# half hour. Nothing collected here is a system of record: the API can
# regenerate all of it, so a lost database should cost time, not data.
INITIAL_DAYS = int(os.environ.get("INITIAL_DAYS", "90"))

# Above this many distinct values a "categorical" field is not a
# category at all, and storing its values would be storing content.
MAX_CARDINALITY = 50


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ fetch

def fetch(url, method="GET", body=None, authenticate=True):
    req = urllib.request.Request(url, method=method)
    req.add_header("Accept", "application/json")
    if authenticate and API_USER:
        token = base64.b64encode(f"{API_USER}:{API_PASSWORD}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, data=data, timeout=TIMEOUT,
                                    context=ssl.create_default_context()) as resp:
            return resp.status, resp.read(), int((time.monotonic() - started) * 1000), None
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), int((time.monotonic() - started) * 1000), None
    except Exception as exc:                        # noqa: BLE001
        return None, b"", int((time.monotonic() - started) * 1000), f"{type(exc).__name__}: {exc}"


def anonymous_check():
    status, _, ms, error = fetch(f"{API_BASE}/api/PublicReports/Affiliates", authenticate=False)
    return status, ms, error


def window(day: date, span: int = 1):
    """A window of `span` days starting at `day`.

    **endDate is inclusive, at day granularity.** Measured 2026-10-13: a
    call with startDate=D and endDate=D+1 returns two days, not one. The
    first backfill used that form and double-counted every day; summing
    seven stored days came to 1.81x the same range fetched in one call,
    and solving the overlap chain reproduced the single call's totals to
    within 0.22% on sent, accepted and revenue alike.

    So a single day is startDate=D, endDate=D. `verify()` re-checks this
    against the live API rather than trusting the comment.

    **The dates are Sydney dates.** Confirmed by the dev team on 13 Oct
    2026: the API reports on Australia/Sydney time, not UTC. That leaves
    these strings as they are; what it changes is which days count as
    finished, and so collectable - see `store.business_today()`.
    """
    end = day + timedelta(days=span - 1)
    return (f"startDate={day.isoformat()}T00:00:00"
            f"&endDate={end.isoformat()}T00:00:00")


# ------------------------------------------------------------------ bands

def _b(*edges):
    """Build (lo, hi, label) bands from edges; hi is exclusive."""
    out, lo = [], None
    for hi in edges:
        label = f"<{hi:g}" if lo is None else f"{lo:g}-{hi:g}"
        out.append((lo, hi, label))
        lo = hi
    out.append((lo, None, f"{lo:g}+"))
    return out


NUMERIC = {
    "Age": [(None, 18, "<18"), (18, 25, "18-24"), (25, 35, "25-34"), (35, 45, "35-44"),
            (45, 55, "45-54"), (55, 65, "55-64"), (65, None, "65+")],
    "WagesMonthly": _b(1000, 2000, 3000, 4000, 5000, 7500),
    "WagesAndCentrelinkMonthly": _b(1, 1000, 2000, 3000),
    "LoanAmount": _b(1000, 2000, 3000, 5000),
    "GamblingPercentageOfIncome": [(None, 0.0001, "0"), (0.0001, 1, "0-1%"), (1, 5, "1-5%"),
                                   (5, 15, "5-15%"), (15, 30, "15-30%"), (30, None, "30%+")],
    "CentrelinkAsPercentOfIncome": [(None, 0.0001, "0"), (0.0001, 25, "0-25%"), (25, 50, "25-50%"),
                                    (50, 75, "50-75%"), (75, None, "75%+")],
    "IncomeSpentOnDayOfDeposit": _b(25, 50, 75, 100),
    "TotalDishonours": [(None, 1, "0"), (1, 2, "1"), (2, 3, "2"), (3, 6, "3-5"),
                        (6, 11, "6-10"), (11, None, "11+")],
    "TotalSaccDishonours": [(None, 1, "0"), (1, 2, "1"), (2, 3, "2"), (3, 6, "3-5"),
                            (6, None, "6+")],
    "DishonourGreaterThan50DaysLast30Days": [(None, 1, "0"), (1, 2, "1"), (2, 3, "2"),
                                             (3, None, "3+")],
    "SaccLoansFundedInLast14Days": [(None, 1, "0"), (1, 2, "1"), (2, 3, "2"), (3, None, "3+")],
    "SaccLoanFundedInStatementPeriod": [(None, 1, "0"), (1, 2, "1"), (2, 4, "2-3"),
                                        (4, None, "4+")],
}

CATEGORICAL = ("EmploymentStatus", "TimeAtEmployer", "HasPrimaryIncomeSource",
               "IsAustralianResident")

RESULT_FIELD = "Result"

# Payload field names a metric may arrive under, newest first. The API
# renamed the misnamed Centrelink field on 14 Oct 2026 - it holds wages
# plus Centrelink, not Centrelink alone - and a rename is invisible in
# the data: the field simply stops appearing and the metric quietly
# empties. Accepting both spellings means a rename costs nothing, and
# the stored metric name stays put either way so history stays joined.
FIELD_ALIASES = {
    # Measured 14 Oct 2026, after the rename: the API returns
    # WageAndCenterlinkMonthly - singular "Wage", and it kept the
    # "Centerlink" misspelling of the original. Guessing the spelling
    # was wrong; the rest are kept so older days still read.
    "WagesAndCentrelinkMonthly": ("WageAndCenterlinkMonthly",
                                  "WagesAndCentrelinkMonthly",
                                  "WageAndCentrelinkMonthly",
                                  "WageAndCentrlinkMonthly",
                                  "CentrelinkMonthly",
                                  "CenterlinkMonthly"),
}


def field_for(item, metric):
    """The name this item carries `metric` under, or None if absent."""
    for name in FIELD_ALIASES.get(metric, (metric,)):
        if name in item:
            return name
    return None


def band_of(metric, value):
    if value is None:
        return "unknown", 99
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "unknown", 99
    for i, (lo, hi, label) in enumerate(NUMERIC[metric]):
        if (lo is None or v >= lo) and (hi is None or v < hi):
            return label, i
    return "unknown", 99


def reduce_quality(items):
    """Rows in, counts out. No row survives this function."""
    results = {str(it.get(RESULT_FIELD) or "unknown") for it in items}
    cap = len(results) > MAX_CARDINALITY
    counts: dict[tuple[str, str, int, str], int] = {}

    cat_values: dict[str, set] = {c: set() for c in CATEGORICAL}
    for it in items:
        for c in CATEGORICAL:
            field = field_for(it, c)
            cat_values[c].add(str(it.get(field)) if field else "None")
    too_many = {c for c, vs in cat_values.items() if len(vs) > MAX_CARDINALITY}

    for it in items:
        result = "(many)" if cap else str(it.get(RESULT_FIELD) or "unknown")
        for metric in NUMERIC:
            field = field_for(it, metric)
            if field:
                band, order = band_of(metric, it.get(field))
                key = (metric, band, order, result)
                counts[key] = counts.get(key, 0) + 1
        for metric in CATEGORICAL:
            field = field_for(it, metric)
            if field is None:
                continue
            raw = it.get(field)
            band = "(high cardinality)" if metric in too_many else str(raw) if raw is not None else "unknown"
            key = (metric, band, 0, result)
            counts[key] = counts.get(key, 0) + 1
    return counts


# What a ping came back as. Supplied by the dev team 13 Oct 2026; there
# is no lookup table for these in either database.
STATUS_LABELS = {0: "FilterFailed", 1: "Sold", 2: "Decline", 3: "Error",
                 4: "Ignored"}

SOLD_STATUS = 1

# Statuses where the ping reached a lender at all. Measured 14 Oct 2026:
# FilterFailed and Ignored are 95% of rows and never get there, and
# Sold + Decline + Error reproduces the lender report's `sent` to within
# 0.2% - 3,412 against 3,406 on 12 Oct. So this is the denominator an
# acceptance rate belongs over.
REACHED_STATUSES = (1, 2, 3)


def reduce_lender_lookup(payload):
    """LenderTiers in, the id-to-name mapping out.

    The endpoint also returns a lender contact email, a logo url and
    marketing copy. None of it is needed to name a tier, so none of it
    is carried out of this function.
    """
    items = payload if isinstance(payload, list) else (payload.get("Items") or [])
    return [{"id": int(_num(it, "Id")),
             "name": it.get("Name") or it.get("DisplayName"),
             "lender_id": int(_num(it, "LenderId")),
             "lender_name": it.get("LenderName"),
             "code": it.get("Code"),
             "price": float(_num(it, "Price") or 0),
             "is_active": bool(it.get("IsActive"))} for it in items]


def reduce_lender_results(payload):
    """The ping log in, counts out. No row survives this function.

    Measured 14 Oct 2026: a bare top-level array, not the `Items`
    envelope the other reports use, and about 99,000 rows a day. Each
    row carries LeadId and LeadApplicationId; neither is kept, because
    a lead identifier outside Azure is what this design avoids. What is
    kept is counts per lender, tier and status, the distinct
    applications behind them, and the cost.

    Distinct counts are taken over the whole day as well, because one
    lead is offered to several lenders: summing the per-status rows
    would count it once per ping.

    `Cost` is the tier's list price, carried on every row whatever came
    back - measured 14 Oct 2026, it equalled `LenderTiers.Price` on all
    75,084 rows of a day and never varied within a tier. It is summed
    per group here because that is lossless, but only the sold rows are
    money, and the reads are what take that distinction.
    """
    items = payload if isinstance(payload, list) else (payload.get("Items") or [])
    counts: dict[tuple[int, int, int], list] = {}
    apps_by_key: dict[tuple[int, int, int], set] = {}
    leads, applications = set(), set()

    for it in items:
        key = (int(_num(it, "LenderId")), int(_num(it, "LenderTierId")),
               int(_num(it, "StatusId")))
        row = counts.get(key)
        if row is None:
            row = counts[key] = [0, 0.0]
            apps_by_key[key] = set()
        row[0] += 1
        row[1] += float(_num(it, "Cost") or 0)
        app = it.get("LeadApplicationId")
        if app is not None:
            apps_by_key[key].add(app)
            applications.add(app)
        lead = it.get("LeadId")
        if lead is not None:
            leads.add(lead)

    out = {k: (pings, len(apps_by_key[k]), round(cost, 2))
           for k, (pings, cost) in counts.items()}
    meta = {"pings": sum(v[0] for v in out.values()),
            "leads": len(leads), "applications": len(applications)}
    return out, meta


def reduce_affiliate_results(items):
    """The same ping log, counted per affiliate and status instead.

    One pass more over rows already in memory, so the source of a lead
    can be set against what lenders did with it - which is the whole
    point of a quality score.
    """
    counts: dict[tuple[int, int], list] = {}
    apps: dict[tuple[int, int], set] = {}
    for it in items:
        key = (int(_num(it, "AffiliateId")), int(_num(it, "StatusId")))
        row = counts.get(key)
        if row is None:
            row = counts[key] = [0, 0.0]
            apps[key] = set()
        row[0] += 1
        row[1] += float(_num(it, "Cost") or 0)
        app = it.get("LeadApplicationId")
        if app is not None:
            apps[key].add(app)
    return {k: (pings, len(apps[k]), round(cost, 2))
            for k, (pings, cost) in counts.items()}


def reduce_affiliate_lender_results(items):
    """The ping log counted a third way: per affiliate, lender and
    status. What says which lenders buy a source's leads, and from
    which sources a lender buys. {(affiliate, lender, status): (pings,
    cost)} - no distinct counts, because nothing reads them per pair.

    Per lender rather than per tier: tiers would multiply the rows by
    three for a question asked about lenders.
    """
    counts: dict[tuple[int, int, int], list] = {}
    for it in items:
        key = (int(_num(it, "AffiliateId")), int(_num(it, "LenderId")),
               int(_num(it, "StatusId")))
        row = counts.get(key)
        if row is None:
            row = counts[key] = [0, 0.0]
        row[0] += 1
        row[1] += float(_num(it, "Cost") or 0)
    return {k: (pings, round(cost, 2)) for k, (pings, cost) in counts.items()}


def reduce_hours(items):
    """The ping log by the hour, for the Today and Yesterday pages.

    Returns (leads, sales, as_of):
      leads {(hour, affiliate_id): (leads first pinged in that hour,
                                    how many of them sold at least once)}
      sales {(hour, affiliate_id, lender_id, tier_id): (reached, sold, value)}
      as_of  the newest DateCreated seen, to the minute

    DateCreated is Sydney local time - measured 15 Oct 2026, the day's
    first ping is at 00:00:56 - so its hour is the hour on the page. A
    lead is counted once, in the hour of its first ping; its identifier
    is used to find that hour and then dropped with the rows. Only pings
    that reached a lender go into sales: value is the tier price on the
    sold ones, which is what the lender pays.
    """
    first: dict = {}
    sold_leads: set = set()
    sales: dict[tuple[int, int, int, int], list] = {}
    as_of = ""
    for it in items:
        created = str(it.get("DateCreated") or "")
        if len(created) < 13:
            continue
        hour = int(created[11:13])
        if created > as_of:
            as_of = created
        affiliate = int(_num(it, "AffiliateId"))
        lead = it.get("LeadId")
        if lead is not None:
            seen = first.get(lead)
            if seen is None or created < seen[0]:
                first[lead] = (created, affiliate)
        status = int(_num(it, "StatusId"))
        if status not in (1, 2, 3):
            continue
        key = (hour, affiliate, int(_num(it, "LenderId")), int(_num(it, "LenderTierId")))
        row = sales.get(key)
        if row is None:
            row = sales[key] = [0, 0, 0.0]
        row[0] += 1
        if status == 1:
            row[1] += 1
            row[2] += float(_num(it, "Cost") or 0)
            if lead is not None:
                sold_leads.add(lead)
    leads: dict[tuple[int, int], list] = {}
    for lead, (created, affiliate) in first.items():
        k = (int(created[11:13]), affiliate)
        row = leads.setdefault(k, [0, 0])
        row[0] += 1
        row[1] += lead in sold_leads
    del first, sold_leads
    leads = {k: tuple(v) for k, v in leads.items()}
    return leads, {k: tuple(v) for k, v in sales.items()}, as_of[:16]


# Categorical fields counted per affiliate and per tier as well. The
# two that describe the customer for every lead: employment status is
# 98% populated, time at employer 99%. HasPrimaryIncomeSource and
# IsAustralianResident are left to the population view - each is only
# filled for some journeys, so a split by buyer would mostly measure
# which journeys a buyer is offered.
BAND_CATEGORICAL = ("EmploymentStatus", "TimeAtEmployer")


def reduce_quality_bands(items):
    """Bands and the lender result, without the cardinality guards the
    population reduction needs. Rows in, counts out.

    The result is kept so a band's sold rate can be taken within one
    population: scoring an external source against figures that include
    internal traffic marks it down for the company it keeps.

    The categorical fields keep their value, as in the population
    reduction, under the same cap: past MAX_CARDINALITY distinct values
    a field is free text, not a category, and is not kept.
    """
    counts: dict[tuple[str, str, int, str], int] = {}
    values: dict[str, set] = {m: set() for m in BAND_CATEGORICAL}
    for it in items:
        for metric in BAND_CATEGORICAL:
            field = field_for(it, metric)
            if field:
                values[metric].add(it.get(field))
    keep = [m for m in BAND_CATEGORICAL if len(values[m]) <= MAX_CARDINALITY]
    for it in items:
        result = str(it.get(RESULT_FIELD) or "unknown")
        for metric in NUMERIC:
            field = field_for(it, metric)
            if field:
                band, order = band_of(metric, it.get(field))
                key = (metric, band, order, result)
                counts[key] = counts.get(key, 0) + 1
        for metric in keep:
            field = field_for(it, metric)
            if field:
                raw = it.get(field)
                key = (metric, "unknown" if raw is None else str(raw), 0, result)
                counts[key] = counts.get(key, 0) + 1
    return counts


def _num(row, *names, default=0):
    for n in names:
        if n in row and row[n] is not None:
            return row[n]
    return default


def reduce_tiers(payload):
    items = payload.get("Items") or []
    rows = [{
        "id": _num(it, "LenderTierId"),
        "name": it.get("LenderTierName"),
        "ignored": _num(it, "TotalIgnored"),
        "processed": _num(it, "TotalProccessed", "TotalProcessed"),
        "sent": _num(it, "TotalSent"),
        "accepted": _num(it, "TotalAccepted"),
        "blacklist": _num(it, "TotalBlacklist"),
        "redirect": _num(it, "TotalRedirect"),
        "conversion_rate": _num(it, "ConversionRate"),
        "earning_per_lead": _num(it, "EarningPerLead"),
        "extra_income": _num(it, "ExtraIncome"),
        "revenue": _num(it, "TotalRevenue"),
        "commission": _num(it, "TotalCommission"),
        "profit": _num(it, "TotalProfit"),
        "blacklist_price": _num(it, "TotalBlacklistPrice"),
    } for it in items]
    totals = {
        "ignored": _num(payload, "TotalIgnored"),
        "processed": _num(payload, "TotalProccessed", "TotalProcessed"),
        "sent": _num(payload, "TotalSent"),
        "accepted": _num(payload, "TotalAccepted"),
        "blacklist": _num(payload, "TotalBlacklist"),
        "redirect": _num(payload, "TotalRedirect"),
        "conversion_rate": _num(payload, "TotalConversionRate"),
        "extra_income": _num(payload, "TotalExtraIncome"),
        "revenue": _num(payload, "TotalRevenue"),
        "commission": _num(payload, "TotalCommission"),
        "profit": _num(payload, "TotalProfit"),
        "blacklist_price": _num(payload, "TotalBlacklistPrice"),
    }
    return rows, totals


# The affiliate reports return no AffiliateId, but the name they return
# carries one: "Borrowly(59)". Measured 15 Oct 2026, every one of 116
# names parsed and every id was in the Affiliates lookup - which is what
# lets affiliate money join the ping log, where the id is all there is.
# The parser lives in store because the migration there needs it too.
affiliate_id_from = store.affiliate_id_from


def reduce_affiliate_lookup(payload):
    """Affiliates in, the id-to-name mapping out.

    **This endpoint returns credentials.** Each row carries the
    affiliate's portal `Username` and `Password` and its
    `CredfinSecretKey` and `CredfinIdentifier`. None of them is read
    here, and nothing downstream can store what this does not return.
    That the reporting API serves them at all is worth raising.
    """
    items = payload if isinstance(payload, list) else (payload.get("Items") or [])
    return [{"id": int(_num(it, "Id")),
             "name": it.get("Name"),
             "group_name": it.get("GroupName"),
             "is_internal": bool(it.get("IsInternalSource")),
             "is_active": bool(it.get("IsActive")) and not it.get("IsDeleted"),
             "commission_type": it.get("CommissionType"),
             "commission_value": it.get("CommissionValue")} for it in items]


def reduce_affiliates(payload):
    """One row per affiliate, plus the report's own totals.

    AffiliateName is a trading name and the only identifier returned,
    so the name is the key - but it ends in the affiliate's id, which
    is parsed out so the ping log can be joined.
    """
    def row(src, name=None):
        return {
            "name": name,
            "received": _num(src, "TotalReceived"),
            "accepted_tc": _num(src, "TotalAcceptedTC"),
            "bs_refreshed": _num(src, "TotalBsRefreshed"),
            "duplicate": _num(src, "TotalDuplicate"),
            "bs_extracted": _num(src, "TotalBsExtracted"),
            "submitted": _num(src, "TotalSubmitted"),
            "sold": _num(src, "TotalSold"),
            "decline": _num(src, "TotalDecline"),
            "redirect": _num(src, "TotalRedirect"),
            "funded": _num(src, "TotalFunded"),
            "revenue": _num(src, "TotalRevenue"),
            "commission": _num(src, "TotalCommission"),
            "bs_cost": _num(src, "TotalBSCost", "TotalBsCost"),
            "final_commission": _num(src, "TotalFinalCommission"),
            "profit": _num(src, "TotalProfit"),
            "final_profit": _num(src, "TotalFinalProfit"),
            "volume_comparison_pct": _num(src, "VolumeComparisonPercentage"),
            # Google Ads spend. Only the internal report carries it, and
            # measured 15 Oct 2026 its totals block reads 0 on finished
            # days while the rows hold the spend - all of it on one FF
            # source - so readers sum the rows.
            "adword_spent": _num(src, "TotalAdwordSpent"),
        }
    items = payload.get("Items") or []
    rows = []
    for it in items:
        name = it.get("AffiliateName")
        r = row(it, name)
        r["affiliate_id"] = affiliate_id_from(name)
        rows.append(r)
    return rows, row(payload)


# ---------------------------------------------------------------- collect

# `internal_affiliates` is the second affiliate report, collected beside
# the first and never added to it: the dev team confirmed on 13 Oct 2026
# that FF and non-FF affiliates can no longer be combined into a single
# affiliate lead count.
KINDS = ("tiers", "quality", "affiliates", "internal_affiliates",
         "lender_results", "lender_lookup", "affiliate_lookup",
         "quality_by_affiliate", "quality_by_tier")

# Kinds that take more than one call, so they are collected after the
# single-call ones rather than through _requests - which also means the
# reports they take their list of ids from are already in.
MULTI_CALL_KINDS = ("quality_by_affiliate", "quality_by_tier")


def _store_tiers(conn, iso, payload, ms, stamp):
    rows, totals = reduce_tiers(json.loads(payload.decode("utf-8", "replace")))
    store.save_tiers(conn, iso, rows, totals, stamp)
    return f"{len(rows)} tier(s)"


def _store_quality(conn, iso, payload, ms, stamp):
    items = (json.loads(payload.decode("utf-8", "replace")) or {}).get("Items") or []
    counts = reduce_quality(items)
    n = len(items)
    del items                                   # rows do not outlive this call
    store.save_quality(conn, iso, counts, n, stamp)
    return f"{n} row(s) -> {len(counts)} count(s)"


def _store_affiliates(conn, iso, payload, ms, stamp, source="external"):
    rows, totals = reduce_affiliates(json.loads(payload.decode("utf-8", "replace")))
    store.save_affiliates(conn, iso, source, rows, totals, stamp)
    return f"{len(rows)} affiliate(s)"


def _store_internal_affiliates(conn, iso, payload, ms, stamp):
    return _store_affiliates(conn, iso, payload, ms, stamp, source="internal")


def _store_lender_results(conn, iso, payload, ms, stamp):
    data = json.loads(payload.decode("utf-8", "replace"))
    counts, meta = reduce_lender_results(data)
    items = data if isinstance(data, list) else (data.get("Items") or [])
    by_affiliate = reduce_affiliate_results(items)
    pairs = reduce_affiliate_lender_results(items)
    leads, sales, as_of = reduce_hours(items)
    del data, items                             # rows do not outlive this call
    store.save_lender_results(conn, iso, counts, meta, stamp)
    store.save_hours(conn, iso, leads, sales, as_of, True, stamp)
    store.save_affiliate_results(conn, iso, by_affiliate, stamp)
    store.save_affiliate_lender_results(conn, iso, pairs, stamp)
    return (f"{meta['pings']:,} ping(s) -> {len(counts)} count(s), "
            f"{meta['applications']:,} application(s)")


def _store_affiliate_lookup(conn, iso, payload, ms, stamp):
    rows = reduce_affiliate_lookup(json.loads(payload.decode("utf-8", "replace")))
    store.save_affiliate_lookup(conn, rows, stamp)
    return f"{len(rows)} affiliate(s)"


def _store_lender_lookup(conn, iso, payload, ms, stamp):
    rows = reduce_lender_lookup(json.loads(payload.decode("utf-8", "replace")))
    store.save_lender_lookup(conn, rows, stamp)
    return f"{len(rows)} tier(s)"


def _requests(day: date):
    """(kind, method, url, body, handler) for one day."""
    qs = window(day)
    prev = day - timedelta(days=1)
    # ExcludeInternalSources is sent as the API expects it but nothing is
    # read from it: the dev team say to ignore the flag, because FF
    # affiliates carry a special computation behind it. The split comes
    # from calling both reports instead, and keeping them apart.
    body = {"StartDate": f"{day.isoformat()}T00:00:00",
            "EndDate": f"{day.isoformat()}T00:00:00",
            "CompareStartDate": f"{prev.isoformat()}T00:00:00",
            "CompareEndDate": f"{prev.isoformat()}T00:00:00",
            "IsAllActiveOnly": False, "ExcludeInternalSources": False}
    return [
        ("tiers", "GET", f"{API_BASE}/api/PublicReports/LenderLeadCountReport?{qs}",
         None, _store_tiers),
        ("quality", "GET", f"{API_BASE}/api/PublicReports/LeadQualityReport?{qs}",
         None, _store_quality),
        ("affiliates", "POST", f"{API_BASE}/api/PublicReports/AffiliateLeadCountReport",
         body, _store_affiliates),
        ("internal_affiliates", "POST",
         f"{API_BASE}/api/PublicReports/InternalAffiliateLeadCountReport",
         body, _store_internal_affiliates),
        # The ping log. Roughly 23 MB and 99,000 rows a day, so it is
        # the heaviest call here by an order of magnitude - which is
        # the reason it is reduced to counts in one pass and the rows
        # dropped rather than held.
        ("lender_results", "GET",
         f"{API_BASE}/api/PublicReports/LenderApplicationResults?{qs}",
         None, _store_lender_results),
        # Not a daily report but a lookup, and the only thing that puts
        # names on the ping log: its rows carry LenderTierId and
        # LenderId and no names at all. Two seconds, so it is simply
        # re-read with each day rather than cached and aged.
        ("lender_lookup", "GET", f"{API_BASE}/api/PublicReports/LenderTiers",
         None, _store_lender_lookup),
        # The other lookup: affiliate id to name, group, and whether the
        # source is internal. Two seconds, same as the tier one.
        ("affiliate_lookup", "GET", f"{API_BASE}/api/PublicReports/Affiliates",
         None, _store_affiliate_lookup),
    ]


def fetch_retrying(url, attempts=3, pause=2.0):
    """A GET that survives a dropped connection.

    A single-call report is retried by the next scheduler pass, which
    costs one call. A day of per-affiliate calls is not: one drop
    among twenty discards the other nineteen, and the day is never
    collected if the drop repeats. Measured 15 Oct 2026, roughly one
    call in twenty was reset by the proxy in front of this collector.
    """
    status = payload = error = None
    for attempt in range(attempts):
        status, payload, _ms, error = fetch(url)
        if status == 200 and payload:
            return payload
        if attempt + 1 < attempts:
            time.sleep(pause * (attempt + 1))
    raise RuntimeError(error or f"HTTP {status}")


# Calls in flight at once for the per-id quality reports. A tier call
# takes five to six seconds and a day has about sixty, so one at a time
# a fresh database's ninety days would take most of a working day.
QUALITY_WORKERS = int(os.environ.get("QUALITY_WORKERS", "4"))


def _quality_split(iso, ids, param):
    """Band counts with the lender result, one LeadQualityReport call
    per id under `param`. {(id, metric, band, order, result): n}, and
    how many rows were read.

    All or nothing: a partial day would understate every id the failed
    call belongs to, and nothing downstream could tell.
    """
    qs = window(date.fromisoformat(iso))

    def one(one_id):
        try:
            payload = fetch_retrying(
                f"{API_BASE}/api/PublicReports/LeadQualityReport?{qs}&{param}={int(one_id)}")
        except RuntimeError as exc:
            raise RuntimeError(f"{param} {one_id}: {exc}") from None
        items = (json.loads(payload.decode("utf-8", "replace")) or {}).get("Items") or []
        # Reduced in the worker, so rows do not outlive the call
        return one_id, len(items), reduce_quality_bands(items)

    counts: dict[tuple, int] = {}
    rows = 0
    with ThreadPoolExecutor(max_workers=max(1, QUALITY_WORKERS)) as pool:
        for one_id, n_rows, reduced in pool.map(one, sorted(ids)):
            rows += n_rows
            for (metric, band, order, result), n in reduced.items():
                if result in NOT_OFFERED:
                    continue
                key = (one_id, metric, band, order, result)
                counts[key] = counts.get(key, 0) + n
    return counts, rows


# With a lender tier filter, LeadQualityReport returns every lead of the
# day, each with what that tier did with it: FilterFailed, Ignored,
# Decline, Error, Sold - or "Not Processed" when the lead never got as
# far as the tier, because it sold higher up the tree or the journey
# ended first. Measured 15 Oct 2026. Those leads were never offered to
# the tier and say nothing about it, so they are not kept: on a typical
# tier they are a third to a half of the rows.
NOT_OFFERED = ("Not Processed",)


def collect_quality_by_affiliate(conn, iso, stamp):
    """Lead quality for each affiliate that sent anything that day.

    `LeadQualityReport` takes an `affiliateId`, so the mix a source
    sends is one call per active affiliate - about twenty of them, a
    second or two each. Which affiliates were active comes from the
    affiliate reports already collected for the day, so a day collected
    out of order simply finds none and is retried.
    """
    ids = [r[0] for r in conn.execute(
        "SELECT DISTINCT affiliate_id FROM affiliate_day WHERE business_date=?"
        " AND affiliate_id IS NOT NULL AND received > 0", (iso,))]
    if not ids:
        raise RuntimeError("no affiliate rows for this day yet")
    counts, rows = _quality_split(iso, ids, "affiliateId")
    store.save_quality_by_affiliate(conn, iso, counts, stamp)
    return f"{len(ids)} affiliate(s), {rows:,} row(s) -> {len(counts)} count(s)"


def collect_quality_by_tier(conn, iso, stamp):
    """Lead quality for each lender tier that was offered anything.

    This is what says whether a lender buys a particular kind of
    customer: per band, how many leads the tier was offered, how many
    got through its filters, and how many it bought. Taken per tier
    because the `lenderId` filter is ignored by the API - measured
    15 Oct 2026, it returns the unfiltered day - while `lenderTierId`
    is honoured.

    About sixty tiers a day are offered something, at around three
    seconds a call. Tiers offered nothing are skipped: there is nothing
    to say about them.
    """
    have = conn.execute("SELECT COUNT(*) FROM lender_tier_day WHERE business_date=?",
                        (iso,)).fetchone()[0]
    if not have:
        raise RuntimeError("no lender report for this day yet")
    ids = [r[0] for r in conn.execute(
        "SELECT lender_tier_id FROM lender_tier_day WHERE business_date=?"
        " AND processed > 0", (iso,))]
    counts, rows = _quality_split(iso, ids, "lenderTierId")
    store.save_quality_by_tier(conn, iso, counts, stamp)
    return f"{len(ids)} tier(s), {rows:,} row(s) -> {len(counts)} count(s)"


MULTI_CALL_COLLECTORS = {"quality_by_affiliate": collect_quality_by_affiliate,
                         "quality_by_tier": collect_quality_by_tier}


def collect_day(conn, day: date, kinds=KINDS):
    """One day. Skips any kind already recorded ok for that day, so adding
    a new report backfills only itself rather than refetching everything."""
    stamp, out = now(), {}
    iso = day.isoformat()
    already = {k for (k,) in conn.execute(
        "SELECT kind FROM collection WHERE business_date=? AND status='ok'", (iso,))}

    for kind, method, url, body, handler in _requests(day):
        if kind not in kinds or kind in already:
            continue
        status, payload, ms, error = fetch(url, method, body)
        if error or status != 200:
            detail = error or f"HTTP {status}"
            store.mark(conn, iso, kind, "failed", detail, ms, stamp)
            out[kind] = detail
            continue
        try:
            detail = handler(conn, iso, payload, ms, stamp)
            store.mark(conn, iso, kind, "ok", detail, ms, stamp)
            out[kind] = "ok"
        except Exception as exc:                # noqa: BLE001
            store.mark(conn, iso, kind, "failed", f"{type(exc).__name__}: {exc}", ms, stamp)
            out[kind] = str(exc)

    for kind in MULTI_CALL_KINDS:
        if kind not in kinds or kind in already:
            continue
        started = time.monotonic()
        try:
            detail = MULTI_CALL_COLLECTORS[kind](conn, iso, stamp)
            store.mark(conn, iso, kind, "ok", detail,
                       int((time.monotonic() - started) * 1000), stamp)
            out[kind] = "ok"
        except Exception as exc:                # noqa: BLE001
            store.mark(conn, iso, kind, "failed", f"{type(exc).__name__}: {exc}",
                       int((time.monotonic() - started) * 1000), stamp)
            out[kind] = str(exc)
    return out


def missing_days(conn, days=BACKFILL_DAYS):
    """Days up to yesterday that are not fully collected. Today is never
    collected: it is still happening.

    Yesterday is a Sydney date, because that is how the API dates its
    reports. On UTC this lagged: Sydney finishes its day ten or eleven
    hours before UTC does, so the newest finished day went uncollected
    for up to another day.
    """
    done = set.intersection(*(store.collected_days(conn, k) for k in KINDS))
    yesterday = store.business_today() - timedelta(days=1)
    wanted = [yesterday - timedelta(days=i) for i in range(days)]
    return [d for d in wanted if d.isoformat() not in done]


def catch_up(conn, limit=DAYS_PER_WAKE, days=BACKFILL_DAYS):
    done = []
    for day in missing_days(conn, days)[:limit]:
        done.append((day.isoformat(), collect_day(conn, day)))
    return done


def verify(days: int = 5):
    """Fetch a range in one call, then each of its days separately, and
    compare. Guards the window semantics: if the API ever changes, or the
    reading above is wrong, the ratio says so instead of the numbers
    quietly being wrong."""
    end = store.business_today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)

    status, payload, ms, error = fetch(
        f"{API_BASE}/api/PublicReports/LenderLeadCountReport?{window(start, days)}")
    if error or status != 200:
        return {"error": error or f"HTTP {status}"}
    _, span_totals = reduce_tiers(json.loads(payload.decode("utf-8", "replace")))

    daily = {"sent": 0, "accepted": 0, "revenue": 0.0}
    per_day = []
    for i in range(days):
        d = start + timedelta(days=i)
        st, pl, _ms, err = fetch(
            f"{API_BASE}/api/PublicReports/LenderLeadCountReport?{window(d)}")
        if err or st != 200:
            return {"error": err or f"HTTP {st} on {d}"}
        _, t = reduce_tiers(json.loads(pl.decode("utf-8", "replace")))
        per_day.append((d.isoformat(), t["sent"], t["accepted"], t["revenue"]))
        for k in daily:
            daily[k] += t[k]

    ratios = {k: (daily[k] / span_totals[k]) if span_totals[k] else None for k in daily}
    good = [r for r in ratios.values() if r is not None]
    verdict = ("single days sum to the range call - the window is correct"
               if good and all(0.97 <= r <= 1.03 for r in good)
               else "MISMATCH - single days do not sum to the range call")
    return {"start": start.isoformat(), "end": end.isoformat(), "days": days,
            "span": span_totals, "daily": daily, "ratios": ratios,
            "per_day": per_day, "verdict": verdict, "ms": ms}


def reset(conn):
    """Drop everything collected. Used when the collection itself was
    wrong, as opposed to merely out of date."""
    with conn:
        for table in ("lender_tier_day", "totals_day", "quality_day",
                      "quality_day_meta", "affiliate_day", "affiliate_totals_day",
                      "lender_result_day", "lender_result_day_meta",
                      "affiliate_result_day", "quality_affiliate_day",
                      "quality_tier_day", "affiliate_lender_day", "hour_leads",
                      "hour_sales", "hour_meta", "live_internal", "collection"):
            conn.execute(f"DELETE FROM {table}")


def rebuild(db_path, days=None):
    """Collect every missing day back to back until there are none left.

    Used on an empty database. Upwards of a minute a day across the five
    reports now that the ping log is among them, so 90 days runs for a
    couple of hours - unattended, and nobody has to press anything.
    """
    days = days or INITIAL_DAYS
    conn = store.connect(db_path)
    try:
        total = len(missing_days(conn, days))
        if not total:
            return 0
        print(f"empty history: rebuilding {total} day(s) from the API", flush=True)
        done = 0
        while True:
            batch = missing_days(conn, days)
            if not batch:
                break
            day = batch[0]
            collect_day(conn, day)
            done += 1
            if done % 10 == 0 or done == total:
                print(f"  rebuilt {done}/{total} ({day.isoformat()})", flush=True)
            if done > days + 5:                     # cannot outrun the window
                break
        return done
    finally:
        conn.close()


def scheduler(db_path):
    """Rebuild once if there is nothing stored, then keep up with new days."""
    try:
        conn = store.connect(db_path)
        empty = not store.collected_days(conn, "tiers")
        conn.close()
        if empty:
            rebuild(db_path)
    except Exception as exc:                        # noqa: BLE001
        print(f"rebuild error: {type(exc).__name__}: {exc}", flush=True)

    while True:
        try:
            conn = store.connect(db_path)
            filled = catch_up(conn)
            conn.close()
            if filled:
                print(f"collected {len(filled)} day(s): "
                      f"{', '.join(d for d, _ in filled)}", flush=True)
        except Exception as exc:                    # noqa: BLE001
            print(f"scheduler error: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(WAKE_SECONDS)


# ------------------------------------------------------------------ live

# How often today is read again. The ping log for a whole day is about
# 22 MB and came back in under seven seconds on 15 Oct 2026, and the
# internal report in about fifteen, so ten minutes is cheap.
LIVE_SECONDS = int(os.environ.get("LIVE_SECONDS", "600"))
_live_lock = threading.Lock()


def collect_live(conn, day: date | None = None):
    """Read today so far: the ping log by the hour, and the FF report
    for Google Ads spend. Nothing here touches the finished-day tables -
    a partial day in them would read as a bad day on every other page."""
    day = day or store.business_today()
    iso, stamp, out = day.isoformat(), now(), {}
    # The lookups name the lenders and say which sources are FF. On a
    # fresh database the live read can come before the first day's
    # collection has fetched them, so it fetches them itself.
    for kind, url, handler in (
            ("affiliate_lookup", f"{API_BASE}/api/PublicReports/Affiliates", _store_affiliate_lookup),
            ("lender_lookup", f"{API_BASE}/api/PublicReports/LenderTiers", _store_lender_lookup)):
        table = "affiliate" if kind == "affiliate_lookup" else "lender_tier"
        if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
            continue
        try:
            out[kind] = handler(conn, iso, fetch_retrying(url), 0, stamp)
        except Exception as exc:                # noqa: BLE001
            out[kind] = f"{type(exc).__name__}: {exc}"
    # Retried: the proxy in front of this service resets about one call
    # in twenty, and a dropped live read would leave the page ten
    # minutes stale.
    try:
        payload = fetch_retrying(
            f"{API_BASE}/api/PublicReports/LenderApplicationResults?{window(day)}")
    except RuntimeError as exc:
        payload = None
        out["hours"] = str(exc)
    if payload is not None:
        data = json.loads(payload.decode("utf-8", "replace"))
        items = data if isinstance(data, list) else (data.get("Items") or [])
        leads, sales, as_of = reduce_hours(items)
        del data, items, payload
        store.save_hours(conn, iso, leads, sales, as_of, False, stamp)
        out["hours"] = f"as of {as_of[11:16]}"

    prev = day - timedelta(days=1)
    body = {"StartDate": f"{iso}T00:00:00", "EndDate": f"{iso}T00:00:00",
            "CompareStartDate": f"{prev.isoformat()}T00:00:00",
            "CompareEndDate": f"{prev.isoformat()}T00:00:00",
            "IsAllActiveOnly": False, "ExcludeInternalSources": False}
    status, payload, ms, error = fetch(
        f"{API_BASE}/api/PublicReports/InternalAffiliateLeadCountReport", "POST", body)
    if error or status != 200:
        out["internal"] = error or f"HTTP {status}"
    else:
        rows, _ = reduce_affiliates(json.loads(payload.decode("utf-8", "replace")))
        totals = {k: sum(r[k] or 0 for r in rows) for k in
                  ("received", "sold", "revenue", "bs_cost", "adword_spent", "profit")}
        store.save_live_internal(conn, iso, totals, stamp)
        out["internal"] = "ok"
    return out


def live_now(db_path):
    """One live read, unless one is already running. True if started."""
    if not _live_lock.acquire(blocking=False):
        return False
    try:
        conn = store.connect(db_path)
        try:
            collect_live(conn)
        finally:
            conn.close()
    except Exception as exc:                        # noqa: BLE001
        print(f"live error: {type(exc).__name__}: {exc}", flush=True)
    finally:
        _live_lock.release()
    return True


def live_in_background(db_path):
    if _live_lock.locked():
        return False
    threading.Thread(target=live_now, args=(db_path,), daemon=True, name="live-now").start()
    return True


def live_loop(db_path):
    """Today, every LIVE_SECONDS. Its own thread, so the Today page
    works from the first minutes after a deploy while the history is
    still rebuilding underneath it."""
    while True:
        live_now(db_path)
        time.sleep(LIVE_SECONDS)


def start(db_path):
    threading.Thread(target=scheduler, args=(db_path,), daemon=True,
                     name="collector").start()
    threading.Thread(target=live_loop, args=(db_path,), daemon=True,
                     name="live").start()
