"""Render the dashboard page from what the collector stored.

Named `dash` rather than `dashboard` so it cannot shadow the repository's
`dashboard/` package, which is the other build - the one that queries the
source database from inside the network.

The look is `dashboard/template.html` - tiles, a trend, a funnel, an
affiliate selector - which was written against the source database and
only runs inside the network. The data is this service's, collected
from the public API and reconciled against the lender report. This
module is the join between them: it builds the payload that template
expects out of counts rather than rows.

`template.html` here is a copy of that file, because Railway serves the
`service` folder alone and cannot see a sibling directory. A test
fails if the two drift.

**The two populations are not mixed.** FF and non-FF affiliate counts
cannot be added into one figure, so the page is built for one source at
a time and says which it is showing.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import journeys
import store
import trends

TEMPLATE = Path(__file__).with_name("template.html")
LENDER_TEMPLATE = Path(__file__).with_name("lenders.html")
PLACEHOLDER = "/*__DASHBOARD_DATA__*/null"
# The lender page borrows the dashboard's stylesheet rather than keeping
# a copy of it, so the two pages cannot drift apart in look.
STYLE_PLACEHOLDER = "/*__SHARED_STYLE__*/"

# The affiliate report's own funnel steps, given the stage ids the page
# already knows. 10 and 13 are the two it treats as outcomes - the good
# one and the bad one - so sold and declined take those.
FUNNEL = (
    ("received", 1, "Received", 10),
    ("accepted_tc", 3, "Accepted T&Cs", 30),
    ("bs_extracted", 7, "Statements extracted", 60),
    ("submitted", 8, "Submitted to lenders", 70),
    ("sold", 10, "Sold", 90),
    ("decline", 13, "Declined", 90),
)

SOURCE_LABELS = {"external": "External affiliates",
                 "internal": "Internal sources (FF)"}


def stages():
    """Only the steps this data has. The page draws what it is given,
    and a funnel padded with stages that never fire reads as attrition
    that did not happen."""
    return {str(sid): {"label": label, "order": order,
                       "outcome": "good" if sid == 10 else "bad" if sid == 13 else None}
            for _field, sid, label, order in FUNNEL}


def payload(conn, days=30, source="external"):
    cutoff = store.business_cutoff(days)
    rows = conn.execute(
        "SELECT business_date, affiliate_id, affiliate_name, received, accepted_tc,"
        " bs_extracted, submitted, sold, decline FROM affiliate_day"
        " WHERE business_date >= ? AND source = ? AND affiliate_id IS NOT NULL"
        " AND received > 0" + store._not_test(), (cutoff, source)).fetchall()

    applications, accepts, stage_counts = [], [], []
    for day, aid, _name, received, tc, bs, submitted, sold, decline in rows:
        applications.append({"Day": day, "AffiliateId": aid,
                             "Applications": received, "AvgLoanAmount": None})
        accepts.append({"Day": day, "AffiliateId": aid,
                        "ApplicationsSold": sold or 0, "AcceptEvents": sold or 0})
        values = {"received": received, "accepted_tc": tc, "bs_extracted": bs,
                  "submitted": submitted, "sold": sold, "decline": decline}
        for field, sid, _label, _order in FUNNEL:
            n = values.get(field) or 0
            if n:
                stage_counts.append({"Day": day, "AffiliateId": aid, "StageId": sid,
                                     "Applications": n, "StageEvents": n})

    seen = {r["AffiliateId"] for r in applications}
    known = {r[0]: r[1:] for r in conn.execute(
        "SELECT affiliate_id, name, is_internal, is_active FROM affiliate")}
    names = {r[0]: r[1] for r in conn.execute(
        "SELECT affiliate_id, affiliate_name FROM affiliate_day"
        " WHERE affiliate_id IS NOT NULL GROUP BY affiliate_id")}
    affiliates = []
    for aid in sorted(seen):
        name, is_internal, is_active = known.get(aid, (None, None, None))
        affiliates.append({"Id": aid, "Name": name or names.get(aid) or f"Affiliate {aid}",
                           "DisplayName": name or names.get(aid) or f"Affiliate {aid}",
                           "IsActive": bool(is_active), "IsDeleted": False,
                           "IsInternalSource": bool(is_internal)})

    # Lead quality, and the scorecard, so the page carries the whole
    # answer rather than linking to it. Both are per source, like the
    # rest of the page.
    rates = store.band_sold_rates(conn, store.QUALITY_METRIC, days, source)
    quality = store.affiliate_quality(conn, store.QUALITY_METRIC, days, source)
    bands = [{"band": band, "order": order, "leads": rates.get(band, (0, 0))[0],
              "soldRate": rates.get(band, (0, 0))[1]}
             for band, order in sorted(
                 {(b, o) for b, o in conn.execute(
                     "SELECT DISTINCT band, band_order FROM quality_day WHERE metric=?",
                     (store.QUALITY_METRIC,))}, key=lambda bo: bo[1])]
    scored = [r for r in store.scorecard(conn, days) if r["source"] == source]
    trend_rows = affiliate_trends(conn, days, source)

    return {
        "qualityMetric": "Monthly income (wages and Centrelink)",
        "qualityBands": bands,
        "scorecard": [{"id": r["id"], "name": r["name"], "leads": r["leads"],
                       "soldRate": r["sold_rate"], "expected": r["expected"],
                       "index": r["quality_index"],
                       "acceptance": r["acceptance"],
                       "profitPerLead": r["profit_per_lead"],
                       "profit": r["profit"]} for r in scored],
        "trends": trend_rows,
        "trendWindow": trends.WINDOW,
        "affiliateQuality": {str(aid): {"leads": q["leads"], "expected": q["expected"],
                                        "bands": q["bands"]}
                             for aid, q in quality.items()},
        "generatedUtc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "windowDays": days,
        "reportingTimeZone": "AUS Eastern Standard Time",
        "source": source,
        "sourceLabel": SOURCE_LABELS.get(source, source),
        "stages": stages(),
        "affiliates": affiliates,
        "applications": applications,
        "accepts": accepts,
        "stage_counts": stage_counts,
    }


def affiliate_trends(conn, days, source):
    """Per affiliate: the three measures Layton watches, as rolling
    seven-day series, each with a call on whether it has dropped.

    Every day in the window is present for every affiliate, zero-filled,
    so a week with no leads reads as a gap in volume rather than being
    silently skipped over.
    """
    cutoff = store.business_cutoff(days)
    rows = conn.execute(
        "SELECT affiliate_id, MAX(affiliate_name), business_date, SUM(received),"
        " SUM(bs_extracted), SUM(submitted), SUM(sold), SUM(revenue),"
        " SUM(bs_cost), SUM(profit) FROM affiliate_day"
        " WHERE business_date >= ? AND source = ? AND affiliate_id IS NOT NULL"
        + store._not_test() + " GROUP BY affiliate_id, business_date",
        (cutoff, source)).fetchall()
    all_days = sorted({r[2] for r in rows})
    known = {r[0]: r[1] for r in conn.execute("SELECT affiliate_id, name FROM affiliate")}

    by_affiliate: dict[int, dict] = {}
    for aid, name, day_, rec, ext, sub_, sold, rev, bs_cost, profit in rows:
        a = by_affiliate.setdefault(aid, {"name": known.get(aid) or name, "days": {},
                                          "bs_cost": 0.0, "profit": 0.0})
        a["bs_cost"] += bs_cost or 0
        a["profit"] += profit or 0
        a["days"][day_] = {"day": day_, "received": rec or 0, "extracted": ext or 0,
                           "submitted": sub_ or 0, "sold": sold or 0, "revenue": rev or 0}

    out = []
    for aid, a in by_affiliate.items():
        series = [a["days"].get(d) or {"day": d, "received": 0, "extracted": 0,
                                        "submitted": 0, "sold": 0, "revenue": 0}
                  for d in all_days]
        leads = sum(d["received"] for d in series)
        if not leads:
            continue
        journey = journeys.journey_of(aid, source)
        measures = {}
        for name, (num, den) in trends.MEASURES.items():
            if name == "leads_to_statement" and not journeys.has_statement_step(journey):
                measures[name] = {"applies": False}
                continue
            measures[name] = {"applies": True,
                              "points": trends.rolling(series, num, den),
                              **trends.assess(series, name)}
        out.append({"id": aid, "name": a["name"], "leads": leads, "journey": journey,
                    # Money over the window, for the trends table's own columns
                    # and its default order.
                    "revenue": sum(d["revenue"] for d in series),
                    "bsCost": a["bs_cost"], "profit": a["profit"],
                    # A line through one or three leads is noise drawn as signal.
                    "enoughToTrend": leads >= store.MIN_SCORED_LEADS,
                    "journeyLabel": journeys.LABELS[journey],
                    "journeyNote": journeys.DESCRIPTIONS[journey],
                    "measures": measures})
    out.sort(key=lambda r: (-r["revenue"], -r["leads"]))
    return out


# The bands each lender's buying is broken down by, in the order offered
# on the page. `money` puts a dollar sign on the band labels.
DEMOGRAPHICS = (
    ("WagesAndCentrelinkMonthly", "Monthly income (wages and Centrelink)", True),
    ("WagesMonthly", "Monthly wages", True),
    ("LoanAmount", "Loan amount", True),
    ("Age", "Age", False),
    ("CentrelinkAsPercentOfIncome", "Centrelink, % of income", False),
    ("GamblingPercentageOfIncome", "Gambling, % of income", False),
    ("IncomeSpentOnDayOfDeposit", "Income spent on payday, %", False),
    ("TotalDishonours", "Dishonours", False),
    ("TotalSaccDishonours", "SACC dishonours", False),
    ("DishonourGreaterThan50DaysLast30Days", "Dishonours over 50 days, last 30 days", False),
    ("SaccLoansFundedInLast14Days", "SACC loans funded, last 14 days", False),
    ("SaccLoanFundedInStatementPeriod", "SACC loans funded in statement period", False),
)


def lender_payload(conn, days=30):
    """The lender page: per lender, the three rates to keep from falling,
    its tiers, and what it buys by band against what it is offered."""
    rows = store.lender_daily(conn, days)
    all_days = sorted({r[2] for r in rows})
    # Profit is the lender report's own: revenue less affiliate commission.
    # It cannot see the internal sources' costs or bank statement costs,
    # which are per lead rather than per lender, so it reads high.
    fields = ("processed", "sent", "accepted", "redirect", "revenue",
              "commission", "profit")
    blank = dict.fromkeys(fields, 0)

    by_lender: dict[int, dict] = {}
    totals: dict[str, dict] = {d: {"day": d, **blank} for d in all_days}
    for lid, name, day_, *values in rows:
        lender = by_lender.setdefault(lid, {"name": (name or "").strip() or f"Lender {lid}",
                                            "days": {}})
        point = {"day": day_, **{f: v or 0 for f, v in zip(fields, values)}}
        lender["days"][day_] = point
        for f in fields:
            totals[day_][f] += point[f]

    tiers: dict[int, list] = {}
    for lid, tid, tname, price, *values in store.lender_tier_totals(conn, days):
        tiers.setdefault(lid, []).append({
            "id": tid, "name": tname, "price": price,
            **{f: v or 0 for f, v in zip(fields, values)}})

    lenders = []
    for lid, lender in by_lender.items():
        series = [lender["days"].get(d) or {"day": d, **blank} for d in all_days]
        sums = {f: sum(p[f] for p in series) for f in fields}
        if not sums["processed"]:
            continue
        measures = {}
        for key, (num, den) in trends.LENDER_MEASURES.items():
            measures[key] = {
                # A line through a handful of leads is noise drawn as signal.
                "enoughToTrend": sums[den] >= store.MIN_SCORED_LEADS,
                "points": trends.rolling(series, num, den),
                **trends.assess(series, key, measures=trends.LENDER_MEASURES)}
        lenders.append({"id": lid, "name": lender["name"], **sums,
                        "measures": measures,
                        "tiers": sorted(tiers.get(lid, []), key=lambda t: -t["processed"])})
    lenders.sort(key=lambda r: (-r["revenue"], -r["sent"], -r["processed"]))

    demographics = {"metrics": [], "bands": {}, "byLender": {}, "all": {}}
    stored = set(store.lender_demographic_metrics(conn))
    for metric, label, money in DEMOGRAPHICS:
        if metric not in stored:
            continue
        per_lender, bands = store.lender_demographics(conn, metric, days)
        if not per_lender:
            continue
        everyone: dict[str, list] = {}
        for counts in per_lender.values():
            for band, trio in counts.items():
                slot = everyone.setdefault(band, [0, 0, 0])
                for i, n in enumerate(trio):
                    slot[i] += n or 0
        demographics["metrics"].append({"key": metric, "label": label, "money": money})
        demographics["bands"][metric] = bands
        demographics["byLender"][metric] = {str(k): v for k, v in per_lender.items()}
        demographics["all"][metric] = everyone

    return {
        "view": "lenders",
        "generatedUtc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "windowDays": days,
        "trendWindow": trends.WINDOW,
        "days": [totals[d] for d in all_days],
        "lenders": lenders,
        "demographics": demographics,
    }


def render(data, template: Path = TEMPLATE) -> str:
    html = template.read_text(encoding="utf-8")
    if STYLE_PLACEHOLDER in html:
        shared = TEMPLATE.read_text(encoding="utf-8")
        start, end = shared.index("<style>") + len("<style>"), shared.index("</style>")
        html = html.replace(STYLE_PLACEHOLDER, shared[start:end])
    if PLACEHOLDER not in html:
        raise ValueError(f"{template}: data placeholder not found")
    # The page is embedded in a <script>, so a closing tag inside the
    # JSON would end it early.
    body = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return html.replace(PLACEHOLDER, body)


def page(conn, days=30, source="external") -> bytes:
    return render(payload(conn, days, source)).encode("utf-8")


def lender_page(conn, days=30) -> bytes:
    return render(lender_payload(conn, days), LENDER_TEMPLATE).encode("utf-8")
