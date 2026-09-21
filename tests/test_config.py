import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from aus_reporting.config import ConfigError, Settings, load_plan

REPO_ROOT = Path(__file__).resolve().parent.parent


def write(tmp, text):
    path = Path(tmp) / "tables.yml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


VALID = """
    databases:
      Overflow:
        - table: Lenders
          mode: snapshot
          target: Lenders
          columns: [Id, Name]
    """


class TestLoadPlan(unittest.TestCase):
    def test_loads_a_valid_file(self):
        with TemporaryDirectory() as tmp:
            plan = load_plan(write(tmp, VALID))
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan.specs[0].table, "Lenders")

    def test_missing_file(self):
        with self.assertRaises(ConfigError) as ctx:
            load_plan("/nonexistent/tables.yml")
        self.assertIn("no such config file", str(ctx.exception))

    def test_rejects_unknown_key(self):
        with TemporaryDirectory() as tmp, self.assertRaises(ConfigError) as ctx:
            load_plan(write(tmp, """
                databases:
                  Overflow:
                    - table: Lenders
                      mode: snapshot
                      target: Lenders
                      columns: [Id]
                      batch_size: 10
                """))
        self.assertIn("unrecognised key", str(ctx.exception))

    def test_rejects_missing_required_key(self):
        with TemporaryDirectory() as tmp, self.assertRaises(ConfigError) as ctx:
            load_plan(write(tmp, """
                databases:
                  Overflow:
                    - table: Lenders
                      mode: snapshot
                      columns: [Id]
                """))
        self.assertIn("target", str(ctx.exception))

    def test_rejects_duplicate_target(self):
        # Two sources landing in one staging table would interleave and
        # make the watermark meaningless.
        with TemporaryDirectory() as tmp, self.assertRaises(ConfigError) as ctx:
            load_plan(write(tmp, """
                databases:
                  Overflow:
                    - table: Lenders
                      mode: snapshot
                      target: Shared
                      columns: [Id]
                    - table: Affiliates
                      mode: snapshot
                      target: Shared
                      columns: [Id]
                """))
        self.assertIn("is used by both", str(ctx.exception))

    def test_rejects_empty_config(self):
        with TemporaryDirectory() as tmp, self.assertRaises(ConfigError):
            load_plan(write(tmp, "databases: {}\n"))

    def test_blocked_column_fails_the_load(self):
        with TemporaryDirectory() as tmp, self.assertRaises(Exception) as ctx:
            load_plan(write(tmp, """
                databases:
                  Overflow:
                    - table: Affiliates
                      mode: snapshot
                      target: Affiliates
                      columns: [Id, Password]
                """))
        self.assertIn("never be extracted", str(ctx.exception))


class TestRepoConfig(unittest.TestCase):
    """The committed tables.yml must always be loadable."""

    def test_tables_yml_is_valid(self):
        plan = load_plan(REPO_ROOT / "tables.yml")
        self.assertGreater(len(plan), 0)
        self.assertEqual(plan.databases, ("Overflow", "OverflowReporting"))

    def test_every_spec_generates_sql(self):
        for spec in load_plan(REPO_ROOT / "tables.yml").specs:
            sql, _ = spec.select_statement(last_value=1, batch_size=100)
            self.assertIn("SELECT", sql)
            self.assertNotIn("SELECT *", sql)

    def test_no_pii_is_extracted_anywhere(self):
        # Intentional: nothing in the committed config approves a
        # restricted column. If that changes it should be a deliberate,
        # reviewed edit that also updates this test.
        for spec in load_plan(REPO_ROOT / "tables.yml").specs:
            self.assertEqual(
                spec.restricted_approved, (),
                f"{spec.qualified} now extracts personal data",
            )


class TestSettings(unittest.TestCase):
    ENV = {
        "AUS_SOURCE_SERVER": "src.database.windows.net",
        "AUS_WAREHOUSE_SERVER": "wh.database.windows.net",
        "AUS_WAREHOUSE_DATABASE": "AusReporting",
        "AUS_SQL_USERNAME": "svc_reporting",
        "AUS_SQL_PASSWORD": "hunter2",
    }

    def test_reads_environment(self):
        settings = Settings.from_env(self.ENV)
        self.assertEqual(settings.username, "svc_reporting")
        self.assertEqual(settings.batch_size, 50_000)

    def test_missing_variables_are_named(self):
        with self.assertRaises(ConfigError) as ctx:
            Settings.from_env({"AUS_SOURCE_SERVER": "x"})
        message = str(ctx.exception)
        self.assertIn("AUS_SQL_PASSWORD", message)
        self.assertIn("AUS_WAREHOUSE_SERVER", message)

    def test_repr_hides_the_password(self):
        settings = Settings.from_env(self.ENV)
        self.assertNotIn("hunter2", repr(settings))
        self.assertIn("<redacted>", repr(settings))


if __name__ == "__main__":
    unittest.main()
