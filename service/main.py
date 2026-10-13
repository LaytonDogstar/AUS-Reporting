"""Overflow reporting service.

Calls the PublicReports API and shows what each report returns.

Standard library only - no pip, no requirements. Runs on Railway, which
can reach admin.overflowhub.com.au because it is a public hostname. It
cannot reach the SQL database: port 1433 is firewalled to allow-listed
addresses, so that half still needs a collector inside the network.

Raw responses are never written to disk. The shape of each response -
field names, types, and numeric values, with every string reduced to a
type and a length - is what gets stored. A name, an email or an address
would appear in a string, and none of that belongs outside Azure. The
numbers are what answer the questions we have.
"""

from __future__ import annotations

import base64
import hmac
import html
import json
import os
import re
import sqlite3
import ssl
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_BASE = os.environ.get("OVERFLOW_API_BASE", "https://admin.overflowhub.com.au").rstrip("/")
API_USER = os.environ.get("OVERFLOW_API_USER", "")
API_PASSWORD = os.environ.get("OVERFLOW_API_PASSWORD", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
PORT = int(os.environ.get("PORT", "8080"))
TIMEOUT = int(os.environ.get("API_TIMEOUT", "120"))

# Railway volumes mount at /data. Without one the file is ephemeral,
# which is fine: everything here can be rebuilt by running again.
DB_PATH = os.environ.get("DB_PATH") or ("/data/overflow.db" if os.path.isdir("/data") else "overflow.db")

GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
DATEISH = re.compile(r"^\d{4}-\d{2}-\d{2}")


# ---------------------------------------------------------------- storage

def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS call (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ran_at TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            method TEXT NOT NULL,
            url TEXT NOT NULL,
            status INTEGER,
            bytes INTEGER,
            ms INTEGER,
            error TEXT
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shape (
            call_id INTEGER NOT NULL REFERENCES call(id),
            ordinal INTEGER NOT NULL,
            path TEXT NOT NULL,
            kind TEXT NOT NULL,
            detail TEXT
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS shape_call ON shape(call_id, ordinal)")
    return conn


# ---------------------------------------------------------------- shaping

def shape(node, path="$", depth=0, out=None):
    """Structure only. Numbers survive; strings are reduced to a length."""
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
    else:
        out.append((path, type(node).__name__, ""))
    return out


# ------------------------------------------------------------------ calls

def endpoints(days: int):
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    fmt = "%Y-%m-%dT00:00:00"
    rng = f"startDate={start.strftime(fmt)}&endDate={end.strftime(fmt)}"
    body = {
        "StartDate": start.strftime(fmt),
        "EndDate": end.strftime(fmt),
        "CompareStartDate": (start - timedelta(days=days)).strftime(fmt),
        "CompareEndDate": start.strftime(fmt),
        "IsAllActiveOnly": False,
        "ExcludeInternalSources": False,
    }
    return [
        ("Affiliates", "GET", f"{API_BASE}/api/PublicReports/Affiliates", None),
        ("LenderTiers", "GET", f"{API_BASE}/api/PublicReports/LenderTiers", None),
        ("LenderLeadCountReport", "GET", f"{API_BASE}/api/PublicReports/LenderLeadCountReport?{rng}", None),
        ("LeadQualityReport", "GET", f"{API_BASE}/api/PublicReports/LeadQualityReport?{rng}", None),
        ("AffiliateLeadCountReport", "POST", f"{API_BASE}/api/PublicReports/AffiliateLeadCountReport", body),
        ("InternalAffiliateLeadCountReport", "POST", f"{API_BASE}/api/PublicReports/InternalAffiliateLeadCountReport",
         {k: v for k, v in body.items() if k != "ExcludeInternalSources"}),
    ]


def fetch(url, method="GET", body=None, authenticate=True):
    req = urllib.request.Request(url, method=method)
    req.add_header("Accept", "application/json")
    if authenticate and API_USER:
        token = base64.b64encode(f"{API_USER}:{API_PASSWORD}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, data=data, timeout=TIMEOUT,
                                    context=ssl.create_default_context()) as resp:
            payload = resp.read()
            return resp.status, payload, int((time.monotonic() - started) * 1000), None
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), int((time.monotonic() - started) * 1000), None
    except Exception as exc:                       # noqa: BLE001 - surfaced to the page
        return None, b"", int((time.monotonic() - started) * 1000), f"{type(exc).__name__}: {exc}"


def anonymous_check():
    """Does a controller named PublicReport actually require credentials?

    Only meaningful before any are sent, so this call carries none.
    """
    status, _, ms, error = fetch(f"{API_BASE}/api/PublicReports/Affiliates", authenticate=False)
    return status, ms, error


def run_all(days: int):
    conn = db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results = []
    with conn:
        for name, method, url, body in endpoints(days):
            status, payload, ms, error = fetch(url, method, body)
            rows = []
            if payload and not error:
                try:
                    rows = shape(json.loads(payload.decode("utf-8", "replace")))
                except json.JSONDecodeError:
                    head = payload[:200].decode("utf-8", "replace")
                    rows = [("$", "not-json", f"{len(payload):,} bytes starting {head[:60]!r}")]
            cur = conn.execute(
                "INSERT INTO call (ran_at, endpoint, method, url, status, bytes, ms, error)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (now, name, method, url.split("?")[0], status, len(payload), ms, error))
            call_id = cur.lastrowid
            conn.executemany(
                "INSERT INTO shape (call_id, ordinal, path, kind, detail) VALUES (?,?,?,?,?)",
                [(call_id, i, p, k, d) for i, (p, k, d) in enumerate(rows)])
            results.append((name, status, len(payload), ms, error, len(rows)))
            # payload goes out of scope here and is never written to disk
    conn.close()
    return results


# ------------------------------------------------------------------- page

CSS = """
:root{--bg:#fbfbfa;--fg:#1a1a1a;--muted:#5b6169;--line:#e3e5e8;--card:#fff;--accent:#1f4e79;
--ok:#1a7f4b;--warn:#9a6700;--bad:#b42318}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#15171a;--fg:#e9eaec;
--muted:#9aa1ab;--line:#2b2f35;--card:#1c1f23;--accent:#7fb2e5;--ok:#4ac98a;--warn:#e3b341;--bad:#ff7b72}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,BlinkMacSystemFont,
"Segoe UI",Roboto,sans-serif;padding:0 16px}
.wrap{max-width:900px;margin:0 auto;padding:32px 0 64px}
h1{font-size:24px;margin:0 0 4px} h2{font-size:17px;margin:28px 0 10px;color:var(--accent)}
.sub{color:var(--muted);margin:0 0 24px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:12px 0}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;font-weight:600;padding:7px 8px;border-bottom:1px solid var(--line);color:var(--muted)}
td{padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
a.btn{display:inline-block;background:var(--accent);color:#fff;text-decoration:none;padding:9px 16px;
border-radius:7px;font-weight:600;font-size:14px}
.note{color:var(--muted);font-size:13.5px}
"""


def page(title, body):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{CSS}</style></head>
<body><div class="wrap">{body}</div></body></html>""".encode()


def home(days: int):
    conn = db()
    latest = conn.execute("SELECT MAX(ran_at) FROM call").fetchone()[0]
    rows = conn.execute(
        "SELECT id, endpoint, method, status, bytes, ms, error FROM call"
        " WHERE ran_at = ? ORDER BY id", (latest,)).fetchall() if latest else []
    conn.close()

    configured = bool(API_USER and API_PASSWORD)
    parts = ["<h1>Overflow reporting</h1>",
             '<p class="sub">PublicReports API. Raw responses are never stored — '
             'only field names, types and numbers.</p>']

    if not configured:
        parts.append('<div class="card"><strong class="bad">Not configured.</strong> '
                     '<span class="note">Set <code>OVERFLOW_API_USER</code> and '
                     '<code>OVERFLOW_API_PASSWORD</code> in Railway → Variables, then redeploy.</span></div>')

    status, ms, error = anonymous_check()
    if error:
        verdict, cls = f"could not reach the API ({error})", "warn"
    elif status == 200:
        verdict, cls = ("answered 200 WITHOUT credentials — anyone with the URL can read it. "
                        "Raise this before anything else."), "bad"
    elif status in (401, 403):
        verdict, cls = f"returned {status}, so credentials are required. That is what we want.", "ok"
    else:
        verdict, cls = f"returned {status}; inconclusive.", "warn"
    parts.append(f'<h2>Is the API public?</h2><div class="card"><span class="{cls}">'
                 f'An anonymous call to <code>/api/PublicReports/Affiliates</code> {html.escape(verdict)}'
                 f'</span> <span class="note">({ms} ms)</span></div>')

    parts.append(f'<h2>Reports</h2><div class="card">'
                 f'<a class="btn" href="/run?days={days}">Call all six endpoints</a>'
                 f'<span class="note" style="margin-left:12px">window: last {days} days '
                 f'&middot; <a href="/?days=30">30</a> &middot; <a href="/?days=90">90</a></span></div>')

    if rows:
        parts.append(f'<p class="note">Last run {html.escape(latest)} UTC</p><div class="card"><table>'
                     "<tr><th>Endpoint</th><th>Status</th><th>Size</th><th>Time</th><th>Fields</th></tr>")
        conn = db()
        for cid, name, method, st, size, ms_, err in rows:
            n = conn.execute("SELECT COUNT(*) FROM shape WHERE call_id=?", (cid,)).fetchone()[0]
            cls = "ok" if st == 200 else "bad"
            cell = html.escape(err) if err else f'<span class="{cls}">{st}</span>'
            parts.append(f"<tr><td><a href='/shape/{cid}'><code>{html.escape(name)}</code></a>"
                         f"<br><span class='note'>{method}</span></td><td>{cell}</td>"
                         f"<td>{size:,} B</td><td>{ms_:,} ms</td><td>{n:,}</td></tr>")
        conn.close()
        parts.append("</table></div>")
    else:
        parts.append('<p class="note">Nothing run yet.</p>')

    parts.append('<h2>What this cannot do</h2><div class="card"><p class="note">'
                 'The SQL database is firewalled to allow-listed addresses, so nothing hosted '
                 'here can reach it. The funnel, the affordability indicators, campaign '
                 'attribution and both cost columns all live there and still need a collector '
                 'running inside the network.</p></div>')
    return page("Overflow reporting", "".join(parts))


def shape_page(call_id: int):
    conn = db()
    call = conn.execute("SELECT endpoint, method, url, status, bytes, ms, ran_at, error"
                        " FROM call WHERE id=?", (call_id,)).fetchone()
    if not call:
        conn.close()
        return None
    rows = conn.execute("SELECT path, kind, detail FROM shape WHERE call_id=? ORDER BY ordinal",
                        (call_id,)).fetchall()
    conn.close()
    name, method, url, status, size, ms, ran_at, error = call
    body = [f"<h1>{html.escape(name)}</h1>",
            f'<p class="sub"><code>{html.escape(method)} {html.escape(url)}</code><br>'
            f'{status} &middot; {size:,} bytes &middot; {ms:,} ms &middot; {html.escape(ran_at)} UTC</p>',
            '<p><a href="/">&larr; back</a></p>']
    if error:
        body.append(f'<div class="card bad">{html.escape(error)}</div>')
    body.append('<div class="card"><table><tr><th>Path</th><th>Type</th><th>Value</th></tr>')
    for path, kind, detail in rows:
        v = html.escape(detail or "")
        if kind in ("string", "guid", "date-like"):
            v = f'<span class="note">{v} — withheld</span>'
        body.append(f"<tr><td class='mono'>{html.escape(path)}</td>"
                    f"<td class='note'>{html.escape(kind)}</td><td class='mono'>{v}</td></tr>")
    body.append("</table></div>")
    return page(name, "".join(body))


# ----------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    server_version = "overflow/1.0"

    def log_message(self, fmt, *args):      # quieter, and never logs headers
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def _authorised(self):
        if not APP_PASSWORD:
            return False
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            _, _, supplied = base64.b64decode(header[6:]).decode().partition(":")
        except Exception:                   # noqa: BLE001
            return False
        return hmac.compare_digest(supplied, APP_PASSWORD)

    def _send(self, code, body, content_type="text/html; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                       # noqa: N802
        path, _, query = self.path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)

        if path == "/healthz":
            return self._send(200, b"ok", "text/plain; charset=utf-8")

        if not APP_PASSWORD:
            return self._send(503, page("Not configured",
                "<h1>Not configured</h1><p class='sub'>Set <code>APP_PASSWORD</code> in Railway "
                "&rarr; Variables. The service refuses to serve anything until it has one, "
                "because a Railway URL is public.</p>"))

        if not self._authorised():
            return self._send(401, b"Authentication required",
                              "text/plain; charset=utf-8",
                              {"WWW-Authenticate": 'Basic realm="Overflow reporting"'})

        try:
            days = max(1, min(365, int(params.get("days", "7"))))
        except ValueError:
            days = 7

        try:
            if path == "/":
                return self._send(200, home(days))
            if path == "/run":
                run_all(days)
                return self._send(303, b"", "text/plain", {"Location": "/"})
            if path.startswith("/shape/"):
                body = shape_page(int(path.rsplit("/", 1)[1]))
                if body is None:
                    return self._send(404, page("Not found", "<h1>Not found</h1>"))
                return self._send(200, body)
        except Exception:                   # noqa: BLE001
            detail = html.escape(traceback.format_exc())
            return self._send(500, page("Error", f"<h1>Error</h1><pre class='mono'>{detail}</pre>"))

        return self._send(404, page("Not found", '<h1>Not found</h1><p><a href="/">home</a></p>'))


if __name__ == "__main__":
    db().close()
    print(f"listening on 0.0.0.0:{PORT}, db at {DB_PATH}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
