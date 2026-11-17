"""What people and agents keep: the lender mapping and the agents' reports.

Both live in the logins database, beside `auth`'s tables, not in the
collected data: clearing or rebuilding the data must never lose a
mapping someone typed in, or a report an agent wrote.

**Lender mapping.** The API knows a tier's lender; it does not know the
brand a tier sells under, the company behind it, or what kind of buyer
it is. Wallet Wizard and Wizit are separate brands of one company,
and a network that resells leads behaves nothing like a direct lender.
An admin records that here, one row per tier, and every change is
audited.

**Agent reports.** The scheduled agents - the data check, the buyer
briefing, the nightly site test - and the on-demand ones post what they
found here, where Layton and the manager agent read it. A report is
text an agent wrote: it is shown escaped, never as markup.

Standard library only, like the rest of the service.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

LENDER_TYPES = ("direct", "network", "product", "other")
TYPE_LABELS = {"direct": "Direct lender", "network": "Network (resells leads)",
               "product": "Product / subscription", "other": "Other"}

AGENTS = ("data", "buyer", "tester", "ux", "developer", "manager")
AGENT_LABELS = {"data": "Data check", "buyer": "Buyer briefing", "tester": "Site test",
                "ux": "UX review", "developer": "Developer", "manager": "Manager"}
KINDS = ("check", "briefing", "review", "build", "summary")
STATUSES = ("ok", "warn", "fail", "info")

MAX_TITLE = 200
MAX_SUMMARY = 2000
MAX_BODY = 200_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS lender_map (
    lender_tier_id INTEGER PRIMARY KEY,
    brand TEXT,
    company TEXT,
    lender_type TEXT,
    notes TEXT,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);
CREATE TABLE IF NOT EXISTS lender_map_audit (
    at TEXT NOT NULL,
    username TEXT,
    lender_tier_id INTEGER NOT NULL,
    field TEXT NOT NULL,
    before TEXT,
    after TEXT
);
CREATE INDEX IF NOT EXISTS lma_at ON lender_map_audit(at);
CREATE TABLE IF NOT EXISTS agent_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    body TEXT,
    run_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ar_agent ON agent_reports(agent, created_at);
"""


def now():
    return datetime.now(timezone.utc)


def stamp(t=None):
    return (t or now()).isoformat(timespec="seconds")


def connect(path):
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


# --------------------------------------------------------- lender mapping

FIELDS = ("brand", "company", "lender_type", "notes")


def lender_map(conn):
    """{tier_id: {brand, company, lender_type, notes, updated_at, updated_by}}"""
    return {r[0]: {"brand": r[1], "company": r[2], "lender_type": r[3], "notes": r[4],
                   "updated_at": r[5], "updated_by": r[6]}
            for r in conn.execute(
                "SELECT lender_tier_id, brand, company, lender_type, notes, updated_at,"
                " updated_by FROM lender_map")}


def _clean(field, value):
    value = (value or "").strip()
    if field == "lender_type":
        value = value.lower()
        if value and value not in LENDER_TYPES:
            raise ValueError(f"type must be one of {', '.join(LENDER_TYPES)}")
    limit = 1000 if field == "notes" else 120
    return value[:limit] or None


def save_tier(conn, tier_id, values, username):
    """Set some or all of a tier's mapping fields; each change is audited.
    Returns how many fields changed."""
    tier_id = int(tier_id)
    clean = {f: _clean(f, values.get(f)) for f in FIELDS if f in values}
    row = conn.execute("SELECT brand, company, lender_type, notes FROM lender_map"
                       " WHERE lender_tier_id=?", (tier_id,)).fetchone()
    before = dict(zip(FIELDS, row)) if row else dict.fromkeys(FIELDS)
    changed = {f: v for f, v in clean.items() if v != before.get(f)}
    if not changed:
        return 0
    after = {**before, **changed}
    at = stamp()
    with conn:
        conn.execute(
            "INSERT INTO lender_map (lender_tier_id, brand, company, lender_type, notes,"
            " updated_at, updated_by) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(lender_tier_id) DO UPDATE SET brand=excluded.brand,"
            " company=excluded.company, lender_type=excluded.lender_type,"
            " notes=excluded.notes, updated_at=excluded.updated_at,"
            " updated_by=excluded.updated_by",
            (tier_id, after["brand"], after["company"], after["lender_type"], after["notes"],
             at, username))
        conn.executemany(
            "INSERT INTO lender_map_audit (at, username, lender_tier_id, field, before, after)"
            " VALUES (?,?,?,?,?,?)",
            [(at, username, tier_id, f, before.get(f), v) for f, v in changed.items()])
    return len(changed)


def apply_to_tiers(conn, tier_ids, values, username):
    """The same fields on several tiers at once - a lender's every tier.
    Only the fields given are touched."""
    return sum(save_tier(conn, t, values, username) for t in tier_ids)


def audit(conn, limit=100):
    return conn.execute(
        "SELECT at, username, lender_tier_id, field, before, after FROM lender_map_audit"
        " ORDER BY at DESC, rowid DESC LIMIT ?", (limit,)).fetchall()


def resolved(tiers, mapping):
    """Every tier with its mapping filled in: what is recorded, else the
    API's own lender name as the brand. `tiers` is
    [(tier_id, tier_name, lender_id, lender_name, price, is_active)]."""
    out = []
    for tid, tname, lid, lname, price, active in tiers:
        m = mapping.get(tid, {})
        out.append({"tier_id": tid, "tier": tname, "lender_id": lid,
                    "lender": (lname or "").strip() or None, "price": price,
                    "active": bool(active),
                    "brand": m.get("brand") or (lname or "").strip() or tname,
                    "company": m.get("company"), "type": m.get("lender_type"),
                    "notes": m.get("notes"), "mapped": tid in mapping,
                    "updated_at": m.get("updated_at"), "updated_by": m.get("updated_by")})
    return out


def lender_profile(resolved_tiers):
    """{lender_id: {brand, company, type}} - a lender as its tiers are
    mapped: the brand and company most of its tiers carry, and its type
    when every mapped tier agrees."""
    by = {}
    for t in resolved_tiers:
        by.setdefault(t["lender_id"], []).append(t)
    out = {}
    for lid, ts in by.items():
        def common(key):
            vals = [t[key] for t in ts if t[key]]
            return max(set(vals), key=vals.count) if vals else None
        types = {t["type"] for t in ts if t["type"]}
        out[lid] = {"brand": common("brand"), "company": common("company"),
                    "type": types.pop() if len(types) == 1 else None}
    return out


# --------------------------------------------------------- agent reports

def add_report(conn, agent, kind, status, title, summary="", body="", run_at=None):
    agent, kind, status = (agent or "").strip().lower(), (kind or "").strip().lower(), \
        (status or "").strip().lower()
    if agent not in AGENTS:
        raise ValueError(f"agent must be one of {', '.join(AGENTS)}")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    title = (title or "").strip()
    if not title:
        raise ValueError("a report needs a title")
    if len(body or "") > MAX_BODY:
        raise ValueError(f"body over {MAX_BODY:,} characters")
    run_at = (run_at or "").strip()[:40] or stamp()
    with conn:
        cur = conn.execute(
            "INSERT INTO agent_reports (agent, kind, status, title, summary, body, run_at,"
            " created_at) VALUES (?,?,?,?,?,?,?,?)",
            (agent, kind, status, title[:MAX_TITLE], (summary or "")[:MAX_SUMMARY], body or "",
             run_at, stamp()))
    return cur.lastrowid


def _row(r, with_body):
    out = {"id": r[0], "agent": r[1], "kind": r[2], "status": r[3], "title": r[4],
           "summary": r[5], "run_at": r[6], "created_at": r[7]}
    if with_body:
        out["body"] = r[8]
    return out


_COLS = "id, agent, kind, status, title, summary, run_at, created_at, body"


def reports(conn, agent=None, since=None, limit=100, with_body=False):
    sql, args = f"SELECT {_COLS} FROM agent_reports WHERE 1=1", []
    if agent:
        sql += " AND agent=?"
        args.append(agent)
    if since:
        sql += " AND created_at >= ?"
        args.append(since)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    args.append(int(limit))
    return [_row(r, with_body) for r in conn.execute(sql, args)]


def report(conn, report_id):
    r = conn.execute(f"SELECT {_COLS} FROM agent_reports WHERE id=?", (int(report_id),)).fetchone()
    return _row(r, True) if r else None


def latest(conn, agent, kind, max_age_hours=None):
    sql, args = f"SELECT {_COLS} FROM agent_reports WHERE agent=? AND kind=?", [agent, kind]
    if max_age_hours:
        sql += " AND created_at >= ?"
        args.append(stamp(now() - timedelta(hours=max_age_hours)))
    r = conn.execute(sql + " ORDER BY created_at DESC, id DESC LIMIT 1", args).fetchone()
    return _row(r, True) if r else None
