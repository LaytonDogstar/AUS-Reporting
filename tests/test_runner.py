"""Batching and watermark behaviour, proved without a database."""

import logging
import unittest

from aus_reporting.runner import extract_table, summarise
from aus_reporting.spec import TableSpec
from aus_reporting.watermark import InMemoryWatermarkStore


class FakeSourceCursor:
    """Serves rows the way a watermarked SELECT would."""

    def __init__(self, rows, watermark_index=0):
        self._rows = sorted(rows, key=lambda r: r[watermark_index])
        self._wm = watermark_index
        self._result = []

    def execute(self, sql, *params):
        if "WHERE" in sql:
            batch_size, last_value = params
            eligible = [r for r in self._rows if r[self._wm] > last_value]
            self._result = eligible[:batch_size]
        else:
            self._result = list(self._rows)
        return self

    def fetchall(self):
        return self._result


class FakeSourceConnection:
    def __init__(self, rows, watermark_index=0):
        self._rows = rows
        self._wm = watermark_index
        self.queries = 0

    def cursor(self):
        self.queries += 1
        return FakeSourceCursor(self._rows, self._wm)


class FakeWarehouseCursor:
    def __init__(self, log):
        self._log = log
        self.fast_executemany = False

    def execute(self, sql, *params):
        self._log.append(("execute", sql, params))
        return self

    def executemany(self, sql, rows):
        self._log.append(("executemany", sql, list(rows)))
        return self


class FakeWarehouseConnection:
    def __init__(self, fail_on_insert=False):
        self.log = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_on_insert = fail_on_insert

    def cursor(self):
        if self.fail_on_insert:
            class Boom(FakeWarehouseCursor):
                def executemany(self, sql, rows):
                    raise RuntimeError("insert exploded")
            return Boom(self.log)
        return FakeWarehouseCursor(self.log)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    @property
    def inserted_rows(self):
        return [r for kind, _, rows in self.log if kind == "executemany" for r in rows]


STAGES = TableSpec(
    database="OverflowReporting",
    table="LeadApplicationStages",
    mode="incremental",
    columns=("Id", "StageId"),
    target="LeadApplicationStages",
    watermark_column="Id",
)

LENDERS = TableSpec(
    database="Overflow",
    table="Lenders",
    mode="snapshot",
    columns=("Id", "Name"),
    target="Lenders",
)


def run(spec, rows, *, batch_size=2, watermarks=None, warehouse=None):
    warehouse = warehouse or FakeWarehouseConnection()
    watermarks = watermarks if watermarks is not None else InMemoryWatermarkStore()
    result = extract_table(
        spec,
        source_conn=FakeSourceConnection(rows),
        warehouse_conn=warehouse,
        watermarks=watermarks,
        batch_size=batch_size,
    )
    return result, warehouse, watermarks


class TestIncremental(unittest.TestCase):
    ROWS = [(1, 10), (2, 20), (3, 30), (4, 40), (5, 50)]

    def test_loads_everything_in_batches(self):
        result, warehouse, _ = run(STAGES, self.ROWS, batch_size=2)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.rows, 5)
        self.assertEqual(result.batches, 3)      # 2 + 2 + 1
        self.assertEqual(len(warehouse.inserted_rows), 5)

    def test_watermark_ends_at_the_highest_id(self):
        result, _, watermarks = run(STAGES, self.ROWS, batch_size=2)
        self.assertEqual(result.new_watermark, 5)
        self.assertEqual(watermarks.get("LeadApplicationStages"), 5)

    def test_resumes_from_an_existing_watermark(self):
        store = InMemoryWatermarkStore({"LeadApplicationStages": 3})
        result, warehouse, _ = run(STAGES, self.ROWS, batch_size=10, watermarks=store)
        self.assertEqual(result.rows, 2)
        self.assertEqual([r[0] for r in warehouse.inserted_rows], [4, 5])

    def test_nothing_new_is_a_no_op(self):
        store = InMemoryWatermarkStore({"LeadApplicationStages": 5})
        result, warehouse, _ = run(STAGES, self.ROWS, batch_size=10, watermarks=store)
        self.assertEqual(result.rows, 0)
        self.assertEqual(result.batches, 0)
        self.assertEqual(warehouse.commits, 0)

    def test_empty_source_is_a_no_op(self):
        result, warehouse, watermarks = run(STAGES, [], batch_size=10)
        self.assertEqual(result.rows, 0)
        self.assertIsNone(watermarks.get("LeadApplicationStages"))

    def test_commit_precedes_the_watermark_move(self):
        # So a crash between the two re-reads a batch rather than losing
        # it. Staging tolerates the duplicate; losing rows is unrecoverable.
        result, warehouse, watermarks = run(STAGES, self.ROWS, batch_size=2)
        self.assertEqual(warehouse.commits, result.batches)
        self.assertEqual(len(watermarks.history), result.batches)

    def test_exact_multiple_of_batch_size_terminates(self):
        result, _, _ = run(STAGES, [(1, 1), (2, 2), (3, 3), (4, 4)], batch_size=2)
        self.assertEqual(result.rows, 4)
        self.assertEqual(result.batches, 2)

    def test_failure_is_captured_not_raised(self):
        warehouse = FakeWarehouseConnection(fail_on_insert=True)
        # The runner logs the traceback by design; keep it out of test output.
        logging.disable(logging.CRITICAL)
        try:
            result, warehouse, watermarks = run(
                STAGES, self.ROWS, batch_size=2, warehouse=warehouse
            )
        finally:
            logging.disable(logging.NOTSET)
        self.assertEqual(result.status, "FAILED")
        self.assertIn("insert exploded", result.error)
        self.assertEqual(warehouse.rollbacks, 1)
        # The mark must not move when the load failed.
        self.assertIsNone(watermarks.get("LeadApplicationStages"))


class TestSnapshot(unittest.TestCase):
    def test_replaces_the_target(self):
        result, warehouse, _ = run(LENDERS, [(1, "A"), (2, "B")])
        self.assertEqual(result.rows, 2)
        self.assertTrue(
            any("TRUNCATE TABLE" in sql for kind, sql, _ in warehouse.log if kind == "execute")
        )
        self.assertEqual(warehouse.commits, 1)

    def test_truncate_happens_before_insert(self):
        _, warehouse, _ = run(LENDERS, [(1, "A")])
        kinds = [kind for kind, _, _ in warehouse.log]
        self.assertLess(kinds.index("execute"), kinds.index("executemany"))

    def test_empty_source_still_clears_the_target(self):
        # Otherwise a source that legitimately emptied leaves stale rows
        # in the warehouse forever.
        _, warehouse, _ = run(LENDERS, [])
        self.assertTrue(
            any("TRUNCATE TABLE" in sql for kind, sql, _ in warehouse.log if kind == "execute")
        )
        self.assertEqual(warehouse.commits, 1)

    def test_does_not_touch_the_watermark(self):
        _, _, watermarks = run(LENDERS, [(1, "A")])
        self.assertEqual(watermarks.history, [])


class TestWatermarkStore(unittest.TestCase):
    def test_refuses_to_go_backwards(self):
        store = InMemoryWatermarkStore({"T": 100})
        with self.assertRaises(ValueError) as ctx:
            store.set("T", 50, 0)
        self.assertIn("backwards", str(ctx.exception))

    def test_allows_the_same_value(self):
        store = InMemoryWatermarkStore({"T": 100})
        store.set("T", 100, 0)
        self.assertEqual(store.get("T"), 100)


class TestDryRun(unittest.TestCase):
    def test_touches_nothing(self):
        warehouse = FakeWarehouseConnection()
        result = extract_table(
            STAGES,
            source_conn=None,
            warehouse_conn=warehouse,
            watermarks=InMemoryWatermarkStore(),
            batch_size=10,
            dry_run=True,
        )
        self.assertEqual(result.status, "skipped")
        self.assertEqual(warehouse.log, [])
        self.assertEqual(warehouse.commits, 0)


class TestSummarise(unittest.TestCase):
    def test_reports_failures(self):
        result, _, _ = run(STAGES, [(1, 1)], batch_size=5)
        text = summarise([result])
        self.assertIn("LeadApplicationStages", text)
        self.assertIn("0 failure(s)", text)


if __name__ == "__main__":
    unittest.main()
