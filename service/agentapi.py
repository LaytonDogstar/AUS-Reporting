"""What the agents read: JSON built from what the service stored.

The scheduled agents run in fresh sessions away from the Railway
volume, so they see the service the way a person does - over HTTP -
with the agent token in place of a login. These are the read-only
views they need:

    status    is collection keeping up: each report's latest good day,
              failures and missing days
    day       one day's figures three ways - as stored per report, as
              the reports' own totals blocks gave them, and as the Day
              page shows them - so the data check can set each against
              the platform and against each other
    movers    per lender, the rates the Lenders page trends, with drops
              called; per lender and affiliate, acceptance shifts; and
              the customer bands whose share moved - what the buyer
              briefing explains
              - plus tiers that started, went quiet or stopped buying,
              each lender's last four fixed weeks, and FF and external
              a day at a time
    tiers     per tier, one figure a day (offered, passed, bought,
              revenue), for a lender or a list of tiers
    lenders   the tier list with the lender mapping filled in

Nothing here returns a lead, an applicant or a credential: counts and
money only, like every page.
"""

from __future__ import annotations

import datetime as dt

import agentstore
import collector
import dash
import store


def _row(conn, sql, args=()):
    r = conn.execute(sql, args)
    names = [d[0] for d in r.description]
    row = r.fetchone()
    return dict(zip(names, row)) if row else None


def status(conn, db_path=""):
    """Is collection keeping up?"""
    today = store.business_today()
    since = (today - dt.timedelta(days=30)).isoformat()
    week = (today - dt.timedelta(days=7)).isoformat()
    kinds = {}
    for kind in collector.KINDS:
        latest = conn.execute("SELECT MAX(business_date) FROM collection WHERE kind=? AND"
                              " status='ok'", (kind,)).fetchone()[0]
        ok30 = conn.execute("SELECT COUNT(*) FROM collection WHERE kind=? AND status='ok' AND"
                            " business_date >= ?", (kind, since)).fetchone()[0]
        failed = [{"date": d, "detail": (detail or "")[:200], "ran_at": ran}
                  for d, detail, ran in conn.execute(
                      "SELECT business_date, detail, ran_at FROM collection WHERE kind=? AND"
                      " status <> 'ok' AND business_date >= ? ORDER BY business_date DESC",
                      (kind, week))]
        kinds[kind] = {"latest_ok": latest, "ok_last_30": ok30, "failed_last_7": failed}
    missing = [d.isoformat() for d in collector.missing_days(conn, 30)]
    live = _row(conn, "SELECT MAX(ran_at) AS last_live FROM collection WHERE ran_at IS NOT NULL")
    return {"business_today": today.isoformat(),
            "expected_latest": (today - dt.timedelta(days=1)).isoformat(),
            "missing_days_last_30": missing,
            "kinds": kinds,
            "last_collection_run": live["last_live"] if live else None,
            "on_volume": str(db_path).startswith("/data"),
            "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def day(conn, iso):
    """One day's figures, stored and as shown."""
    d = dt.date.fromisoformat(iso).isoformat()
    lender_totals = _row(conn, "SELECT processed, sent, accepted, redirect, revenue, commission,"
                               " profit FROM totals_day WHERE business_date=?", (d,))
    lender_tiers = _row(conn, "SELECT COUNT(*) AS tiers, SUM(processed) AS processed,"
                              " SUM(sent) AS sent, SUM(accepted) AS accepted,"
                              " SUM(redirect) AS redirect, SUM(revenue) AS revenue,"
                              " SUM(commission) AS commission, SUM(profit) AS profit"
                              " FROM lender_tier_day WHERE business_date=?", (d,))
    affiliates = {}
    for source in store.SOURCES:
        affiliates[source] = {
            "totals": _row(conn, "SELECT received, bs_extracted, submitted, sold, decline,"
                                 " revenue, commission, bs_cost, profit, adword_spent"
                                 " FROM affiliate_totals_day WHERE business_date=? AND source=?",
                           (d, source)),
            "rows": _row(conn, "SELECT COUNT(*) AS affiliates, SUM(received) AS received,"
                               " SUM(bs_extracted) AS bs_extracted, SUM(submitted) AS submitted,"
                               " SUM(sold) AS sold, SUM(decline) AS decline,"
                               " SUM(revenue) AS revenue, SUM(commission) AS commission,"
                               " SUM(bs_cost) AS bs_cost, SUM(profit) AS profit,"
                               " SUM(adword_spent) AS adword_spent"
                               " FROM affiliate_day WHERE business_date=? AND source=?",
                         (d, source)),
        }
    by_status = {str(s): {"pings": p, "applications": a, "cost": round(c or 0, 2)}
                 for s, p, a, c in conn.execute(
                     "SELECT status_id, SUM(pings), SUM(applications), SUM(cost)"
                     " FROM lender_result_day WHERE business_date=? GROUP BY status_id", (d,))}
    sold = by_status.get("1", {})
    ping_log = {
        "meta": _row(conn, "SELECT pings, leads, applications FROM lender_result_day_meta"
                           " WHERE business_date=?", (d,)),
        "by_status": by_status,
        "reached": sum(by_status.get(s, {}).get("pings", 0) for s in ("1", "2", "3")),
        "sold": sold.get("pings", 0),
        "sold_value": sold.get("cost", 0),
    }
    quality = _row(conn, "SELECT rows FROM quality_day_meta WHERE business_date=?", (d,))
    collection = {k: {"status": s, "detail": (det or "")[:200]} for k, s, det in conn.execute(
        "SELECT kind, status, detail FROM collection WHERE business_date=?", (d,))}

    shown = None
    try:
        p = dash.period_payload(conn, d, d, "all", "day")
        if p.get("collectedDays"):
            t = p["total"]["day"]
            shown = {k: round(sum(t[k]), 2) for k in ("leads", "soldLeads", "sold", "value",
                                                     "reached", "offered")}
            shown["profit"] = p.get("profit")
    except Exception as exc:                         # noqa: BLE001
        shown = {"error": f"{type(exc).__name__}: {exc}"}
    return {"date": d, "lender_report": {"totals_block": lender_totals, "tiers_summed": lender_tiers},
            "affiliate_reports": affiliates, "ping_log": ping_log,
            "lead_quality": {"rows": quality["rows"] if quality else None},
            "collection": collection, "day_page": shown}


def lenders(conn, mapping):
    tiers = conn.execute("SELECT lender_tier_id, lender_tier_name, lender_id, lender_name, price,"
                         " is_active FROM lender_tier ORDER BY lender_name, lender_tier_name"
                         ).fetchall()
    return agentstore.resolved(tiers, mapping)


def movers(conn, mapping, days=28):
    """What moved among the buyers, for the briefing to explain."""
    lp = dash.lender_payload(conn, days)
    profile = agentstore.lender_profile(lenders(conn, mapping))
    out_lenders = []
    for l in lp.get("lenders", []):
        m = {k: {f: l["measures"][k].get(f) for f in ("current", "baseline", "change", "flag",
                                                      "enough")}
             for k in l.get("measures", {})}
        prof = profile.get(l["id"], {})
        out_lenders.append({
            "id": l["id"], "name": l["name"], "brand": prof.get("brand"),
            "company": prof.get("company"), "type": prof.get("type"),
            "processed": l.get("processed"), "sent": l.get("sent"),
            "accepted": l.get("accepted"), "redirect": l.get("redirect"),
            "revenue": l.get("revenue"), "measures": m,
            "tiers": [{"id": t["id"], "name": t["name"], "price": t.get("price"),
                       "processed": t.get("processed"), "sent": t.get("sent"),
                       "accepted": t.get("accepted"), "revenue": t.get("revenue")}
                      for t in l.get("tiers", [])]})
    pairs = []
    for lid, rows in (lp.get("affiliatesByLender") or {}).items():
        for r in rows:
            a = r.get("acceptance", {})
            if a.get("flag") or (a.get("change") is not None and abs(a["change"]) >= 0.25
                                 and a.get("enough")):
                pairs.append({"lender_id": int(lid), "affiliate": r["name"],
                              "internal": r.get("internal"), "passed": r["passed"],
                              "sold": r["sold"], "value": r["value"],
                              "acceptance_now": a.get("current"),
                              "acceptance_before": a.get("baseline"),
                              "change": a.get("change"), "flag": a.get("flag")})
    shifts = []
    try:
        cp = dash.customer_payload(conn, days)
        names = {str(l["key"]): l["name"] for l in cp.get("lenders", [])}
        labels = {a["key"]: a["label"] for a in cp.get("attrs", [])}
        for key, metrics in (cp.get("trends") or {}).items():
            if key not in names:
                continue                             # tiers are in the payload too
            for metric, bands in metrics.items():
                for band, t in bands.items():
                    if t.get("shift"):
                        shifts.append({"lender": names[key], "measure": labels.get(metric, metric),
                                       "band": band, "share_now": t.get("current"),
                                       "share_before": t.get("baseline"),
                                       "change": t.get("change"), "direction": t["shift"]})
    except Exception as exc:                         # noqa: BLE001
        shifts = [{"error": f"{type(exc).__name__}: {exc}"}]
    days_list = lp.get("days", [])[-14:]
    return {"window_days": days, "trend_window": lp.get("trendWindow"),
            "daily_totals_last_14": days_list, "lenders": out_lenders,
            "affiliate_acceptance_moves": sorted(pairs, key=lambda p: -(p["value"] or 0)),
            "customer_shifts": shifts,
            "tier_events": tier_events(conn),
            "lender_weeks": lender_weeks(conn),
            "source_days": source_days(conn, days),
            "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


# ---- history: what the first briefing had to dig for by hand ----------

QUIET_DAYS = 5          # a tier offered nothing for this long has gone quiet
NEW_DAYS = 14           # a tier first offered within this long is new
WEEKS = 4


def _dates(days):
    end = store.business_today() - dt.timedelta(days=1)
    return [(end - dt.timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]


def _tier_rows(conn, start, end):
    """{tier_id: {date: (processed, sent, accepted, revenue)}}, plus the
    tier's name, lender id and lender name; excluded lenders left out."""
    meta = {r[0]: (r[1], r[2], (r[3] or "").strip())
            for r in conn.execute("SELECT lender_tier_id, lender_tier_name, lender_id, lender_name"
                                  " FROM lender_tier")}
    rows = {}
    for d, tid, tname, p, s, a, rev in conn.execute(
            "SELECT business_date, lender_tier_id, lender_tier_name, processed, sent, accepted,"
            " revenue FROM lender_tier_day WHERE business_date >= ? AND business_date <= ?",
            (start, end)):
        name, lid, lname = meta.get(tid, (tname, None, None))
        if lid in store.EXCLUDED_LENDER_IDS:
            continue
        t = rows.setdefault(tid, {"id": tid, "name": name or tname, "lender_id": lid,
                                  "lender": lname, "days": {}})
        t["days"][d] = (p or 0, s or 0, a or 0, round(rev or 0, 2))
    return rows


def tier_days(conn, days=28, lender_id=None, tier_ids=None):
    """Per tier, one figure a day: offered (processed), passed (sent),
    bought (accepted) and revenue - what shows a tier starting, stopping
    or changing on a given day, which 28-day totals hide."""
    dates = _dates(days)
    rows = _tier_rows(conn, dates[0], dates[-1])
    out = []
    for t in rows.values():
        if lender_id is not None and t["lender_id"] != lender_id:
            continue
        if tier_ids is not None and t["id"] not in tier_ids:
            continue
        series = [t["days"].get(d, (0, 0, 0, 0)) for d in dates]
        if not any(any(v) for v in series):
            continue
        out.append({"id": t["id"], "name": t["name"], "lender_id": t["lender_id"],
                    "lender": t["lender"],
                    "offered": [v[0] for v in series], "passed": [v[1] for v in series],
                    "bought": [v[2] for v in series], "revenue": [v[3] for v in series]})
    return {"dates": dates, "tiers": sorted(out, key=lambda t: (t["lender"] or "", t["name"] or ""))}


def tier_events(conn, days=35):
    """Tiers that started, went quiet, or stopped buying in the window -
    each one a likely cause of a lender-level move (a new tier dilutes
    its lender's filter pass; a quiet one takes its sales to zero)."""
    dates = _dates(days)
    rows = _tier_rows(conn, dates[0], dates[-1])
    events = []
    for t in rows.values():
        offered = [t["days"].get(d, (0,))[0] for d in dates]
        bought = [t["days"].get(d, (0, 0, 0))[2] for d in dates]
        revenue = [t["days"].get(d, (0, 0, 0, 0))[3] for d in dates]
        active = [i for i, v in enumerate(offered) if v]
        if not active:
            continue
        first, last = active[0], active[-1]
        base = dict(tier_id=t["id"], tier=t["name"], lender_id=t["lender_id"], lender=t["lender"])
        if first >= len(dates) - NEW_DAYS:
            n = len(dates) - first
            events.append({**base, "event": "new tier", "since": dates[first],
                           "offered_per_day": round(sum(offered[first:]) / n),
                           "bought_per_day": round(sum(bought[first:]) / n, 1)})
        elif last <= len(dates) - 1 - QUIET_DAYS:
            before = offered[max(0, last - 13):last + 1]
            events.append({**base, "event": "offered nothing", "since": dates[last + 1],
                           "offered_per_day_before": round(sum(before) / len(before)),
                           "revenue_per_day_before": round(sum(revenue[max(0, last - 13):last + 1])
                                                           / len(before), 2)})
        else:
            earlier = bought[:-7]
            if sum(earlier) >= 14 and sum(bought[-7:]) == 0 and sum(offered[-7:]):
                events.append({**base, "event": "stopped buying", "since": dates[-7],
                               "bought_per_day_before": round(sum(earlier) / len(earlier), 1),
                               "offered_last_7": sum(offered[-7:])})
    return sorted(events, key=lambda e: e["since"], reverse=True)


def lender_weeks(conn, weeks=WEEKS):
    """Each lender's fixed weeks ending yesterday (newest last): offered,
    passed, bought, revenue - every lender, including those at $0, so a
    lender falling to nothing is as visible as one that dipped."""
    dates = _dates(7 * weeks)
    rows = _tier_rows(conn, dates[0], dates[-1])
    by = {}
    for t in rows.values():
        lid = t["lender_id"]
        l = by.setdefault(lid, {"lender_id": lid, "lender": t["lender"],
                                "weeks": [[0, 0, 0, 0.0] for _ in range(weeks)]})
        for i, d in enumerate(dates):
            v = t["days"].get(d)
            if v:
                w = l["weeks"][i // 7]
                for k in range(4):
                    w[k] += v[k]
    out = []
    for l in by.values():
        ws = [{"offered": w[0], "passed": w[1], "bought": w[2], "revenue": round(w[3], 2)}
              for w in l["weeks"]]
        prior = [w["revenue"] for w in ws[:-1]]
        avg = sum(prior) / len(prior) if prior else 0
        l["weeks"] = ws
        l["revenue_change_vs_prior_avg"] = (round(ws[-1]["revenue"] / avg - 1, 3) if avg else None)
        l["revenue_gap_vs_prior_avg"] = round(ws[-1]["revenue"] - avg, 2)
        out.append(l)
    starts = [dates[i * 7] for i in range(weeks)]
    return {"week_starts": starts, "lenders": sorted(out, key=lambda l: l["revenue_gap_vs_prior_avg"])}


def source_days(conn, days=28):
    """FF (internal) and external, each from its own affiliate report, a
    day at a time: leads received, sold, revenue. Never added together."""
    dates = _dates(days)
    got = {(d, s): (r, so, rev) for d, s, r, so, rev in conn.execute(
        "SELECT business_date, source, received, sold, revenue FROM affiliate_totals_day"
        " WHERE business_date >= ? AND business_date <= ?", (dates[0], dates[-1]))}
    out = {"dates": dates}
    for source in ("external", "internal"):
        vals = [got.get((d, source)) for d in dates]
        out[source] = {"received": [v[0] if v else None for v in vals],
                       "sold": [v[1] if v else None for v in vals],
                       "revenue": [round(v[2], 2) if v and v[2] is not None else None for v in vals]}
    return out
