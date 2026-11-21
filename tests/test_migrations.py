import os
import subprocess
import sys
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from backend.db import models  # noqa: F401
from backend.db.session import Base

ROOT = Path(__file__).resolve().parent.parent


def _upgrade(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    return create_engine(url)


def test_upgrade_head_creates_all_tables(tmp_path, monkeypatch):
    engine = _upgrade(tmp_path, monkeypatch)
    try:
        expected = {"workspace", "agent", "prompt_version", "behavior_map", "run", "criterion", "event"}
        expected |= {"test_case", "test_result", "job"}
        assert set(inspect(engine).get_table_names()) >= expected
    finally:
        engine.dispose()


def test_models_match_migration(tmp_path, monkeypatch):
    engine = _upgrade(tmp_path, monkeypatch)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            assert compare_metadata(ctx, Base.metadata) == []
    finally:
        engine.dispose()


def test_downgrade_to_0001_then_upgrade_again(tmp_path, monkeypatch):
    engine = _upgrade(tmp_path, monkeypatch)
    try:
        cfg = Config(str(ROOT / "alembic.ini"))
        command.downgrade(cfg, "0001")
        insp = inspect(engine)
        assert not {"test_case", "test_result", "job"} & set(insp.get_table_names())
        assert "judge_check" not in {c["name"] for c in insp.get_columns("criterion")}
        assert "number" not in {c["name"] for c in insp.get_columns("prompt_version")}
        command.upgrade(cfg, "head")
        insp = inspect(engine)
        assert {"test_case", "test_result", "job"} <= set(insp.get_table_names())
    finally:
        engine.dispose()


def test_upgrade_backfills_existing_prompt_versions_to_number_1(tmp_path, monkeypatch):
    from sqlalchemy import text

    url = f"sqlite:///{tmp_path / 'b.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    command.upgrade(cfg, "0001")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO workspace (id, token_hash, created_at, expires_at) "
                    "VALUES ('w', 'h', '2026-01-01', '2026-01-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO agent (id, workspace_id, name, user_role_label, last_checked_at_run, created_at) "
                    "VALUES ('a', 'w', 'n', 'r', 0, '2026-01-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO prompt_version (id, agent_id, text, origin, status, created_at) "
                    "VALUES ('p', 'a', 't', 'initial', 'promoted', '2026-01-01')"
                )
            )
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT number FROM prompt_version WHERE id = 'p'")).scalar() == 1
    finally:
        engine.dispose()


def test_alembic_cli_works_from_another_working_directory(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["DATABASE_URL"] = f"sqlite:///{tmp_path / 'x.db'}"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "current"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _insert_0002_rows(engine):
    """Populate a database at revision 0002, including an agent with two versions."""
    from sqlalchemy import text

    with engine.begin() as conn:
        for sql in (
            "INSERT INTO workspace (id, token_hash, created_at, expires_at) VALUES ('w', 'h', '2026-01-01', '2026-01-01')",
            "INSERT INTO agent (id, workspace_id, name, user_role_label, last_checked_at_run, created_at) "
            "VALUES ('a', 'w', 'n', 'r', 0, '2026-01-01')",
            "INSERT INTO prompt_version (id, agent_id, text, origin, status, number, created_at) "
            "VALUES ('p1', 'a', 't', 'initial', 'promoted', 1, '2026-01-01')",
            "INSERT INTO prompt_version (id, agent_id, text, origin, status, number, created_at) "
            "VALUES ('p2', 'a', 't2', 'manual', 'promoted', 2, '2026-01-02')",
            "INSERT INTO criterion (id, agent_id, position, name, definition, good_example, bad_example, "
            "why_discovered, source, created_at) VALUES ('c', 'a', 1, 'n', 'd', 'g', 'b', 'w', 'discovered', '2026-01-01')",
            "INSERT INTO test_case (id, criterion_id, role, scripted_user_messages, created_at) "
            "VALUES ('t', 'c', 'fix', '[\"hi\"]', '2026-01-01')",
        ):
            conn.execute(text(sql))


def test_upgrade_0003_keeps_0002_rows_and_applies_defaults(tmp_path, monkeypatch):
    from sqlalchemy import text

    url = f"sqlite:///{tmp_path / 'e.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    command.upgrade(cfg, "0002")
    engine = create_engine(url)
    try:
        _insert_0002_rows(engine)
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT origin FROM test_case WHERE id = 't'")).scalar() == "run"
            assert conn.execute(text("SELECT revision FROM criterion WHERE id = 'c'")).scalar() == 0
            rows = conn.execute(
                text("SELECT id, number, change_report, verification, source_test_id FROM prompt_version ORDER BY number")
            ).all()
            assert [tuple(r) for r in rows] == [("p1", 1, None, None, None), ("p2", 2, None, None, None)]
    finally:
        engine.dispose()


def test_0003_unique_constraint_enforced_after_upgrade(tmp_path, monkeypatch):
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    url = f"sqlite:///{tmp_path / 'u.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    command.upgrade(cfg, "0002")
    engine = create_engine(url)
    try:
        _insert_0002_rows(engine)
        command.upgrade(cfg, "head")
        uniques = {u["name"] for u in inspect(engine).get_unique_constraints("prompt_version")}
        assert "uq_prompt_version_agent_number" in uniques
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO prompt_version (id, agent_id, text, origin, status, number, created_at) "
                        "VALUES ('p3', 'a', 't', 'suggested', 'proposed', 2, '2026-01-03')"
                    )
                )
    finally:
        engine.dispose()


def test_downgrade_to_0002_then_upgrade_again(tmp_path, monkeypatch):
    from sqlalchemy import text

    url = f"sqlite:///{tmp_path / 'd.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    command.upgrade(cfg, "0002")
    engine = create_engine(url)
    try:
        _insert_0002_rows(engine)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0002")
        insp = inspect(engine)
        assert not {"change_report", "verification", "source_test_id"} & {
            c["name"] for c in insp.get_columns("prompt_version")
        }
        assert "origin" not in {c["name"] for c in insp.get_columns("test_case")}
        assert "revision" not in {c["name"] for c in insp.get_columns("criterion")}
        assert "uq_prompt_version_agent_number" not in {
            u["name"] for u in insp.get_unique_constraints("prompt_version")
        }
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM prompt_version")).scalar() == 2
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT origin FROM test_case WHERE id = 't'")).scalar() == "run"
            assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
    finally:
        engine.dispose()
