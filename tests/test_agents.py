"""The agents' side of the service: their token, what they may read and
post, the lender mapping, and the briefing on Today.

The token is a read-only login plus one write - posting a report - and
nothing else; these tests hold that line.
"""

import http.client
import json
import re
import sys
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent / "service"
if str(SERVICE) not in sys.path:
    sys.path.insert(0, str(SERVICE))

import agentstore                                          # noqa: E402
import main                                                # noqa: E402
import store                                               # noqa: E402
try:                                                       # run as tests.test_agents
    from tests.test_logins import Server                   # noqa: E402
except ImportError:                                        # or found by discover -s tests
    from test_logins import Server                         # noqa: E402

TOKEN = "t" * 40
STAMP = "2026-11-16T00:00:00+00:00"


def call(port, method, path, token=None, body=None, cookie=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cookie:
        headers["Cookie"] = cookie
    data = None
    if body is not None:
        data = json.dumps(body).encode() if not isinstance(body, (bytes, str)) else body
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=headers)
    r = conn.getresponse()
    text = r.read().decode("utf-8", "replace")
    conn.close()
    return r.status, text


REPORT = {"agent": "data", "kind": "check", "status": "warn", "title": "Data check 2026-11-16",
          "summary": "One gap.", "body": "Lender revenue is 2% below the affiliates'."}


class AgentToken(Server):
    def setUp(self):
        super().setUp()
        self._old = main.AGENT_TOKEN
        main.AGENT_TOKEN = TOKEN
        self.addCleanup(setattr, main, "AGENT_TOKEN", self._old)

    def test_the_agent_api_needs_the_token(self):
        self.assertEqual(call(self.port, "GET", "/api/agent/status")[0], 303)   # to the login
        self.assertEqual(call(self.port, "GET", "/api/agent/status", token="wrong" * 10)[0], 303)
        status, text = call(self.port, "GET", "/api/agent/status", token=TOKEN)
        self.assertEqual(status, 200)
        self.assertIn("kinds", json.loads(text))

    def test_the_favicon_answers_without_a_403(self):
        # Browsers fetch it on every page; a 403 logged a console error on all of them.
        self.assertEqual(call(self.port, "GET", "/favicon.ico")[0], 204)
        self.assertEqual(call(self.port, "GET", "/favicon.ico", token=TOKEN)[0], 204)

    def test_a_short_token_is_never_accepted(self):
        main.AGENT_TOKEN = "short"
        self.assertEqual(call(self.port, "GET", "/api/agent/status", token="short")[0], 303)

    def test_the_token_reads_pages_as_an_analyst_and_nothing_admin(self):
        self.assertEqual(call(self.port, "GET", "/collection", token=TOKEN)[0], 200)
        self.assertEqual(call(self.port, "GET", "/admin", token=TOKEN)[0], 403)
        self.assertEqual(call(self.port, "GET", "/admin/reports", token=TOKEN)[0], 403)

    def test_the_token_cannot_take_an_action(self):
        # Actions read the session cookie, never the token: no session, no action.
        status, _ = call(self.port, "POST", "/collect", token=TOKEN, body="days=1&csrf=")
        self.assertEqual(status, 303)

    def test_an_agent_posts_a_report_and_an_admin_reads_it(self):
        status, text = call(self.port, "POST", "/api/agent/report", token=TOKEN, body=REPORT)
        self.assertEqual(status, 201, text)
        rid = json.loads(text)["id"]
        a = self.admin()
        status, _, page = a.get("/admin/reports")
        self.assertEqual(status, 200)
        self.assertIn("Data check 2026-11-16", page)
        status, _, page = a.get(f"/admin/reports/view?id={rid}")
        self.assertIn("2% below", page)
        # and the manager agent can read it back, body included
        status, text = call(self.port, "GET", "/api/agent/reports?body=1", token=TOKEN)
        self.assertEqual(json.loads(text)["reports"][0]["body"], REPORT["body"])

    def test_a_report_without_the_token_is_refused(self):
        self.assertEqual(call(self.port, "POST", "/api/agent/report", body=REPORT)[0], 401)

    def test_a_bad_report_is_refused_with_the_reason(self):
        status, text = call(self.port, "POST", "/api/agent/report", token=TOKEN,
                            body={**REPORT, "status": "great"})
        self.assertEqual(status, 400)
        self.assertIn("status", text)

    def test_a_report_is_shown_escaped(self):
        call(self.port, "POST", "/api/agent/report", token=TOKEN,
             body={**REPORT, "title": "<script>alert(1)</script>"})
        _, _, page = self.admin().get("/admin/reports")
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)


class Briefing(Server):
    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, main, "AGENT_TOKEN", main.AGENT_TOKEN)
        main.AGENT_TOKEN = TOKEN

    def test_the_latest_briefing_heads_today(self):
        call(self.port, "POST", "/api/agent/report", token=TOKEN,
             body={"agent": "buyer", "kind": "briefing", "status": "info",
                   "title": "Moneyspot bought less", "summary": "Acceptance fell 15%."})
        _, _, page = self.admin().get("/today")
        self.assertIn("Moneyspot bought less", page)
        self.assertIn("/briefing?id=", page)

    def test_viewers_can_open_a_briefing_but_not_other_reports(self):
        _, text = call(self.port, "POST", "/api/agent/report", token=TOKEN, body=REPORT)
        check_id = json.loads(text)["id"]
        a = self.admin()
        # /briefing?id= only ever shows a briefing
        _, _, page = a.get(f"/briefing?id={check_id}")
        self.assertNotIn("2% below", page)


class LenderMapping(Server):
    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, main, "AGENT_TOKEN", main.AGENT_TOKEN)
        conn = store.connect(main.DB_PATH)
        store.save_lender_lookup(conn, [
            {"id": 10175, "name": "Wallet Wizard API T1", "lender_id": 29,
             "lender_name": "Wallet Wizard ", "code": None, "price": 15.0, "is_active": True},
            {"id": 10191, "name": "Wallet Wizard API T2", "lender_id": 29,
             "lender_name": "Wallet Wizard ", "code": None, "price": 7.0, "is_active": True}], STAMP)
        conn.close()

    def test_an_admin_maps_a_lender_and_every_change_is_logged(self):
        a = self.admin()
        status, _, page = a.get("/admin/lenders")
        self.assertEqual(status, 200)
        self.assertIn("Wallet Wizard API T1", page)
        csrf = a.csrf()
        self.assertEqual(a.post("/admin/lenders/apply", {"csrf": csrf, "lender_id": "29",
                                                         "company": "Credit Corp",
                                                         "lender_type": "direct"})[0], 303)
        self.assertEqual(a.post("/admin/lenders/save", {"csrf": csrf, "tier_id": "10191",
                                                        "brand": "Wallet Wizard",
                                                        "company": "Credit Corp",
                                                        "lender_type": "direct",
                                                        "notes": "$7 tier"})[0], 303)
        conn = agentstore.connect(main.auth_path())
        m = agentstore.lender_map(conn)
        self.assertEqual(m[10175]["company"], "Credit Corp")
        self.assertEqual(m[10191]["notes"], "$7 tier")
        self.assertGreaterEqual(len(agentstore.audit(conn)), 5)
        conn.close()
        main.AGENT_TOKEN = TOKEN
        status, text = call(self.port, "GET", "/api/agent/lenders", token=TOKEN)
        tiers = {t["tier_id"]: t for t in json.loads(text)["tiers"]}
        self.assertEqual((tiers[10191]["brand"], tiers[10191]["company"]),
                         ("Wallet Wizard", "Credit Corp"))
        self.assertEqual(tiers[10175]["brand"], "Wallet Wizard")      # the API's name, trimmed

    def test_an_unknown_type_is_refused(self):
        a = self.admin()
        status, _, page = a.post("/admin/lenders/save", {"csrf": a.csrf(), "tier_id": "10175",
                                                         "lender_type": "bank"})
        self.assertEqual(status, 400)
        self.assertIn("type must be one of", page)

    def test_a_blank_apply_box_leaves_the_field_alone(self):
        a = self.admin()
        csrf = a.csrf()
        a.post("/admin/lenders/save", {"csrf": csrf, "tier_id": "10175", "brand": "Wallet Wizard",
                                       "company": "Credit Corp", "lender_type": "direct", "notes": ""})
        a.post("/admin/lenders/apply", {"csrf": csrf, "lender_id": "29", "brand": "", "company": "",
                                        "lender_type": "network"})
        conn = agentstore.connect(main.auth_path())
        m = agentstore.lender_map(conn)[10175]
        conn.close()
        self.assertEqual((m["company"], m["lender_type"]), ("Credit Corp", "network"))

    def test_only_admins_reach_the_mapping(self):
        a = self.admin()
        status, _, text = a.post("/admin/create", {"csrf": a.csrf(), "username": "viv",
                                                   "role": "analyst"})
        password = re.search(r"<code>([A-Za-z0-9]{16})</code>", text).group(1)
        b = self.browser()
        b.login("viv", password)
        self.assertEqual(b.get("/admin/lenders")[0], 403)
        self.assertEqual(b.get("/admin/reports")[0], 403)
        self.assertEqual(b.get("/api/agent/status")[0], 403)


class Store(unittest.TestCase):
    def test_a_lender_takes_its_tiers_brand_and_a_type_only_when_they_agree(self):
        tiers = [{"lender_id": 1, "brand": "A", "company": "Co", "type": "direct"},
                 {"lender_id": 1, "brand": "A", "company": None, "type": "network"},
                 {"lender_id": 1, "brand": "B", "company": "Co", "type": None}]
        prof = agentstore.lender_profile(tiers)[1]
        self.assertEqual((prof["brand"], prof["company"], prof["type"]), ("A", "Co", None))


if __name__ == "__main__":
    unittest.main()
