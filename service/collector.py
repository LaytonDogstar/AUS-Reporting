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


def window(day: date):
    nxt = day + timedelta(days=1)
    return (f"startDate={day.isoformat()}T00:00:00"
            f"&endDate={nxt.isoformat()}T00:00:00")


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


# ---------------------------------------------------------------- collect

def collect_day(conn, day: date):
    """One day of both reports. Returns (kind -> status)."""
    stamp, out = now(), {}
    qs = window(day)
    iso = day.isoformat()

    status, payload, ms, error = fetch(
        f"{API_BASE}/api/PublicReports/LenderLeadCountReport?{qs}")
    if error or status != 200:
        store.mark(conn, iso, "tiers", "failed", error or f"HTTP {status}", ms, stamp)
        out["tiers"] = error or f"HTTP {status}"
    else:
        try:
            rows, totals = reduce_tiers(json.loads(payload.decode("utf-8", "replace")))
            store.save_tiers(conn, iso, rows, totals, stamp)
            store.mark(conn, iso, "tiers", "ok", f"{len(rows)} tier(s)", ms, stamp)
            out["tiers"] = "ok"
        except Exception as exc:                    # noqa: BLE001
            store.mark(conn, iso, "tiers", "failed", f"{type(exc).__name__}: {exc}", ms, stamp)
            out["tiers"] = str(exc)

    status, payload, ms, error = fetch(
        f"{API_BASE}/api/PublicReports/LeadQualityReport?{qs}")
    if error or status != 200:
        store.mark(conn, iso, "quality", "failed", error or f"HTTP {status}", ms, stamp)
        out["quality"] = error or f"HTTP {status}"
    else:
        try:
            items = (json.loads(payload.decode("utf-8", "replace")) or {}).get("Items") or []
            counts = reduce_quality(items)
            n = len(items)
            del items, payload                      # rows do not outlive this call
            store.save_quality(conn, iso, counts, n, stamp)
            store.mark(conn, iso, "quality", "ok", f"{n} row(s) -> {len(counts)} count(s)", ms, stamp)
            out["quality"] = "ok"
        except Exception as exc:                    # noqa: BLE001
            store.mark(conn, iso, "quality", "failed", f"{type(exc).__name__}: {exc}", ms, stamp)
            out["quality"] = str(exc)
    return out


def missing_days(conn, days=BACKFILL_DAYS):
    """Days up to yesterday that are not fully collected. Today is never
    collected: it is still happening."""
    done = store.collected_days(conn, "tiers") & store.collected_days(conn, "quality")
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    wanted = [yesterday - timedelta(days=i) for i in range(days)]
    return [d for d in wanted if d.isoformat() not in done]


def catch_up(conn, limit=DAYS_PER_WAKE, days=BACKFILL_DAYS):
    done = []
    for day in missing_days(conn, days)[:limit]:
        done.append((day.isoformat(), collect_day(conn, day)))
    return done


def scheduler(db_path):
    """Fill in missing days, a few at a time, for as long as we run."""
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
