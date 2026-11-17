"""The testing agent's nightly check of the live site.

Two passes over every dashboard page, signed in with the agent token:

1. **Served.** Each page answers 200, in reasonable time, with its data
   built in - not the empty placeholder, not an error page.
2. **Works in a browser.** Each page is opened in headless Chromium:
   any script error fails it; the first sparkline is clicked and its
   pop-up must open, as the dashboard rule requires of every one.

Then collection freshness: yesterday should be in.

    python tools/agents/site_check.py            # print the report
    python tools/agents/site_check.py --post     # and post it to Agent reports
    python tools/agents/site_check.py --no-browser

Needs AGENT_TOKEN. The browser pass runs browser_check.js with Node's
Playwright (installed in the cloud sessions; Chromium at /opt/pw-browsers).
Reads only.
"""

from __future__ import annotations

import argparse
import os
import re

from common import BASE, TOKEN, get_json, post_report, request

PAGES = ["/today", "/yesterday", "/day", "/days", "/", "/lenders", "/customers", "/briefing",
         "/scorecard", "/quality", "/collection"]
# Pages built from a data payload, and so carrying one when they work.
DATA_PAGES = {"/today", "/yesterday", "/day", "/days", "/", "/lenders", "/customers"}
SLOW_SECONDS = 20


def served(findings):
    for path in PAGES:
        try:
            status, body, secs = request("GET", path)
        except RuntimeError as exc:
            findings.append(("fail", path, f"no answer: {exc}"))
            continue
        text = body.decode("utf-8", "replace")
        if status != 200:
            findings.append(("fail", path, f"HTTP {status}"))
            continue
        if "Traceback (most recent call last)" in text or "<h1>Error</h1>" in text:
            findings.append(("fail", path, "the page shows a server error"))
            continue
        if path in DATA_PAGES and ("/*__DASHBOARD_DATA__*/" in text or "const DATA = null" in text):
            if "still being" in text or "Nothing collected" in text:
                findings.append(("warn", path, "served, but says nothing is collected yet"))
            else:
                findings.append(("fail", path, "served without its data"))
            continue
        sev = "warn" if secs > SLOW_SECONDS else "ok"
        findings.append((sev, path, f"200 in {secs:.1f}s, {len(body) // 1024} KB"
                                    + (" - slow" if sev == "warn" else "")))


def in_browser(findings):
    """Each data page in headless Chromium, via browser_check.js (Node's
    Playwright, which the cloud sessions have)."""
    import json
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        findings.append(("warn", "browser", "Node is not installed here: browser pass skipped"))
        return
    env = dict(os.environ, BASE=BASE, AGENT_TOKEN=TOKEN, PAGES=",".join(sorted(DATA_PAGES)))
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_check.js")
    try:
        out = subprocess.run([node, script], env=env, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        findings.append(("fail", "browser", "the browser pass took over 15 minutes"))
        return
    rows = []
    for line in out.stdout.splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    if not rows:
        err = (out.stderr or "").strip().splitlines()[-1:] or ["no output"]
        sev = "warn" if "Cannot find module 'playwright'" in (out.stderr or "") else "fail"
        findings.append((sev, "browser", f"browser pass did not run: {err[0][:200]}"))
        return
    for r in rows:
        findings.append(("ok" if r.get("ok") else "fail", f"{r.get('path')} (browser)", r.get("note", "")))


def freshness(findings):
    st = get_json("/api/agent/status")
    latest = (st["kinds"].get("tiers") or {}).get("latest_ok")
    if latest != st["expected_latest"]:
        findings.append(("warn", "data", f"latest collected day is {latest}, expected {st['expected_latest']}"))
    else:
        findings.append(("ok", "data", f"yesterday ({latest}) is collected"))
    if st["missing_days_last_30"]:
        findings.append(("warn", "data", f"{len(st['missing_days_last_30'])} day(s) in the last 30 "
                                         "not fully collected"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--post", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    findings = []
    status, body, _ = request("GET", "/healthz")
    findings.append(("ok" if status == 200 else "fail", "/healthz", f"HTTP {status}"))
    served(findings)
    if not args.no_browser:
        in_browser(findings)
    freshness(findings)
    worst = "fail" if any(f[0] == "fail" for f in findings) else \
        "warn" if any(f[0] == "warn" for f in findings) else "ok"
    bad = [f for f in findings if f[0] != "ok"]
    summary = (f"All {len(findings)} checks passed on {re.sub('https?://', '', BASE)}." if not bad
               else f"{len(bad)} of {len(findings)} checks need a look: "
               + "; ".join(f"{w} {m}" for _s, w, m in bad[:3]) + ("…" if len(bad) > 3 else ""))
    lines = []
    if bad:
        lines += ["NEEDS A LOOK"] + [f"- [{s.upper()}] {w}: {m}" for s, w, m in bad] + [""]
    lines += ["EVERYTHING CHECKED"] + [f"- [{s.upper()}] {w}: {m}" for s, w, m in findings]
    text = "\n".join(lines)
    print(f"STATUS: {worst.upper()}\n{summary}\n\n{text}")
    if args.post:
        print("\nposted:", post_report("tester", "check", worst, "Nightly site check", summary, text))


if __name__ == "__main__":
    main()
