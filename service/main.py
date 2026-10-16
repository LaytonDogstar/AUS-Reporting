"""Overflow reporting service: web layer.

Standard library only. Fetching lives in collector.py, storage in
store.py. This file serves pages and triggers collection.
"""

from __future__ import annotations

import gzip
import hmac
import html
import json
import os
import re
import threading
import traceback
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import auth
import collector
import dash
import store

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
PORT = int(os.environ.get("PORT", "8080"))
DB_PATH = os.environ.get("DB_PATH") or ("/data/overflow.db" if os.path.isdir("/data") else "overflow.db")

GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
DATEISH = re.compile(r"^\d{4}-\d{2}-\d{2}")

_busy = threading.Lock()

# Who the current request is for, so a page can show an admin's buttons
# and carry the form token without every renderer taking a user.
CURRENT = threading.local()


def auth_path():
    """The logins live beside the collected data, in their own file, so
    clearing the data never touches a login. On a Railway volume both
    survive a deploy."""
    return os.environ.get("AUTH_DB_PATH") or os.path.join(
        os.path.dirname(os.path.abspath(DB_PATH)), "auth.db")


def current_user():
    return getattr(CURRENT, "user", None)


def is_admin():
    u = current_user()
    return bool(u) and u["role"] == "admin"


def post_button(action, label, cls="btn", fields=None, confirm=None):
    """An action as a POST form carrying the session's form token. Every
    action is one: a link that changes something can be followed from
    another site by anyone who can get an admin to click it."""
    u = current_user() or {}
    hidden = "".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(str(v))}">'
                     for k, v in {"csrf": u.get("csrf", ""), **(fields or {})}.items())
    onsubmit = f' onsubmit="return confirm({html.escape(json.dumps(confirm))})"' if confirm else ""
    return (f'<form method="post" action="{html.escape(action)}" style="display:inline"{onsubmit}>'
            f'{hidden}<button class="{cls}" type="submit">{html.escape(label)}</button></form>')


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
:root:not([data-theme=light]){--bg:#15171a;--fg:#e9eaec;
--muted:#9aa1ab;--line:#2b2f35;--card:#1c1f23;--accent:#7fb2e5;--ok:#4ac98a;--warn:#e3b341;--bad:#ff7b72}
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
button.btn{background:var(--accent);color:#fff;border:0;padding:8px 15px;border-radius:7px;
font:inherit;font-weight:600;font-size:13.5px;margin-right:8px;cursor:pointer}
button.btn.quiet{background:transparent;color:var(--accent);border:1px solid var(--line)}
button.btn.small{padding:4px 10px;font-size:12.5px;font-weight:500;margin:0 4px 0 0}
button.btn.danger{background:transparent;color:var(--bad);border:1px solid var(--line)}
input.f,select.f{font:inherit;font-size:14px;padding:7px 10px;border:1px solid var(--line);
border-radius:7px;background:var(--card);color:var(--fg)}
.cred{background:var(--bg);border:1px solid var(--line);border-left:3px solid var(--ok);
border-radius:8px;padding:10px 12px;margin:10px 0}
.cred code{font-size:15px;user-select:all}
.tabs{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:14px 0 6px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
a.seglink{padding:6px 13px;font-size:13px;text-decoration:none;color:var(--muted);
border-right:1px solid var(--line);background:var(--card)}
a.seglink:last-child{border-right:0}
a.seglink[aria-current="page"]{background:var(--accent);color:#fff;font-weight:600}
.lbl{font-size:13px;color:var(--muted)}
button.ghost{background:none;border:1px solid var(--line);color:var(--fg);border-radius:7px;
padding:5px 11px;font:inherit;font-size:13px;cursor:pointer}
.note{color:var(--muted);font-size:13px}
.bar{height:7px;background:var(--accent);border-radius:4px;display:inline-block;min-width:2px}
"""


def page(title, body):
    return f"""<!doctype html><html lang="en" data-theme="dark"><head><meta charset="utf-8">
<script>try{{var t=localStorage.getItem("theme");if(t)document.documentElement.setAttribute("data-theme",t)}}catch(e){{}}</script>
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>{CSS}</style></head><body><div class="wrap">{body}</div></body></html>""".encode()


def nav(active=""):
    items = [("/", "Dashboard"), ("/lenders", "Lenders"), ("/customers", "Customers"),
             ("/today", "Today"), ("/yesterday", "Yesterday"), ("/day", "Day"), ("/days", "Days"),
             ("/scorecard", "Scorecard"),
             ("/affiliates", "Affiliates"), ("/quality", "Lead quality"),
             ("/pings", "Ping log"), ("/collection", "Collection"),
             ("/verify", "Window check"), ("/explore", "API explorer")]
    role = (current_user() or {}).get("role", "admin")
    items = [(h, t) for h, t in items if role == "admin" or auth.can_view(role, h)]
    if role == "admin":
        items.append(("/admin", "Admin"))
    # The five dashboard pages are the tabs, with the range beside the
    # period ones, as on the dashboard pages themselves; the rest are
    # links beneath.
    tabbed = {h for h, _ in PERIOD_TABS + DAY_TABS}
    others = [(h, t) for h, t in items if h not in tabbed]
    tabs = f'<div class="tabs">{page_tabs(getattr(CURRENT, "path", active) or active)}</div>'
    return tabs + ('<p class="note">' + " &middot; ".join(
        f"<strong>{t}</strong>" if h == active else f'<a href="{h}">{t}</a>'
        for h, t in others) + "</p>")


def money(v):
    return f"{v:,.2f}" if v is not None else "—"


# ----------------------------------------------------------------- pages

def dashboard_page(days=30, source="both"):
    """The dashboard proper: tiles, trend and funnel, from collected counts.

    Built for one population at a time. FF and non-FF lead counts cannot
    be added into a single figure, so a page showing both would open on
    a headline number that does not mean anything.
    """
    conn = store.connect(DB_PATH)
    try:
        data = (dash.payload_both(conn, days) if source == "both"
                else dash.payload(conn, days, source))
    finally:
        conn.close()
    role = (current_user() or {}).get("role", "admin")
    data["pagesAllowed"] = [h for h in ("/scorecard", "/quality", "/pings", "/affiliates",
                                        "/collection") if auth.can_view(role, h)]
    if not data["applications"]:
        other = "internal" if source == "external" else "external"
        return page("Dashboard", f'<h1>Dashboard</h1><p class="sub">'
                    f'{html.escape(dash.SOURCE_LABELS.get(source, source))}, last '
                    f'{days} days.</p>' + nav("/") +
                    '<div class="card"><p class="note">Nothing collected for this source '
                    f'yet. <a href="/?source={other}">Try the other one</a>, or '
                    '<a href="/collection">see what the collector is doing</a>.</p></div>')
    return dash.render(data).encode("utf-8")


def lenders_page(days=30):
    """Per lender: whether its filter pass, acceptance and redirect rates
    are holding up, and which customers it buys against those it is
    offered. Lenders are not split by source - the lender report covers
    every source together."""
    conn = store.connect(DB_PATH)
    try:
        data = dash.lender_payload(conn, days)
    finally:
        conn.close()
    if not data["lenders"]:
        return page("Lenders", '<h1>Lenders</h1>' + nav("/lenders") +
                    '<div class="card"><p class="note">Nothing collected yet. '
                    '<a href="/collection">See what the collector is doing</a>.</p></div>')
    return dash.render(data, dash.LENDER_TEMPLATE).encode("utf-8")


def today_page(which="today", source="all"):
    """The day hour by hour against the norm, for all sources or one
    population: today read live, or yesterday."""
    conn = store.connect(DB_PATH)
    try:
        data = dash.today_payload(conn, which, source)
    finally:
        conn.close()
    return dash.render(data, dash.TODAY_TEMPLATE).encode("utf-8")


def period_page(start, end, source="all", which="days"):
    """The Today view over a chosen finished day, or a run of them: one
    point a day, each day against its own typical day."""
    conn = store.connect(DB_PATH)
    try:
        data = dash.period_payload(conn, start, end, source, which)
    finally:
        conn.close()
    return dash.render(data, dash.TODAY_TEMPLATE).encode("utf-8")


def resolve_day(params):
    """The day the Day page shows: the one asked for, no later than
    yesterday, else yesterday."""
    yesterday = store.business_today() - timedelta(days=1)
    try:
        day = date.fromisoformat(params.get("date", ""))
    except ValueError:
        return yesterday
    return min(day, yesterday)


def period_span(rng):
    """The finished days a range covers on the Days page: a custom range's
    own dates, or the last n days to yesterday; no more than
    dash.PERIOD_MAX_DAYS, keeping the latest."""
    yesterday = store.business_today() - timedelta(days=1)
    end = date.fromisoformat(rng["end"]) if rng["end"] else yesterday
    start = (date.fromisoformat(rng["start"]) if rng["start"]
             else end - timedelta(days=rng["days"] - 1))
    start = max(start, end - timedelta(days=dash.PERIOD_MAX_DAYS - 1))
    return start.isoformat(), end.isoformat()


def customers_page(days=30):
    """Buyer profiles: what each lender buys against every lead
    received, every measure at once, and how its mix is moving."""
    conn = store.connect(DB_PATH)
    try:
        data = dash.customer_payload(conn, days)
    finally:
        conn.close()
    if not data["attrs"] or not data["lenders"]:
        return page("Customers", '<h1>Customers</h1>' + nav("/customers") +
                    '<div class="card"><p class="note">Lead quality per lender is still being '
                    'collected. <a href="/collection">See what the collector is doing</a>.'
                    '</p></div>')
    return dash.render(data, dash.CUSTOMER_TEMPLATE).encode("utf-8")


def collection_page():
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
         'counts per band before anything is written.</p>', nav("/collection")]

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
        if is_admin():
            p.append(post_button("/collect", "Collect missing days") +
                     post_button("/collect", "Backfill 90 days", "btn quiet", {"days": 90}) +
                     '<a class="btn quiet" href="/reset">Clear and start again</a>')
        else:
            p.append('<p class="note">An admin can collect missing days or start again.</p>')
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
    "WagesAndCentrelinkMonthly": "Wages and Centrelink, monthly",
}

METRIC_NOTES = {
    "WagesAndCentrelinkMonthly":
        "<strong>This is not Centrelink income.</strong> It holds wages plus "
        "Centrelink. The API returned it as <code>CenterlinkMonthly</code> until "
        "14 Oct 2026, when it was renamed; days collected under either name are the "
        "same measure and are counted as one series. Read the bands as total monthly "
        "income, and do not set them against "
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


def scorecard_page(days=30):
    """What a lead from each source is worth, rather than how many.

    Ranked on profit per lead. Volume says who is big; profit per lead
    says who is worth having, and the two disagree often enough that
    the ranking is the point.
    """
    conn = store.connect(DB_PATH)
    rows = store.scorecard(conn, days)
    conn.close()

    p = [f"<h1>Affiliate scorecard</h1>",
         f'<p class="sub">Last {days} days, Sydney dates. Ranked by profit per lead — '
         'what a lead from that source is worth, not how many it sends.</p>',
         nav("/scorecard")]
    if not rows:
        p.append('<p class="note">Nothing collected yet.</p>')
        return page("Affiliate scorecard", "".join(p))

    leads = sum(r["leads"] for r in rows)
    profit = sum(r["profit"] for r in rows)
    average = profit / leads if leads else 0
    internal = [r for r in rows if r["source"] == "internal"]
    p.append('<div class="card"><table>'
             f'<tr><td>Affiliates</td><td class="n">{len(rows)}</td></tr>'
             f'<tr><td>Leads</td><td class="n">{leads:,}</td></tr>'
             f'<tr><td>Profit</td><td class="n">{money(profit)}</td></tr>'
             f'<tr><td>Profit per lead, overall</td><td class="n">{average:,.2f}</td></tr>'
             '</table><p class="note">Internal and external sources are both here and are '
             'marked, but they are separate populations: their lead counts cannot be added '
             'into one figure, and the overall profit per lead above is a weighted average '
             'rather than a target.</p></div>')

    best = max(r["profit_per_lead"] for r in rows) or 1
    p.append('<h2>By affiliate</h2><div class="card"><table>'
             '<tr><th>Affiliate</th><th class="n">Leads</th><th class="n">Sold</th>'
             '<th class="n">Expected</th><th class="n">Sells to</th>'
             '<th class="n">Acceptance</th><th class="n">Rev / lead</th>'
             '<th class="n">Profit / lead</th><th class="n">Profit</th><th></th></tr>')
    for r in rows:
        tag = ' <span class="note">internal</span>' if r["source"] == "internal" else ""
        acceptance = (f'{100 * r["acceptance"]:.1f}%' if r["acceptance"] is not None
                      else '<span class="note">—</span>')
        expected = (f'{100 * r["expected"]:.1f}%' if r["expected"]
                    else '<span class="note">—</span>')
        index = r["quality_index"]
        if index is None:
            sells_to = '<span class="note">—</span>'
        else:
            cls_q = "ok" if index >= 1.1 else "bad" if index < 0.9 else ""
            sells_to = (f'<span class="{cls_q}">{index:.2f}&times;</span>' if cls_q
                        else f'{index:.2f}&times;')
        ppl = r["profit_per_lead"]
        width = int(90 * ppl / best) if ppl > 0 else 0
        cls = ' class="bad"' if ppl < 0 else ""
        p.append(f'<tr><td>{html.escape(r["name"] or "—")}{tag}</td>'
                 f'<td class="n">{r["leads"]:,}</td>'
                 f'<td class="n">{100 * r["sold_rate"]:.1f}%</td>'
                 f'<td class="n">{expected}</td>'
                 f'<td class="n">{sells_to}</td>'
                 f'<td class="n">{acceptance}</td>'
                 f'<td class="n">{r["revenue_per_lead"]:,.2f}</td>'
                 f'<td class="n"{cls}>{ppl:,.2f}</td>'
                 f'<td class="n">{money(r["profit"])}</td>'
                 f'<td><span class="bar" style="width:{width}px"></span></td></tr>')
    p.append('</table><p class="note"><strong>Sold</strong> is of the leads that affiliate '
             'sent. <strong>Acceptance</strong> is from the ping log: of the times one of '
             'its leads reached a lender, how often the lender bought — so a source can '
             'send few leads and still be the one lenders want. A dash means the ping log '
             'has no rows for that affiliate in this window.</p></div>')

    p.append('<h2>How to read this</h2><div class="card"><p class="note">'
             'Profit per lead is revenue minus commission, divided by leads received. '
             'Commission is money paid away to the affiliate, so this is what the business '
             'keeps per lead it is sent — the figure to rank on. Revenue per lead flatters '
             'sources on high commission; volume flatters nobody. Bank statement cost is in '
             'the affiliate report but not in its profit column, so profit here is before '
             'that cost.</p>'
             '<p class="note"><strong>Expected</strong> is the sold rate this source\'s lead '
             'mix would produce at population rates — a quality score that owes nothing to '
             'how well the leads were sold. It is taken over monthly income, where the sold '
             'rate runs from 15.1% under a dollar to 47.7% above three thousand. '
             '<strong>Sells to</strong> is the actual sold rate over that expectation: above '
             '1.00 means the source sells better than its leads alone predict, below means '
             'worse, and it is shown only where a source sent at least 100 leads — below '
             'that its own sold rate is noise. A source can send good leads and sell them '
             'badly, and only these two columns apart can tell you which.</p></div>')
    return page("Affiliate scorecard", "".join(p))


def pings_page(days=30):
    """What the pingtree did, from LenderApplicationResults.

    The lender report says what each tier bought. This says what each
    tier was offered and what it did with it - and it is the only place
    a decline is an event rather than a residue.
    """
    conn = store.connect(DB_PATH)
    totals = store.lender_result_totals(conn, days)
    reach = store.lender_result_reach(conn, days)
    statuses = store.lender_result_status(conn, days)
    league = store.lender_result_league(conn, days)
    lookup = store.lender_lookup(conn)
    conn.close()

    p = ["<h1>Ping log</h1>",
         f'<p class="sub">Last {days} days, Sydney dates. One row per lead and lender, '
         'reduced to counts — no lead identifier is stored.</p>', nav("/pings")]
    if not totals or not totals[0]:
        p.append('<p class="note">Nothing collected yet.</p>')
        return page("Ping log", "".join(p))

    pings, leads, applications = totals
    all_pings, reached, sold, sold_value = reach
    reached = reached or 0
    # Over pings that reached a lender, not every ping: most are
    # filtered before they get there, and 47 "lenders tried" against 45
    # lenders in existence is the tell.
    per_app = reached / applications if applications else 0
    p.append('<div class="card"><table>'
             f'<tr><td>Pings</td><td class="n">{pings:,}</td></tr>'
             f'<tr><td>Reached a lender</td><td class="n">{reached:,}</td>'
             f'<td class="n note">{100 * reached / pings:.1f}% of pings</td></tr>'
             f'<tr><td>Sold</td><td class="n">{(sold or 0):,}</td>'
             f'<td class="n note">{100 * (sold or 0) / reached:.1f}% of those that reached'
             '</td></tr>'
             f'<tr><td>Revenue from the sold</td><td class="n">{money(sold_value)}</td></tr>'
             f'<tr><td>Applications behind them</td><td class="n">{applications:,}</td></tr>'
             f'<tr><td>Lenders reached per application</td>'
             f'<td class="n">{per_app:.2f}</td></tr>'
             '</table><p class="note"><strong>Most pings never reach a lender.</strong> '
             'FilterFailed and Ignored are stopped by the pingtree first, so a rate taken '
             'over every ping is not an acceptance rate. Sold, Decline and Error are the '
             "ones that got there, and they reproduce the lender report's sent to within "
             '0.2%, which is why they are the denominator here.</p></div>')

    if statuses:
        total = sum(n for _s, n, _c in statuses) or 1
        p.append('<h2>What came back</h2><div class="card"><table>'
                 '<tr><th>Status</th><th class="n">Pings</th><th class="n">Share</th>'
                 '<th class="n">Reached a lender</th></tr>')
        for status_id, n, _cost in statuses:
            label = collector.STATUS_LABELS.get(status_id, f"status {status_id}")
            got_there = "yes" if status_id in collector.REACHED_STATUSES else "no"
            p.append(f'<tr><td>{html.escape(label)}</td><td class="n">{n:,}</td>'
                     f'<td class="n">{100 * n / total:.1f}%</td>'
                     f'<td class="n note">{got_there}</td></tr>')
        p.append('</table><p class="note">Status codes from the dev team, 13 Oct 2026: '
                 '0 FilterFailed, 1 Sold, 2 Decline, 3 Error, 4 Ignored. There is no cost '
                 "column here because a ping carries the tier's list price whatever the "
                 'outcome — only the sold ones are money.</p></div>')

    if league:
        p.append('<h2>By tier</h2><div class="card"><table>'
                 '<tr><th>Tier</th><th>Lender</th><th class="n">Reached</th>'
                 '<th class="n">Sold</th><th class="n">Declined</th>'
                 '<th class="n">Acceptance</th><th class="n">Revenue</th></tr>')
        unnamed = 0
        for lender_id, tier_id, _p, reached_n, sold_n, declined_n, value in league:
            rate = (100 * sold_n / reached_n) if reached_n else 0
            tier_name, _lid, lender_name = lookup.get(tier_id, (None, None, None))
            unnamed += 0 if tier_name else 1
            tier_cell = (html.escape(tier_name) if tier_name
                         else f'<span class="note">tier {tier_id}</span>')
            lender_cell = (html.escape(lender_name) if lender_name
                           else f'<span class="note">lender {lender_id}</span>')
            p.append(f'<tr><td>{tier_cell}</td><td>{lender_cell}</td>'
                     f'<td class="n">{reached_n:,}</td><td class="n">{sold_n:,}</td>'
                     f'<td class="n">{declined_n:,}</td><td class="n">{rate:.1f}%</td>'
                     f'<td class="n">{money(value)}</td></tr>')
        note = ('Acceptance is sold over pings that reached that tier: of the times a lead '
                'got to it, how often it bought. Revenue is the tier list price of the sold '
                'ones, which is what the lender report charges: measured on 12 Oct 2026 the '
                'two agreed to within 0.4%, 14,514 here against 14,454 there, the gap being '
                'six sold rows that arrived after the report was taken.')
        if unnamed:
            note += (f' <strong>{unnamed} of {len(league)} tiers could not be named</strong> '
                     'from the LenderTiers lookup — an id in the ping log that the lookup '
                     'does not have is worth raising.')
        p.append(f'</table><p class="note">{note}</p></div>')
    return page("Ping log", "".join(p))


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
            '<div class="card">' + post_button("/reset", "Yes, clear it") +
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
         ('<div class="card">' + post_button("/explore/run", "Call all seven endpoints") + '</div>'
          if is_admin() else '<p class="note">An admin can call the endpoints again.</p>')]
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
        # Added to the API 14 Oct 2026: the ping log, one row per lead
        # and lender with a status. Route and method confirmed the same
        # day without credentials: unauthenticated, this path with the
        # range parameters answers 401 exactly as the other GET reports
        # do, while POST to it and the obvious name variants answer 404.
        # So it is a GET taking the same window. The field names still
        # need an authenticated call, which is what this probe records.
        ("LenderApplicationResults", "GET",
         f"{collector.API_BASE}/api/PublicReports/LenderApplicationResults?{rng}", None),
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


# ------------------------------------------------------------------ range

# The windows offered, as the US dashboard offers them, plus Custom.
RANGE_PRESETS = (7, 14, 28, 30, 56, 90)
DEFAULT_DAYS = 30
MAX_RANGE_DAYS = 366
RANGE_COOKIE = "rng"
# Pages that show a period, and so take the range. Today, Yesterday and
# Day are one day each and do not.
RANGE_PAGES = ("/", "/lenders", "/customers", "/scorecard", "/affiliates", "/quality", "/pings",
               "/days")


def resolve_range(params, cookie):
    """The window a request wants: from its query if it names one, else
    the one remembered from earlier, else 30 days. Returns
    {days, start, end, label, key}; start and end are set for a custom
    range only, which pins the store's window to those dates."""
    def preset(n):
        return {"days": n, "start": None, "end": None, "label": f"Last {n} days",
                "phrase": f"the last {n} days", "key": str(n)}

    def custom(a, b):
        try:
            start, end = date.fromisoformat(a), date.fromisoformat(b)
        except (TypeError, ValueError):
            return None
        yesterday = store.business_today() - timedelta(days=1)
        end = min(end, yesterday)
        if start > end:
            start, end = end, start
        if (end - start).days + 1 > MAX_RANGE_DAYS:
            start = end - timedelta(days=MAX_RANGE_DAYS - 1)
        days = (end - start).days + 1
        label = (f"{start.strftime('%-d %b')} – {end.strftime('%-d %b %Y')}"
                 if start.year == end.year else
                 f"{start.strftime('%-d %b %Y')} – {end.strftime('%-d %b %Y')}")
        return {"days": days, "start": start.isoformat(), "end": end.isoformat(),
                "label": label, "phrase": label, "key": f"{start.isoformat()}..{end.isoformat()}"}

    def parse_days(v):
        try:
            n = int(v)
        except (TypeError, ValueError):
            return None
        return preset(max(1, min(MAX_RANGE_DAYS, n)))

    if params.get("from") and params.get("to"):
        r = custom(params["from"], params["to"])
        if r:
            return r, True
    if params.get("days"):
        r = parse_days(params["days"])
        if r:
            return r, True
    if cookie:
        if ".." in cookie:
            r = custom(*cookie.split("..", 1))
        else:
            r = parse_days(cookie)
        if r:
            return r, False
    return preset(DEFAULT_DAYS), False


def range_control(rng):
    """The range picker: a list of presets and Custom, with two dates.
    Choosing reloads the page with the new range in its query, which the
    server remembers for every other page."""
    yesterday = (store.business_today() - timedelta(days=1)).isoformat()
    custom = rng["start"] is not None
    opts = "".join(f'<option value="{n}"{" selected" if not custom and rng["days"] == n else ""}>'
                   f'Last {n} days</option>' for n in RANGE_PRESETS)
    if not custom and rng["days"] not in RANGE_PRESETS:
        opts += f'<option value="{rng["days"]}" selected>Last {rng["days"]} days</option>'
    opts += f'<option value="custom"{" selected" if custom else ""}>Custom…</option>'
    start = rng["start"] or (store.business_today() - timedelta(days=rng["days"])).isoformat()
    end = rng["end"] or yesterday
    return f"""<span class="rangectl" style="display:inline-flex;gap:6px;align-items:center;flex-wrap:wrap">
<label class="lbl" for="rangeSel">Range</label>
<select id="rangeSel" class="f" aria-label="Date range">{opts}</select>
<span id="rangeCustom"{"" if custom else " hidden"} style="display:inline-flex;gap:6px;align-items:center">
<input type="date" id="rangeFrom" class="f" value="{start}" max="{yesterday}" aria-label="From">
<span class="lbl">→</span>
<input type="date" id="rangeTo" class="f" value="{end}" max="{yesterday}" aria-label="To">
<button type="button" id="rangeApply" class="ghost">Apply</button></span>
<script>(function () {{
  var sel = document.getElementById("rangeSel"), box = document.getElementById("rangeCustom");
  function go(q) {{
    var u = new URL(location.href);
    ["days", "from", "to"].forEach(function (k) {{ u.searchParams.delete(k); }});
    Object.keys(q).forEach(function (k) {{ u.searchParams.set(k, q[k]); }});
    location.href = u.pathname + u.search;
  }}
  sel.addEventListener("change", function () {{
    if (sel.value === "custom") {{ box.hidden = false; box.style.display = "inline-flex"; return; }}
    go({{ days: sel.value }});
  }});
  document.getElementById("rangeApply").addEventListener("click", function () {{
    var a = document.getElementById("rangeFrom").value, b = document.getElementById("rangeTo").value;
    if (a && b) go({{ from: a, to: b }});
  }});
  if (box.hidden) box.style.display = "none";
}})();</script></span>"""


PERIOD_TABS = (("/", "Affiliates"), ("/lenders", "Lenders"), ("/customers", "Customers"))
DAY_TABS = (("/today", "Today"), ("/yesterday", "Yesterday"), ("/day", "Day"), ("/days", "Days"))


def day_control(day):
    """The Day page's date: picking one reloads the page on it."""
    yesterday = (store.business_today() - timedelta(days=1)).isoformat()
    return f"""<span class="rangectl" style="display:inline-flex;gap:6px;align-items:center">
<label class="lbl" for="daySel">Date</label>
<input type="date" id="daySel" class="f" value="{day}" max="{yesterday}" aria-label="Day">
<script>(function () {{
  var d = document.getElementById("daySel");
  d.addEventListener("change", function () {{
    if (!d.value) return;
    var u = new URL(location.href);
    u.searchParams.set("date", d.value);
    location.href = u.pathname + u.search;
  }});
}})();</script></span>"""


def page_tabs(active):
    """The page switch, the same on every dashboard page: the day views
    first - Today, Yesterday, a chosen Day and a run of Days - then the
    pages over a period, each with its date or range beside it."""
    rng = getattr(CURRENT, "range", None)

    current = ' aria-current="page"'

    def group(tabs, label):
        return (f'<nav class="seg" aria-label="{label}">' + "".join(
            f'<a class="seglink" href="{h}"{current if h == active else ""}>{t}</a>'
            for h, t in tabs) + "</nav>")
    out = group(DAY_TABS, "Day views")
    if active == "/day" and getattr(CURRENT, "day", None):
        out += day_control(CURRENT.day)
    if rng and active == "/days":
        out += range_control(rng)
    out += group(PERIOD_TABS, "Pages over a period")
    if rng and active in RANGE_PAGES and active != "/days":
        out += range_control(rng)
    return out


# ----------------------------------------------------------------- logins

def login_page(next_path="/", error=""):
    err = f'<p class="bad">{html.escape(error)}</p>' if error else ""
    return page("Sign in", f"""
<div style="max-width:360px;margin:9vh auto 0">
<h1>AUS Lead Reporting</h1><p class="sub">Sign in to continue</p>
<form method="post" action="/login" class="card" style="padding:20px">
<input type="hidden" name="next" value="{html.escape(next_path)}">
<p><label class="note" for="u">Username</label><br>
<input class="f" id="u" name="username" autocomplete="username" autocapitalize="none"
 spellcheck="false" required autofocus style="width:100%"></p>
<p><label class="note" for="p">Password</label><br>
<input class="f" id="p" name="password" type="password" autocomplete="current-password"
 required style="width:100%"></p>
<button class="btn" type="submit" style="width:100%;margin:6px 0 0">Sign in</button>{err}
</form>
<p class="note">Ask an admin for a login. Passwords are generated for you and can be reset,
not recovered.</p></div>""")


def _when(ts):
    if not ts:
        return "—"
    t = datetime.fromisoformat(ts).astimezone(store.REPORT_TZ)
    return t.strftime("%d %b %H:%M")


def admin_page(notice=""):
    """Users, adding one, and the login log - the US dashboard's admin
    tab, without its storage and tree-upload cards."""
    conn = auth.connect(auth_path())
    try:
        rows, log = auth.users(conn), auth.login_log(conn, 200)
    finally:
        conn.close()
    me = current_user()["username"]
    roles = "".join(f'<option value="{r}">{html.escape(auth.ROLE_LABELS[r])}</option>'
                    for r in auth.ROLES)
    p = ["<h1>Admin</h1>",
         '<p class="sub">Who can sign in, what they can see, and when they last did.</p>',
         nav(), notice,
         '<h2>Add a user</h2><div class="card">'
         '<form method="post" action="/admin/create" style="display:flex;gap:8px;flex-wrap:wrap">'
         f'<input type="hidden" name="csrf" value="{html.escape(current_user()["csrf"])}">'
         '<input class="f" name="username" placeholder="username, e.g. sam or sam@company.com"'
         ' autocapitalize="none" spellcheck="false" required style="min-width:260px">'
         f'<select class="f" name="role">{roles}</select>'
         '<button class="btn" type="submit">Create &amp; generate password</button></form>'
         '<p class="note" style="margin-top:10px">The generated password is shown once — copy it '
         'and send it to the person. It is stored only as a salted hash and can be reset, not '
         'recovered. <b>Viewer</b>: the dashboard pages. <b>Analyst</b>: those and the '
         'diagnostic pages (collection, ping log, window check, API explorer), read-only. '
         '<b>Admin</b>: everything, including collecting, clearing and managing users.</p></div>',
         '<h2>Users</h2><div class="card" style="overflow-x:auto"><table>'
         '<tr><th>User</th><th>Role</th><th>Status</th><th>Created</th><th>Last sign-in</th>'
         '<th class="n">Sign-ins</th><th>Last seen</th><th>Actions</th></tr>',
         f'<tr><td><b>{auth.BREAK_GLASS}</b></td><td>admin</td><td class="ok">always</td>'
         '<td colspan="4" class="note">The <code>APP_PASSWORD</code> sign-in. Not stored here, so it '
         'cannot be locked out or deleted; change it in Railway.</td><td></td></tr>']
    for u in rows:
        name = u["username"]
        self_row = name == me
        role_sel = "".join(f'<option value="{r}"{" selected" if r == u["role"] else ""}>{r}</option>'
                           for r in auth.ROLES)
        role_cell = (u["role"] if self_row else
                     f'<form method="post" action="/admin/role" style="display:inline">'
                     f'<input type="hidden" name="csrf" value="{html.escape(current_user()["csrf"])}">'
                     f'<input type="hidden" name="username" value="{html.escape(name)}">'
                     f'<select class="f" name="role" onchange="this.form.submit()" '
                     f'style="padding:3px 6px;font-size:13px">{role_sel}</select></form>')
        actions = ""
        if not self_row:
            actions = (post_button("/admin/reset", "Reset password", "btn quiet small", {"username": name},
                                   f"Reset {name}'s password? Their current one stops working.") +
                       post_button("/admin/active", "Deactivate" if u["active"] else "Activate",
                                   "btn quiet small", {"username": name, "active": 0 if u["active"] else 1}) +
                       post_button("/admin/delete", "Delete", "btn danger small", {"username": name},
                                   f"Delete {name}? This cannot be undone."))
        actions += f' <a class="note" href="/admin/activity?u={urllib.parse.quote(name)}">activity</a>'
        p.append(f'<tr><td><b>{html.escape(name)}</b>{" <span class=note>(you)</span>" if self_row else ""}</td>'
                 f'<td>{role_cell}</td>'
                 f'<td class="{"ok" if u["active"] else "warn"}">{"active" if u["active"] else "deactivated"}</td>'
                 f'<td class="note">{_when(u["created_at"])}'
                 f'{" by " + html.escape(u["created_by"]) if u["created_by"] else ""}</td>'
                 f'<td>{_when(u["last_login"])}</td><td class="n">{u["logins"]}</td>'
                 f'<td>{_when(u["last_seen"])}</td><td style="white-space:nowrap">{actions}</td></tr>')
    if not rows:
        p.append('<tr><td colspan="8" class="note">No users yet — add one above.</td></tr>')
    p.append("</table></div>")
    p.append('<h2>Sign-ins</h2><div class="card" style="overflow-x:auto"><table>'
             '<tr><th>When (Sydney)</th><th>User</th><th>Result</th><th>Role</th><th>Address</th>'
             '<th>Device</th></tr>')
    for e in log:
        p.append(f'<tr><td>{_when(e["at"])}</td><td>{html.escape(e["username"] or "—")}</td>'
                 f'<td class="{"ok" if e["ok"] else "bad"}">{"signed in" if e["ok"] else "failed"}</td>'
                 f'<td>{html.escape(e["role"] or "—")}</td><td class="mono">{html.escape(e["ip"] or "")}</td>'
                 f'<td class="note">{html.escape(auth.device(e["user_agent"]))}</td></tr>')
    if not log:
        p.append('<tr><td colspan="6" class="note">No sign-ins recorded yet.</td></tr>')
    p.append(f'</table><p class="note">The last 200. After {auth.LOCKOUT_FAILURES} failures in '
             f'{auth.LOCKOUT_MINUTES} minutes from one address, sign-ins from it are refused until '
             f'the window passes.</p></div>')
    return page("Admin", "".join(p))


def admin_activity_page(username):
    conn = auth.connect(auth_path())
    try:
        sittings = auth.sessions_for(conn, username)
    finally:
        conn.close()
    p = [f"<h1>{html.escape(username)} — activity</h1>",
         '<p class="sub">Pages opened, grouped into sittings: a new one starts after a '
         '15-minute gap. Time is from the first page to the last in each.</p>',
         '<p><a href="/admin">&larr; Admin</a></p><div class="card"><table>'
         '<tr><th>Sitting started (Sydney)</th><th class="n">Minutes</th><th class="n">Pages</th>'
         '<th>Sections viewed</th></tr>']
    for s_ in sittings[:200]:
        mins = round((s_["last"] - s_["start"]).total_seconds() / 60)
        p.append(f'<tr><td>{_when(s_["start"].isoformat())}</td><td class="n">{mins}</td>'
                 f'<td class="n">{s_["views"]}</td>'
                 f'<td class="note">{html.escape(", ".join(s_["pages"]))}</td></tr>')
    if not sittings:
        p.append('<tr><td colspan="4" class="note">Nothing recorded yet.</td></tr>')
    p.append("</table></div>")
    return page(f"{username} activity", "".join(p))


def user_bar(user):
    """Who is signed in, an Admin link for admins, and Sign out - added
    to the top of every page, whatever template drew it."""
    admin = (' <a href="/admin" style="color:inherit">Admin</a> ·'
             if user["role"] == "admin" else "")
    return (
        '<div style="max-width:1180px;margin:0 auto;padding:8px 16px 0;display:flex;gap:8px;'
        'justify-content:flex-end;align-items:center;font:12.5px/1.4 system-ui,sans-serif;opacity:.8">'
        f'<span>{html.escape(user["username"])} · {html.escape(user["role"])}</span> ·{admin}'
        '<form method="post" action="/logout" style="display:inline;margin:0">'
        f'<input type="hidden" name="csrf" value="{html.escape(user["csrf"])}">'
        '<button type="submit" style="background:none;border:0;padding:0;color:inherit;'
        'font:inherit;text-decoration:underline;cursor:pointer">Sign out</button></form></div>')


def safe_next(target):
    """Only a path on this site: never `//elsewhere` or a full URL."""
    target = target or "/"
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


class Handler(BaseHTTPRequestHandler):
    server_version = "overflow/2.0"
    COOKIE = "ovs"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)

    # ---- who is asking ------------------------------------------------
    def _client_ip(self):
        # The last address is the one Railway's proxy added; anything
        # before it came from the client and could be anything.
        fwd = self.headers.get("X-Forwarded-For", "")
        return fwd.split(",")[-1].strip() if fwd else self.client_address[0]

    def _token(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == self.COOKIE:
                return v
        return None

    def _user(self):
        token = self._token()
        if not token:
            return None
        conn = auth.connect(auth_path())
        try:
            return auth.session_user(conn, token)
        finally:
            conn.close()

    def _cookie(self, token, max_age):
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "") == "https" else ""
        return (f"{self.COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; "
                f"Max-Age={max_age}{secure}")

    # ---- responses ----------------------------------------------------
    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        user = current_user()
        if b"<!--__TABS__-->" in body:
            body = body.replace(b"<!--__TABS__-->",
                                page_tabs(getattr(CURRENT, "path", "")).encode(), 1)
        cookie = getattr(CURRENT, "range_cookie", None)
        if cookie:
            headers = {**(headers or {}), "Set-Cookie": cookie}
            CURRENT.range_cookie = None
        if user and ctype.startswith("text/html") and b"<body" in body[:20000]:
            body = re.sub(rb"<body([^>]*)>", lambda m: m.group(0) + user_bar(user).encode(),
                          body, count=1)
        # The dashboard pages carry their data inline - up to a megabyte
        # on the Customers page - and it compresses about tenfold.
        accepts = self.headers.get("Accept-Encoding", "") if self.headers else ""
        compress = len(body) > 2048 and "gzip" in accepts
        if compress:
            body = gzip.compress(body, compresslevel=6)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if compress:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if user:
            self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location, headers=None):
        return self._send(303, b"", "text/plain", {"Location": location, **(headers or {})})

    def _forbidden(self):
        return self._send(403, page("Not available", "<h1>Not available to your login</h1>"
                                    '<p class="sub">Ask an admin if you need it.</p>'
                                    '<p><a href="/">Dashboard</a></p>'))

    def _not_configured(self):
        return self._send(503, page("Not configured",
            "<h1>Not configured</h1><p class='sub'>Set <code>APP_PASSWORD</code> in Railway "
            "&rarr; Variables. A Railway URL is public, so nothing is served without one.</p>"))

    # ---- GET ------------------------------------------------------------
    def _cookie_value(self, name):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return urllib.parse.unquote(v)
        return None

    def do_GET(self):                                # noqa: N802
        CURRENT.user = CURRENT.range = CURRENT.range_cookie = CURRENT.day = None
        store.set_window()
        path, _, query = self.path.partition("?")
        CURRENT.path = path
        params = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}

        if path == "/healthz":
            return self._send(200, b"ok", "text/plain; charset=utf-8")
        if not APP_PASSWORD:
            return self._not_configured()
        user = self._user()
        if path == "/login":
            if user:
                return self._redirect(safe_next(params.get("next")))
            return self._send(200, login_page(safe_next(params.get("next"))))
        if not user:
            return self._redirect("/login?next=" + urllib.parse.quote(safe_next(self.path)))
        CURRENT.user = user
        if path.startswith("/admin") or path == "/reset":
            allowed = user["role"] == "admin"
        else:
            allowed = auth.can_view(user["role"], path)
        if not allowed:
            return self._forbidden()
        try:
            conn = auth.connect(auth_path())
            try:
                auth.record_view(conn, user["username"], path)
            finally:
                conn.close()
        except Exception as exc:                     # noqa: BLE001
            print(f"activity not recorded: {exc}", flush=True)

        # The range is remembered across pages: one picked on Affiliates
        # holds on Lenders, Customers and the rest until another is picked.
        days = DEFAULT_DAYS
        if path in RANGE_PAGES:
            rng, picked = resolve_range(params, self._cookie_value(RANGE_COOKIE))
            CURRENT.range, days = rng, rng["days"]
            store.set_window(rng["start"], rng["end"], rng["label"], rng["phrase"], rng["days"])
            if picked:
                CURRENT.range_cookie = (f"{RANGE_COOKIE}={urllib.parse.quote(rng['key'])}; Path=/; "
                                        "SameSite=Lax; Max-Age=31536000")
        try:
            if path == "/":
                # Side by side by default: both populations on one page,
                # never added together.
                source = params.get("source", "both")
                if source not in ("both", "external", "internal"):
                    source = "both"
                return self._send(200, dashboard_page(days, source))
            if path == "/lenders":
                return self._send(200, lenders_page(days))
            if path in ("/today", "/yesterday"):
                source = params.get("source", "all")
                if source not in dash.TODAY_SOURCES:
                    source = "all"
                if path == "/today" and params.get("refresh") == "1":
                    # A live read of the public API: costs nothing to
                    # repeat and changes nothing stored for a past day,
                    # so any signed-in user may ask for one.
                    collector.live_in_background(DB_PATH)
                    return self._redirect(f"/today?source={source}")
                return self._send(200, today_page(path[1:], source))
            if path in ("/day", "/days"):
                source = params.get("source", "all")
                if source not in dash.TODAY_SOURCES:
                    source = "all"
                # The day's figures are the ping log's, not the window's.
                store.set_window()
                if path == "/day":
                    day = resolve_day(params).isoformat()
                    CURRENT.day = day
                    return self._send(200, period_page(day, day, source, "day"))
                start, end = period_span(CURRENT.range)
                return self._send(200, period_page(start, end, source, "days"))
            if path == "/customers":
                return self._send(200, customers_page(days))
            if path == "/collection":
                return self._send(200, collection_page())
            if path == "/affiliates":
                return self._send(200, affiliates_page(days))
            if path == "/scorecard":
                return self._send(200, scorecard_page(days))
            if path == "/pings":
                return self._send(200, pings_page(days))
            if path == "/quality":
                return self._send(200, quality_page(days))
            if path == "/explore":
                return self._send(200, explore_page())
            if path == "/verify":
                return self._send(200, verify_page(5))
            if path == "/reset":
                return self._send(200, page("Reset", confirm_reset()))
            if path == "/admin":
                return self._send(200, admin_page())
            if path == "/admin/activity":
                return self._send(200, admin_activity_page(params.get("u", "")))
            if path.startswith("/shape/"):
                body = shape_page(int(path.rsplit("/", 1)[1]))
                return self._send(200, body) if body else self._send(404, page("Not found", "<h1>Not found</h1>"))
        except Exception:                            # noqa: BLE001
            return self._send(500, page("Error", f"<h1>Error</h1><pre class='mono'>"
                                                 f"{html.escape(traceback.format_exc())}</pre>"))
        return self._send(404, page("Not found", '<h1>Not found</h1><p><a href="/">home</a></p>'))

    # ---- POST -----------------------------------------------------------
    def do_POST(self):                               # noqa: N802
        CURRENT.user = None
        path = self.path.partition("?")[0]
        try:
            length = min(int(self.headers.get("Content-Length", "0") or 0), 16384)
        except ValueError:
            length = 0
        form = {k: v[0] for k, v in urllib.parse.parse_qs(
            self.rfile.read(length).decode("utf-8", "replace")).items()}
        if not APP_PASSWORD:
            return self._not_configured()

        if path == "/login":
            return self._login(form)

        user = self._user()
        if not user:
            return self._redirect("/login")
        # Every action carries the session's form token: a page on
        # another site can make a browser send the cookie, not the token.
        if not hmac.compare_digest(form.get("csrf", ""), user["csrf"]):
            return self._send(403, page("Expired", "<h1>That form had expired</h1>"
                                        '<p class="sub">Go back, reload the page and try again.</p>'))
        CURRENT.user = user

        if path == "/logout":
            conn = auth.connect(auth_path())
            try:
                auth.end_session(conn, self._token())
            finally:
                conn.close()
            return self._redirect("/login", {"Set-Cookie": self._cookie("", 0)})

        if user["role"] != "admin":
            return self._forbidden()
        try:
            if path == "/collect":
                try:
                    days = max(1, min(365, int(form.get("days", collector.DAYS_PER_WAKE))))
                except ValueError:
                    days = collector.DAYS_PER_WAKE
                collect_in_background(days)
                return self._redirect("/collection")
            if path == "/reset":
                conn = store.connect(DB_PATH)
                collector.reset(conn)
                conn.close()
                return self._redirect("/collection")
            if path == "/explore/run":
                explore_run()
                return self._redirect("/explore")
            if path.startswith("/admin/"):
                return self._admin_action(path, form, user)
        except Exception:                            # noqa: BLE001
            return self._send(500, page("Error", f"<h1>Error</h1><pre class='mono'>"
                                                 f"{html.escape(traceback.format_exc())}</pre>"))
        return self._send(404, page("Not found", "<h1>Not found</h1>"))

    def _login(self, form):
        username = (form.get("username") or "").strip().lower()
        password = form.get("password") or ""
        next_path = safe_next(form.get("next"))
        ip, agent = self._client_ip(), self.headers.get("User-Agent", "")
        conn = auth.connect(auth_path())
        try:
            if auth.locked_out(conn, ip):
                auth.record_login(conn, username, False, None, ip, agent)
                return self._send(429, login_page(next_path, "Too many failed sign-ins. Wait "
                                                  f"{auth.LOCKOUT_MINUTES} minutes and try again."))
            role = auth.check_password(conn, username, password, APP_PASSWORD)
            auth.record_login(conn, username, bool(role), role, ip, agent)
            if not role:
                return self._send(401, login_page(next_path, "That username and password do not match."))
            token, _csrf = auth.start_session(conn, username)
            auth.prune(conn)
        finally:
            conn.close()
        return self._redirect(next_path, {"Set-Cookie": self._cookie(token, auth.SESSION_DAYS * 86400)})

    def _admin_action(self, path, form, user):
        target = (form.get("username") or "").strip().lower()
        if target == user["username"] and path != "/admin/create":
            # An admin cannot lock themselves out from here.
            return self._send(400, admin_page('<div class="card bad">You cannot change your own '
                                              'login here.</div>'))
        conn = auth.connect(auth_path())
        notice = ""
        try:
            if path == "/admin/create":
                try:
                    pw = auth.create_user(conn, target, form.get("role", "viewer"), user["username"])
                    notice = self._cred_box(auth.clean_username(target), pw, "one-time password")
                except ValueError as exc:
                    notice = f'<div class="card bad">{html.escape(str(exc))}</div>'
            elif path == "/admin/reset":
                try:
                    notice = self._cred_box(target, auth.reset_password(conn, target), "new password")
                except ValueError as exc:
                    notice = f'<div class="card bad">{html.escape(str(exc))}</div>'
            elif path == "/admin/role":
                auth.set_role(conn, target, form.get("role", ""))
            elif path == "/admin/active":
                auth.set_active(conn, target, form.get("active") == "1")
            elif path == "/admin/delete":
                auth.delete_user(conn, target)
            else:
                return self._send(404, page("Not found", "<h1>Not found</h1>"))
        finally:
            conn.close()
        if notice:
            # The password is in this response and nowhere else, so it is
            # rendered here rather than redirected to.
            return self._send(200, admin_page(notice))
        return self._redirect("/admin")

    @staticmethod
    def _cred_box(username, password, label):
        return (f'<div class="cred"><b>{html.escape(username)}</b> — {label} (copy it now; it is '
                f'shown once): <code>{html.escape(password)}</code></div>')


if __name__ == "__main__":
    store.connect(DB_PATH).close()
    auth.connect(auth_path()).close()
    collector.start(DB_PATH)
    print(f"listening on 0.0.0.0:{PORT}, db at {DB_PATH}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
