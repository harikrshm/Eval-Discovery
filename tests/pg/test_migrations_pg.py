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

ASSIST_TABLES = ["assist_page_view", "assist_question"]

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


# Valid at head only: an assist run (session id, source, brief) plus both signal tables.
ROWS_ASSIST = [
    "INSERT INTO run (id, agent_id, prompt_version_id, position, messages, source, session_id, brief_snapshot, "
    "created_at) VALUES ('r2', 'a', 'p2', 2, '[]', 'extension', 's1', '{}', '2026-01-02')",
    "INSERT INTO assist_page_view (session_id, workspace_id, patent_number, started_at, updated_at, dwell_ms, "
    "sections, bounce, source, assist_version) "
    "VALUES ('s1', 'w', 'US1', '2026-01-02', '2026-01-02', 10, '{}', false, 'extension', null)",
    "INSERT INTO assist_question (id, session_id, workspace_id, patent_number, text, intent, section_focus, "
    "answer, rating, rating_reason, reasked, citation_ok, source, assist_version, created_at) "
    "VALUES ('q', 's1', 'w', 'US1', 'what?', 'other', null, 'that', null, null, false, true, 'extension', 1, "
    "'2026-01-02')",
]


def _fresh(pg_engine, pg_url, revision):
    pgutil.reset_schema(pg_engine)
    pgutil.alembic_upgrade(pg_url, revision)


def _insert_rows(engine):
    with engine.begin() as conn:
        for sql in ROWS_0002:
            conn.execute(text(sql))


def _insert_assist_rows(engine):
    with engine.begin() as conn:
        for sql in ROWS_ASSIST:
            conn.execute(text(sql))


def _counts(engine, tables=TABLES):
    with engine.connect() as conn:
        return {t: conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one() for t in tables}


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
        assert conn.execute(text("SELECT kind FROM agent WHERE id = 'a'")).scalar_one() == "general"
        assert conn.execute(text("SELECT source FROM run WHERE id = 'r'")).scalar_one() == "manual"
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0005"
    assert _counts(pg_engine)["prompt_version"] == 2


def test_upgrade_from_0003_defaults_the_new_columns_and_keeps_rows(pg_engine, pg_url):
    _fresh(pg_engine, pg_url, "0003")
    _insert_rows(pg_engine)
    before = _counts(pg_engine)

    pgutil.alembic_upgrade(pg_url, "head")

    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT kind FROM agent")).scalars().all() == ["general"]
        row = conn.execute(text("SELECT source, session_id, brief_snapshot FROM run WHERE id = 'r'")).one()
        assert tuple(row) == ("manual", None, None)
        assert conn.execute(text("SELECT brief_snapshot FROM test_case WHERE id = 't'")).scalar_one() is None
    assert _counts(pg_engine) == before
    assert _counts(pg_engine, ASSIST_TABLES) == {"assist_page_view": 0, "assist_question": 0}


def test_unique_run_agent_session_allows_many_nulls_and_rejects_a_duplicate(pg_engine, pg_url):
    _fresh(pg_engine, pg_url, "0003")
    _insert_rows(pg_engine)
    pgutil.alembic_upgrade(pg_url, "head")
    assert "uq_run_agent_session" in _uniques(pg_engine, "run")

    def insert_run(run_id, position, session):
        with pg_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO run (id, agent_id, prompt_version_id, position, messages, session_id, created_at) "
                    f"VALUES ('{run_id}', 'a', 'p2', {position}, '[]', {session}, '2026-01-03')"
                )
            )

    insert_run("r3", 3, "null")
    insert_run("r4", 4, "null")  # NULL session ids are distinct
    insert_run("r5", 5, "'s1'")
    with pytest.raises(IntegrityError):
        insert_run("r6", 6, "'s1'")


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
    _insert_assist_rows(pg_engine)
    every_table = TABLES + ASSIST_TABLES
    assert all(count >= 1 for count in _counts(pg_engine, every_table).values())

    with pg_engine.begin() as conn:
        conn.execute(text("DELETE FROM workspace WHERE id = 'w'"))

    assert _counts(pg_engine, every_table) == {table: 0 for table in every_table}


def test_deleting_a_workspace_cascades_through_the_orm_too(pg_engine, pg_url):
    """The path purge_expired takes: the ORM must delete children in an order Postgres accepts."""
    _fresh(pg_engine, pg_url, "0002")
    _insert_rows(pg_engine)
    pgutil.alembic_upgrade(pg_url, "head")
    _insert_assist_rows(pg_engine)
    every_table = TABLES + ASSIST_TABLES
    assert all(count >= 1 for count in _counts(pg_engine, every_table).values())

    with Session(pg_engine) as db:
        db.delete(db.get(Workspace, "w"))
        db.commit()

    assert _counts(pg_engine, every_table) == {table: 0 for table in every_table}


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


def test_downgrade_to_0003_then_upgrade_again(pg_engine, pg_url):
    _fresh(pg_engine, pg_url, "0003")
    _insert_rows(pg_engine)
    pgutil.alembic_upgrade(pg_url, "head")

    pgutil.alembic_downgrade(pg_url, "0003")

    assert not set(ASSIST_TABLES) & set(inspect(pg_engine).get_table_names())
    assert "kind" not in _columns(pg_engine, "agent")
    assert not {"source", "session_id", "brief_snapshot"} & _columns(pg_engine, "run")
    assert "brief_snapshot" not in _columns(pg_engine, "test_case")
    assert "uq_run_agent_session" not in _uniques(pg_engine, "run")
    assert _counts(pg_engine)["run"] == 1

    pgutil.alembic_upgrade(pg_url, "head")

    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT kind FROM agent WHERE id = 'a'")).scalar_one() == "general"
        assert conn.execute(text("SELECT source FROM run WHERE id = 'r'")).scalar_one() == "manual"
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
    assert "uq_run_agent_session" in _uniques(pg_engine, "run")
    assert _counts(pg_engine)["run"] == 1


def test_upgrade_from_0004_adds_a_null_pattern_run_ids_and_keeps_rows(pg_engine, pg_url):
    _fresh(pg_engine, pg_url, "0004")
    _insert_rows(pg_engine)
    assert "pattern_run_ids" not in _columns(pg_engine, "agent")
    before = _counts(pg_engine)

    pgutil.alembic_upgrade(pg_url, "head")

    assert "pattern_run_ids" in _columns(pg_engine, "agent")
    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT pattern_run_ids FROM agent WHERE id = 'a'")).scalar_one() is None
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
    assert _counts(pg_engine) == before


def test_downgrade_to_0004_then_upgrade_again(pg_engine, pg_url):
    _fresh(pg_engine, pg_url, "0004")
    _insert_rows(pg_engine)
    pgutil.alembic_upgrade(pg_url, "head")
    with pg_engine.begin() as conn:
        conn.execute(text("UPDATE agent SET pattern_run_ids = '[\"r\"]' WHERE id = 'a'"))

    pgutil.alembic_downgrade(pg_url, "0004")

    assert "pattern_run_ids" not in _columns(pg_engine, "agent")
    assert _counts(pg_engine)["agent"] == 1

    pgutil.alembic_upgrade(pg_url, "head")

    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT pattern_run_ids FROM agent WHERE id = 'a'")).scalar_one() is None
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
