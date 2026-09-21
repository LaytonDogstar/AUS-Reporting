"""Tracking how far each incremental table has been read.

The store lives in the warehouse, not the source: the source is
read-only, and the warehouse is where "what have we already loaded" and
"what is loaded" belong together so they cannot disagree.
"""

from __future__ import annotations

from typing import Protocol


class WatermarkStore(Protocol):
    """Read and advance the high-water mark for a table."""

    def get(self, target: str) -> int | None:
        """Last value loaded, or ``None`` if the table has never run."""

    def set(self, target: str, value: int, rows_loaded: int) -> None:
        """Record a new high-water mark."""


class InMemoryWatermarkStore:
    """For tests and dry runs."""

    def __init__(self, initial: dict[str, int] | None = None) -> None:
        self._values: dict[str, int] = dict(initial or {})
        self.history: list[tuple[str, int, int]] = []

    def get(self, target: str) -> int | None:
        return self._values.get(target)

    def set(self, target: str, value: int, rows_loaded: int) -> None:
        current = self._values.get(target)
        if current is not None and value < current:
            # Going backwards would silently re-load rows and, worse,
            # suggest the source was rewound underneath us.
            raise ValueError(
                f"{target}: refusing to move the watermark backwards "
                f"({current} -> {value})"
            )
        self._values[target] = value
        self.history.append((target, value, rows_loaded))


class SqlWatermarkStore:
    """The real store: ``ctl.ExtractWatermark`` in the warehouse."""

    def __init__(self, connection) -> None:
        self._connection = connection

    def get(self, target: str) -> int | None:
        cursor = self._connection.cursor()
        cursor.execute(
            "SELECT LastValue FROM ctl.ExtractWatermark WHERE TargetTable = ?;",
            target,
        )
        row = cursor.fetchone()
        return None if row is None else row[0]

    def set(self, target: str, value: int, rows_loaded: int) -> None:
        current = self.get(target)
        if current is not None and value < current:
            raise ValueError(
                f"{target}: refusing to move the watermark backwards "
                f"({current} -> {value})"
            )

        cursor = self._connection.cursor()
        cursor.execute(
            """
            MERGE ctl.ExtractWatermark AS t
            USING (SELECT ? AS TargetTable, ? AS LastValue, ? AS RowsLoaded) AS s
               ON t.TargetTable = s.TargetTable
            WHEN MATCHED THEN UPDATE SET
                 t.LastValue      = s.LastValue,
                 t.RowsLoaded     = s.RowsLoaded,
                 t.LastRunUtc     = SYSUTCDATETIME()
            WHEN NOT MATCHED THEN INSERT
                 (TargetTable, LastValue, RowsLoaded, LastRunUtc)
                 VALUES (s.TargetTable, s.LastValue, s.RowsLoaded, SYSUTCDATETIME());
            """,
            target, value, rows_loaded,
        )
        self._connection.commit()
