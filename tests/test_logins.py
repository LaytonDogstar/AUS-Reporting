"""Logins and access, end to end against the real request handler.

Three roles, as on the US dashboard: viewer, analyst, admin. Every page
needs a session; every action is a POST that carries the session's form
token; and APP_PASSWORD signs in as a break-glass admin that is never
stored.
"""

import http.client
import re
import sqlite3
import sys
import tempfile
import threading
import unittest
import unittest.mock
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent / "service"
if str(SERVICE) not in sys.path:
    sys.path.insert(0, str(SERVICE))

import auth                                                # noqa: E402
import main                                                # noqa: E402
import store                                               # noqa: E402

APP_PASSWORD = "break-glass-secret"


class Browser:
    """A client that keeps its cookie and does not follow redirects."""

    def __init__(self, port):
        self.port, self.cookies = port, {}

    @property
    def cookie(self):
        """The session cookie's value, or None once signed out."""
        return self.cookies.get(main.Handler.COOKIE)

    def request(self, method, path, form=None, ip=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {}
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if ip:
            headers["X-Forwarded-For"] = ip
        body = None
        if form is not None:
            body = urllib.parse.urlencode(form)
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request(method, path, body=body, headers=headers)
        r = conn.getresponse()
        text = r.read().decode("utf-8", "replace")
        for header, value in r.getheaders():
            if header.lower() == "set-cookie":
                name, _, v = value.split(";")[0].partition("=")
                if v:
                    self.cookies[name] = v
                else:
                    self.cookies.pop(name, None)
        conn.close()
        return r.status, r.getheader("Location"), text

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, form=None, ip=None):
        return self.request("POST", path, form or {}, ip)

    def login(self, username, password, ip=None):
        return self.post("/login", {"username": username, "password": password, "next": "/"}, ip)

    def csrf(self):
        _, _, text = self.get("/today")
        return re.search(r'name="csrf" value="([^"]+)"', text).group(1)


class Server(unittest.TestCase):
    """The real handler on a free port, with an empty database."""

    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        main.DB_PATH = str(tmp / "o.db")
        main.APP_PASSWORD = APP_PASSWORD
        store.connect(main.DB_PATH).close()
        # The Collection page calls the live API without credentials to
        # check it is closed; these tests are about access, not that.
        patcher = unittest.mock.patch.object(main.collector, "anonymous_check",
                                             return_value=(401, 1, None))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), main.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def browser(self):
        return Browser(self.port)

    def admin(self):
        b = self.browser()
        status, _, _ = b.login("admin", APP_PASSWORD)
        self.assertEqual(status, 303)
        return b



class Logins(Server):
    def make_user(self, role):
        a = self.admin()
        status, _, text = a.post("/admin/create", {"csrf": a.csrf(), "username": f"sam-{role}",
                                                   "role": role})
        self.assertEqual(status, 200)
        password = re.search(r"<code>([A-Za-z0-9]{16})</code>", text).group(1)
        b = self.browser()
        self.assertEqual(b.login(f"sam-{role}", password)[0], 303)
        return b, password

    # ---- signing in ------------------------------------------------------
    def test_every_page_needs_a_login(self):
        for path in ("/", "/today", "/lenders", "/collection", "/admin"):
            status, location, _ = self.browser().get(path)
            self.assertEqual(status, 303, path)
            self.assertTrue(location.startswith("/login?next="), location)

    def test_healthz_stays_open(self):
        self.assertEqual(self.browser().get("/healthz")[0], 200)

    def test_app_password_is_a_break_glass_admin(self):
        b = self.admin()
        status, _, text = b.get("/admin")
        self.assertEqual(status, 200)
        self.assertIn("admin · admin", text)

    def test_a_wrong_password_is_refused_and_logged(self):
        b = self.browser()
        status, _, text = b.login("admin", "nope")
        self.assertEqual(status, 401)
        self.assertIn("do not match", text)
        self.assertIsNone(b.cookie)
        conn = auth.connect(main.auth_path())
        self.addCleanup(conn.close)
        self.assertEqual(auth.login_log(conn)[0]["ok"], 0)

    def test_repeated_failures_lock_the_address_out(self):
        b = self.browser()
        for _ in range(auth.LOCKOUT_FAILURES):
            b.login("admin", "wrong", ip="203.0.113.9")
        status, _, text = b.login("admin", APP_PASSWORD, ip="203.0.113.9")
        self.assertEqual(status, 429)
        self.assertIn("Too many failed sign-ins", text)

    def test_someone_elses_failures_do_not_lock_a_user_out(self):
        b = self.browser()
        for _ in range(auth.LOCKOUT_FAILURES):
            b.login("admin", "wrong", ip="203.0.113.9")
        self.assertEqual(self.browser().login("admin", APP_PASSWORD, ip="198.51.100.4")[0], 303)

    def test_next_never_leaves_the_site(self):
        b = self.browser()
        _, location, _ = b.post("/login", {"username": "admin", "password": APP_PASSWORD,
                                           "next": "//evil.example/x"})
        self.assertEqual(location, "/")

    def test_sign_out_ends_the_session(self):
        b = self.admin()
        b.post("/logout", {"csrf": b.csrf()})
        self.assertEqual(b.get("/today")[0], 303)

    # ---- roles -------------------------------------------------------------
    def test_a_viewer_sees_the_dashboard_and_nothing_else(self):
        b, _ = self.make_user("viewer")
        for path in ("/", "/lenders", "/customers", "/today", "/yesterday", "/day", "/days"):
            self.assertEqual(b.get(path)[0], 200, path)
        for path in ("/collection", "/pings", "/explore", "/admin", "/reset"):
            self.assertEqual(b.get(path)[0], 403, path)
        self.assertEqual(b.post("/collect", {"csrf": b.csrf()})[0], 403)

    def test_an_analyst_sees_diagnostics_but_cannot_act(self):
        b, _ = self.make_user("analyst")
        self.assertEqual(b.get("/collection")[0], 200)
        self.assertNotIn('action="/collect"', b.get("/collection")[2])
        self.assertEqual(b.get("/admin")[0], 403)
        self.assertEqual(b.post("/reset", {"csrf": b.csrf()})[0], 403)

    def test_an_admin_sees_the_actions_as_forms(self):
        text = self.admin().get("/collection")[2]
        self.assertIn('<form method="post" action="/collect"', text)
        self.assertNotIn('href="/collect', text)

    def test_an_action_without_the_form_token_is_refused(self):
        b = self.admin()
        self.assertEqual(b.post("/reset", {})[0], 403)
        self.assertEqual(b.post("/reset", {"csrf": "forged"})[0], 403)

    def test_actions_are_not_reachable_by_get(self):
        b = self.admin()
        self.assertEqual(b.get("/collect")[0], 404)
        self.assertEqual(b.get("/explore/run")[0], 404)

    # ---- managing users ------------------------------------------------------
    def test_the_password_is_stored_only_as_a_salted_hash(self):
        _, password = self.make_user("viewer")
        raw = sqlite3.connect(main.auth_path()).execute(
            "SELECT pw_hash, pw_salt FROM users").fetchone()
        self.assertNotIn(password.encode(), raw[0])
        self.assertEqual(len(raw[1]), 16)
        self.assertNotIn(password, Path(main.auth_path()).read_bytes().decode("latin-1"))

    def test_deactivating_a_user_ends_their_session_at_once(self):
        b, _ = self.make_user("viewer")
        a = self.admin()
        a.post("/admin/active", {"csrf": a.csrf(), "username": "sam-viewer", "active": "0"})
        self.assertEqual(b.get("/today")[0], 303)

    def test_a_demotion_applies_to_a_live_session(self):
        b, _ = self.make_user("analyst")
        a = self.admin()
        a.post("/admin/role", {"csrf": a.csrf(), "username": "sam-analyst", "role": "viewer"})
        self.assertEqual(b.get("/collection")[0], 403)

    def test_a_reset_password_replaces_the_old_one(self):
        b, old = self.make_user("viewer")
        a = self.admin()
        status, _, text = a.post("/admin/reset", {"csrf": a.csrf(), "username": "sam-viewer"})
        new = re.search(r"<code>([A-Za-z0-9]{16})</code>", text).group(1)
        self.assertEqual(b.get("/today")[0], 303)                 # old session ended
        self.assertEqual(self.browser().login("sam-viewer", old)[0], 401)
        self.assertEqual(self.browser().login("sam-viewer", new)[0], 303)

    def test_the_break_glass_name_cannot_be_taken(self):
        a = self.admin()
        _, _, text = a.post("/admin/create", {"csrf": a.csrf(), "username": "admin", "role": "viewer"})
        self.assertIn("reserved", text)

    def test_page_views_are_recorded_for_the_activity_log(self):
        b, _ = self.make_user("viewer")
        b.get("/lenders")
        conn = auth.connect(main.auth_path())
        self.addCleanup(conn.close)
        self.assertIn("/lenders", auth.sessions_for(conn, "sam-viewer")[0]["pages"])

    def test_no_app_password_serves_nothing(self):
        main.APP_PASSWORD = ""
        self.assertEqual(self.browser().get("/login")[0], 503)


if __name__ == "__main__":
    unittest.main()
