"""Helpers for the real-Postgres tests: schema reset, Alembic runs and concurrent requests."""

import os
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url

from backend.db.session import Base
from tests.pg import guard

ROOT = Path(__file__).resolve().parents[2]


def reset_schema(engine: Engine) -> None:
    """DROP SCHEMA public CASCADE; CREATE SCHEMA public, after re-checking which database this is.

    Refuses (raises) unless the connected database passes the same name rule as the skip guard, so
    a mistake in wiring can never wipe a database that is not a test one.
    """
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        name = conn.execute(text("SELECT current_database()")).scalar_one()
        if not guard.database_name_allowed(name, os.environ):
            raise RuntimeError(f"Refusing to reset database {name!r}.")
        conn.execute(text("SET lock_timeout = '15s'"))
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


def truncate_all(engine: Engine) -> None:
    """Empty every application table (the Alembic version table is kept)."""
    names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))


@contextmanager
def _database_url_env(url: str) -> Iterator[None]:
    """migrations/env.py reads DATABASE_URL, so point it at the test database for one call."""
    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old


def _config() -> Config:
    return Config(str(ROOT / "alembic.ini"))


def alembic_upgrade(url: str, revision: str = "head") -> None:
    _assert_test_url(url)
    with _database_url_env(url):
        command.upgrade(_config(), revision)


def alembic_downgrade(url: str, revision: str) -> None:
    _assert_test_url(url)
    with _database_url_env(url):
        command.downgrade(_config(), revision)


def _assert_test_url(url: str) -> None:
    name = make_url(url).database or ""
    if not guard.database_name_allowed(name, os.environ):
        raise RuntimeError(f"Refusing to run migrations against database {name!r}.")


def race(client_factory: Callable, requests: Sequence[tuple[str, str, dict | None]], headers: dict):
    """Fire the requests at the same instant, each from its own thread with its own client.

    `requests` is a list of (method, path, json body). Threads build their client first, then wait
    on a barrier so the calls start together. Returns the responses in request order; an error in
    any thread is re-raised here.
    """
    count = len(requests)
    barrier = threading.Barrier(count, timeout=30)
    responses: list = [None] * count
    errors: list[BaseException] = []

    def worker(index: int, method: str, path: str, body: dict | None) -> None:
        try:
            client = client_factory()
            barrier.wait()
            responses[index] = client.request(method, path, headers=headers, json=body)
        except BaseException as exc:  # noqa: BLE001 - reported to the main thread below
            errors.append(exc)
            barrier.abort()

    threads = [
        threading.Thread(target=worker, args=(i, *request), daemon=True) for i, request in enumerate(requests)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=90)
    assert not any(thread.is_alive() for thread in threads), "a racing request did not finish"
    if errors:
        raise errors[0]
    return responses
