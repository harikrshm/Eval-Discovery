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
