"""Minimal product event log (Phase 1 names only)."""

from sqlalchemy.orm import Session

from backend.db.models import Event

EVENT_NAMES: frozenset[str] = frozenset(
    {"agent_created", "run_saved", "loop_detected", "criterion_created"}
)


def log_event(db: Session, workspace_id: str, name: str, properties: dict | None = None) -> Event:
    if name not in EVENT_NAMES:
        raise ValueError(f"Unknown event name: {name!r}")
    event = Event(workspace_id=workspace_id, name=name, properties=properties or {})
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
