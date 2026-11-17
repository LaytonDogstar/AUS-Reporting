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
  * with --filters, each flagged lender's biggest tiers' failed-filter
    counts from the portal, yesterday against the week before - the
    usual answer to "why did it stop buying"

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

    if args.filters and flagged:
        from portal import Portal
        try:
            portal = Portal()
            week_start = (store.business_today() - timedelta(days=8)).isoformat()
            week_end = (store.business_today() - timedelta(days=2)).isoformat()
            for e in flagged:
                for t in e["tiers"][:2]:
                    t["failed_filters_yesterday"] = portal.failed_filters(t["id"], yesterday, yesterday)
                    t["failed_filters_week_before"] = portal.failed_filters(t["id"], week_start, week_end)
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
        "customer_shifts": movers["customer_shifts"][:25],
        "notes": ["Rates are rolling 7-day; 'change' is the latest week against the weeks before.",
                  "A flag means a fall of at least 10% that is beyond chance (15% for money).",
                  "Failed-filter counts overlap: one lead fails several filters.",
                  "Tippla's revenue is $0 by design; Dummy and DCS are excluded."],
    }
    print(json.dumps(digest, indent=1, default=str))


if __name__ == "__main__":
    main()
