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

    def test_windowed_queries_carry_the_window_token(self):
        for name in queries.WINDOWED:
            _, sql = queries.SOURCES[name]
            self.assertIn("{{WINDOW_DAYS}}", sql, name)

    def test_unwindowed_queries_have_no_window_token(self):
        for name in set(queries.SOURCES) - queries.WINDOWED:
            _, sql = queries.SOURCES[name]
            self.assertNotIn("{{WINDOW_DAYS}}", sql, name)

    def test_window_is_substituted_as_a_negative_integer(self):
        for name in queries.WINDOWED:
            sql = queries.load_sql(name, 90)
            self.assertIn("DATEADD(DAY, -90,", sql, name)
            self.assertNotIn("{{", sql, name)

    def test_non_integer_window_is_refused_outright(self):
        # The token is not a bound parameter, so only an integer may ever
        # reach it. Anything else raises rather than being sanitised -
        # failing loudly beats silently rewriting what the caller asked for.
        for bad in ("30; DROP TABLE x", "1 OR 1=1", 3.5, [30], True):
            with self.assertRaises(TypeError, msg=repr(bad)):
                queries.load_sql("stage_counts", bad)

    def test_none_means_leave_the_token_alone(self):
        # The documented default: load the SQL for reading, unsubstituted.
        self.assertIn("{{WINDOW_DAYS}}", queries.load_sql("stage_counts"))

    def test_negative_or_positive_days_both_look_back(self):
        self.assertIn("DATEADD(DAY, -30,", queries.load_sql("accepts", -30))
        self.assertIn("DATEADD(DAY, -30,", queries.load_sql("accepts", 30))

    def test_no_tokens_survive_a_full_load(self):
        for name, (_, _) in queries.sources(45).items():
            self.assertNotIn("{{", queries.sources(45)[name][1], name)

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


class TestBuilderParity(unittest.TestCase):
    """The Python and PowerShell builders must not drift apart.

    They read the same .sql files and the same stages.json, which is the
    real guarantee. These check that the PowerShell side still agrees on
    the contract around those files.
    """

    PS = REPO / "dashboard" / "Build-Dashboard.ps1"

    def setUp(self):
        self.ps = self.PS.read_text(encoding="utf-8")

    def test_powershell_builder_exists(self):
        self.assertTrue(self.PS.exists())

    def test_both_substitute_the_same_tokens(self):
        for token in ("{{AEST_SHIFT_HOURS}}", "{{WINDOW_DAYS}}"):
            self.assertIn(token, self.ps, token)

    def test_both_use_the_same_placeholder(self):
        self.assertIn(PLACEHOLDER, self.ps)
        self.assertIn(PLACEHOLDER, TEMPLATE.read_text(encoding="utf-8"))

    def test_both_escape_closing_script_tags(self):
        self.assertIn(r'Replace("</", "<\/")', self.ps)

    def test_powershell_reads_the_shared_files(self):
        for fragment in ("stages.json", "template.html", '"sql"'):
            self.assertIn(fragment, self.ps, fragment)

    def test_powershell_names_no_query_of_its_own(self):
        # Every query must come from a .sql file; a SELECT written inline
        # here is exactly the drift this arrangement exists to prevent.
        for word in ("SELECT ", "FROM dbo."):
            self.assertNotIn(word, self.ps, f"inline SQL: {word!r}")

    def test_powershell_emits_every_key_the_page_reads(self):
        for key in ("generatedUtc", "windowDays", "aestShiftHours", "stages",
                    "stage_counts", "applications", "accepts", "affiliates"):
            self.assertIn(key, self.ps, key)

    def test_powershell_sets_json_depth(self):
        # PowerShell's ConvertTo-Json defaults to depth 2, which would
        # silently flatten every row into a type name.
        self.assertIn("-Depth 10", self.ps)

    def test_powershell_connects_read_only(self):
        self.assertIn("ApplicationIntent", self.ps)
        self.assertIn("READ UNCOMMITTED", self.ps)

    def test_stage_metadata_is_the_single_source(self):
        meta = json.loads((REPO / "dashboard" / "stages.json").read_text())
        self.assertEqual(set(meta["sources"]), set(queries.SOURCES))
        self.assertEqual(set(meta["windowed"]), queries.WINDOWED)
        self.assertEqual(
            {int(k) for k in meta["stages"]}, set(queries.STAGES)
        )


class TestQueryPageContract(unittest.TestCase):
    """The page and the queries have to agree on column names.

    An earlier version of this tried to parse every selected column out
    of the SQL and flag unused ones. That parser matched `AS float` from
    a CAST and missed columns written without `AS` - more machinery than
    the problem deserved. This checks the direction that actually breaks
    things: a column the page reads must still be selected.
    """

    # what the page reads -> the query that must provide it
    REQUIRED = {
        "applications": ["Day", "AffiliateId", "Applications", "AvgLoanAmount"],
        "accepts":      ["Day", "AffiliateId", "ApplicationsSold"],
        "stage_counts": ["Day", "AffiliateId", "StageId", "Applications"],
        "affiliates":   ["Id", "Name", "DisplayName"],
    }

    def test_every_column_the_page_reads_is_selected(self):
        for name, columns in self.REQUIRED.items():
            _, sql = queries.SOURCES[name]
            for column in columns:
                self.assertRegex(sql, r"\b" + column + r"\b",
                                 f"{name}.sql no longer selects {column}")

    def test_the_page_reads_every_required_column(self):
        # Keeps the list above honest rather than aspirational.
        page = TEMPLATE.read_text(encoding="utf-8")
        for name, columns in self.REQUIRED.items():
            for column in columns:
                self.assertRegex(page, r"\b" + column + r"\b",
                                 f"{column} is required but the page never reads it")

    def test_the_expensive_distinct_is_gone(self):
        # COUNT(DISTINCT LeadId) over 4.4M uniqueidentifiers cost about
        # 50 of a 65-second build, for a column nothing displayed.
        # Comments are stripped: the file explains the removal by name.
        _, sql = queries.SOURCES["applications"]
        body = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
        body = re.sub(r"--[^\n]*", " ", body)
        self.assertNotIn("COUNT(DISTINCT LeadId)", body)
        self.assertNotIn("MonthlyIncome", body)
