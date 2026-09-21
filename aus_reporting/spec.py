"""Table specs and the SQL generated from them.

Everything here is pure: no connection, no I/O. That is deliberate -
the statements the extract will run against production are the part most
worth unit-testing, and they are testable without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import SpecError
from .sensitive import check_columns

INCREMENTAL = "incremental"
SNAPSHOT = "snapshot"
MODES = (INCREMENTAL, SNAPSHOT)

# Watermarking relies on a monotonically increasing key, so only the
# integer identity columns qualify. A uniqueidentifier primary key is
# random, and a date column is unindexed in both source databases, so
# neither can be used. See ARCHITECTURE.md.
WATERMARK_TYPES = ("int", "bigint")


def quote_ident(name: str) -> str:
    """Bracket-quote a SQL Server identifier."""
    if not name or not name.strip():
        raise ValueError("identifier must be a non-empty string")
    return "[" + name.replace("]", "]]") + "]"


def quote_object(schema: str, table: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(table)}"


@dataclass(frozen=True)
class TableSpec:
    """How one source table is extracted."""

    database: str
    table: str
    mode: str
    columns: tuple[str, ...]
    target: str
    schema: str = "dbo"
    watermark_column: str | None = None
    restricted_approved: tuple[str, ...] = ()
    note: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise SpecError(
                f"{self.qualified}: mode must be one of {MODES}, got {self.mode!r}"
            )
        if not self.columns:
            raise SpecError(f"{self.qualified}: no columns listed")

        seen: dict[str, str] = {}
        for column in self.columns:
            key = column.lower()
            if key in seen:
                raise SpecError(
                    f"{self.qualified}: column {column!r} listed twice "
                    f"(also as {seen[key]!r})"
                )
            seen[key] = column

        if self.mode == INCREMENTAL:
            if not self.watermark_column:
                raise SpecError(
                    f"{self.qualified}: incremental mode needs a watermark_column"
                )
            if self.watermark_column.lower() not in seen:
                # Without it in the projection there is nothing to advance
                # the watermark from after a batch.
                raise SpecError(
                    f"{self.qualified}: watermark_column "
                    f"{self.watermark_column!r} must also appear in columns"
                )
        elif self.watermark_column:
            raise SpecError(
                f"{self.qualified}: watermark_column is meaningless in "
                f"{SNAPSHOT} mode"
            )

        check_columns(
            self.columns,
            approved=self.restricted_approved,
            where=self.qualified,
        )

    @property
    def qualified(self) -> str:
        return f"{self.database}.{self.schema}.{self.table}"

    @property
    def column_list(self) -> str:
        return ", ".join(quote_ident(c) for c in self.columns)

    def select_statement(
        self,
        *,
        last_value: int | None = None,
        batch_size: int | None = None,
    ) -> tuple[str, tuple]:
        """Build the SELECT for this table.

        Returns ``(sql, params)`` ready for a parameterised execute. For
        incremental specs the statement is a clustered-index seek on the
        watermark column, which is why it costs the same on 194M rows as
        on 1M.
        """
        target = quote_object(self.schema, self.table)

        if self.mode == SNAPSHOT:
            return f"SELECT {self.column_list}\nFROM {target};", ()

        watermark = quote_ident(self.watermark_column)  # type: ignore[arg-type]
        top = "TOP (?) " if batch_size is not None else ""

        sql = (
            f"SELECT {top}{self.column_list}\n"
            f"FROM {target}\n"
            f"WHERE {watermark} > ?\n"
            f"ORDER BY {watermark};"
        )

        # A NULL watermark means "never run"; -1 precedes every identity
        # seed SQL Server will allocate by default.
        effective = -1 if last_value is None else last_value
        params: tuple = (batch_size, effective) if batch_size is not None else (effective,)
        return sql, params

    def insert_statement(self, staging_schema: str = "stg") -> str:
        placeholders = ", ".join("?" for _ in self.columns)
        return (
            f"INSERT INTO {quote_object(staging_schema, self.target)} "
            f"({self.column_list}) VALUES ({placeholders});"
        )

    def truncate_statement(self, staging_schema: str = "stg") -> str:
        if self.mode != SNAPSHOT:
            raise SpecError(
                f"{self.qualified}: refusing to truncate an {INCREMENTAL} "
                f"target; that would discard history the watermark will not "
                f"re-read"
            )
        return f"TRUNCATE TABLE {quote_object(staging_schema, self.target)};"


@dataclass(frozen=True)
class ExtractPlan:
    """The set of specs to run, grouped by source database.

    Grouping matters: Azure SQL Database cannot switch database on a
    connection, so each database needs its own.
    """

    specs: tuple[TableSpec, ...] = field(default_factory=tuple)

    @property
    def databases(self) -> tuple[str, ...]:
        seen: list[str] = []
        for spec in self.specs:
            if spec.database not in seen:
                seen.append(spec.database)
        return tuple(seen)

    def for_database(self, database: str) -> tuple[TableSpec, ...]:
        return tuple(s for s in self.specs if s.database == database)

    def __len__(self) -> int:
        return len(self.specs)
