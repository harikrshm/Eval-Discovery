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
    expected = {"workspace", "agent", "prompt_version", "behavior_map", "run", "criterion", "event"}
    assert set(inspect(engine).get_table_names()) >= expected


def test_models_match_migration(tmp_path, monkeypatch):
    engine = _upgrade(tmp_path, monkeypatch)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        assert compare_metadata(ctx, Base.metadata) == []
