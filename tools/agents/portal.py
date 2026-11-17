"""Read-only access to the Overflow portal, for the agents.

Signs in with OVERFLOW_PORTAL_EMAIL / OVERFLOW_PORTAL_PASSWORD and reads
one thing: a lender tier's Failed Filters summary - which filters turned
its leads away, and how many. Never the per-lead lists behind it (they
carry names and emails), and never anything that changes the portal:
the only form it ever submits is the sign-in.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request

BASE = "https://admin.overflowhub.com.au"


class Portal:
    def __init__(self):
        email, password = os.environ.get("OVERFLOW_PORTAL_EMAIL"), os.environ.get("OVERFLOW_PORTAL_PASSWORD")
        if not (email and password):
            raise RuntimeError("OVERFLOW_PORTAL_EMAIL / OVERFLOW_PORTAL_PASSWORD are not set")
        bundle = os.environ.get("SSL_CERT_FILE") or "/root/.ccr/ca-bundle.crt"
        ctx = ssl.create_default_context(cafile=bundle) if os.path.exists(bundle) else ssl.create_default_context()
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar),
                                              urllib.request.HTTPSHandler(context=ctx))
        self.op.addheaders = [("User-Agent", "aus-reporting-agent (read-only)")]
        html = self._get("/Account")
        token = re.search(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"', html).group(1)
        form = urllib.parse.urlencode({"__RequestVerificationToken": token, "username": email,
                                       "password": password, "RememberMe": "false",
                                       "RedirectUrl": ""}).encode()
        r = self.op.open(urllib.request.Request(BASE + "/Account/Login", data=form), timeout=120)
        if "kt_login_signin_form" in r.read().decode("utf-8", "replace"):
            raise RuntimeError("portal sign-in refused")

    def _get(self, path, attempts=3):
        """GET with retries: the portal resets connections now and then."""
        for i in range(attempts):
            try:
                with self.op.open(BASE + path, timeout=300) as r:
                    return r.read().decode("utf-8", "replace")
            except (ConnectionError, TimeoutError, OSError):
                if i == attempts - 1:
                    raise
                time.sleep(5 * (i + 1))

    def failed_filters(self, tier_id, start, end):
        """{processed, passed, failed, ignored, filters: [(name, count)]} for a tier.
        Counts only. A lead fails several filters, so the counts overlap,
        and on a single day a filter's count can exceed the leads
        processed: compare week-long windows, not single days."""
        q = (f"startDate={start}&endDate={end}&affiliateId=null&lenderTierId={int(tier_id)}"
             "&affiliateGroupId=null&lenderId=null")
        d = json.loads(self._get(f"/api/Reports/FailedFilterReports?{q}"))
        return {"processed": d.get("LeadProcessed"), "passed": d.get("LeadPassed"),
                "failed": d.get("LeadFailed"), "ignored": d.get("LeadIgnored"),
                "filters": sorted(((it["FailedFilter"], it["Count"]) for it in d.get("Items", [])),
                                  key=lambda x: -x[1])}


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 4:
        sys.exit("usage: portal.py <tier_id> <start YYYY-MM-DD> <end YYYY-MM-DD>")
    print(json.dumps(Portal().failed_filters(sys.argv[1], sys.argv[2], sys.argv[3]), indent=1))
