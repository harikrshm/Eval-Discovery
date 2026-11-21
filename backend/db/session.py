"""Database engine and session setup.

Uses plain SQLAlchemy against standard Postgres (DATABASE_URL); tests and local
development use SQLite through the same models.
"""

import os
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DEFAULT_DATABASE_URL = "sqlite:///./dev.db"


def normalize_database_url(url: str) -> str:
    """Point bare postgres URLs at the psycopg (v3) driver; leave others alone."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def make_engine(url: str) -> Engine:
    url = normalize_database_url(url)
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


class Base(DeclarativeBase):
    pass


engine = make_engine(os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
