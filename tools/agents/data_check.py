"""The data agent's check: are the dashboard's numbers right?

For each recent finished day, three comparisons:

1. **Stored against the platform now.** The lender report, both
   affiliate reports and the ping log are fetched fresh and set against
   what the dashboard stored when it collected the day. A gap means the
   platform restated the day after collection, or collection went wrong.
2. **Report against report.** Lender revenue against the two affiliate
   reports' revenue added (money adds; lead counts never do), and the
   lender report's `sent` against the ping log's reached. These tied to
   the cent and to within 0.3% when the dashboard was built.
3. **Page against store.** What the Day page shows - leads sold and
   their value, from the hourly tables - against the ping log stored
   for the day, with the excluded lenders left out of both.

Plus collection health: missing days, failed reports, a stale latest day.

    python tools/agents/data_check.py                 # yesterday and the day before
    python tools/agents/data_check.py --date 2026-11-15 --days 1
    python tools/agents/data_check.py --post          # post to Agent reports

Needs AGENT_TOKEN, and OVERFLOW_API_USER / OVERFLOW_API_PASSWORD for the
platform's reports. Reads only; prints counts and money, never a lead.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

from common import get_json, pct_gap, post_report

import collector                                           # noqa: E402  (service/, via common)
import store                                               # noqa: E402

RESTATE_WARN, RESTATE_FAIL = 0.005, 0.03     # stored vs platform now
CROSS_WARN, CROSS_FAIL = 0.01, 0.05          # report vs report, page vs store


def platform_day(day):
    """The platform's own figures for a day, fetched now."""
    out = {}
    for kind, method, url, body, _handler in collector._requests(day):
        if kind not in ("tiers", "affiliates", "internal_affiliates", "lender_results"):
            continue
        status, payload, _ms, error = collector.fetch(url, method, body)
        if error or status != 200:
            out[kind] = {"error": error or f"HTTP {status}"}
            continue
        data = json.loads(payload.decode("utf-8", "replace"))
        if kind == "tiers":
            _rows, totals = collector.reduce_tiers(data)
            out[kind] = totals or {}
        elif kind in ("affiliates", "internal_affiliates"):
            _rows, totals = collector.reduce_affiliates(data)
            out[kind] = totals or {}
        else:
            counts, meta = collector.reduce_lender_results(data)
            del data
            excluded = set(store.EXCLUDED_LENDER_IDS)
            def tally(keep):
                sold = sum(p for (l, _t, s), (p, _a, _c) in counts.items() if s == 1 and keep(l))
                value = sum(c for (l, _t, s), (_p, _a, c) in counts.items() if s == 1 and keep(l))
                reached = sum(p for (l, _t, s), (p, _a, _c) in counts.items()
                              if s in (1, 2, 3) and keep(l))
                return sold, round(value, 2), reached
            sold, value, reached = tally(lambda l: True)
            sold_x, value_x, reached_x = tally(lambda l: l not in excluded)
            out[kind] = {"pings": meta["pings"], "applications": meta["applications"],
                         "sold": sold, "sold_value": value, "reached": reached,
                         "sold_kept": sold_x, "sold_value_kept": value_x, "reached_kept": reached_x}
    return out


def check_day(iso, findings):
    d = date.fromisoformat(iso)
    mine = get_json(f"/api/agent/day?date={iso}")
    theirs = platform_day(d)

    def compare(area, label, stored, fresh, warn, fail, unit=""):
        gap = pct_gap(stored, fresh)
        if gap is None:
            findings.append(("warn", iso, area, f"{label}: no figure to compare "
                                                f"(stored {stored!r}, platform {fresh!r})"))
            return
        sev = "fail" if gap > fail else "warn" if gap > warn else "ok"
        fmt = (lambda v: f"${v:,.2f}") if unit == "$" else (lambda v: f"{v:,.0f}")
        findings.append((sev, iso, area, f"{label}: dashboard {fmt(stored)} vs {fmt(fresh)} "
                                         f"({gap:.2%} apart)"))

    # 1. stored against the platform now
    lt = (mine["lender_report"] or {}).get("totals_block") or {}
    pt = theirs.get("tiers", {})
    if "error" in pt:
        findings.append(("warn", iso, "platform", f"lender report not fetched: {pt['error']}"))
    elif lt:
        for f, unit in (("sent", ""), ("accepted", ""), ("revenue", "$")):
            compare("restated?", f"lender report {f}", lt.get(f), pt.get(f), RESTATE_WARN, RESTATE_FAIL, unit)
    else:
        findings.append(("fail", iso, "collection", "lender report not stored for this day"))
    for source, kind in (("external", "affiliates"), ("internal", "internal_affiliates")):
        st = ((mine["affiliate_reports"] or {}).get(source) or {}).get("totals") or {}
        pa = theirs.get(kind, {})
        if "error" in pa:
            findings.append(("warn", iso, "platform", f"{source} affiliate report not fetched: {pa['error']}"))
        elif st:
            for f, unit in (("received", ""), ("sold", ""), ("revenue", "$")):
                compare("restated?", f"{source} affiliates {f}", st.get(f), pa.get(f),
                        RESTATE_WARN, RESTATE_FAIL, unit)
        else:
            findings.append(("fail", iso, "collection", f"{source} affiliate report not stored"))
    pl = mine["ping_log"]
    pp = theirs.get("lender_results", {})
    if "error" in pp:
        findings.append(("warn", iso, "platform", f"ping log not fetched: {pp['error']}"))
    elif pl.get("meta"):
        compare("restated?", "ping log pings", pl["meta"]["pings"], pp["pings"], RESTATE_WARN, RESTATE_FAIL)
        compare("restated?", "ping log sold", pl["sold"], pp["sold"], RESTATE_WARN, RESTATE_FAIL)
    else:
        findings.append(("fail", iso, "collection", "ping log not stored for this day"))

    # 2. report against report
    ext = ((mine["affiliate_reports"] or {}).get("external") or {}).get("totals") or {}
    inn = ((mine["affiliate_reports"] or {}).get("internal") or {}).get("totals") or {}
    if lt and ext and inn:
        compare("reports agree?", "lender revenue vs both affiliate reports' revenue",
                lt.get("revenue"), (ext.get("revenue") or 0) + (inn.get("revenue") or 0),
                CROSS_WARN, CROSS_FAIL, "$")
    if lt and pl.get("meta"):
        compare("reports agree?", "lender report sent vs ping log reached",
                lt.get("sent"), pl.get("reached"), CROSS_WARN, CROSS_FAIL)

    # 3. page against store
    shown = mine.get("day_page")
    if isinstance(shown, dict) and "error" in shown:
        findings.append(("fail", iso, "page", f"Day page did not build: {shown['error']}"))
    elif shown and "error" not in pp and pp:
        compare("page right?", "Day page sold vs ping log sold (excluded lenders out)",
                shown.get("sold"), pp.get("sold_kept"), CROSS_WARN, CROSS_FAIL)
        compare("page right?", "Day page value vs ping log sold value (excluded lenders out)",
                shown.get("value"), pp.get("sold_value_kept"), CROSS_WARN, CROSS_FAIL, "$")
    elif not shown:
        findings.append(("warn", iso, "page", "Day page has no figures for this day"))


def check_status(findings):
    st = get_json("/api/agent/status")
    if st["missing_days_last_30"]:
        n = len(st["missing_days_last_30"])
        findings.append(("warn" if n <= 2 else "fail", "-", "collection",
                         f"{n} day(s) in the last 30 not fully collected: "
                         + ", ".join(st["missing_days_last_30"][:10])))
    for kind, k in st["kinds"].items():
        if k["latest_ok"] is None or k["latest_ok"] < st["expected_latest"]:
            findings.append(("warn", "-", "collection",
                             f"{kind}: latest good day {k['latest_ok']}, expected {st['expected_latest']}"))
        for f in k["failed_last_7"]:
            findings.append(("warn", f["date"], "collection", f"{kind} failed: {f['detail']}"))
    if not st.get("on_volume"):
        findings.append(("fail", "-", "storage", "database is not on the /data volume: a deploy "
                                                 "would wipe the data and the logins"))
    return st


def render(findings, days):
    worst = "fail" if any(f[0] == "fail" for f in findings) else \
        "warn" if any(f[0] == "warn" for f in findings) else "ok"
    bad = [f for f in findings if f[0] != "ok"]
    lines = [f"Days checked: {', '.join(days)}", ""]
    if bad:
        lines.append("NEEDS A LOOK")
        lines += [f"- [{s.upper()}] {d} · {a}: {m}" for s, d, a, m in bad]
        lines.append("")
    lines.append("EVERYTHING CHECKED")
    lines += [f"- [{s.upper()}] {d} · {a}: {m}" for s, d, a, m in findings]
    summary = (f"All {len(findings)} checks within tolerance." if not bad else
               f"{len(bad)} of {len(findings)} checks need a look: "
               + "; ".join(m for _s, _d, _a, m in bad[:3]) + ("…" if len(bad) > 3 else ""))
    return worst, summary, "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--date", help="last day to check (default: yesterday, Sydney)")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--post", action="store_true", help="post the report to the dashboard")
    args = ap.parse_args()
    if not (collector.API_USER and collector.API_PASSWORD):
        sys.exit("OVERFLOW_API_USER / OVERFLOW_API_PASSWORD are not set in this environment.")
    last = date.fromisoformat(args.date) if args.date else store.business_today() - timedelta(days=1)
    days = [(last - timedelta(days=i)).isoformat() for i in range(max(1, args.days))]
    findings = []
    check_status(findings)
    for iso in days:
        try:
            check_day(iso, findings)
        except Exception as exc:                     # noqa: BLE001
            findings.append(("fail", iso, "check", f"could not check: {type(exc).__name__}: {exc}"))
    status, summary, body = render(findings, days)
    print(f"STATUS: {status.upper()}\n{summary}\n\n{body}")
    if args.post:
        url = post_report("data", "check", status, f"Data check · {days[0]}", summary, body)
        print(f"\nposted: {url}")


if __name__ == "__main__":
    main()
