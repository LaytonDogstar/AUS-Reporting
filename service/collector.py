"""Fetch the reports, reduce them to things worth keeping, and store.

Runs one day at a time. The reports take fifteen to seventeen seconds
each, so nothing here can happen while a user waits; a background thread
fills in whatever days are missing and the pages read what it stored.
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
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
    "CenterlinkMonthly": _b(1, 1000, 2000, 3000),
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
            cat_values[c].add(str(it.get(c)))
    too_many = {c for c, vs in cat_values.items() if len(vs) > MAX_CARDINALITY}

    for it in items:
        result = "(many)" if cap else str(it.get(RESULT_FIELD) or "unknown")
        for metric in NUMERIC:
            if metric in it:
                band, order = band_of(metric, it.get(metric))
                key = (metric, band, order, result)
                counts[key] = counts.get(key, 0) + 1
        for metric in CATEGORICAL:
            if metric not in it:
                continue
            raw = it.get(metric)
            band = "(high cardinality)" if metric in too_many else str(raw) if raw is not None else "unknown"
            key = (metric, band, 0, result)
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


def reduce_affiliates(payload):
    """One row per affiliate, plus the report's own totals.

    AffiliateName is a trading name and the only identifier returned -
    the API gives no AffiliateId - so the name is the key.
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
        }
    items = payload.get("Items") or []
    rows = [row(it, it.get("AffiliateName")) for it in items]
    return rows, row(payload)


# ---------------------------------------------------------------- collect

# `internal_affiliates` is the second affiliate report, collected beside
# the first and never added to it: the dev team confirmed on 13 Oct 2026
# that FF and non-FF affiliates can no longer be combined into a single
# affiliate lead count.
KINDS = ("tiers", "quality", "affiliates", "internal_affiliates")


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
    ]


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
                      "collection"):
            conn.execute(f"DELETE FROM {table}")


def rebuild(db_path, days=None):
    """Collect every missing day back to back until there are none left.

    Used on an empty database. Roughly 48 seconds a day across the four
    reports, so 90 days takes a little over an hour - unattended, and
    nobody has to press anything.
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


def start(db_path):
    threading.Thread(target=scheduler, args=(db_path,), daemon=True,
                     name="collector").start()
