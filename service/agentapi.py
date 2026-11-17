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
            "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
