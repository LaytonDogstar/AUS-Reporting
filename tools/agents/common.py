"""Shared by the agent scripts: reaching the dashboard and posting a report.

The agents run in fresh cloud sessions, away from the Railway volume, so
they read the dashboard over HTTPS with the agent token, as a person
would with a login. The token and the dashboard's address come from the
environment:

    AGENT_TOKEN          the bearer token, also set in Railway
    AUS_DASHBOARD_URL    defaults to the production Railway address

Standard library only, like the service.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVICE = REPO / "service"
if str(SERVICE) not in sys.path:
    sys.path.insert(0, str(SERVICE))

BASE = os.environ.get("AUS_DASHBOARD_URL", "https://aus-reporting-production.up.railway.app").rstrip("/")
TOKEN = os.environ.get("AGENT_TOKEN", "")
TIMEOUT = 180


def _ctx():
    bundle = os.environ.get("SSL_CERT_FILE") or "/root/.ccr/ca-bundle.crt"
    return ssl.create_default_context(cafile=bundle) if os.path.exists(bundle) else ssl.create_default_context()


def need_token():
    if len(TOKEN) < 32:
        sys.exit("AGENT_TOKEN is not set (or too short) in this environment. Add it in the "
                 "environment settings and in Railway -> Variables, the same value in both.")


def request(method, path, body=None, attempts=3):
    """(status, bytes, seconds). Retries a dropped connection, not an answer."""
    need_token()
    headers = {"Authorization": f"Bearer {TOKEN}", "User-Agent": "aus-reporting-agent"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    last = None
    for attempt in range(attempts):
        started = time.monotonic()
        req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx()) as r:
                return r.status, r.read(), time.monotonic() - started
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), time.monotonic() - started
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{method} {path}: {last}")


def get_json(path):
    status, body, _ = request("GET", path)
    if status != 200:
        raise RuntimeError(f"GET {path} -> HTTP {status}: {body[:300]!r}")
    return json.loads(body)


def post_report(agent, kind, status, title, summary="", body=""):
    """Post to the dashboard's Agent reports. Returns the report's URL."""
    code, resp, _ = request("POST", "/api/agent/report",
                            {"agent": agent, "kind": kind, "status": status, "title": title,
                             "summary": summary, "body": body})
    if code != 201:
        raise RuntimeError(f"report not posted: HTTP {code} {resp[:300]!r}")
    return BASE + json.loads(resp)["url"]


def pct_gap(a, b):
    """Relative gap between two figures, or None when there is nothing to compare."""
    if a is None or b is None:
        return None
    if not a and not b:
        return 0.0
    base = max(abs(a), abs(b))
    return abs(a - b) / base if base else 0.0
