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
