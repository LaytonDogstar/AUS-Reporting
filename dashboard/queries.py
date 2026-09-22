"""Loading the queries and stage metadata.

The SQL lives in ``dashboard/sql/*.sql`` and the stage labels in
``dashboard/stages.json`` so that both builders - this one and
``Build-Dashboard.ps1`` - read exactly the same definitions. Two
implementations of the same queries would drift, and the drift would
show up as two dashboards disagreeing rather than as an error.

All of it is read-only and runs under READ UNCOMMITTED, so it cannot
block the replication subscriber it reads from.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
SQL_DIR = HERE / "sql"
META_PATH = HERE / "stages.json"

_meta = json.loads(META_PATH.read_text(encoding="utf-8"))

# The source server runs UTC; the reporting day is AEST. Changing this
# to follow Sydney local time including AEDT is a change here and
# nowhere else - both builders substitute it into the SQL.
AEST_SHIFT_HOURS: int = _meta["aestShiftHours"]

# Which database each query runs against. Azure SQL Database cannot join
# across the two, so they are fetched separately and related in the page.
SOURCES_DB: dict[str, str] = dict(_meta["sources"])

# Queries taking the look-back window as their one parameter. The value
# passed is NEGATIVE days (DATEADD cannot take "-?" - a parameter marker
# may not carry a sign).
WINDOWED: set[str] = set(_meta["windowed"])

# Stage labels, supplied by the development team 22 Sep 2026. There is
# no lookup table for these in either source database, so this file is
# the only place they are recorded.
STAGES: dict[int, dict] = {int(k): v for k, v in _meta["stages"].items()}


def load_sql(name: str, window_days: int | None = None) -> str:
    """Read one query with its tokens substituted.

    ``window_days`` is coerced to an int before substitution. It is a
    token rather than a bound parameter because pyodbc uses ``?`` and
    SqlClient uses ``@name``, and these files are shared with the
    PowerShell builder - so neither placeholder style works for both.
    An integer is the only thing that may ever be substituted here.
    """
    path = SQL_DIR / f"{name}.sql"
    if not path.exists():
        raise FileNotFoundError(f"no such query: {path}")

    sql = path.read_text(encoding="utf-8").replace(
        "{{AEST_SHIFT_HOURS}}", str(int(AEST_SHIFT_HOURS))
    )
    if window_days is not None:
        # Strict: only a real int. bool is an int in Python, and a float
        # would truncate silently, so both are refused.
        if isinstance(window_days, bool) or not isinstance(window_days, int):
            raise TypeError(
                f"window_days must be an int, got {type(window_days).__name__}: "
                f"{window_days!r}. It is substituted into SQL, not bound, so "
                f"nothing else may reach it."
            )
        sql = sql.replace("{{WINDOW_DAYS}}", str(-abs(window_days)))
    return sql


def sources(window_days: int) -> dict[str, tuple[str, str]]:
    """name -> (database, ready-to-run sql) for a given look-back window."""
    return {
        name: (database, load_sql(name, window_days))
        for name, database in SOURCES_DB.items()
    }


# Unsubstituted, for tests and for reading. Use sources() to run.
SOURCES: dict[str, tuple[str, str]] = {
    name: (database, load_sql(name)) for name, database in SOURCES_DB.items()
}


def aest_day(column: str) -> str:
    """The expression used to turn a UTC column into an AEST day."""
    return f"CAST(DATEADD(HOUR, {AEST_SHIFT_HOURS}, {column}) AS date)"
