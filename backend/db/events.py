"""Minimal product event log (Phase 1-3 names)."""

import logging

from sqlalchemy.orm import Session

from backend.db.models import Event

logger = logging.getLogger(__name__)

EVENT_NAMES: frozenset[str] = frozenset(
    {
        "agent_created",
        "run_saved",
        "loop_detected",
        "criterion_created",
        "test_created",
        "replay_started",
        "replay_passed",
        "replay_failed",
        "fix_suggested",
        "fix_approved",
        "fix_rejected",
        "fix_verified",
        "fix_verification_failed",
        "fix_promoted",
        "version_restored",
        "report_viewed",
        "test_generated",
    }
)


def log_event(db: Session, workspace_id: str, name: str, properties: dict | None = None) -> Event:
    if name not in EVENT_NAMES:
        raise ValueError(f"Unknown event name: {name!r}")
    event = Event(workspace_id=workspace_id, name=name, properties=properties or {})
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def try_log_event(
    db: Session, workspace_id: str, name: str, properties: dict | None = None
) -> None:
    """Best-effort `log_event`: analytics must never fail a request."""
    try:
        log_event(db, workspace_id, name, properties)
    except Exception:
        db.rollback()
        logger.exception("Failed to log event %r for workspace %s", name, workspace_id)
