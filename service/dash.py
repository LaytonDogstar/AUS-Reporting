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
CUSTOMER_TEMPLATE = Path(__file__).with_name("customers.html")
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
                 "internal": "Internal sources (FF)",
                 "all": "All sources",
                 "both": "External and FF, side by side"}

# Which populations the Today and Yesterday pages can show. `all` is
# offered there alone: their figures come from the ping log, which
# counts both populations alike, where the affiliate reports do not.
TODAY_SOURCES = ("all", "external", "internal")


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
        " WHERE business_date >= ? AND business_date <= win_end() AND source = ? AND affiliate_id IS NOT NULL"
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
        # Google Ads, which only the internal report carries.
        "paid": paid_acquisition(conn, days) if source == "internal" else None,
        # Which lenders bought each affiliate's leads, for its drill-down.
        "lendersByAffiliate": {aid: rows for aid, rows in
                               pair_drilldown(conn, days, "affiliate").items()
                               if int(aid) in seen},
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


# A paid-acquisition measure is flagged when the latest week is at least
# this much below the weeks before it.
PAID_DROP = 0.15


def paid_acquisition(conn, days=30):
    """FF's Google Ads: spend, what it brought in, and what is left.

    From the internal report's rows. Revenue and profit are FF's own
    across every internal source; profit is the report's, net of
    statement cost and Ads spend. All the spend is booked to one source,
    so this is read for FF as a whole and never per source.

    Each measure is a rolling seven days with the latest week set against
    the weeks before, as the trend tables are, read over at least
    store.TREND_DAYS so a short range still has a line; the totals are
    the range's own.
    """
    start = store.business_cutoff(days)
    rows = store.ff_money_daily(conn, days, since=store.trend_cutoff(days))
    series = [{"day": d, "revenue": rev or 0, "bs_cost": bs or 0, "spend": ads or 0,
               "profit": prof or 0, "received": rec or 0, "sold": sold or 0}
              for d, rev, bs, ads, prof, rec, sold in rows]
    window = [d for d in series if d["day"] >= start]
    if not window or not any(d["spend"] for d in window):
        return None

    def split(field):
        recent, before = series[-trends.WINDOW:], series[:-trends.WINDOW]
        cur = sum(d[field] for d in recent) / len(recent) if recent else None
        base = sum(d[field] for d in before) / len(before) if before else None
        return cur, base

    def measure(points, cur, base):
        change = (cur - base) / abs(base) if cur is not None and base else None
        return {"points": points, "current": cur, "baseline": base, "change": change,
                "flag": change is not None and change <= -PAID_DROP}

    out = {}
    for field in ("spend", "revenue", "profit"):
        cur, base = split(field)
        out[field] = measure(trends.rolling_mean(series, field), cur, base)
    recent, before = series[-trends.WINDOW:], series[:-trends.WINDOW]
    r_cur = (sum(d["revenue"] for d in recent) / sum(d["spend"] for d in recent)
             if sum(d["spend"] for d in recent) else None)
    r_base = (sum(d["revenue"] for d in before) / sum(d["spend"] for d in before)
              if sum(d["spend"] for d in before) else None)
    out["roas"] = measure(trends.rolling(series, "revenue", "spend"), r_cur, r_base)
    out["totals"] = {k: sum(d[k] for d in window) for k in
                     ("spend", "revenue", "profit", "bs_cost", "received", "sold")}
    out["days"] = len(window)
    return out


def payload_both(conn, days=30):
    """The Affiliates page with both populations on it, side by side.

    Each population is built exactly as its own page would be, and every
    row carries which one it came from, so the page can show them next
    to each other - two figures in a tile, two charts, two funnels, one
    table with FF marked - and never add one to the other. The two
    reports count differently (the dev team, 13 Oct 2026), so a sum of
    the two would be a number that means nothing.
    """
    ext, ff = payload(conn, days, "external"), payload(conn, days, "internal")
    out = dict(ext)
    for key in ("applications", "accepts", "stage_counts"):
        out[key] = ([{**r, "Pop": "external"} for r in ext[key]] +
                    [{**r, "Pop": "internal"} for r in ff[key]])
    out["affiliates"] = ([{**a, "Pop": "external"} for a in ext["affiliates"]] +
                         [{**a, "Pop": "internal"} for a in ff["affiliates"]])
    out["trends"] = sorted([{**r, "pop": "external"} for r in ext["trends"]] +
                           [{**r, "pop": "internal"} for r in ff["trends"]],
                           key=lambda r: (-r["revenue"], -r["leads"]))
    out["scorecard"] = ([{**r, "pop": "external"} for r in ext["scorecard"]] +
                        [{**r, "pop": "internal"} for r in ff["scorecard"]])
    out["lendersByAffiliate"] = {**ext["lendersByAffiliate"], **ff["lendersByAffiliate"]}
    out["affiliateQuality"] = {**ext["affiliateQuality"], **ff["affiliateQuality"]}
    # Quality rates are per population too: a band's sold rate is taken
    # within one, so each affiliate is read against its own.
    out["qualityBandsBySource"] = {"external": ext["qualityBands"], "internal": ff["qualityBands"]}
    out["paid"] = ff["paid"]
    out["source"] = "both"
    out["sourceLabel"] = SOURCE_LABELS["both"]
    return out


def affiliate_trends(conn, days, source):
    """Per affiliate: the three measures Layton watches, as rolling
    seven-day series, each with a call on whether it has dropped.

    Every day in the window is present for every affiliate, zero-filled,
    so a week with no leads reads as a gap in volume rather than being
    silently skipped over. The lines read over at least store.TREND_DAYS,
    so a short range still has weeks before its latest; the totals are
    the range's own.
    """
    start = store.business_cutoff(days)
    cutoff = store.trend_cutoff(days)
    rows = conn.execute(
        "SELECT affiliate_id, MAX(affiliate_name), business_date, SUM(received),"
        " SUM(bs_extracted), SUM(submitted), SUM(sold), SUM(revenue),"
        " SUM(bs_cost), SUM(profit) FROM affiliate_day"
        " WHERE business_date >= ? AND business_date <= win_end() AND source = ? AND affiliate_id IS NOT NULL"
        + store._not_test() + " GROUP BY affiliate_id, business_date",
        (cutoff, source)).fetchall()
    all_days = sorted({r[2] for r in rows})
    known = {r[0]: r[1] for r in conn.execute("SELECT affiliate_id, name FROM affiliate")}

    by_affiliate: dict[int, dict] = {}
    for aid, name, day_, rec, ext, sub_, sold, rev, bs_cost, profit in rows:
        a = by_affiliate.setdefault(aid, {"name": known.get(aid) or name, "days": {},
                                          "bs_cost": 0.0, "profit": 0.0})
        if day_ >= start:
            a["bs_cost"] += bs_cost or 0
            a["profit"] += profit or 0
        a["days"][day_] = {"day": day_, "received": rec or 0, "extracted": ext or 0,
                           "submitted": sub_ or 0, "sold": sold or 0, "revenue": rev or 0}

    out = []
    for aid, a in by_affiliate.items():
        series = [a["days"].get(d) or {"day": d, "received": 0, "extracted": 0,
                                        "submitted": 0, "sold": 0, "revenue": 0}
                  for d in all_days]
        window = [d for d in series if d["day"] >= start]
        leads = sum(d["received"] for d in window)
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
                    "revenue": sum(d["revenue"] for d in window),
                    "bsCost": a["bs_cost"], "profit": a["profit"],
                    # A line through one or three leads is noise drawn as signal.
                    "enoughToTrend": sum(d["received"] for d in series) >= store.MIN_SCORED_LEADS,
                    "journeyLabel": journeys.LABELS[journey],
                    "journeyNote": journeys.DESCRIPTIONS[journey],
                    "measures": measures})
    out.sort(key=lambda r: (-r["revenue"], -r["leads"]))
    return out


def pair_drilldown(conn, days, by):
    """Affiliate-by-lender, from the ping log, grouped one way round.

    by="affiliate": {affiliate_id: [one row per lender that was offered
    its leads]}; by="lender": {lender_id: [one row per affiliate]}. Each
    row carries its counts over the window and two trend lines: what the
    lender bought per day, as a seven-day average, and its acceptance -
    bought over passed its filters - with the same drop call as the
    trends tables.
    """
    start = store.business_cutoff(days)
    rows = store.affiliate_lender_daily(conn, days, since=store.trend_cutoff(days))
    all_days = sorted({r[0] for r in rows})
    fields = ("offered", "passed", "sold", "value")
    pairs: dict[tuple[int, int], dict] = {}
    for day_, aid, lid, *values in rows:
        pairs.setdefault((aid, lid), {})[day_] = {"day": day_, **dict(zip(fields, values))}

    affiliates = {r[0]: (r[1], bool(r[2])) for r in conn.execute(
        "SELECT affiliate_id, name, is_internal FROM affiliate")}
    lenders = store.lender_names(conn)

    grouped: dict[int, list] = {}
    for (aid, lid), by_day in pairs.items():
        series = [by_day.get(d_) or {"day": d_, "offered": 0, "passed": 0, "sold": 0,
                                     "value": 0.0} for d_ in all_days]
        sums = {f: sum(p[f] or 0 for p in series if p["day"] >= start) for f in fields}
        # Whether a line is worth drawing is over the days it is drawn on.
        span = {f: sum(p[f] or 0 for p in series) for f in fields}
        if not sums["offered"]:
            continue
        acceptance = trends.assess(series, "acceptance", measures=trends.PAIR_MEASURES)
        name, internal = affiliates.get(aid, (None, False))
        row = {**sums,
               "id": lid if by == "affiliate" else aid,
               "name": (lenders.get(lid, f"Lender {lid}") if by == "affiliate"
                        else name or f"Affiliate {aid}"),
               "internal": internal if by == "lender" else None,
               "bought": {"points": trends.rolling_mean(series, "sold"),
                          "enoughToTrend": span["sold"] >= 10},
               "acceptance": {"points": trends.rolling(series, "sold", "passed"),
                              # A line through a handful of leads is noise.
                              "enoughToTrend": span["passed"] >= store.MIN_SCORED_LEADS,
                              **acceptance}}
        grouped.setdefault(aid if by == "affiliate" else lid, []).append(row)

    for group in grouped.values():
        bought = sum(r["sold"] for r in group)
        for r in group:
            r["share"] = r["sold"] / bought if bought else None
        group.sort(key=lambda r: (-r["value"], -r["sold"], -r["offered"]))
    return {str(k): v for k, v in grouped.items()}


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
    # Trends read over at least store.TREND_DAYS; totals are the range's.
    start = store.business_cutoff(days)
    rows = store.lender_daily(conn, days, since=store.trend_cutoff(days))
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
        sums = {f: sum(p[f] for p in series if p["day"] >= start) for f in fields}
        span = {f: sum(p[f] for p in series) for f in fields}
        if not sums["processed"]:
            continue
        measures = {}
        for key, (num, den) in trends.LENDER_MEASURES.items():
            measures[key] = {
                # A line through a handful of leads is noise drawn as signal.
                "enoughToTrend": span[den] >= store.MIN_SCORED_LEADS,
                "points": trends.rolling(series, num, den),
                **trends.assess(series, key, measures=trends.LENDER_MEASURES)}
        lenders.append({"id": lid, "name": lender["name"], **sums,
                        "measures": measures,
                        # Its own days, for the tiles' trends when it is picked.
                        "daily": [{k: p[k] for k in ("day", "processed", "sent", "accepted",
                                                     "redirect", "revenue")} for p in series],
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
        # Which affiliates' leads each lender bought, for its drill-down.
        "affiliatesByLender": pair_drilldown(conn, days, "lender"),
        "demographics": demographics,
    }


# Bands that are not customers. Age under 18 is test data - see the
# quality page - and would otherwise sit in every lender's mix.
NOT_CUSTOMERS = {("Age", "<18")}

# The buyer profile: every measure, in this order, set out at once. The
# first two are categorical and keep their stored value as the band.
PROFILE = (
    ("EmploymentStatus", "Employment", False),
    ("TimeAtEmployer", "Time at employer", False),
) + DEMOGRAPHICS
CATEGORY_ORDER = {
    "EmploymentStatus": ["Full time", "Part time", "Casual", "Self-employed",
                         "Centrelink", "Unemployed"],
    "TimeAtEmployer": ["Less than 3 months", "3 months to 1 year", "More than 1 year"],
}
MIN_PROFILE_PURCHASES = 20      # a lender's profile over fewer is noise


def customer_payload(conn, days=30):
    """Buyer profiles, after the US dashboard's: what each lender buys
    against the pool of every lead received, every measure at once.

    For each measure and band: the pool share (that band's share of all
    leads received), and per lender its share of its own purchases, the
    index (100 = in proportion to the pool, 200 = twice as common, 0 =
    never) and the share of the band's leads it took. Per lender, each
    band's share as a rolling seven-day line beside the pool's, with a
    shift in either direction called beyond chance.

    Lines are values on one shared day axis rather than (day, value)
    pairs: fourteen measures by twenty lenders by six bands is a lot of
    repeated dates.
    """
    cutoff = store.business_cutoff(days)
    axis = [r[0] for r in conn.execute(
        "SELECT DISTINCT business_date FROM quality_tier_day WHERE business_date >= ? AND business_date <= win_end()"
        " ORDER BY 1", (cutoff,))]
    names = store.lender_names(conn)

    money = {}
    for lid, name, _day, _p, _s, accepted, _r, revenue, *_ in store.lender_daily(conn, days):
        m = money.setdefault(lid, {"name": (name or "").strip() or names.get(lid, f"Lender {lid}"),
                                   "revenue": 0.0, "accepted": 0})
        m["revenue"] += revenue or 0
        m["accepted"] += accepted or 0
    tiers: dict[int, list] = {}
    for lid, tid, tname, _price, _p, _s, accepted, _r, revenue, *_ in store.lender_tier_totals(conn, days):
        if accepted:
            tiers.setdefault(lid, []).append({"key": f"T{tid}", "name": tname, "revenue": revenue or 0,
                                              "accepted": accepted})

    # Profiles are kept per lender ("L12") and per tier ("T90"): the grid
    # shows lenders, and a lender's profile can be opened for one tier.
    attrs, trend = [], {}
    bought_by: dict[str, int] = {}
    for metric, label, is_money in PROFILE:
        pool_daily: dict[str, dict] = {}
        for day_, band, n in conn.execute(
                "SELECT business_date, band, SUM(n) FROM quality_day WHERE metric=?"
                " AND business_date >= ? AND business_date <= win_end() GROUP BY 1, 2", (metric, cutoff)):
            if (metric, band) not in NOT_CUSTOMERS:
                pool_daily.setdefault(day_, {})[band] = n or 0
        rows = [r for r in store.tier_demographics_daily(conn, metric, days)
                if (metric, r[3]) not in NOT_CUSTOMERS]
        if not rows or not pool_daily:
            continue
        sold: dict[str, dict] = {}
        order: dict[str, int] = {}
        for day_, lid, tid, band, band_order, _offered, n in rows:
            order[band] = band_order
            for key in (f"L{lid}", f"T{tid}"):
                cell = sold.setdefault(key, {}).setdefault(day_, {})
                cell[band] = cell.get(band, 0) + (n or 0)
        pool = {}
        for day_counts in pool_daily.values():
            for band, n in day_counts.items():
                pool[band] = pool.get(band, 0) + n
                order.setdefault(band, 0)
        pool_total = sum(pool.values())
        known = CATEGORY_ORDER.get(metric)
        bands = sorted(order, key=(lambda b: (known.index(b) if b in known else 99, b)) if known
                       else (lambda b: (order[b], b)))
        bands = [b for b in bands if b != "unknown"] + (["unknown"] if "unknown" in order else [])

        pool_series = [{"day": d_, "p": 0, "P": sum(pool_daily.get(d_, {}).values())} for d_ in axis]
        levels = []
        for band in bands:
            for i, d_ in enumerate(axis):
                pool_series[i]["p"] = pool_daily.get(d_, {}).get(band, 0)
            pool_line = [None if v is None else round(v, 4)
                         for _, v in trends.rolling(pool_series, "p", "P")]
            cells = {}
            for key, by_day in sold.items():
                n = sum(c.get(band, 0) for c in by_day.values())
                total = sum(sum(c.values()) for c in by_day.values())
                ps = pool.get(band, 0) / pool_total if pool_total else 0
                share = n / total if total else 0
                cells[key] = {"n": n, "share": round(share, 5),
                              "index": round(100 * share / ps, 2) if ps else None,
                              "take": round(n / pool[band], 5) if pool.get(band) else None}
            levels.append({"band": band, "poolShare": pool.get(band, 0) / pool_total if pool_total else 0,
                           "poolN": pool.get(band, 0), "pool": pool_line, "cells": cells})
        attrs.append({"key": metric, "label": label, "money": is_money, "levels": levels})

        for key, by_day in sold.items():
            total = sum(sum(c.values()) for c in by_day.values())
            bought_by[key] = max(bought_by.get(key, 0), total)
            series_total = [sum(by_day.get(d_, {}).values()) for d_ in axis]
            for band in bands:
                series = [{"day": d_, "s": by_day.get(d_, {}).get(band, 0), "S": series_total[i]}
                          for i, d_ in enumerate(axis)]
                if not any(p["s"] for p in series):
                    continue                # never bought: a flat line at zero says nothing
                shift = trends.assess_shift(series, "s", "S")
                r4 = lambda v: None if v is None else round(v, 4)
                trend.setdefault(key, {}).setdefault(metric, {})[band] = {
                    "share": [r4(v) for _, v in trends.rolling(series, "s", "S")],
                    "current": r4(shift["current"]), "baseline": r4(shift["baseline"]),
                    "change": r4(shift["change"]), "shift": shift["shift"]}

    lenders = []
    for key, bought in bought_by.items():
        if not key.startswith("L") or bought < MIN_PROFILE_PURCHASES:
            continue
        lid = int(key[1:])
        m = money.get(lid, {"name": names.get(lid, f"Lender {lid}"), "revenue": 0.0, "accepted": 0})
        own_tiers = []
        for t in sorted(tiers.get(lid, []), key=lambda t: -t["revenue"]):
            t_bought = bought_by.get(t["key"], 0)
            own_tiers.append({**t, "bought": t_bought,
                              "avgPrice": t["revenue"] / t["accepted"] if t["accepted"] else 0,
                              # too few purchases make a profile of noise
                              "profiled": t_bought >= MIN_PROFILE_PURCHASES})
        lenders.append({"id": lid, "key": key, "name": m["name"], "revenue": m["revenue"],
                        "bought": bought,
                        "avgPrice": (m["revenue"] / m["accepted"]) if m["accepted"] else 0,
                        "tiers": own_tiers})
    lenders.sort(key=lambda r: (-r["revenue"], -r["bought"]))
    shown = {l["key"] for l in lenders} | {t["key"] for l in lenders for t in l["tiers"]
                                           if t["profiled"]}
    for a in attrs:
        for level in a["levels"]:
            level["cells"] = {k: v for k, v in level["cells"].items() if k in shown}
    return {
        "view": "customers",
        "generatedUtc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "windowDays": days,
        "trendWindow": trends.WINDOW,
        # rolling points start a window in
        "axis": axis[trends.WINDOW - 1:],
        "lenders": lenders,
        "attrs": attrs,
        "trends": {k: v for k, v in trend.items() if k in shown},
    }


def render(data, template: Path = TEMPLATE) -> str:
    info = store.window_info()
    if info:
        data.update({k: v for k, v in info.items() if v is not None})
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


def customer_page(conn, days=30) -> bytes:
    return render(customer_payload(conn, days), CUSTOMER_TEMPLATE).encode("utf-8")


def lender_page(conn, days=30) -> bytes:
    return render(lender_payload(conn, days), LENDER_TEMPLATE).encode("utf-8")


# ------------------------------------------------------------------ today

TODAY_TEMPLATE = Path(__file__).with_name("today.html")

# The norm a day is set against: the same weekday over the last eight
# weeks when there are at least three of them, otherwise the last 28
# finished days. Lending has a weekly shape - Saturday is not a slow
# Monday - so a like-for-like weekday is the better yardstick once the
# history has it. A fresh deploy rebuilds newest first, so for the first
# hour or so the norm is the few days already back.
NORM_WEEKDAY_MIN = 3
NORM_WEEKDAYS = 4
NORM_DAYS = 28

# A lender is called off its usual appetite only when the gap is at
# least this many sales and at least one standard deviation of a count
# that size: two sales against an expected one is not news.
MOVER_MIN = 2
MOVERS_SHOWN = 6
TOP_TIERS = 8
EPL_ROWS = 8


def _norm_days(conn, day: dt.date):
    before = store.hour_days(conn, day.isoformat(), 120)
    same = [d for d in before if dt.date.fromisoformat(d).weekday() == day.weekday()
            and (day - dt.date.fromisoformat(d)).days <= 56][:NORM_WEEKDAYS]
    if len(same) >= NORM_WEEKDAY_MIN:
        return same, "a typical " + day.strftime("%A")
    recent = [d for d in before if (day - dt.date.fromisoformat(d)).days <= NORM_DAYS]
    return recent, ("a typical day" if recent else None)


def _hours():
    return [0] * 24


def movers(lenders, leads_so_far, norm_leads):
    """Lenders off their usual appetite: sold against what their norm
    buy-rate would have bought from the leads in so far.

    Expectation is scaled to the day's supply, not its clock: a lender
    buying its usual share of fewer leads is not pulling back.
    """
    out = []
    if not leads_so_far or not norm_leads:
        return out
    for r in lenders:
        n_sold = r["sum"]["norm"]["sold"]
        rate_norm = n_sold / norm_leads
        expected = rate_norm * leads_so_far
        sold = r["sum"]["day"]["sold"]
        gap = sold - expected
        if abs(gap) < MOVER_MIN or abs(gap) < max(expected, 1) ** 0.5:
            continue
        out.append({"id": r["id"], "name": r["name"], "sold": sold,
                    "expected": round(expected, 1), "gap": round(gap, 1),
                    "rate": sold / leads_so_far, "rateNorm": rate_norm})
    out.sort(key=lambda m: m["gap"])
    return out


def epl_breakdown(lenders):
    """Why earnings per sale differ from the norm, lender by lender.

    EPL is the share-weighted average of each lender's price, so the gap
    splits exactly into mix - a lender taking a bigger or smaller share
    of the sales, valued at its usual price - and price - a lender paying
    more or less per sale than usual, at today's share:

        mix   = (share_today - share_norm) * price_norm
        price = share_today * (price_today - price_norm)

    Summed over every lender, mix + price is the whole gap. A lender new
    to the day is priced at its own price both ways, so it lands in mix.
    """
    sold_t = sum(r["sum"]["day"]["sold"] for r in lenders)
    sold_n = sum(r["sum"]["norm"]["sold"] for r in lenders)
    if not sold_t or not sold_n:
        return None
    rows = []
    for r in lenders:
        st, sn = r["sum"]["day"]["sold"], r["sum"]["norm"]["sold"]
        vt, vn = r["sum"]["day"]["value"], r["sum"]["norm"]["value"]
        pt = vt / st if st else None
        pn = vn / sn if sn else pt
        share_t, share_n = st / sold_t, sn / sold_n
        mix = (share_t - share_n) * (pn or 0)
        price = share_t * ((pt or 0) - (pn or 0)) if st else 0.0
        rows.append({"id": r["id"], "name": r["name"], "shareDay": share_t,
                     "shareNorm": share_n, "priceDay": pt, "priceNorm": pn if sn else None,
                     "mix": mix, "price": price, "impact": mix + price})
    epl_t = sum(r["sum"]["day"]["value"] for r in lenders) / sold_t
    epl_n = sum(r["sum"]["norm"]["value"] for r in lenders) / sold_n
    rows.sort(key=lambda r: -abs(r["impact"]))
    shown, rest = rows[:EPL_ROWS], rows[EPL_ROWS:]
    if rest:
        shown.append({"id": None, "name": f"Everyone else ({len(rest)})",
                      "shareDay": sum(r["shareDay"] for r in rest),
                      "shareNorm": sum(r["shareNorm"] for r in rest),
                      "priceDay": None, "priceNorm": None,
                      "mix": sum(r["mix"] for r in rest), "price": sum(r["price"] for r in rest),
                      "impact": sum(r["impact"] for r in rest)})
    return {"eplDay": epl_t, "eplNorm": epl_n, "gap": epl_t - epl_n,
            "mix": sum(r["mix"] for r in rows), "price": sum(r["price"] for r in rows),
            "rows": shown}


def _aggregate(conn, rows, nb, place):
    """Leads and sales into buckets, each with the period's own figure and
    the typical one beside it.

    `rows` are (leads, sales, filtered) as store.hour_rows gives them; `nb`
    is how many buckets - hours of a day, or days of a period; `place(d, h)`
    says where a row of day `d`, hour `h` lands: [(side, bucket, weight)].
    Returns the totals, and the lender, tier and affiliate buckets.
    """
    leads_rows, sales_rows, filtered_rows = rows
    tiers = {r[0]: {"tier": r[1], "lender_id": r[2], "lender": r[3]} for r in conn.execute(
        "SELECT lender_tier_id, lender_tier_name, lender_id, lender_name FROM lender_tier")}
    affiliates = {r[0]: r[1] for r in conn.execute("SELECT affiliate_id, name FROM affiliate")}
    lender_names = {t["lender_id"]: (t["lender"] or "").strip() or t["tier"]
                    for t in tiers.values() if t["lender_id"] is not None}

    def blank():
        # `offered` is what a lender evaluated: passed its filters
        # (reached) or failed them. Filter pass is reached / offered.
        return {k: [0] * nb for k in ("leads", "soldLeads", "sold", "value", "reached", "offered")}

    total = {"day": blank(), "norm": blank()}
    lenders, tier_rows, affs = {}, {}, {}

    def bucket(table, key, name_fn):
        b = table.get(key)
        if b is None:
            b = table[key] = {"day": blank(), "norm": blank(), "name": name_fn()}
        return b

    def aff_name(aid):
        return lambda: affiliates.get(aid) or f"Affiliate {aid}"

    for d, h, aid, n, n_sold in leads_rows:
        for side, i, w in place(d, h):
            for b in (total, bucket(affs, aid, aff_name(aid))):
                b[side]["leads"][i] += n * w
                b[side]["soldLeads"][i] += n_sold * w
    for d, h, aid, lid, tid, reached, sold, value in sales_rows:
        info = tiers.get(tid, {})
        lname = (info.get("lender") or "").strip() or info.get("tier") or f"Lender {lid}"
        for side, i, w in place(d, h):
            for b in (total, bucket(lenders, lid, lambda: lname),
                      bucket(tier_rows, tid, lambda: (info.get("tier") or f"Tier {tid}")),
                      bucket(affs, aid, aff_name(aid))):
                b[side]["reached"][i] += reached * w
                b[side]["offered"][i] += reached * w
                b[side]["sold"][i] += sold * w
                b[side]["value"][i] += value * w
            tier_rows[tid]["lender"] = lname
    for d, h, aid, lid, n in filtered_rows:
        # Lender and affiliate only: the counts are not kept per tier.
        lname = lender_names.get(lid) or f"Lender {lid}"
        for side, i, w in place(d, h):
            for b in (total, bucket(lenders, lid, lambda: lname),
                      bucket(affs, aid, aff_name(aid))):
                b[side]["offered"][i] += n * w
    return total, lenders, tier_rows, affs


def _rounded(b):
    return {side: {k: [round(v, 3) for v in arr] for k, arr in b[side].items()}
            for side in ("day", "norm")}


def _summary(b):
    return {side: {k: round(sum(arr), 2) for k, arr in b[side].items()}
            for side in ("day", "norm")}


def _entity_list(table, limit=None, key="value"):
    rows = []
    for eid, b in table.items():
        s = _summary(b)
        if not (s["day"]["sold"] or s["norm"]["sold"] or s["day"]["leads"]):
            continue
        rows.append({"id": eid, "name": b["name"], "lender": b.get("lender"),
                     "sum": s, **_rounded(b)})
    rows.sort(key=lambda r: (-r["sum"]["day"][key], -r["sum"]["norm"][key]))
    return rows[:limit] if limit else rows


def _money_parts(conn, source, days, norm_weights):
    """FF money and each population's profit: the days' own, summed, and
    the typical figure, each norm day weighted as the buckets weight it.

    A day not yet in the reports counts as missing rather than zero."""
    cache = {}

    def money(d, pop):
        if (d, pop) not in cache:
            cache[(d, pop)] = store.ff_money(conn, d, pop)
        return cache[(d, pop)]

    def typical(pop, field):
        # Per period day, the average of its norm days that the report has.
        total, any_ = 0.0, False
        for _day, weights in norm_weights:
            vals = [(money(d, pop)[field] or 0) for d in weights
                    if money(d, pop) and not money(d, pop)["live"]]
            if vals:
                total += sum(vals) / len(vals)
                any_ = True
        return total if any_ else None

    ff = None
    if source in ("internal", "all"):
        have = [money(d, "internal") for d in days if money(d, "internal")]
        cur = None
        if have:
            cur = {k: sum(m[k] or 0 for m in have) for k in
                   ("received", "sold", "revenue", "bs_cost", "adword_spent", "profit")}
            cur.update(live=any(m["live"] for m in have),
                       collected_at=max(m["collected_at"] or "" for m in have) or None)
        norm = {k: typical("internal", k) for k in ("revenue", "adword_spent", "profit", "bs_cost")}
        ff = {"day": cur, "norm": norm if norm["revenue"] is not None else None}

    pops = {"all": ("external", "internal"), "external": ("external",),
            "internal": ("internal",)}[source]
    profit = {}
    for pop in pops:
        have = [money(d, pop) for d in days if money(d, pop)]
        profit[pop] = {"day": sum(m["profit"] or 0 for m in have) if have else None,
                       "live": any(m["live"] for m in have),
                       "collected_at": max((m["collected_at"] or "" for m in have), default="") or None,
                       "norm": typical(pop, "profit")}
    return ff, profit


def today_payload(conn, which="today", source="external"):
    """The Today and Yesterday pages: one day hour by hour against the
    norm, for one population. The day is `which` - today, still being
    read live, or yesterday."""
    today = store.business_today()
    day = today if which == "today" else today - dt.timedelta(days=1)
    iso = day.isoformat()
    meta = store.hour_meta(conn, iso)
    norm_days, norm_label = _norm_days(conn, day)
    n_norm = len(norm_days) or 1

    def place(d, h):
        return [("day", h, 1)] if d == iso else [("norm", h, 1 / n_norm)]

    total, lenders, tier_rows, affs = _aggregate(
        conn, store.hour_rows(conn, [iso] + norm_days, source), 24, place)
    internal = store.is_internal_map(conn)
    # FF money - Google Ads spend beside what it bought - and profit, from
    # the two affiliate reports: the day's own and the norm days' average.
    # FF's profit is after statement cost and Ads spend; external's after
    # commission and statement cost. Profit is money, so for All sources
    # the page adds the two - unlike lead counts, which the reports count
    # differently.
    ff, profit = _money_parts(conn, source, [iso], [(iso, norm_days)])

    lender_rows = _entity_list(lenders)
    day_sum, norm_sum = _summary(total)["day"], _summary(total)["norm"]
    return {
        "page": which,
        "date": iso,
        "weekday": day.strftime("%A"),
        "today": today.isoformat(),
        "meta": meta,
        "normDays": norm_days,
        "normLabel": norm_label,
        "source": source,
        "sourceLabel": SOURCE_LABELS.get(source, source),
        "total": _rounded(total),
        "lenders": lender_rows,
        "movers": movers(lender_rows, day_sum["leads"], norm_sum["leads"]),
        "epl": epl_breakdown(lender_rows),
        "tiers": _entity_list(tier_rows, TOP_TIERS * 3, key="sold"),
        "affiliates": [{**a, "internal": internal.get(a["id"], False)}
                       for a in _entity_list(affs)],
        "ff": ff,
        "profit": profit,
        "moverMin": MOVER_MIN,
        "moversShown": MOVERS_SHOWN,
        "topTiers": TOP_TIERS,
        "eplRows": EPL_ROWS,
        "generatedUtc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }


# The Day and Days pages: no longer than this, so a page stays quick to build.
PERIOD_MAX_DAYS = 90


def period_payload(conn, start, end, source="all", page="days"):
    """The Day and Days pages: the Today view over any finished day or run
    of days, one point a day rather than one an hour.

    Each day is set against its own typical day - the same weekday over
    the eight weeks before it, as Yesterday is - so the typical figure for
    the period is the sum of its days' and keeps their weekday mix.
    """
    first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    span = [(first + dt.timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]
    index = {d: i for i, d in enumerate(span)}
    # Every finished day, so a day early in the history can borrow weeks
    # after it; 56 days beyond the period's end is as far as any reaches.
    finished = store.hour_days(conn, (last + dt.timedelta(days=57)).isoformat(),
                               len(span) + 200)
    collected = [d for d in span if d in set(finished)]

    # A day not collected has no figure, so it has no typical one either:
    # the comparison runs over the days the page has. A day too early in
    # the history to have weeks before it borrows the nearest weeks after.
    have = set(collected)
    norms = []
    for d in span:
        if d not in have:
            norms.append((d, []))
            continue
        day = dt.date.fromisoformat(d)
        gap = {x: (day - dt.date.fromisoformat(x)).days for x in finished if x != d}
        pick = [x for x in gap if 0 < gap[x] <= 56 and gap[x] % 7 == 0][:NORM_WEEKDAYS]
        if len(pick) < NORM_WEEKDAY_MIN:
            pick = [x for x in gap if 0 < gap[x] <= NORM_DAYS]
        if len(pick) < NORM_WEEKDAY_MIN:
            near = sorted((x for x in gap if abs(gap[x]) <= 56 and gap[x] % 7 == 0),
                          key=lambda x: abs(gap[x]))
            pick = near[:NORM_WEEKDAYS] or pick
        if not pick:
            # Last resort, the history is that short: the nearest days.
            pick = sorted((x for x in gap if abs(gap[x]) <= NORM_DAYS),
                          key=lambda x: abs(gap[x]))[:NORM_WEEKDAYS]
        norms.append((d, pick))
    weights: dict[str, list] = {}
    for i, (_d, nd) in enumerate(norms):
        for x in nd:
            weights.setdefault(x, []).append((i, 1 / len(nd)))

    def place(d, _h):
        out = [("norm", i, w) for i, w in weights.get(d, ())]
        if d in have:
            out.append(("day", index[d], 1))
        return out

    dates = sorted(set(span) | set(weights))
    total, lenders, tier_rows, affs = _aggregate(
        conn, store.day_rows(conn, dates, source), len(span), place)
    internal = store.is_internal_map(conn)
    ff, profit = _money_parts(conn, source, collected, norms)

    lender_rows = _entity_list(lenders)
    day_sum, norm_sum = _summary(total)["day"], _summary(total)["norm"]
    used = sorted({x for _d, nd in norms for x in nd}, reverse=True)
    return {
        "page": page,
        "period": True,
        "start": start,
        "end": end,
        "buckets": span,
        "collectedDays": collected,
        "date": start if len(span) == 1 else None,
        "weekday": first.strftime("%A"),
        "today": store.business_today().isoformat(),
        "meta": {"complete": len(collected) == len(span), "as_of": None,
                 "days": len(span), "collected": len(collected)} if collected else None,
        "normDays": used,
        "normLabel": ("a typical " + first.strftime("%A")) if len(span) == 1 else "typical days",
        "source": source,
        "sourceLabel": SOURCE_LABELS.get(source, source),
        "total": _rounded(total),
        "lenders": lender_rows,
        "movers": movers(lender_rows, day_sum["leads"], norm_sum["leads"]),
        "epl": epl_breakdown(lender_rows),
        "tiers": [],
        "affiliates": [{**a, "internal": internal.get(a["id"], False)}
                       for a in _entity_list(affs)],
        "ff": ff,
        "profit": profit,
        "moverMin": MOVER_MIN,
        "moversShown": MOVERS_SHOWN,
        "topTiers": TOP_TIERS,
        "eplRows": EPL_ROWS,
        "generatedUtc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
