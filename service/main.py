"""Overflow reporting service: web layer.

Standard library only. Fetching lives in collector.py, storage in
store.py. This file serves pages and triggers collection.
"""

from __future__ import annotations

import base64
import hmac
import html
import json
import os
import re
import threading
import traceback
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import collector
import store

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
PORT = int(os.environ.get("PORT", "8080"))
DB_PATH = os.environ.get("DB_PATH") or ("/data/overflow.db" if os.path.isdir("/data") else "overflow.db")

GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
DATEISH = re.compile(r"^\d{4}-\d{2}-\d{2}")

_busy = threading.Lock()


# ---------------------------------------------------------------- shaping

def shape(node, path="$", depth=0, out=None):
    if out is None:
        out = []
    if len(out) > 4000 or depth > 8:
        return out
    if node is None:
        out.append((path, "null", ""))
    elif isinstance(node, bool):
        out.append((path, "bool", str(node)))
    elif isinstance(node, (int, float)):
        out.append((path, "number", f"{node:,}" if isinstance(node, int) else str(node)))
    elif isinstance(node, str):
        kind = "guid" if GUID.match(node) else "date-like" if DATEISH.match(node) else "string"
        out.append((path, kind, f"len {len(node)}"))
    elif isinstance(node, list):
        out.append((path, "array", f"{len(node):,} item(s)"))
        if node:
            shape(node[0], f"{path}[0]", depth + 1, out)
    elif isinstance(node, dict):
        if not node:
            out.append((path, "object", "empty"))
        for key, value in node.items():
            shape(value, f"{path}.{key}", depth + 1, out)
    return out


CSS = """
:root{--bg:#fbfbfa;--fg:#1a1a1a;--muted:#5b6169;--line:#e3e5e8;--card:#fff;--accent:#1f4e79;
--ok:#1a7f4b;--warn:#9a6700;--bad:#b42318}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#15171a;--fg:#e9eaec;
--muted:#9aa1ab;--line:#2b2f35;--card:#1c1f23;--accent:#7fb2e5;--ok:#4ac98a;--warn:#e3b341;--bad:#ff7b72}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,BlinkMacSystemFont,
"Segoe UI",Roboto,sans-serif;padding:0 16px}
.wrap{max-width:1000px;margin:0 auto;padding:30px 0 64px}
h1{font-size:23px;margin:0 0 4px}h2{font-size:16px;margin:26px 0 9px;color:var(--accent)}
.sub{color:var(--muted);margin:0 0 22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:15px;margin:11px 0}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;font-weight:600;padding:7px 8px;border-bottom:1px solid var(--line);color:var(--muted)}
td{padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
a.btn{display:inline-block;background:var(--accent);color:#fff;text-decoration:none;padding:8px 15px;
border-radius:7px;font-weight:600;font-size:13.5px;margin-right:8px}
a.btn.quiet{background:transparent;color:var(--accent);border:1px solid var(--line)}
.note{color:var(--muted);font-size:13px}
.bar{height:7px;background:var(--accent);border-radius:4px;display:inline-block;min-width:2px}
"""


def page(title, body):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>{CSS}</style></head><body><div class="wrap">{body}</div></body></html>""".encode()


def nav(active=""):
    items = [("/", "Overview"), ("/affiliates", "Affiliates"),
             ("/quality", "Lead quality"), ("/verify", "Window check"),
             ("/explore", "API explorer")]
    return ('<p class="note">' + " &middot; ".join(
        f"<strong>{t}</strong>" if h == active else f'<a href="{h}">{t}</a>'
        for h, t in items) + "</p>")


def money(v):
    return f"{v:,.2f}" if v is not None else "—"


# ----------------------------------------------------------------- pages

def overview():
    conn = store.connect(DB_PATH)
    series = store.totals_series(conn, 60)
    missing = collector.missing_days(conn)
    recent = conn.execute(
        "SELECT business_date, kind, status, detail, ms FROM collection"
        " ORDER BY business_date DESC, kind LIMIT 8").fetchall()
    league = store.tier_league(conn, 30)
    conn.close()

    p = ["<h1>Overflow reporting</h1>",
         '<p class="sub">Collected from the PublicReports API. Lead rows are reduced to '
         'counts per band before anything is written.</p>', nav("/")]

    status, ms, err = collector.anonymous_check()
    if err:
        v, c = f"could not reach the API ({err})", "warn"
    elif status == 200:
        v, c = "answered 200 WITHOUT credentials — raise this before anything else.", "bad"
    elif status in (401, 403):
        v, c = f"returned {status}; credentials are required.", "ok"
    else:
        v, c = f"returned {status}; inconclusive.", "warn"
    p.append(f'<div class="card"><span class="note">Anonymous call to '
             f'<code>/PublicReports/Affiliates</code>:</span> <span class="{c}">{html.escape(v)}</span> '
             f'<span class="note">({ms} ms)</span></div>')

    p.append('<h2>Collection</h2><div class="card">')
    if _busy.locked():
        p.append('<p class="warn">Collecting now — this page refreshes as days land.</p>'
                 '<meta http-equiv="refresh" content="20">')
    else:
        p.append('<a class="btn" href="/collect">Collect missing days</a>'
                 '<a class="btn quiet" href="/collect?days=90">Backfill 90 days</a>'
                 '<a class="btn quiet" href="/reset">Clear and start again</a>')
    p.append(f'<p class="note">{len(series)} day(s) stored &middot; '
             f'{len(missing)} missing in the last {collector.BACKFILL_DAYS} &middot; '
             f'the background collector wakes every {collector.WAKE_SECONDS // 60} min '
             f'and takes {collector.DAYS_PER_WAKE} day(s) at a time.</p>')
    if recent:
        p.append('<table><tr><th>Day</th><th>Kind</th><th>Status</th><th>Detail</th><th class="n">Time</th></tr>')
        for d, kind, st, detail, ms_ in recent:
            cls = "ok" if st == "ok" else "bad"
            p.append(f'<tr><td>{html.escape(d)}</td><td class="note">{html.escape(kind)}</td>'
                     f'<td class="{cls}">{html.escape(st)}</td><td class="note">{html.escape(detail or "")}</td>'
                     f'<td class="n">{(ms_ or 0):,} ms</td></tr>')
        p.append("</table>")
    p.append("</div>")

    if series:
        p.append('<h2>Daily totals</h2><div class="card"><table>'
                 '<tr><th>Day</th><th class="n">Sent</th><th class="n">Accepted</th>'
                 '<th class="n">Conv.</th><th class="n">Revenue</th><th class="n">Commission</th>'
                 '<th class="n">Profit</th></tr>')
        for d, sent, acc, cr, rev, com, prof, *_ in series:
            p.append(f'<tr><td>{html.escape(d)}</td><td class="n">{(sent or 0):,}</td>'
                     f'<td class="n">{(acc or 0):,}</td><td class="n">{(cr or 0):.2f}%</td>'
                     f'<td class="n">{money(rev)}</td><td class="n">{money(com)}</td>'
                     f'<td class="n">{money(prof)}</td></tr>')
        p.append('</table><p class="note">Conversion is accepted over <em>sent</em>, which is how '
                 'the API computes it — not accepted over applications received.</p></div>')
    else:
        p.append('<p class="note">No days collected yet. Press <em>Collect missing days</em>.</p>')

    if league:
        top = max((r[5] or 0) for r in league) or 1
        p.append('<h2>Lender tiers, last 30 days</h2><div class="card"><table>'
                 '<tr><th>Tier</th><th class="n">Sent</th><th class="n">Accepted</th>'
                 '<th class="n">Conv.</th><th class="n">Profit</th><th></th></tr>')
        for name, s, a, rev, com, prof in league:
            cr = (100 * a / s) if s else 0
            w = int(90 * (prof or 0) / top) if (prof or 0) > 0 else 0
            p.append(f'<tr><td>{html.escape(name or "—")}</td><td class="n">{s:,}</td>'
                     f'<td class="n">{a:,}</td><td class="n">{cr:.1f}%</td>'
                     f'<td class="n">{money(prof)}</td>'
                     f'<td><span class="bar" style="width:{w}px"></span></td></tr>')
        p.append("</table></div>")

    p.append('<h2>What this cannot do</h2><div class="card"><p class="note">'
             'The SQL database is firewalled to allow-listed addresses, so nothing hosted here '
             'can reach it. The funnel, campaign attribution, geography and ten of the twenty-two '
             'affordability columns still need a collector running inside the network.</p></div>')
    return page("Overflow reporting", "".join(p))


SOURCE_LABELS = (("external", "Affiliate report"),
                 ("internal", "Internal affiliate report"))


def affiliate_section(conn, days, source, label):
    """One report's funnel and league table.

    The two reports are rendered one after the other and never added up.
    FF and non-FF affiliates are different populations, and the dev team
    confirmed on 13 Oct 2026 that they can no longer be combined into a
    single affiliate lead count.
    """
    funnel = store.affiliate_funnel(conn, days, source)
    league = store.affiliate_league(conn, days, source)
    p = [f"<h2>{label}</h2>"]
    if not funnel or funnel[0] is None:
        p.append('<div class="card"><p class="note">Nothing collected yet.</p></div>')
        return "".join(p)

    received, tc, bs, sub_, sold, dec, redirect, dup, funded = funnel
    steps = [("Received", received), ("Accepted T&amp;Cs", tc), ("Statements extracted", bs),
             ("Submitted to lenders", sub_), ("Sold", sold)]
    widest = max(v or 0 for _, v in steps) or 1
    p.append('<div class="card"><table>'
             '<tr><th>Step</th><th class="n">Leads</th><th class="n">Of received</th><th></th></tr>')
    for step_label, v in steps:
        v = v or 0
        p.append(f'<tr><td>{step_label}</td><td class="n">{v:,}</td>'
                 f'<td class="n">{100 * v / received:.1f}%</td>'
                 f'<td><span class="bar" style="width:{int(200 * v / widest)}px"></span></td></tr>')
    p.append(f'<tr><td class="note">Declined</td><td class="n">{(dec or 0):,}</td>'
             f'<td class="n">{100 * (dec or 0) / received:.1f}%</td><td></td></tr>')
    p.append("</table>")
    p.append('<p class="note"><strong>Read this per affiliate, not in aggregate.</strong> '
             'Only some affiliates emit an Accepted T&amp;Cs step, so the overall figure '
             'mixes journeys and the step-to-step percentages are not a real sequence. '
             f'Funded is {funded or 0} — lenders rarely report funding back.</p></div>')

    if league:
        top = max((r[10] or 0) for r in league) or 1
        p.append('<div class="card"><table>'
                 '<tr><th>Affiliate</th><th class="n">Received</th><th class="n">Sold</th>'
                 '<th class="n">Rate</th><th class="n">Revenue</th><th class="n">BS cost</th>'
                 '<th class="n">Profit</th><th></th></tr>')
        for name, r, tc_, bs_, _sub, sold_, dec_, rev, com, bsc, prof in league:
            rate = (100 * sold_ / r) if r else 0
            w = int(90 * (prof or 0) / top) if (prof or 0) > 0 else 0
            p.append(f'<tr><td>{html.escape(name or "—")}</td><td class="n">{r:,}</td>'
                     f'<td class="n">{sold_:,}</td><td class="n">{rate:.1f}%</td>'
                     f'<td class="n">{money(rev)}</td><td class="n">{money(bsc)}</td>'
                     f'<td class="n">{money(prof)}</td>'
                     f'<td><span class="bar" style="width:{w}px"></span></td></tr>')
        p.append("</table></div>")
    return "".join(p)


def affiliates_page(days=30):
    conn = store.connect(DB_PATH)
    sections = [affiliate_section(conn, days, source, label)
                for source, label in SOURCE_LABELS]
    conn.close()

    p = ["<h1>Affiliates</h1>",
         f'<p class="sub">Last {days} days, Sydney dates. The affiliate report carries the '
         'funnel: received through to sold or declined.</p>', nav("/affiliates")]
    p.append('<div class="card"><p class="note"><strong>Two reports, never added together.'
             '</strong> FF and non-FF affiliates are separate populations and cannot be '
             'combined into a single affiliate lead count, so each report is shown on its '
             'own. Which report holds which population is not yet confirmed.</p></div>')
    p.extend(sections)

    p.append('<h2>Before trusting the money columns</h2><div class="card"><p class="note">'
             'This report and the lender report disagree on revenue for the same window — '
             '71,237 against 122,813 — while agreeing on commission to within 1%. Profit '
             'therefore differs more than fourfold. Part of the answer is that the affiliate '
             'side is two reports rather than one; whether the lender report spans both '
             'populations is still open with the dev team. Until that is settled, treat '
             'revenue and profit here as unverified.</p></div>')
    return page("Affiliates", "".join(p))


# What the dev team said these fields actually are, 13 Oct 2026. Each one
# reads as something it is not, and the error is invisible in the numbers:
# a share here is of the leads where the field was captured at all, which
# for two of them is a subset of the funnel rather than all of it.
METRIC_TITLES = {
    "CenterlinkMonthly": "Wages and Centrelink, monthly",
}

METRIC_NOTES = {
    "CenterlinkMonthly":
        "<strong>Despite the name, this is not Centrelink income.</strong> It holds "
        "wages plus Centrelink — the dev team confirmed the field is misnamed and "
        "should read <code>WageAndCentrlinkMonthly</code>. Read the bands as total "
        "monthly income, and do not set them against "
        "<code>CentrelinkAsPercentOfIncome</code>.",
    "HasPrimaryIncomeSource":
        "<strong>Not a population rate.</strong> The field is only ever populated for "
        "leads whose bank statements were downloaded, so <em>false</em> means not "
        "determined rather than no income.",
    "IsAustralianResident":
        "<strong>Not a population rate.</strong> Only captured for iframe leads, so the "
        "unknowns are leads that arrived another way, not questions left unanswered.",
    "Age":
        "Age as at application time, which is the figure to use — date of birth holds "
        "values from 1753 to 2881 and cannot be calculated from. The "
        "<code>&lt;18</code> band is test data, not real applicants.",
}


def quality_page(days=30):
    """Distributions, each band split by the lender's result.

    The marginal distribution says how many applicants look a certain
    way. The split says which of them are worth having, which is the
    question actually being asked.
    """
    conn = store.connect(DB_PATH)
    metrics = store.quality_metrics(conn)
    p = ["<h1>Lead quality</h1>",
         f'<p class="sub">Last {days} days, each band split by the lender result. '
         'Counts only — no applicant row is ever stored.</p>', nav("/quality")]
    if not metrics:
        p.append('<p class="note">Nothing collected yet.</p>')
        conn.close()
        return page("Lead quality", "".join(p))

    # Which results exist, and which are common enough to be columns.
    overall: dict[str, int] = {}
    for metric in metrics:
        for _band, _order, result, n in store.quality_distribution(conn, metric, days):
            overall[result] = overall.get(result, 0) + n
    ranked = sorted(overall, key=lambda r: -overall[r])
    columns = ranked[:4]
    grand = sum(overall.values()) or 1

    p.append('<div class="card"><p class="note">Results seen: ' + ", ".join(
        f"<strong>{html.escape(r)}</strong> {100 * overall[r] / grand:.1f}%"
        for r in ranked[:6]) + ("" if len(ranked) <= 6 else f", and {len(ranked)-6} more")
        + '</p></div>')

    for metric in metrics:
        rows = store.quality_distribution(conn, metric, days)
        bands: dict[tuple[str, int], dict[str, int]] = {}
        for band, order, result, n in rows:
            bands.setdefault((band, order), {})[result] = \
                bands.setdefault((band, order), {}).get(result, 0) + n
        if not bands:
            continue
        totals = {k: sum(v.values()) for k, v in bands.items()}
        grand_metric = sum(totals.values()) or 1
        base = {c: sum(v.get(c, 0) for v in bands.values()) for c in columns}

        title = METRIC_TITLES.get(metric, metric)
        p.append(f'<h2>{html.escape(title)}</h2><div class="card">')
        if title != metric:
            p.append(f'<p class="note">Reported by the API as '
                     f'<code>{html.escape(metric)}</code>.</p>')
        if metric in METRIC_NOTES:
            p.append(f'<p class="note">{METRIC_NOTES[metric]}</p>')
        p.append('<table><tr>'
                 '<th>Band</th><th class="n">Leads</th><th class="n">Share</th>'
                 + "".join(f'<th class="n">{html.escape(c)}</th>' for c in columns)
                 + "</tr>")
        for key, n in sorted(totals.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            band = key[0]
            shown = f"{n:,}" if n >= 10 else "&lt;10"
            cells = ""
            for c in columns:
                if n < 10:
                    cells += '<td class="n note">—</td>'
                    continue
                rate = 100 * bands[key].get(c, 0) / n
                overall_rate = 100 * base[c] / grand_metric if grand_metric else 0
                lift = rate - overall_rate
                # Shown as a difference in points, not coloured. Whether
                # being above the average is good depends on the column -
                # a low decline rate is a good thing - so the page states
                # the direction and leaves the judgement to the reader.
                mark = (f'<span class="note"> {lift:+.0f}</span>'
                        if abs(lift) >= 5 else "")
                strong = ' style="font-weight:600"' if abs(lift) >= 10 else ""
                cells += f'<td class="n"{strong}>{rate:.1f}%{mark}</td>'
            p.append(f'<tr><td>{html.escape(band)}</td><td class="n">{shown}</td>'
                     f'<td class="n note">{100 * n / grand_metric:.1f}%</td>{cells}</tr>')
        p.append("</table>" + '<p class="note">Each percentage is of that band. '
                 "The small number beside it is the difference, in points, from this "
                 "metric's overall rate — so <em>+12</em> on Offer means that band is "
                 "offered twelve points more often than average. Shown only where the "
                 "gap reaches five points, bold at ten.</p></div>")
    conn.close()
    return page("Lead quality", "".join(p))


def confirm_reset():
    return ("<h1>Clear everything collected?</h1>"
            '<p class="sub">Removes every stored day — tiers, totals and quality counts — '
            "so a backfill starts clean. Use it when the collection itself was wrong, not "
            "merely stale. The API explorer's recorded shapes are kept.</p>"
            '<div class="card"><a class="btn" href="/reset?confirm=yes">Yes, clear it</a>'
            '<a class="btn quiet" href="/">Cancel</a></div>')


def verify_page(days=5):
    r = collector.verify(days)
    p = ["<h1>Window check</h1>",
         '<p class="sub">Fetches a range in one call, then each of its days separately, '
         'and compares. If single days do not sum to the range, the window is wrong and '
         'every stored figure is wrong with it.</p>', nav()]
    if "error" in r:
        p.append(f'<div class="card bad">{html.escape(r["error"])}</div>')
        return page("Window check", "".join(p))
    ok = r["verdict"].startswith("single days")
    p.append(f'<div class="card"><strong class="{"ok" if ok else "bad"}">'
             f'{html.escape(r["verdict"])}</strong><p class="note">'
             f'{html.escape(r["start"])} to {html.escape(r["end"])}, {r["days"]} days</p>'
             '<table><tr><th>Measure</th><th class="n">One range call</th>'
             '<th class="n">Days summed</th><th class="n">Ratio</th></tr>')
    for k in ("sent", "accepted", "revenue"):
        ratio = r["ratios"][k]
        cls = "ok" if ratio and 0.97 <= ratio <= 1.03 else "bad"
        p.append(f'<tr><td>{k}</td><td class="n">{r["span"][k]:,.2f}</td>'
                 f'<td class="n">{r["daily"][k]:,.2f}</td>'
                 f'<td class="n {cls}">{ratio:.3f}</td></tr>' if ratio
                 else f'<tr><td>{k}</td><td class="n">—</td><td class="n">—</td><td>—</td></tr>')
    p.append("</table></div>")
    p.append('<div class="card"><table><tr><th>Day</th><th class="n">Sent</th>'
             '<th class="n">Accepted</th><th class="n">Revenue</th></tr>')
    for d, sent, acc, rev in r["per_day"]:
        p.append(f'<tr><td>{html.escape(d)}</td><td class="n">{sent:,}</td>'
                 f'<td class="n">{acc:,}</td><td class="n">{rev:,.2f}</td></tr>')
    p.append("</table></div>")
    return page("Window check", "".join(p))


def explore_page():
    conn = store.connect(DB_PATH)
    latest = conn.execute("SELECT MAX(ran_at) FROM call").fetchone()[0]
    rows = conn.execute("SELECT id, endpoint, method, status, bytes, ms, error FROM call"
                        " WHERE ran_at=? ORDER BY id", (latest,)).fetchall() if latest else []
    conn.close()
    p = ["<h1>API explorer</h1>",
         '<p class="sub">Calls every endpoint once and records the shape of each response — '
         'field names, types and numbers, with strings reduced to a length. For inspecting the '
         'API, not for collection.</p>', nav("/explore"),
         '<div class="card"><a class="btn" href="/explore/run">Call all six endpoints</a></div>']
    if rows:
        p.append(f'<p class="note">Last run {html.escape(latest)} UTC</p><div class="card"><table>'
                 '<tr><th>Endpoint</th><th>Status</th><th class="n">Size</th><th class="n">Time</th></tr>')
        for cid, name, method, st, size, ms, err in rows:
            cls = "ok" if st == 200 else "bad"
            cell = html.escape(err) if err else f'<span class="{cls}">{st}</span>'
            p.append(f"<tr><td><a href='/shape/{cid}'><code>{html.escape(name)}</code></a>"
                     f"<br><span class='note'>{method}</span></td><td>{cell}</td>"
                     f"<td class='n'>{size:,} B</td><td class='n'>{ms:,} ms</td></tr>")
        p.append("</table></div>")
    return page("API explorer", "".join(p))


def explore_run():
    import urllib.parse  # noqa: F401 - kept local; this path is rarely used
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=7)
    fmt = "%Y-%m-%dT00:00:00"
    rng = f"startDate={start.strftime(fmt)}&endDate={end.strftime(fmt)}"
    body = {"StartDate": start.strftime(fmt), "EndDate": end.strftime(fmt),
            "CompareStartDate": (start - timedelta(days=7)).strftime(fmt),
            "CompareEndDate": start.strftime(fmt),
            "IsAllActiveOnly": False, "ExcludeInternalSources": False}
    calls = [
        ("Affiliates", "GET", f"{collector.API_BASE}/api/PublicReports/Affiliates", None),
        ("LenderTiers", "GET", f"{collector.API_BASE}/api/PublicReports/LenderTiers", None),
        ("LenderLeadCountReport", "GET", f"{collector.API_BASE}/api/PublicReports/LenderLeadCountReport?{rng}", None),
        ("LeadQualityReport", "GET", f"{collector.API_BASE}/api/PublicReports/LeadQualityReport?{rng}", None),
        ("AffiliateLeadCountReport", "POST", f"{collector.API_BASE}/api/PublicReports/AffiliateLeadCountReport", body),
        ("InternalAffiliateLeadCountReport", "POST",
         f"{collector.API_BASE}/api/PublicReports/InternalAffiliateLeadCountReport",
         {k: v for k, v in body.items() if k != "ExcludeInternalSources"}),
    ]
    conn = store.connect(DB_PATH)
    stamp = collector.now()
    with conn:
        for name, method, url, b in calls:
            status, payload, ms, error = collector.fetch(url, method, b)
            rows = []
            if payload and not error:
                try:
                    rows = shape(json.loads(payload.decode("utf-8", "replace")))
                except json.JSONDecodeError:
                    rows = [("$", "not-json", f"{len(payload):,} bytes")]
            cur = conn.execute("INSERT INTO call (ran_at, endpoint, method, url, status, bytes, ms, error)"
                               " VALUES (?,?,?,?,?,?,?,?)",
                               (stamp, name, method, url.split("?")[0], status, len(payload), ms, error))
            conn.executemany("INSERT INTO shape (call_id, ordinal, path, kind, detail) VALUES (?,?,?,?,?)",
                             [(cur.lastrowid, i, p_, k, d) for i, (p_, k, d) in enumerate(rows)])
    conn.close()


def shape_page(call_id):
    conn = store.connect(DB_PATH)
    call = conn.execute("SELECT endpoint, method, url, status, bytes, ms, ran_at, error"
                        " FROM call WHERE id=?", (call_id,)).fetchone()
    if not call:
        conn.close()
        return None
    rows = conn.execute("SELECT path, kind, detail FROM shape WHERE call_id=? ORDER BY ordinal",
                        (call_id,)).fetchall()
    conn.close()
    name, method, url, status, size, ms, ran_at, error = call
    b = [f"<h1>{html.escape(name)}</h1>",
         f'<p class="sub"><code>{html.escape(method)} {html.escape(url)}</code><br>{status} &middot; '
         f'{size:,} bytes &middot; {ms:,} ms &middot; {html.escape(ran_at)} UTC</p>',
         '<p><a href="/explore">&larr; back</a></p>']
    if error:
        b.append(f'<div class="card bad">{html.escape(error)}</div>')
    b.append('<div class="card"><table><tr><th>Path</th><th>Type</th><th>Value</th></tr>')
    for path, kind, detail in rows:
        v = html.escape(detail or "")
        if kind in ("string", "guid", "date-like"):
            v = f'<span class="note">{v} — withheld</span>'
        b.append(f"<tr><td class='mono'>{html.escape(path)}</td><td class='note'>{html.escape(kind)}</td>"
                 f"<td class='mono'>{v}</td></tr>")
    b.append("</table></div>")
    return page(name, "".join(b))


# ---------------------------------------------------------------- server

def collect_in_background(days):
    def work():
        with _busy:
            conn = store.connect(DB_PATH)
            try:
                collector.catch_up(conn, limit=days, days=max(days, collector.BACKFILL_DAYS))
            finally:
                conn.close()
    if _busy.locked():
        return False
    threading.Thread(target=work, daemon=True, name="collect-now").start()
    return True


class Handler(BaseHTTPRequestHandler):
    server_version = "overflow/2.0"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def _authorised(self):
        if not APP_PASSWORD:
            return False
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            _, _, supplied = base64.b64decode(header[6:]).decode().partition(":")
        except Exception:                            # noqa: BLE001
            return False
        return hmac.compare_digest(supplied, APP_PASSWORD)

    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                                # noqa: N802
        path, _, query = self.path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)

        if path == "/healthz":
            return self._send(200, b"ok", "text/plain; charset=utf-8")
        if not APP_PASSWORD:
            return self._send(503, page("Not configured",
                "<h1>Not configured</h1><p class='sub'>Set <code>APP_PASSWORD</code> in Railway "
                "&rarr; Variables. A Railway URL is public, so nothing is served without one.</p>"))
        if not self._authorised():
            return self._send(401, b"Authentication required", "text/plain; charset=utf-8",
                              {"WWW-Authenticate": 'Basic realm="Overflow reporting"'})
        try:
            days = max(1, min(365, int(params.get("days", "30"))))
        except ValueError:
            days = 30
        try:
            if path == "/":
                return self._send(200, overview())
            if path == "/affiliates":
                return self._send(200, affiliates_page(days))
            if path == "/quality":
                return self._send(200, quality_page(days))
            if path == "/explore":
                return self._send(200, explore_page())
            if path == "/explore/run":
                explore_run()
                return self._send(303, b"", "text/plain", {"Location": "/explore"})
            if path == "/verify":
                return self._send(200, verify_page(min(days, 10)))
            if path == "/reset":
                if params.get("confirm") == "yes":
                    conn = store.connect(DB_PATH)
                    collector.reset(conn)
                    conn.close()
                    return self._send(303, b"", "text/plain", {"Location": "/"})
                return self._send(200, page("Reset", confirm_reset()))
            if path == "/collect":
                collect_in_background(days if "days" in params else collector.DAYS_PER_WAKE)
                return self._send(303, b"", "text/plain", {"Location": "/"})
            if path.startswith("/shape/"):
                body = shape_page(int(path.rsplit("/", 1)[1]))
                return self._send(200, body) if body else self._send(404, page("Not found", "<h1>Not found</h1>"))
        except Exception:                            # noqa: BLE001
            return self._send(500, page("Error", f"<h1>Error</h1><pre class='mono'>"
                                                 f"{html.escape(traceback.format_exc())}</pre>"))
        return self._send(404, page("Not found", '<h1>Not found</h1><p><a href="/">home</a></p>'))


if __name__ == "__main__":
    store.connect(DB_PATH).close()
    collector.start(DB_PATH)
    print(f"listening on 0.0.0.0:{PORT}, db at {DB_PATH}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
