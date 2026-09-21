"""Connections to the source databases and the warehouse.

``pyodbc`` is imported lazily so that the pure logic in :mod:`spec`,
:mod:`config` and :mod:`sensitive` can be imported and tested on a
machine with no ODBC driver installed.
"""

from __future__ import annotations

from contextlib import contextmanager

from .config import Settings


def connection_string(
    settings: Settings,
    *,
    server: str,
    database: str,
    username: str,
    password: str,
    readonly: bool = False,
) -> str:
    """Build an ODBC connection string.

    Encryption is always on and the server certificate is always
    verified: these are Azure SQL endpoints over the public internet.
    """
    parts = [
        f"DRIVER={{{settings.driver}}}",
        f"SERVER=tcp:{server},1433",
        f"DATABASE={database}",
        f"UID={username}",
        f"PWD={password}",
        "Encrypt=yes",
        "TrustServerCertificate=no",
        f"Connection Timeout={settings.login_timeout}",
        "APP=AUS-Reporting-Extract",
    ]
    if readonly:
        parts.append("ApplicationIntent=ReadOnly")
    return ";".join(parts) + ";"


@contextmanager
def connect(
    settings: Settings,
    *,
    server: str,
    database: str,
    username: str,
    password: str,
    readonly: bool = False,
):
    """Yield an open connection, closing it on the way out."""
    try:
        import pyodbc
    except ImportError:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "pyodbc is not installed. Run: pip install -r requirements.txt\n"
            "It also needs the Microsoft ODBC Driver 18 for SQL Server, which "
            "is a separate download and is usually already present on a "
            "machine with SSMS installed."
        ) from None

    conn = pyodbc.connect(
        connection_string(
            settings,
            server=server,
            database=database,
            username=username,
            password=password,
            readonly=readonly,
        )
    )
    try:
        # Reporting reads must never block the replication subscriber.
        conn.cursor().execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;")
        yield conn
    finally:
        conn.close()


@contextmanager
def source(settings: Settings, database: str):
    """Open a read-only connection to one source database."""
    with connect(
        settings,
        server=settings.source_server,
        database=database,
        username=settings.source_username,
        password=settings.source_password,
        readonly=True,
    ) as conn:
        yield conn


@contextmanager
def warehouse(settings: Settings):
    """Open a read-write connection to the warehouse."""
    with connect(
        settings,
        server=settings.warehouse_server,
        database=settings.warehouse_database,
        username=settings.warehouse_username,
        password=settings.warehouse_password,
    ) as conn:
        yield conn
