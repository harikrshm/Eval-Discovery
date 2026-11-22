"""Decide whether the real-Postgres tests may run, and refuse to touch a database that is not a test one.

Pure logic over an env mapping (no database access), so it is unit-tested without Postgres.
The skip reasons never contain the URL, so a password cannot leak into test output.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

ENV_URL = "TEST_DATABASE_URL"
ENV_ALLOW_RESET = "ALLOW_PG_TEST_RESET"
DRIVER = "postgresql+psycopg"


@dataclass(frozen=True)
class PgDecision:
    """`url` is set when the tests may run; otherwise `skip_reason` says why not."""

    url: str | None
    skip_reason: str | None
    database: str | None = None


def _skip(reason: str) -> PgDecision:
    return PgDecision(url=None, skip_reason=reason)


def database_name_allowed(name: str, env: Mapping[str, str]) -> bool:
    """A database may be reset only when its name contains 'test' or the override is set to 1."""
    return "test" in name.lower() or env.get(ENV_ALLOW_RESET) == "1"


def decide(env: Mapping[str, str]) -> PgDecision:
    raw = (env.get(ENV_URL) or "").strip()
    if not raw:
        return _skip(f"{ENV_URL} is not set; real-Postgres tests are skipped.")
    try:
        url = make_url(raw)
    except ArgumentError:
        return _skip(f"{ENV_URL} is not a valid database URL.")
    if url.drivername != DRIVER:
        return _skip(f"{ENV_URL} must use the {DRIVER}:// driver.")
    name = url.database
    if not name:
        return _skip(f"{ENV_URL} must name a database.")
    if not database_name_allowed(name, env):
        return _skip(
            f"Refusing to reset database {name!r}: its name does not contain 'test'. "
            f"Set {ENV_ALLOW_RESET}=1 to allow it."
        )
    return PgDecision(url=raw, skip_reason=None, database=name)
