"""Executing an extract plan."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .spec import INCREMENTAL, SNAPSHOT, TableSpec
from .watermark import WatermarkStore

log = logging.getLogger(__name__)


@dataclass
class TableResult:
    spec: TableSpec
    rows: int = 0
    batches: int = 0
    new_watermark: int | None = None
    skipped: bool = False
    error: str | None = None

    @property
    def status(self) -> str:
        if self.error:
            return "FAILED"
        if self.skipped:
            return "skipped"
        return "ok"


def extract_table(
    spec: TableSpec,
    *,
    source_conn,
    warehouse_conn,
    watermarks: WatermarkStore,
    batch_size: int,
    staging_schema: str = "stg",
    dry_run: bool = False,
) -> TableResult:
    """Copy one table from source into its staging target.

    Incremental specs read ``WHERE watermark > last`` in batches and
    advance the mark per batch, so an interrupted run resumes rather
    than restarting. Snapshot specs replace the target inside a single
    transaction, so a reader never sees a half-loaded table.
    """
    result = TableResult(spec=spec)

    if dry_run:
        last = watermarks.get(spec.target)
        sql, params = spec.select_statement(
            last_value=last, batch_size=batch_size if spec.mode == INCREMENTAL else None
        )
        log.info("[dry run] %s\n%s\nparams=%r", spec.qualified, sql, params)
        result.skipped = True
        return result

    try:
        if spec.mode == SNAPSHOT:
            result.rows = _load_snapshot(
                spec, source_conn, warehouse_conn, staging_schema
            )
            result.batches = 1
        else:
            result.rows, result.batches, result.new_watermark = _load_incremental(
                spec, source_conn, warehouse_conn, watermarks, batch_size, staging_schema
            )
    except Exception as exc:  # surfaced per table; one failure must not stop the run
        log.exception("%s failed", spec.qualified)
        result.error = f"{type(exc).__name__}: {exc}"

    return result


def _load_snapshot(spec, source_conn, warehouse_conn, staging_schema: str) -> int:
    sql, params = spec.select_statement()
    rows = source_conn.cursor().execute(sql, *params).fetchall()

    cursor = warehouse_conn.cursor()
    try:
        cursor.execute(spec.truncate_statement(staging_schema))
        if rows:
            cursor.fast_executemany = True
            cursor.executemany(spec.insert_statement(staging_schema), rows)
        warehouse_conn.commit()
    except Exception:
        warehouse_conn.rollback()
        raise
    return len(rows)


def _load_incremental(
    spec, source_conn, warehouse_conn, watermarks, batch_size, staging_schema: str
):
    last = watermarks.get(spec.target)
    watermark_index = [c.lower() for c in spec.columns].index(
        spec.watermark_column.lower()
    )

    insert_sql = spec.insert_statement(staging_schema)
    total = 0
    batches = 0

    while True:
        sql, params = spec.select_statement(last_value=last, batch_size=batch_size)
        rows = source_conn.cursor().execute(sql, *params).fetchall()
        if not rows:
            break

        cursor = warehouse_conn.cursor()
        try:
            cursor.fast_executemany = True
            cursor.executemany(insert_sql, rows)
            warehouse_conn.commit()
        except Exception:
            warehouse_conn.rollback()
            raise

        # Rows are ordered by the watermark, so the last one is the max.
        last = rows[-1][watermark_index]
        total += len(rows)
        batches += 1

        # Committed before the mark moves, so a crash here re-reads the
        # batch rather than losing it. Staging must therefore tolerate
        # duplicates; the model layer de-duplicates on the primary key.
        watermarks.set(spec.target, last, total)

        if len(rows) < batch_size:
            break

    return total, batches, last


def run_plan(
    plan,
    *,
    settings,
    open_source,
    warehouse_conn,
    watermarks: WatermarkStore,
    staging_schema: str = "stg",
    dry_run: bool = False,
) -> list[TableResult]:
    """Run every spec in ``plan``, one source database at a time.

    ``open_source`` is a callable taking a database name and returning a
    context manager yielding a connection. Injected so the orchestration
    can be tested without a server.
    """
    results: list[TableResult] = []

    for database in plan.databases:
        specs = plan.for_database(database)
        log.info("%s: %d table(s)", database, len(specs))

        with open_source(database) as source_conn:
            for spec in specs:
                results.append(
                    extract_table(
                        spec,
                        source_conn=source_conn,
                        warehouse_conn=warehouse_conn,
                        watermarks=watermarks,
                        batch_size=settings.batch_size,
                        staging_schema=staging_schema,
                        dry_run=dry_run,
                    )
                )

    return results


def summarise(results: list[TableResult]) -> str:
    lines = [f"{'table':44s} {'status':8s} {'rows':>10s} {'batches':>8s}"]
    lines.append("-" * 74)
    for r in results:
        lines.append(
            f"{r.spec.qualified[:43]:44s} {r.status:8s} {r.rows:>10,} {r.batches:>8}"
        )
    failed = [r for r in results if r.error]
    lines.append("")
    lines.append(
        f"{len(results)} table(s), {sum(r.rows for r in results):,} row(s), "
        f"{len(failed)} failure(s)"
    )
    for r in failed:
        lines.append(f"  FAILED {r.spec.qualified}: {r.error}")
    return "\n".join(lines)
