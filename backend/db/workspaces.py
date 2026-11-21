"""Anonymous workspace identity: a random token whose SHA-256 hash is all we store."""

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import Workspace, utcnow

TOKEN_TTL_DAYS = 30


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_workspace(db: Session, now: datetime | None = None) -> tuple[Workspace, str]:
    """Create a workspace and return it with the raw token (never stored)."""
    now = now or utcnow()
    token = secrets.token_urlsafe(32)
    workspace = Workspace(
        token_hash=hash_token(token),
        created_at=now,
        expires_at=now + timedelta(days=TOKEN_TTL_DAYS),
    )
    db.add(workspace)
    db.commit()
    return workspace, token


def find_workspace(db: Session, token: str, now: datetime | None = None) -> Workspace | None:
    """Return the live workspace for a token, or None if unknown or expired."""
    now = now or utcnow()
    workspace = db.scalar(select(Workspace).where(Workspace.token_hash == hash_token(token)))
    if workspace is None or workspace.expires_at <= now:
        return None
    return workspace


def purge_expired(db: Session, now: datetime | None = None) -> int:
    """Delete expired workspaces (and everything under them via ORM cascade)."""
    now = now or utcnow()
    expired = db.scalars(select(Workspace).where(Workspace.expires_at <= now)).all()
    for workspace in expired:
        db.delete(workspace)
    db.commit()
    return len(expired)
