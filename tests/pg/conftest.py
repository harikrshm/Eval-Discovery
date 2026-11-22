"""Fixtures for the real-Postgres tests.

Everything here depends on `pg_url`, which skips unless TEST_DATABASE_URL is a postgresql+psycopg
URL whose database name contains 'test' (or ALLOW_PG_TEST_RESET=1). Without it every test skips and
nothing connects anywhere.
"""

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.pg import guard, pgutil


@pytest.fixture(scope="session")
def pg_url():
    decision = guard.decide(os.environ)
    if decision.skip_reason is not None:
        pytest.skip(decision.skip_reason)
    return decision.url


@pytest.fixture(scope="module")
def pg_engine(pg_url):
    """An engine on the test database with an empty `public` schema (reset once per module)."""
    engine = create_engine(pg_url, pool_pre_ping=True)
    try:
        pgutil.reset_schema(engine)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def pg_migrated(pg_engine, pg_url):
    """`pg_engine` with the schema upgraded to head by Alembic."""
    pgutil.alembic_upgrade(pg_url, "head")
    return pg_engine


@pytest.fixture
def pg_clean(pg_migrated):
    """The migrated engine with every table emptied before the test."""
    pgutil.truncate_all(pg_migrated)
    return pg_migrated


@pytest.fixture
def pg_sessions(pg_clean):
    """A session factory like the app's (expire_on_commit=False); each call makes a new session."""
    return sessionmaker(bind=pg_clean, expire_on_commit=False)


@pytest.fixture
def pg_client_factory(pg_sessions):
    """Build TestClients whose `get_db` yields a NEW session per request from the Postgres engine.

    Not used as context managers, so there is no lifespan worker. Each racing thread builds its own
    client because a TestClient is not guaranteed to be thread-safe.
    """
    from fastapi.testclient import TestClient

    from backend.db.session import get_db
    from backend.main import app

    def override_get_db():
        db = pg_sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield lambda: TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def pg_client(pg_client_factory):
    return pg_client_factory()
