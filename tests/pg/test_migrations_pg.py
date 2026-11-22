"""The Alembic migrations on a real Postgres: backfills, constraints, cascades and a round trip."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db.models import Workspace
from backend.db.session import Base
from tests.pg import pgutil

pytestmark = pytest.mark.pg

TABLES = [
    "workspace",
    "agent",
    "event",
    "behavior_map",
    "prompt_version",
    "run",
    "criterion",
    "test_case",
    "test_result",
    "job",
]

# Valid at revision 0002 and at head (the columns added by 0003 are nullable or have server defaults).
ROWS_0002 = [
    "INSERT INTO workspace (id, token_hash, created_at, expires_at) "
    "VALUES ('w', 'h', '2026-01-01', '2026-02-01')",
    "INSERT INTO agent (id, workspace_id, name, user_role_label, last_checked_at_run, created_at, "
    "current_prompt_version_id) VALUES ('a', 'w', 'n', 'r', 0, '2026-01-01', 'p2')",
    "INSERT INTO event (id, workspace_id, name, properties, created_at) "
    "VALUES ('e', 'w', 'agent_created', '{}', '2026-01-01')",
    "INSERT INTO behavior_map (id, agent_id, prompt_hash, analysis) VALUES ('bm', 'a', 'x', '{}')",
    "INSERT INTO prompt_version (id, agent_id, text, origin, status, number, created_at) "
    "VALUES ('p1', 'a', 't', 'initial', 'superseded', 1, '2026-01-01')",
    "INSERT INTO prompt_version (id, agent_id, text, origin, status, number, created_at) "
    "VALUES ('p2', 'a', 't2', 'manual', 'promoted', 2, '2026-01-02')",
    "INSERT INTO run (id, agent_id, prompt_version_id, position, messages, created_at) "
    "VALUES ('r', 'a', 'p1', 1, '[]', '2026-01-01')",
    "INSERT INTO criterion (id, agent_id, position, name, definition, good_example, bad_example, "
    "why_discovered, source, created_at) VALUES ('c', 'a', 1, 'n', 'd', 'g', 'b', 'w', 'discovered', '2026-01-01')",
    "INSERT INTO test_case (id, criterion_id, role, scripted_user_messages, created_at) "
    "VALUES ('t', 'c', 'fix', '[\"hi\"]', '2026-01-01')",
    "INSERT INTO test_result (id, test_id, prompt_version_id, kind, verdict, rationale, transcript, stale, "
    "created_at) VALUES ('tr', 't', 'p2', 'replay', 'fail', 'because', '[]', false, '2026-01-01')",
    "INSERT INTO job (id, workspace_id, type, payload, status, attempts, created_at) "
    "VALUES ('j', 'w', 'replay', '{}', 'queued', 0, '2026-01-01')",
]


def _fresh(pg_engine, pg_url, revision):
    pgutil.reset_schema(pg_engine)
    pgutil.alembic_upgrade(pg_url, revision)


def _insert_rows(engine):
    with engine.begin() as conn:
        for sql in ROWS_0002:
            conn.execute(text(sql))


def _counts(engine):
    with engine.connect() as conn:
        return {t: conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one() for t in TABLES}


def _columns(engine, table):
    return {c["name"] for c in inspect(engine).get_columns(table)}


def _uniques(engine, table):
    return {u["name"] for u in inspect(engine).get_unique_constraints(table)}


def test_upgrade_from_0002_backfills_and_keeps_rows(pg_engine, pg_url):
    _fresh(pg_engine, pg_url, "0002")
    _insert_rows(pg_engine)
    assert set(_counts(pg_engine).values()) == {1, 2}  # prompt_version holds two, the rest one

    pgutil.alembic_upgrade(pg_url, "head")

    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT origin FROM test_case WHERE id = 't'")).scalar_one() == "run"
        assert conn.execute(text("SELECT revision FROM criterion WHERE id = 'c'")).scalar_one() == 0
        rows = conn.execute(
            text("SELECT id, number, change_report, verification, source_test_id FROM prompt_version ORDER BY number")
        ).all()
        assert [tuple(r) for r in rows] == [("p1", 1, None, None, None), ("p2", 2, None, None, None)]
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0003"
    assert _counts(pg_engine)["prompt_version"] == 2


def test_unique_agent_number_rejects_a_duplicate(pg_engine, pg_url):
    _fresh(pg_engine, pg_url, "0002")
    _insert_rows(pg_engine)
    pgutil.alembic_upgrade(pg_url, "head")

    assert "uq_prompt_version_agent_number" in _uniques(pg_engine, "prompt_version")
    with pytest.raises(IntegrityError):
        with pg_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO prompt_version (id, agent_id, text, origin, status, number, created_at) "
                    "VALUES ('p3', 'a', 't', 'suggested', 'proposed', 2, '2026-01-03')"
                )
            )
    # The same number under another agent is fine.
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO agent (id, workspace_id, name, user_role_label, last_checked_at_run, created_at) "
                "VALUES ('a2', 'w', 'n2', 'r', 0, '2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO prompt_version (id, agent_id, text, origin, status, number, created_at) "
                "VALUES ('p4', 'a2', 't', 'initial', 'promoted', 2, '2026-01-03')"
            )
        )


def test_models_match_the_migrated_schema(pg_engine, pg_url):
    _fresh(pg_engine, pg_url, "head")
    with pg_engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []


def test_deleting_a_workspace_cascades_through_every_table_in_the_database(pg_engine, pg_url):
    _fresh(pg_engine, pg_url, "0002")
    _insert_rows(pg_engine)
    pgutil.alembic_upgrade(pg_url, "head")
    assert all(count >= 1 for count in _counts(pg_engine).values())

    with pg_engine.begin() as conn:
        conn.execute(text("DELETE FROM workspace WHERE id = 'w'"))

    assert _counts(pg_engine) == {table: 0 for table in TABLES}


def test_deleting_a_workspace_cascades_through_the_orm_too(pg_engine, pg_url):
    """The path purge_expired takes: the ORM must delete children in an order Postgres accepts."""
    _fresh(pg_engine, pg_url, "0002")
    _insert_rows(pg_engine)
    pgutil.alembic_upgrade(pg_url, "head")

    with Session(pg_engine) as db:
        db.delete(db.get(Workspace, "w"))
        db.commit()

    assert _counts(pg_engine) == {table: 0 for table in TABLES}


def test_downgrade_to_0002_then_upgrade_again(pg_engine, pg_url):
    _fresh(pg_engine, pg_url, "0002")
    _insert_rows(pg_engine)
    pgutil.alembic_upgrade(pg_url, "head")

    pgutil.alembic_downgrade(pg_url, "0002")

    assert not {"change_report", "verification", "source_test_id"} & _columns(pg_engine, "prompt_version")
    assert "origin" not in _columns(pg_engine, "test_case")
    assert "revision" not in _columns(pg_engine, "criterion")
    assert "uq_prompt_version_agent_number" not in _uniques(pg_engine, "prompt_version")
    assert _counts(pg_engine)["prompt_version"] == 2

    pgutil.alembic_upgrade(pg_url, "head")

    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT origin FROM test_case WHERE id = 't'")).scalar_one() == "run"
        assert conn.execute(text("SELECT revision FROM criterion WHERE id = 'c'")).scalar_one() == 0
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
    assert "uq_prompt_version_agent_number" in _uniques(pg_engine, "prompt_version")
    assert _counts(pg_engine)["prompt_version"] == 2
