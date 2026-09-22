"""Build the dashboard: run the queries, write a self-contained HTML file.

Deliberately produces a FILE, not a server. No port to open, no service
to keep running, no authentication to configure, and it works from a
file share or an email attachment. Schedule it and the file refreshes
itself.

The page carries its data inline as JSON, so filtering and hovering
happen in the browser with no further queries. Nothing is fetched from
the internet when the page opens - it works offline.

    python -m dashboard.build --days 90 --out dashboard.html
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from decimal import Decimal
from pathlib import Path

from aus_reporting import db
from aus_reporting.config import Settings
from aus_reporting.errors import AusReportingError

from . import queries

log = logging.getLogger(__name__)

TEMPLATE = Path(__file__).with_name("template.html")
PLACEHOLDER = "/*__DASHBOARD_DATA__*/null"


def _jsonable(value):
    """Make a pyodbc row value JSON-safe."""
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()[:10]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def fetch(connection, sql: str, params: tuple = ()) -> list[dict]:
    cursor = connection.cursor().execute(sql, *params)
    columns = [c[0] for c in cursor.description]
    return [
        {col: _jsonable(val) for col, val in zip(columns, row)}
        for row in cursor.fetchall()
    ]


def collect(settings: Settings, days: int) -> dict:
    """Run every query and return the page's data payload.

    Queries are grouped by database because Azure SQL Database cannot
    join across the two - they are fetched separately and related in the
    page by AffiliateId.
    """
    by_database: dict[str, list[str]] = {}
    for name, (database, _) in queries.SOURCES.items():
        by_database.setdefault(database, []).append(name)

    results: dict[str, list[dict]] = {}

    for database, names in by_database.items():
        log.info("%s: %d quer(ies)", database, len(names))
        with db.source(settings, database) as connection:
            for name in names:
                _, sql = queries.SOURCES[name]
                params = (-abs(days),) if name in queries.WINDOWED else ()
                results[name] = fetch(connection, sql, params)
                log.info("  %-14s %6d row(s)", name, len(results[name]))

    return {
        "generatedUtc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "windowDays": days,
        "aestShiftHours": queries.AEST_SHIFT_HOURS,
        "stages": {str(k): v for k, v in queries.STAGES.items()},
        **results,
    }


def render(data: dict, template: Path = TEMPLATE) -> str:
    html = template.read_text(encoding="utf-8")
    if PLACEHOLDER not in html:
        raise AusReportingError(
            f"{template}: data placeholder {PLACEHOLDER!r} not found"
        )
    # separators keep the payload tight; the page can be a few MB.
    payload = json.dumps(data, separators=(",", ":"))
    # </script> inside a string would close the tag early.
    payload = payload.replace("</", "<\\/")
    return html.replace(PLACEHOLDER, payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m dashboard.build",
        description="Build the AUS reporting dashboard as a single HTML file.",
    )
    parser.add_argument("--days", type=int, default=90,
                        help="days of history to include (default 90)")
    parser.add_argument("--out", type=Path, default=Path("dashboard.html"),
                        help="output file (default dashboard.html)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    if args.days < 1:
        print("--days must be at least 1", file=sys.stderr)
        return 2

    try:
        settings = Settings.from_env()
    except AusReportingError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    started = dt.datetime.now()
    try:
        data = collect(settings, args.days)
    except AusReportingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    args.out.write_text(render(data), encoding="utf-8")
    seconds = (dt.datetime.now() - started).total_seconds()

    size_mb = args.out.stat().st_size / 1_048_576
    print(f"\nwrote {args.out} ({size_mb:.1f} MB) in {seconds:.1f}s")
    print(f"  {args.days} days, {len(data['stage_counts']):,} stage rows, "
          f"{len(data['applications']):,} application rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
