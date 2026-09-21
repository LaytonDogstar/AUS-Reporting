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
        "AUS_SOURCE_USERNAME": "LaytonB",
        "AUS_SOURCE_PASSWORD": "source-secret",
        "AUS_WAREHOUSE_SERVER": "wh.database.windows.net",
        "AUS_WAREHOUSE_DATABASE": "AusReporting",
    }

    def test_reads_environment(self):
        settings = Settings.from_env(self.ENV)
        self.assertEqual(settings.source_username, "LaytonB")
        self.assertEqual(settings.batch_size, 50_000)

    def test_warehouse_uses_its_own_login_when_given(self):
        env = dict(self.ENV,
                   AUS_WAREHOUSE_USERNAME="svc_warehouse",
                   AUS_WAREHOUSE_PASSWORD="warehouse-secret")
        settings = Settings.from_env(env)
        self.assertEqual(settings.warehouse_username, "svc_warehouse")
        self.assertEqual(settings.warehouse_password, "warehouse-secret")
        self.assertEqual(settings.source_username, "LaytonB")

    def test_warehouse_falls_back_to_source_login(self):
        # Only correct when both live on one server; not the default.
        settings = Settings.from_env(self.ENV)
        self.assertEqual(settings.warehouse_username, "LaytonB")
        self.assertEqual(settings.warehouse_password, "source-secret")

    def test_missing_variables_are_named(self):
        with self.assertRaises(ConfigError) as ctx:
            Settings.from_env({"AUS_SOURCE_SERVER": "x"})
        message = str(ctx.exception)
        self.assertIn("AUS_SOURCE_PASSWORD", message)
        self.assertIn("AUS_WAREHOUSE_SERVER", message)

    def test_repr_hides_both_passwords(self):
        env = dict(self.ENV,
                   AUS_WAREHOUSE_USERNAME="svc_warehouse",
                   AUS_WAREHOUSE_PASSWORD="warehouse-secret")
        text = repr(Settings.from_env(env))
        self.assertNotIn("source-secret", text)
        self.assertNotIn("warehouse-secret", text)
        self.assertEqual(text.count("<redacted>"), 2)


class TestConnectionString(unittest.TestCase):
    """The credentials used must match the server being connected to."""

    def test_source_and_warehouse_use_different_logins(self):
        from aus_reporting.db import connection_string

        settings = Settings.from_env(dict(
            TestSettings.ENV,
            AUS_WAREHOUSE_USERNAME="svc_warehouse",
            AUS_WAREHOUSE_PASSWORD="warehouse-secret",
        ))

        src = connection_string(
            settings, server=settings.source_server, database="Overflow",
            username=settings.source_username, password=settings.source_password,
            readonly=True)
        wh = connection_string(
            settings, server=settings.warehouse_server,
            database=settings.warehouse_database,
            username=settings.warehouse_username,
            password=settings.warehouse_password)

        self.assertIn("UID=LaytonB", src)
        self.assertIn("ApplicationIntent=ReadOnly", src)
        self.assertIn("UID=svc_warehouse", wh)
        self.assertNotIn("ApplicationIntent=ReadOnly", wh)
        for cs in (src, wh):
            self.assertIn("Encrypt=yes", cs)
            self.assertIn("TrustServerCertificate=no", cs)


if __name__ == "__main__":
    unittest.main()
