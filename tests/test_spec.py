import unittest

from aus_reporting.sensitive import BlockedColumnError
from aus_reporting.spec import ExtractPlan, TableSpec, quote_ident


def incremental(**kwargs):
    defaults = dict(
        database="OverflowReporting",
        table="LeadApplicationStages",
        mode="incremental",
        columns=("Id", "LeadApplicationId", "DateCreated"),
        target="LeadApplicationStages",
        watermark_column="Id",
    )
    defaults.update(kwargs)
    return TableSpec(**defaults)


def snapshot(**kwargs):
    defaults = dict(
        database="Overflow",
        table="Lenders",
        mode="snapshot",
        columns=("Id", "Name"),
        target="Lenders",
    )
    defaults.update(kwargs)
    return TableSpec(**defaults)


class TestQuoting(unittest.TestCase):
    def test_brackets_identifiers(self):
        self.assertEqual(quote_ident("DateCreated"), "[DateCreated]")

    def test_escapes_closing_bracket(self):
        self.assertEqual(quote_ident("we]rd"), "[we]]rd]")

    def test_rejects_empty(self):
        for bad in ("", "   "):
            with self.assertRaises(ValueError):
                quote_ident(bad)


class TestValidation(unittest.TestCase):
    def test_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            snapshot(mode="sideways")

    def test_rejects_no_columns(self):
        with self.assertRaises(ValueError):
            snapshot(columns=())

    def test_rejects_duplicate_columns_case_insensitively(self):
        with self.assertRaises(ValueError) as ctx:
            snapshot(columns=("Id", "Name", "name"))
        self.assertIn("twice", str(ctx.exception))

    def test_incremental_needs_a_watermark(self):
        with self.assertRaises(ValueError) as ctx:
            incremental(watermark_column=None)
        self.assertIn("watermark_column", str(ctx.exception))

    def test_watermark_must_be_selected(self):
        # Otherwise there is nothing to advance the mark from.
        with self.assertRaises(ValueError) as ctx:
            incremental(columns=("LeadApplicationId", "DateCreated"))
        self.assertIn("must also appear in columns", str(ctx.exception))

    def test_snapshot_rejects_a_watermark(self):
        with self.assertRaises(ValueError):
            snapshot(watermark_column="Id")

    def test_blocked_column_rejected_at_construction(self):
        with self.assertRaises(BlockedColumnError):
            snapshot(columns=("Id", "Password"))


class TestSelectStatement(unittest.TestCase):
    def test_snapshot_selects_named_columns_only(self):
        sql, params = snapshot().select_statement()
        self.assertIn("SELECT [Id], [Name]", sql)
        self.assertIn("FROM [dbo].[Lenders]", sql)
        self.assertNotIn("*", sql)
        self.assertEqual(params, ())

    def test_incremental_seeks_on_the_watermark(self):
        sql, params = incremental().select_statement(last_value=500, batch_size=1000)
        self.assertIn("TOP (?)", sql)
        self.assertIn("WHERE [Id] > ?", sql)
        self.assertIn("ORDER BY [Id]", sql)
        self.assertEqual(params, (1000, 500))

    def test_first_run_starts_below_every_identity_seed(self):
        _, params = incremental().select_statement(last_value=None, batch_size=10)
        self.assertEqual(params, (10, -1))

    def test_incremental_without_batch_size_omits_top(self):
        sql, params = incremental().select_statement(last_value=7)
        self.assertNotIn("TOP", sql)
        self.assertEqual(params, (7,))

    def test_no_interpolated_values(self):
        # Values must arrive as parameters, never in the text.
        sql, _ = incremental().select_statement(last_value=99999, batch_size=12345)
        self.assertNotIn("99999", sql)
        self.assertNotIn("12345", sql)


class TestWriteStatements(unittest.TestCase):
    def test_insert_placeholders_match_column_count(self):
        sql = incremental().insert_statement()
        self.assertIn("[stg].[LeadApplicationStages]", sql)
        self.assertEqual(sql.count("?"), 3)

    def test_truncate_allowed_for_snapshot(self):
        self.assertIn("TRUNCATE TABLE", snapshot().truncate_statement())

    def test_truncate_refused_for_incremental(self):
        # Truncating an incremental target would discard history the
        # watermark will never re-read.
        with self.assertRaises(ValueError) as ctx:
            incremental().truncate_statement()
        self.assertIn("refusing to truncate", str(ctx.exception))


class TestExtractPlan(unittest.TestCase):
    def test_groups_by_database_preserving_order(self):
        plan = ExtractPlan(specs=(snapshot(), incremental(), snapshot(table="Affiliates", target="Affiliates")))
        self.assertEqual(plan.databases, ("Overflow", "OverflowReporting"))
        self.assertEqual(len(plan.for_database("Overflow")), 2)
        self.assertEqual(len(plan), 3)


if __name__ == "__main__":
    unittest.main()
