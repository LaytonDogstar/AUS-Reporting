"""The buyer agent's raw material: what moved among the lenders.

Prints one compact JSON digest for the agent to explain:

  * yesterday's totals against the previous 13 days
  * every lender with a flagged drop (filter pass, acceptance or
    redirect), and the biggest revenue movers, with their tiers and the
    lender mapping's brand, company and type
  * affiliate-by-lender acceptance moves (which sources a lender is
    turning away)
  * customer-mix shifts beyond chance (which customers a lender is
    buying more or less of)
  * tiers that started, went quiet or stopped buying in the last five
    weeks (a new tier also dilutes its lender's filter pass, so check
    here before reading a filter-pass flag as a fall)
  * every lender's last four fixed weeks, biggest revenue falls first,
    so a lender going to $0 shows even though its rates cannot
  * FF and external a day at a time, each from its own report
  * per-tier daily figures for the flagged lenders and event tiers
  * with --filters, each flagged lender's biggest tiers' failed-filter
    counts from the portal: last week, the week before, and four weeks
    back - a change older than a fortnight shows only against the last

    python tools/agents/buyer_data.py --filters > /tmp/digest.json

Needs AGENT_TOKEN; --filters also needs the portal login. Counts only.
"""

from __future__ import annotations

import argparse
import json
from datetime import timedelta

from common import get_json

import store                                               # noqa: E402

TOP_MOVERS = 8


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--filters", action="store_true", help="add portal failed-filter counts")
    args = ap.parse_args()
    movers = get_json(f"/api/agent/movers?days={args.days}")
    yesterday = (store.business_today() - timedelta(days=1)).isoformat()
    status = get_json("/api/agent/status")

    flagged, others = [], []
    for l in movers["lenders"]:
        m = l["measures"]
        entry = {k: l[k] for k in ("id", "name", "brand", "company", "type", "revenue",
                                   "processed", "sent", "accepted")}
        entry["measures"] = m
        entry["tiers"] = sorted(l["tiers"], key=lambda t: -(t.get("revenue") or 0))[:4]
        (flagged if any(v.get("flag") for v in m.values()) else others).append(entry)
    big = sorted(others, key=lambda e: -abs(((e["measures"].get("acceptance") or {}).get("change") or 0)
                                           * (e["revenue"] or 0)))[:TOP_MOVERS]

    weeks = movers.get("lender_weeks") or {"lenders": []}
    falls = [l for l in weeks["lenders"] if l["revenue_gap_vs_prior_avg"] < 0][:10]
    rises = sorted((l for l in weeks["lenders"] if l["revenue_gap_vs_prior_avg"] > 0),
                   key=lambda l: -l["revenue_gap_vs_prior_avg"])[:5]
    events = movers.get("tier_events") or []
    flagged_ids = {e["id"] for e in flagged}
    tier_ids = {t["id"] for e in flagged for t in e["tiers"]} | {e["tier_id"] for e in events}
    tier_daily = get_json("/api/agent/tiers?days=35&ids=" + ",".join(map(str, sorted(tier_ids)))) \
        if tier_ids else {}

    if args.filters and flagged:
        from portal import Portal
        today = store.business_today()
        windows = {"last_7_days": (today - timedelta(days=7), today - timedelta(days=1)),
                   "the_7_days_before": (today - timedelta(days=14), today - timedelta(days=8)),
                   "four_weeks_back": (today - timedelta(days=35), today - timedelta(days=29))}
        try:
            portal = Portal()
            for e in flagged:
                for t in e["tiers"][:2]:
                    t["failed_filters"] = {}
                    for label, (a, b) in windows.items():
                        try:
                            t["failed_filters"][label] = portal.failed_filters(
                                t["id"], a.isoformat(), b.isoformat())
                        except Exception as exc:     # noqa: BLE001
                            t["failed_filters"][label] = {"error": f"{type(exc).__name__}: {exc}"}
        except Exception as exc:                     # noqa: BLE001
            flagged.append({"portal_error": f"{type(exc).__name__}: {exc}"})

    digest = {
        "yesterday": yesterday,
        "collection": {"expected_latest": status["expected_latest"],
                       "latest_lender_report": status["kinds"].get("tiers", {}).get("latest_ok"),
                       "missing_days": status["missing_days_last_30"]},
        "daily_totals_last_14": movers["daily_totals_last_14"],
        "flagged_lenders": flagged,
        "other_big_movers": big,
        "affiliate_acceptance_moves": movers["affiliate_acceptance_moves"][:20],
        "customer_shifts": movers["customer_shifts"][:60],
        "tier_events": events,
        "lender_weeks": {"week_starts": weeks.get("week_starts"), "biggest_falls": falls,
                         "biggest_rises": rises,
                         "flagged": [l for l in weeks["lenders"] if l["lender_id"] in flagged_ids]},
        "source_days": movers.get("source_days"),
        "tier_daily": tier_daily,
        "notes": ["Rates are rolling 7-day; 'change' is the latest week against the weeks before.",
                  "A flag means a fall of at least 10% that is beyond chance (15% for money).",
                  "Failed-filter counts overlap: one lead fails several filters; use the week windows.",
                  "A new tier (tier_events) lowers its lender's filter pass without anything getting worse.",
                  "lender_weeks are fixed 7-day weeks ending yesterday, newest last; same-weekday "
                  "comparisons come from daily_totals_last_14 and source_days.",
                  "Tippla's revenue is $0 by design; Dummy and DCS are excluded."],
    }
    print(json.dumps(digest, indent=1, default=str))


if __name__ == "__main__":
    main()
