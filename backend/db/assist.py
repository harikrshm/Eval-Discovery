"""Persistence helpers for the Patent Assist agent: its lookup, the signal rows and flagged runs."""

import os
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.assist.brief import Brief
from backend.db.models import Agent, AssistPageView, AssistQuestion, Run, Workspace, utcnow

ASSIST_AGENT_ENV = "ASSIST_AGENT_ID"
ASSIST_AGENT_MISSING = "The assistant agent is not configured."


def get_assist_agent(db: Session) -> tuple[Agent, Workspace]:
    """The configured assist agent and the workspace that owns it.

    Reads ASSIST_AGENT_ID at call time. Raises LookupError (one fixed message, so nothing leaks about
    which check failed) when it is unset, unknown or not an assist agent.
    """
    agent_id = os.environ.get(ASSIST_AGENT_ENV, "").strip()
    if not agent_id:
        raise LookupError(ASSIST_AGENT_MISSING)
    agent = db.get(Agent, agent_id)
    if agent is None or agent.kind != "assist":
        raise LookupError(ASSIST_AGENT_MISSING)
    workspace = db.get(Workspace, agent.workspace_id)
    if workspace is None:
        raise LookupError(ASSIST_AGENT_MISSING)
    return agent, workspace


BOUNCE_MAX_DWELL_MS = 10_000
BOUNCE_ENGAGED_SECTION_MS = 3_000


def is_bounce(dwell_ms: int, sections: dict) -> bool:
    """A bounce is a short visit (under 10 s) in which no section was visible for 3 s or more."""
    return dwell_ms < BOUNCE_MAX_DWELL_MS and not any(
        ms >= BOUNCE_ENGAGED_SECTION_MS for ms in sections.values()
    )


class SessionConflict(LookupError):
    """A heartbeat that cannot join its session: the id belongs to another workspace or another patent."""


def _same_patent(a: str, b: str) -> bool:
    return a.strip().upper() == b.strip().upper()


def _load_view(db: Session, session_id: str) -> AssistPageView | None:
    """The session's page view, read with a row lock (a no-op on SQLite) so a merge cannot lose a concurrent one."""
    return db.scalar(select(AssistPageView).where(AssistPageView.session_id == session_id).with_for_update())


def upsert_page_view(
    db: Session,
    workspace_id: str,
    session_id: str,
    patent_number: str,
    dwell_ms: int,
    sections: dict,
    bounce: bool | None,
    assist_version: int | None,
    source: str,
    started_at: datetime | None = None,
) -> AssistPageView:
    """Create the page view for a session, or merge into it (keeping `started_at`) when it exists.

    A heartbeat only ever raises what is stored: `dwell_ms` and each section's `visible_ms` become the
    larger of the stored and incoming value, and a section missing from the incoming call keeps its
    stored value. `bounce=None` derives it from the merged values (see `is_bounce`); a bool is stored as given.
    The row is read under a lock, so concurrent heartbeats cannot lower a stored value.

    A session id never moves between workspaces, and one session is one patent: the patent number stored
    on first sight is pinned (compared after strip + upper). Either mismatch raises `SessionConflict` and
    changes nothing. `assist_version` is set on insert only, so it stays the version first seen.
    """
    for attempt in (1, 2):
        try:
            view = _load_view(db, session_id)
            now = utcnow()
            if view is None:
                view = AssistPageView(
                    session_id=session_id,
                    workspace_id=workspace_id,
                    patent_number=patent_number,
                    started_at=started_at or now,
                    updated_at=now,
                    dwell_ms=dwell_ms,
                    sections=dict(sections),
                    bounce=is_bounce(dwell_ms, sections) if bounce is None else bounce,
                    source=source,
                    assist_version=assist_version,
                )
                db.add(view)
            else:
                if view.workspace_id != workspace_id:
                    raise SessionConflict(session_id)
                if not _same_patent(view.patent_number, patent_number):
                    raise SessionConflict(session_id)
                view.updated_at = now
                merged_dwell = max(view.dwell_ms or 0, dwell_ms)
                merged = dict(view.sections or {})
                for name, ms in sections.items():
                    merged[name] = max(merged.get(name, 0), ms)
                view.dwell_ms = merged_dwell
                view.sections = merged  # a new dict, so the JSON column registers the change
                view.bounce = is_bounce(merged_dwell, merged) if bounce is None else bounce
                view.source = source
            db.commit()
            return view
        except IntegrityError:
            db.rollback()  # two first writes for one session raced; the second pass updates
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


def add_question(
    db: Session,
    workspace_id: str,
    session_id: str,
    patent_number: str,
    text: str,
    intent: str,
    section_focus: str | None,
    answer: str,
    citation_ok: bool,
    source: str,
    assist_version: int,
    rating: str | None = None,
    rating_reason: str | None = None,
    reasked: bool = False,
    created_at: datetime | None = None,
) -> AssistQuestion:
    question = AssistQuestion(
        workspace_id=workspace_id,
        session_id=session_id,
        patent_number=patent_number,
        text=text,
        intent=intent,
        section_focus=section_focus,
        answer=answer,
        rating=rating,
        rating_reason=rating_reason,
        reasked=reasked,
        citation_ok=citation_ok,
        source=source,
        assist_version=assist_version,
        created_at=created_at or utcnow(),
    )
    db.add(question)
    db.commit()
    return question


def _question(db: Session, question_id: str) -> AssistQuestion:
    question = db.get(AssistQuestion, question_id)
    if question is None:
        raise LookupError(question_id)
    return question


def set_rating(db: Session, question_id: str, rating: str | None, reason: str | None) -> AssistQuestion:
    question = _question(db, question_id)
    question.rating = rating
    question.rating_reason = reason
    db.commit()
    return question


def mark_reasked(db: Session, question_id: str) -> AssistQuestion:
    question = _question(db, question_id)
    question.reasked = True
    db.commit()
    return question


def _brief_json(brief: Brief | dict) -> dict:
    return brief.model_dump(mode="json") if isinstance(brief, Brief) else dict(brief)


def save_flagged_run(
    db: Session,
    agent: Agent,
    session_id: str,
    messages: list[dict],
    brief: Brief | dict,
    source: str,
) -> Run:
    """Save a flagged conversation as the agent's run for this session; one run per (agent, session).

    A new run takes the agent's current prompt version and the next free position (the same count + 1
    the run API uses). A second call for the same session updates the messages, brief and source and
    keeps the run's position and prompt version. A position or session conflict from a concurrent
    save is retried once.
    """
    stored_messages = [{"role": m["role"], "content": m["content"]} for m in messages]
    snapshot = _brief_json(brief)
    for attempt in (1, 2):
        try:
            run = db.scalar(select(Run).where(Run.agent_id == agent.id, Run.session_id == session_id))
            if run is None:
                position = db.scalar(select(func.count()).select_from(Run).where(Run.agent_id == agent.id)) + 1
                run = Run(
                    agent_id=agent.id,
                    prompt_version_id=agent.current_prompt_version_id,
                    position=position,
                    messages=stored_messages,
                    source=source,
                    session_id=session_id,
                    brief_snapshot=snapshot,
                )
                db.add(run)
            else:
                run.messages = stored_messages
                run.brief_snapshot = snapshot
                run.source = source
            db.commit()
            return run
        except IntegrityError:
            db.rollback()
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


def count_runs(db: Session, agent: Agent) -> int:
    return db.scalar(select(func.count()).select_from(Run).where(Run.agent_id == agent.id))


def has_run_for_session(db: Session, agent: Agent, session_id: str) -> bool:
    return db.scalar(select(Run.id).where(Run.agent_id == agent.id, Run.session_id == session_id)) is not None
