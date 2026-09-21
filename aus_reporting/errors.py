"""Error types shared across the package.

A single base means the CLI can catch every configuration problem in one
place and report it as a message rather than a traceback, while callers
that care about the distinction can still catch the specific type.
"""

from __future__ import annotations


class AusReportingError(Exception):
    """Base for every error this package raises deliberately."""


class ConfigError(AusReportingError, ValueError):
    """``tables.yml`` or the environment is malformed."""


class BlockedColumnError(AusReportingError, ValueError):
    """A spec named a column that may never be extracted."""


class UnapprovedRestrictedColumnError(AusReportingError, ValueError):
    """A spec named a personal identifier without approving it."""


class SpecError(AusReportingError, ValueError):
    """A table spec is internally inconsistent."""
