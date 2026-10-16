"""Logins: users, sessions, the login log and who looked at what.

Modelled on the US dashboard's: an admin creates a user with a role and
the service generates the password, shown once and stored only as a
salted hash. Three roles:

    viewer    the dashboard pages
    analyst   those and the diagnostic pages - collection, ping log,
              window check, API explorer - read-only
    admin     everything, plus collecting, clearing, and managing users

Kept in its own database file beside the collector's, so clearing the
collected data never touches a login. It needs a Railway volume to
survive a deploy, as the collected data does.

`APP_PASSWORD` is a break-glass admin that is never stored: whatever
happens to the user table, it still opens the service.

Standard library only, like the rest of the service.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

ROLES = ("viewer", "analyst", "admin")
ROLE_LABELS = {"viewer": "Viewer", "analyst": "Analyst (read-only, incl. diagnostics)",
               "admin": "Admin"}
BREAK_GLASS = "admin"           # the username APP_PASSWORD signs in as

SESSION_DAYS = 30               # a session ends this long after sign-in
IDLE_DAYS = 7                   # or after this long unused
# Failed sign-ins from one address within the window before it is
# refused. Per address, not per username: a username lockout would let
# anyone lock a real user - or the break-glass admin - out by typing
# wrong passwords at it. Generated passwords are about 92 bits, so it is
# a weak APP_PASSWORD this mostly protects.
LOCKOUT_FAILURES = 10
LOCKOUT_MINUTES = 15
ACTIVITY_KEEP_DAYS = 180

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    pw_salt BLOB NOT NULL,
    pw_hash BLOB NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    created_by TEXT,
    last_login TEXT
);
-- The token itself is never stored, only its hash: a copy of this file
-- does not hand anyone a live session.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    csrf TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_events (
    at TEXT NOT NULL,
    username TEXT,
    ok INTEGER NOT NULL,
    role TEXT,
    ip TEXT,
    user_agent TEXT
);
CREATE INDEX IF NOT EXISTS le_at ON login_events(at);
CREATE TABLE IF NOT EXISTS activity (
    at TEXT NOT NULL,
    username TEXT NOT NULL,
    path TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS act_user ON activity(username, at);
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


# ----------------------------------------------------------- passwords

def _hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


def new_password():
    """Sixteen characters, letters and digits: easy to paste, no symbol
    a chat app or email client will mangle."""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(16))


def clean_username(name):
    name = (name or "").strip().lower()
    if not (2 <= len(name) <= 64) or not all(c.isalnum() or c in "._-@" for c in name):
        raise ValueError("Usernames are 2 to 64 letters, digits or . _ - @.")
    if name == BREAK_GLASS:
        raise ValueError(f"'{BREAK_GLASS}' is reserved for the APP_PASSWORD sign-in.")
    return name


# ---------------------------------------------------------------- users

def create_user(conn, username, role, created_by=None):
    """A new user and its one-time password. Raises ValueError."""
    username = clean_username(username)
    if role not in ROLES:
        raise ValueError("Unknown role.")
    if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        raise ValueError(f"'{username}' already exists.")
    password, salt = new_password(), secrets.token_bytes(16)
    with conn:
        conn.execute("INSERT INTO users (username, role, pw_salt, pw_hash, active, created_at,"
                     " created_by) VALUES (?,?,?,?,1,?,?)",
                     (username, role, salt, _hash(password, salt), stamp(), created_by))
    return password


def reset_password(conn, username):
    """A new one-time password; every session the user had ends."""
    if not conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        raise ValueError("No such user.")
    password, salt = new_password(), secrets.token_bytes(16)
    with conn:
        conn.execute("UPDATE users SET pw_salt=?, pw_hash=? WHERE username=?",
                     (salt, _hash(password, salt), username))
        conn.execute("DELETE FROM sessions WHERE username=?", (username,))
    return password


def set_role(conn, username, role):
    if role not in ROLES:
        raise ValueError("Unknown role.")
    with conn:
        conn.execute("UPDATE users SET role=? WHERE username=?", (role, username))


def set_active(conn, username, active):
    with conn:
        conn.execute("UPDATE users SET active=? WHERE username=?", (1 if active else 0, username))
        if not active:
            conn.execute("DELETE FROM sessions WHERE username=?", (username,))


def delete_user(conn, username):
    with conn:
        conn.execute("DELETE FROM users WHERE username=?", (username,))
        conn.execute("DELETE FROM sessions WHERE username=?", (username,))


def users(conn):
    rows = conn.execute(
        "SELECT u.username, u.role, u.active, u.created_at, u.created_by, u.last_login,"
        " (SELECT COUNT(*) FROM login_events e WHERE e.username=u.username AND e.ok=1),"
        " (SELECT MAX(at) FROM activity a WHERE a.username=u.username)"
        " FROM users u ORDER BY u.username").fetchall()
    return [dict(zip(("username", "role", "active", "created_at", "created_by", "last_login",
                      "logins", "last_seen"), r)) for r in rows]


# ------------------------------------------------------------- sign-in

def locked_out(conn, ip):
    since = stamp(now() - timedelta(minutes=LOCKOUT_MINUTES))
    n = conn.execute("SELECT COUNT(*) FROM login_events WHERE ok=0 AND at>=? AND ip=?",
                     (since, ip)).fetchone()[0]
    return n >= LOCKOUT_FAILURES


def check_password(conn, username, password, app_password=""):
    """The role if the password is right, else None.

    The break-glass admin is checked first and never stored. Every
    comparison is constant-time, and an unknown username still costs a
    hash, so timing does not say which usernames exist.
    """
    username = (username or "").strip().lower()
    if username == BREAK_GLASS:
        ok = bool(app_password) and hmac.compare_digest(password.encode(), app_password.encode())
        return "admin" if ok else None
    row = conn.execute("SELECT role, pw_salt, pw_hash, active FROM users WHERE username=?",
                       (username,)).fetchone()
    if row is None:
        _hash(password, b"\0" * 16)
        return None
    role, salt, stored, active = row
    if not hmac.compare_digest(_hash(password, salt), stored) or not active:
        return None
    return role


def record_login(conn, username, ok, role, ip, user_agent):
    with conn:
        conn.execute("INSERT INTO login_events (at, username, ok, role, ip, user_agent)"
                     " VALUES (?,?,?,?,?,?)",
                     (stamp(), (username or "")[:64], 1 if ok else 0, role, ip,
                      (user_agent or "")[:300]))
        if ok:
            conn.execute("UPDATE users SET last_login=? WHERE username=?", (stamp(), username))


def start_session(conn, username):
    """A new session: (cookie token, csrf token)."""
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    with conn:
        conn.execute("INSERT INTO sessions (token_hash, username, csrf, created_at, last_seen)"
                     " VALUES (?,?,?,?,?)", (_token_hash(token), username, csrf, stamp(), stamp()))
    return token, csrf


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def session_user(conn, token):
    """{username, role, csrf} for a live session, else None. A user who
    was deactivated, deleted or demoted is seen as such straight away:
    the role is read from the user, not from the session."""
    if not token:
        return None
    row = conn.execute("SELECT s.username, s.csrf, s.created_at, s.last_seen, u.role, u.active"
                       " FROM sessions s LEFT JOIN users u ON u.username=s.username"
                       " WHERE s.token_hash=?", (_token_hash(token),)).fetchone()
    if row is None:
        return None
    username, csrf, created, seen, role, active = row
    t = now()
    expired = (t - datetime.fromisoformat(created) > timedelta(days=SESSION_DAYS)
               or t - datetime.fromisoformat(seen) > timedelta(days=IDLE_DAYS))
    if username == BREAK_GLASS:
        role, active = "admin", 1
    if expired or not active or role not in ROLES:
        end_session(conn, token)
        return None
    if t - datetime.fromisoformat(seen) > timedelta(minutes=5):
        with conn:
            conn.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?",
                         (stamp(), _token_hash(token)))
    return {"username": username, "role": role, "csrf": csrf}


def end_session(conn, token):
    with conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))


# ---------------------------------------------------------------- access

VIEWER_PATHS = ("/", "/lenders", "/customers", "/today", "/yesterday", "/day", "/days",
                "/scorecard", "/affiliates", "/quality")
ANALYST_PATHS = ("/collection", "/pings", "/verify", "/explore")
ANALYST_PREFIXES = ("/shape/",)


def can_view(role, path):
    """Whether a role may open a page. Actions are POSTs and admin-only."""
    if role == "admin":
        return True
    if path in VIEWER_PATHS:
        return role in ("viewer", "analyst")
    if path in ANALYST_PATHS or path.startswith(ANALYST_PREFIXES):
        return role == "analyst"
    return False


# -------------------------------------------------------------- activity

def record_view(conn, username, path):
    with conn:
        conn.execute("INSERT INTO activity (at, username, path) VALUES (?,?,?)",
                     (stamp(), username, path[:200]))


def prune(conn):
    with conn:
        conn.execute("DELETE FROM activity WHERE at < ?",
                     (stamp(now() - timedelta(days=ACTIVITY_KEEP_DAYS)),))
        conn.execute("DELETE FROM login_events WHERE at < ?",
                     (stamp(now() - timedelta(days=ACTIVITY_KEEP_DAYS)),))
        conn.execute("DELETE FROM sessions WHERE last_seen < ?",
                     (stamp(now() - timedelta(days=IDLE_DAYS)),))


def login_log(conn, limit=300, ok_only=False):
    rows = conn.execute("SELECT at, username, ok, role, ip, user_agent FROM login_events"
                        + (" WHERE ok=1" if ok_only else "") +
                        " ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(zip(("at", "username", "ok", "role", "ip", "user_agent"), r)) for r in rows]


SESSION_GAP = timedelta(minutes=15)


def sessions_for(conn, username, limit=2000):
    """Page views grouped into sittings: a new one after a 15-minute gap,
    as the US dashboard counts them. Newest first."""
    views = conn.execute("SELECT at, path FROM activity WHERE username=? ORDER BY at DESC"
                         " LIMIT ?", (username, limit)).fetchall()
    out = []
    for at, path in reversed(views):
        t = datetime.fromisoformat(at)
        if not out or t - out[-1]["last"] > SESSION_GAP:
            out.append({"start": t, "last": t, "views": 0, "pages": []})
        s = out[-1]
        s["last"] = t
        s["views"] += 1
        if path not in s["pages"]:
            s["pages"].append(path)
    return list(reversed(out))


def device(user_agent):
    u = (user_agent or "").lower()
    os_ = ("iOS" if "iphone" in u or "ipad" in u else "Android" if "android" in u
           else "Mac" if "mac os" in u else "Windows" if "windows" in u
           else "Linux" if "linux" in u else "")
    br = ("Edge" if "edg/" in u else "Chrome" if "chrome" in u else "Firefox" if "firefox" in u
          else "Safari" if "safari" in u else "")
    return (br + " " + os_).strip() or "other"
