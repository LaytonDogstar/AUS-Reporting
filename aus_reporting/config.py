"""Loading table specs from ``tables.yml`` and settings from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from .errors import ConfigError
from .spec import ExtractPlan, TableSpec

_ALLOWED_KEYS = {
    "table", "schema", "mode", "columns", "target",
    "watermark_column", "restricted_approved", "note",
}


def load_plan(path: str | Path) -> ExtractPlan:
    """Read ``tables.yml`` into an :class:`ExtractPlan`.

    Every spec is validated on construction, so a bad column or a
    missing watermark fails here rather than part-way through a run
    against production.
    """
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"no such config file: {path}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: not valid YAML: {exc}") from None

    if not isinstance(raw, dict) or "databases" not in raw:
        raise ConfigError(f"{path}: expected a top-level 'databases' mapping")

    specs: list[TableSpec] = []
    targets: dict[str, str] = {}

    for database, tables in (raw["databases"] or {}).items():
        if not isinstance(tables, list):
            raise ConfigError(
                f"{path}: databases.{database} should be a list of tables"
            )
        for entry in tables:
            if not isinstance(entry, dict):
                raise ConfigError(
                    f"{path}: databases.{database} contains a non-mapping entry"
                )

            unknown = set(entry) - _ALLOWED_KEYS
            if unknown:
                raise ConfigError(
                    f"{path}: {database}.{entry.get('table', '?')} has "
                    f"unrecognised key(s): {', '.join(sorted(unknown))}"
                )

            missing = {"table", "mode", "columns", "target"} - set(entry)
            if missing:
                raise ConfigError(
                    f"{path}: {database}.{entry.get('table', '?')} is missing "
                    f"{', '.join(sorted(missing))}"
                )

            spec = TableSpec(
                database=database,
                table=entry["table"],
                schema=entry.get("schema", "dbo"),
                mode=entry["mode"],
                columns=tuple(entry["columns"]),
                target=entry["target"],
                watermark_column=entry.get("watermark_column"),
                restricted_approved=tuple(entry.get("restricted_approved", ())),
                note=entry.get("note"),
            )

            # Two sources landing in one staging table would interleave
            # silently, and the watermark would be meaningless.
            if spec.target in targets:
                raise ConfigError(
                    f"{path}: target {spec.target!r} is used by both "
                    f"{targets[spec.target]} and {spec.qualified}"
                )
            targets[spec.target] = spec.qualified
            specs.append(spec)

    if not specs:
        raise ConfigError(f"{path}: no tables configured")

    return ExtractPlan(specs=tuple(specs))


@dataclass(frozen=True)
class Settings:
    """Connection settings, read from the environment.

    The source and the warehouse are separate logical servers with
    separate logins: the source login is read-only and issued by the
    Azure admins, while the warehouse is ours. They are configured
    independently, and the warehouse falls back to the source
    credentials only when it is not given its own - which is the
    unusual case, not the default.

    Credentials are never read from ``tables.yml`` or any file in this
    repository. On a server, set these from Key Vault.
    """

    source_server: str
    source_username: str
    source_password: str
    warehouse_server: str
    warehouse_database: str
    warehouse_username: str
    warehouse_password: str
    driver: str = "ODBC Driver 18 for SQL Server"
    batch_size: int = 50_000
    login_timeout: int = 30

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        src = dict(os.environ if env is None else env)

        required = (
            "AUS_SOURCE_SERVER",
            "AUS_SOURCE_USERNAME",
            "AUS_SOURCE_PASSWORD",
            "AUS_WAREHOUSE_SERVER",
            "AUS_WAREHOUSE_DATABASE",
        )
        missing = [key for key in required if not src.get(key)]
        if missing:
            raise ConfigError(
                "missing required environment variable(s): "
                + ", ".join(missing)
                + ". See .env.example."
            )

        # Same server for both is possible but not assumed; when the
        # warehouse has its own login, it is used.
        warehouse_username = src.get("AUS_WAREHOUSE_USERNAME") or src["AUS_SOURCE_USERNAME"]
        warehouse_password = src.get("AUS_WAREHOUSE_PASSWORD") or src["AUS_SOURCE_PASSWORD"]

        return cls(
            source_server=src["AUS_SOURCE_SERVER"],
            source_username=src["AUS_SOURCE_USERNAME"],
            source_password=src["AUS_SOURCE_PASSWORD"],
            warehouse_server=src["AUS_WAREHOUSE_SERVER"],
            warehouse_database=src["AUS_WAREHOUSE_DATABASE"],
            warehouse_username=warehouse_username,
            warehouse_password=warehouse_password,
            driver=src.get("AUS_ODBC_DRIVER", cls.driver),
            batch_size=int(src.get("AUS_BATCH_SIZE", cls.batch_size)),
            login_timeout=int(src.get("AUS_LOGIN_TIMEOUT", cls.login_timeout)),
        )

    def __repr__(self) -> str:  # keep passwords out of tracebacks and logs
        return (
            f"Settings(source_server={self.source_server!r}, "
            f"source_username={self.source_username!r}, "
            f"source_password=<redacted>, "
            f"warehouse_server={self.warehouse_server!r}, "
            f"warehouse_database={self.warehouse_database!r}, "
            f"warehouse_username={self.warehouse_username!r}, "
            f"warehouse_password=<redacted>, "
            f"driver={self.driver!r}, batch_size={self.batch_size})"
        )
