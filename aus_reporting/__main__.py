"""Command line entry point: ``python -m aus_reporting``."""

from __future__ import annotations

import argparse
import logging
import sys
from functools import partial
from pathlib import Path

from . import db
from .config import Settings, load_plan
from .errors import AusReportingError
from .runner import run_plan, summarise
from .watermark import InMemoryWatermarkStore, SqlWatermarkStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m aus_reporting",
        description="Extract source tables into the reporting warehouse.",
    )
    parser.add_argument(
        "--config", default="tables.yml", type=Path, help="path to tables.yml"
    )
    parser.add_argument(
        "--only", action="append", default=[],
        help="limit to these staging targets; repeatable",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="validate config and print the SQL without connecting to anything",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    try:
        plan = load_plan(args.config)
    except AusReportingError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    if args.only:
        wanted = set(args.only)
        unknown = wanted - {s.target for s in plan.specs}
        if unknown:
            print(f"unknown target(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        plan = type(plan)(specs=tuple(s for s in plan.specs if s.target in wanted))

    print(f"{len(plan)} table(s) across {len(plan.databases)} database(s)")

    if args.dry_run:
        # No settings needed: nothing connects, so no credentials are read.
        class _Noop:
            batch_size = 50_000

        from contextlib import nullcontext

        results = run_plan(
            plan,
            settings=_Noop(),
            open_source=lambda _db: nullcontext(None),
            warehouse_conn=None,
            watermarks=InMemoryWatermarkStore(),
            dry_run=True,
        )
        print(summarise(results))
        print("\ndry run: config is valid and no column is disallowed")
        return 0

    try:
        settings = Settings.from_env()
    except AusReportingError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    with db.warehouse(settings) as warehouse_conn:
        results = run_plan(
            plan,
            settings=settings,
            open_source=partial(db.source, settings),
            warehouse_conn=warehouse_conn,
            watermarks=SqlWatermarkStore(warehouse_conn),
        )

    print(summarise(results))
    return 1 if any(r.error for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
