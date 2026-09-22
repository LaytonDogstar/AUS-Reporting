"""Dashboard build tests, and a demo page rendered from synthetic data.

No database required. The synthetic data mirrors the real shape and the
real proportions measured on 22 Sep 2026, so the rendered page is a fair
likeness of the real one.
"""

import datetime as dt
import json
import random
import re
import unittest
from decimal import Decimal
from pathlib import Path

from dashboard import queries
from dashboard.build import PLACEHOLDER, TEMPLATE, _jsonable, render
from aus_reporting.errors import AusReportingError

REPO = Path(__file__).resolve().parent.parent

# Measured share of applications reaching each stage.
STAGE_SHARE = {
    1: 1.00, 2: 0.032, 3: 0.043, 4: 0.043, 5: 0.470, 6: 0.0003,
    7: 0.378, 8: 0.872, 9: 0.872, 12: 0.277, 10: 0.080, 13: 0.156,
}
AFFILIATES = [
    (11, "Beacon Leads"), (14, "Northline Media"), (22, "Harbourpoint"),
    (31, "Solaris Direct"), (37, "Kestrel Digital"), (44, "Redgum Partners"),
]


def synthetic(days: int = 90, seed: int = 7) -> dict:
    rng = random.Random(seed)
    today = dt.date(2026, 9, 22)
    applications, accepts, stage_counts = [], [], []

    for back in range(days):
        day = (today - dt.timedelta(days=back)).isoformat()
        weekday = (today - dt.timedelta(days=back)).weekday()
        seasonal = 0.62 if weekday >= 5 else 1.0
        for aff_id, _ in AFFILIATES:
            base = rng.randint(120, 460) * seasonal * rng.uniform(0.85, 1.15)
            apps = max(1, int(base))
            applications.append({
                "Day": day, "AffiliateId": aff_id, "Applications": apps,
                "DistinctLeads": int(apps * rng.uniform(0.70, 0.95)),
                "AvgLoanAmount": round(rng.uniform(1800, 4200), 2),
                "AvgMonthlyIncome": round(rng.uniform(3200, 7400), 2),
            })
            sold = int(apps * rng.uniform(0.22, 0.34))
            accepts.append({
                "Day": day, "AffiliateId": aff_id,
                "ApplicationsSold": sold, "AcceptEvents": int(sold * 1.01),
            })
            for stage_id, share in STAGE_SHARE.items():
                # Not every affiliate runs every stage - the real reason
                # aggregate funnel percentages are wrong.
                if stage_id in (3, 4) and aff_id not in (11, 22):
                    continue
                if stage_id == 6 and aff_id != 37:
                    continue
                n = int(apps * share * rng.uniform(0.88, 1.12))
                if n <= 0:
                    continue
                stage_counts.append({
                    "Day": day, "AffiliateId": aff_id, "StageId": stage_id,
                    "Applications": n, "StageEvents": int(n * rng.uniform(1.0, 1.2)),
                })

    return {
        "generatedUtc": dt.datetime(2026, 9, 22, 6, 15, tzinfo=dt.timezone.utc).isoformat(timespec="seconds"),
        "windowDays": days,
        "aestShiftHours": queries.AEST_SHIFT_HOURS,
        "stages": {str(k): v for k, v in queries.STAGES.items()},
        "affiliates": [
            {"Id": i, "Name": n, "DisplayName": n, "IsActive": True,
             "IsDeleted": False, "IsInternalSource": False}
            for i, n in AFFILIATES
        ],
        "applications": applications,
        "accepts": accepts,
        "stage_counts": stage_counts,
    }


class TestJsonable(unittest.TestCase):
    def test_dates_become_iso_days(self):
        self.assertEqual(_jsonable(dt.date(2026, 9, 22)), "2026-09-22")
        self.assertEqual(_jsonable(dt.datetime(2026, 9, 22, 13, 5)), "2026-09-22")

    def test_decimals_become_floats(self):
        self.assertEqual(_jsonable(Decimal("1234.56")), 1234.56)
        self.assertIsInstance(_jsonable(Decimal("1")), float)

    def test_passes_through_plain_values(self):
        for v in (None, 1, "x", True):
            self.assertEqual(_jsonable(v), v)


class TestQueries(unittest.TestCase):
    def test_every_source_names_a_database(self):
        for name, (database, sql) in queries.SOURCES.items():
            self.assertIn(database, ("Overflow", "OverflowReporting"), name)
            self.assertTrue(sql.strip(), name)

    def test_windowed_queries_take_exactly_one_parameter(self):
        for name in queries.WINDOWED:
            _, sql = queries.SOURCES[name]
            self.assertEqual(sql.count("?"), 1, name)

    def test_unwindowed_queries_take_none(self):
        for name in set(queries.SOURCES) - queries.WINDOWED:
            _, sql = queries.SOURCES[name]
            self.assertEqual(sql.count("?"), 0, name)

    def test_all_queries_are_read_only(self):
        for name, (_, sql) in queries.SOURCES.items():
            upper = sql.upper()
            for word in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "TRUNCATE", "ALTER "):
                self.assertNotIn(word, upper, f"{name} contains {word.strip()}")

    def test_no_banking_or_credential_columns(self):
        # Comments are stripped first: they legitimately NAME the columns
        # being avoided, and the rule is about what is selected.
        for name, (_, sql) in queries.SOURCES.items():
            executable = re.sub(r"--[^\n]*", " ", sql).lower()
            for word in ("password", "secretkey", "clientsecret", "accountnumber",
                         "sortcode", "dateofbirth", "mobilenumber", "email"):
                self.assertNotIn(word, executable, f"{name} selects {word}")

    def test_the_comment_stripper_does_not_hide_a_real_column(self):
        # Guards the test above: a genuinely selected column is still caught.
        bad = "SELECT Id, Password FROM dbo.Affiliates; -- nothing to see"
        executable = re.sub(r"--[^\n]*", " ", bad).lower()
        self.assertIn("password", executable)

    def test_dates_are_shifted_to_aest(self):
        # Every day-grain query must use the AEST shift, or days are UTC.
        for name in queries.WINDOWED:
            _, sql = queries.SOURCES[name]
            self.assertIn(f"DATEADD(HOUR, {queries.AEST_SHIFT_HOURS}", sql, name)

    def test_funnel_stages_are_ordered_and_complete(self):
        funnel = {k: v for k, v in queries.STAGES.items() if v["order"] is not None}
        self.assertIn(1, funnel)    # Received, the denominator
        self.assertIn(10, funnel)   # Offer
        self.assertIn(13, funnel)   # Decline
        # Never emitted or error-only stages stay out of the funnel.
        for stage_id in (11, 14, 15, 16, 17):
            self.assertIsNone(queries.STAGES[stage_id]["order"],
                              f"stage {stage_id} should not be a funnel step")

    def test_the_two_outcomes_are_marked(self):
        self.assertEqual(queries.STAGES[10]["outcome"], "good")
        self.assertEqual(queries.STAGES[13]["outcome"], "bad")


class TestRender(unittest.TestCase):
    def test_embeds_the_payload(self):
        html = render({"generatedUtc": "x", "stages": {}, "affiliates": [],
                       "applications": [], "accepts": [], "stage_counts": []})
        self.assertNotIn(PLACEHOLDER, html)
        self.assertIn('"generatedUtc":"x"', html)

    def test_escapes_closing_script_tags(self):
        # A </script> inside the JSON would end the block early.
        html = render({"stages": {}, "affiliates": [], "applications": [],
                       "accepts": [], "stage_counts": [],
                       "generatedUtc": "</script><img src=x>"})
        self.assertNotIn("</script><img", html)
        self.assertEqual(html.count("</script>"), 1)

    def test_missing_placeholder_is_an_error(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
            f.write("<html>no placeholder</html>")
            path = Path(f.name)
        with self.assertRaises(AusReportingError):
            render({}, template=path)

    def test_template_has_no_external_resources(self):
        # The page must work offline from a file share.
        html = TEMPLATE.read_text(encoding="utf-8")
        for pattern in (r'src="https?://', r'href="https?://', r'@import'):
            self.assertIsNone(re.search(pattern, html), pattern)


class TestDemoPage(unittest.TestCase):
    """Writes a demo page from synthetic data, for eyeballing."""

    def test_builds_a_demo(self):
        data = synthetic()
        html = render(data)
        out = REPO / "dashboard" / "demo.html"
        out.write_text(html, encoding="utf-8")
        self.assertGreater(len(html), 50_000)
        self.assertIn("Beacon Leads", html)
        # The payload must be valid JSON once un-escaped.
        payload = html.split("const DATA = ", 1)[1].split(";\n", 1)[0]
        json.loads(payload.replace("<\\/", "</"))


if __name__ == "__main__":
    unittest.main()
