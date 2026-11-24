"""Persistence helpers for the Patent Assist agent: its lookup, the signal rows and flagged runs."""

import logging
import os
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.assist.adoption import flag_reasons
from backend.core.assist.bounce import is_bounce
from backend.core.assist.brief import Brief
from backend.core.assist.convo_judge import FLAG_GRADE_AT_OR_BELOW, RUBRIC_VERSION, Verdict
from backend.core.assist.evidence import MAX_EVIDENCE_CHARS, cut_on_line, evidence_summary
from backend.db.models import Agent, AssistJudgement, AssistPageView, AssistQuestion, Run, Workspace, utcnow

logger = logging.getLogger(__name__)

ASSIST_AGENT_ENV = "ASSIST_AGENT_ID"
ASSIST_AGENT_MISSING = "The assistant agent is not configured."
MAX_ASSIST_RUNS = 1000  # flagged conversations kept per workspace; past it new sessions are not saved
FLAGGED_SOURCE = "assist"  # the run source of a conversation flagged by a signal, a failed check or the judge


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


class SessionConflict(LookupError):
    """A heartbeat that cannot join its session: the id belongs to another workspace or another patent."""


def _same_patent(a: str, b: str) -> bool:
    return a.strip().upper() == b.strip().upper()


IMPORTED = "imported"


def session_is_imported(db: Session, session_id: str) -> bool:
    """True when the session id belongs to imported data: its page view or any of its questions is imported."""
    return (
        db.scalar(
            select(AssistPageView.session_id).where(
                AssistPageView.session_id == session_id, AssistPageView.source == IMPORTED
            )
        )
        is not None
        or db.scalar(
            select(AssistQuestion.id)
            .where(AssistQuestion.session_id == session_id, AssistQuestion.source == IMPORTED)
            .limit(1)
        )
        is not None
    )


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
    changes nothing, and so does a non-imported heartbeat for a session whose stored source is `imported`. `assist_version` is set on insert only, so it stays the version first seen.
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
                if view.source == IMPORTED and source != IMPORTED:
                    raise SessionConflict(session_id)  # a live heartbeat never turns imported data live
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
    """Store one question. A non-imported question for an imported session raises `SessionConflict`."""
    if source != IMPORTED and session_is_imported(db, session_id):
        raise SessionConflict(session_id)
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


def save_flagged_conversation(db: Session, agent: Agent, session_id: str, messages: list[dict], brief) -> None:
    """Save a flagged conversation as the session's run (rating, re-ask, citation-failure and judge paths alike).

    A new session is skipped once the workspace holds MAX_ASSIST_RUNS runs; the signal is still recorded.
    A failed save never fails the call (the signal or answer is already stored).
    """
    try:
        if count_runs(db, agent) >= MAX_ASSIST_RUNS and not has_run_for_session(db, agent, session_id):
            logger.warning("Assist flagged-run cap reached; conversation not saved")
            return
        save_flagged_run(db, agent, session_id, messages=messages, brief=brief, source=FLAGGED_SOURCE)
    except Exception:
        db.rollback()
        logger.warning("Saving the flagged assist run failed", exc_info=True)


def _judged_question(db: Session, workspace_id: str, session_id: str, worst_id: str | None) -> AssistQuestion | None:
    """The question the judge's flag lands on: the named worst one of this session, else the session's latest."""
    questions = list(
        db.scalars(
            select(AssistQuestion)
            .where(AssistQuestion.workspace_id == workspace_id, AssistQuestion.session_id == session_id)
            .order_by(AssistQuestion.created_at.desc(), AssistQuestion.id.desc())
        )
    )
    for question in questions:
        if worst_id is not None and question.id == worst_id:
            return question
    return questions[0] if questions else None


def record_judgement(
    db: Session,
    agent: Agent,
    workspace: Workspace,
    session_id: str,
    verdict: Verdict,
    *,
    model: str,
    prompt_version: int,
    turns_messages: list[dict],
) -> AssistJudgement:
    """Store the conversation judge's verdict and, for a low grade, put the conversation into the flagged loop.

    One judgement per (workspace, session, prompt version): a second insert for the same pair raises
    `IntegrityError` (after a rollback) and changes nothing. An imported session raises `SessionConflict`:
    the judge never grades imported data.

    A grade at or below FLAG_GRADE_AT_OR_BELOW stores `flagged=True`, sets `judge_flagged` on the worst
    question (the latest question of the session when the verdict names none, or names one that is not in
    the session) and saves the conversation as the session's flagged run through `save_flagged_conversation`
    (so the run cap and the swallow-and-log behaviour apply). A conversation that was never flagged before
    has no stored page brief, so its run is saved with `brief={}`: it can feed pattern discovery but not
    replay tests. A session that already has a run keeps that run's stored brief.
    """
    if session_is_imported(db, session_id):
        raise SessionConflict(session_id)
    flagged = verdict.grade <= FLAG_GRADE_AT_OR_BELOW
    # Looked up before the insert so the query cannot autoflush a duplicate judgement outside the try below.
    target = _judged_question(db, workspace.id, session_id, verdict.worst_question_id) if flagged else None
    row = AssistJudgement(
        workspace_id=workspace.id,
        session_id=session_id,
        prompt_version=prompt_version,
        rubric_version=RUBRIC_VERSION,
        model=model,
        answered=verdict.answered,
        concise=verdict.concise,
        citations_sensible=verdict.citations_sensible,
        legal=verdict.legal,
        grade=verdict.grade,
        reason=verdict.reason,
        worst_question_id=verdict.worst_question_id,
        flagged=flagged,
        source="live",
    )
    db.add(row)
    if target is not None:
        target.judge_flagged = True
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise
    if flagged:
        existing = db.scalar(select(Run.brief_snapshot).where(Run.agent_id == agent.id, Run.session_id == session_id))
        save_flagged_conversation(db, agent, session_id, turns_messages, existing or {})
    return row


# ---- flywheel evidence ---------------------------------------------------------

EVIDENCE_SOURCES = ("assist", "imported")  # run sources that carry assistant evidence (never "manual")
MAX_EVIDENCE_QUESTIONS = 2  # question blocks per run, newest first
MAX_RUN_EVIDENCE_CHARS = MAX_EVIDENCE_CHARS  # evidence text per run
MAX_EVIDENCE_RUNS = 10  # runs with evidence in one call
_BLOCK_SEPARATOR = "\n---\n"


def _is_flagged(question: AssistQuestion) -> bool:
    return bool(flag_reasons(question.rating, question.reasked, question.citation_ok, question.judge_flagged))


def run_evidence(db: Session, run: Run) -> str:
    """The evidence text for a flagged run: one `evidence_summary` block per question of its session.

    Questions come from `assist_question` by (workspace, session_id): the newest flagged ones first,
    then the newest others, at most MAX_EVIDENCE_QUESTIONS blocks, listed newest first. The text is at
    most MAX_RUN_EVIDENCE_CHARS characters and is cut on a line boundary. "" for a run without a session
    or without stored questions.
    """
    if not run.session_id:
        return ""
    workspace_id = db.scalar(select(Agent.workspace_id).where(Agent.id == run.agent_id))
    if workspace_id is None:
        return ""
    questions = list(
        db.scalars(
            select(AssistQuestion)
            .where(AssistQuestion.workspace_id == workspace_id, AssistQuestion.session_id == run.session_id)
            .order_by(AssistQuestion.created_at.desc(), AssistQuestion.id.desc())
        )
    )
    flagged = [q for q in questions if _is_flagged(q)]
    rest = [q for q in questions if not _is_flagged(q)]
    chosen = (flagged + rest)[:MAX_EVIDENCE_QUESTIONS]
    chosen.sort(key=lambda q: (q.created_at, q.id), reverse=True)
    snapshot = run.brief_snapshot or {}
    blocks = [
        evidence_summary(
            run.messages or [],
            snapshot,
            {
                "intent": q.intent,
                "section_focus": q.section_focus,
                "rating": q.rating,
                "reasked": q.reasked,
                "citation_ok": q.citation_ok,
                "assist_version": q.assist_version,
                "completeness": snapshot.get("completeness"),
            },
        )
        for q in chosen
    ]
    return cut_on_line(_BLOCK_SEPARATOR.join(blocks), MAX_RUN_EVIDENCE_CHARS)


def evidence_for_runs(db: Session, runs: Sequence[Run | None]) -> list[str]:
    """Evidence texts labelled with their run number, "Run <i>:\n<text>", for the runs that have any.

    ``i`` is the 1-based index in ``runs``, the same index the transcripts of the pattern check use,
    so a manual run (or a run with no question rows) leaves a gap instead of shifting the numbering.
    A None entry (a run that no longer exists) and runs not sourced from the assistant are skipped.
    At most MAX_EVIDENCE_RUNS entries are returned (the newest).
    """
    out = []
    for index, run in enumerate(runs, 1):
        if run is None or run.source not in EVIDENCE_SOURCES:
            continue
        text = run_evidence(db, run)
        if text:
            out.append(f"Run {index}:\n{text}")
    return out[-MAX_EVIDENCE_RUNS:]


def pattern_run_evidence(db: Session, agent: Agent) -> list[str]:
    """Evidence of exactly the runs the stored pattern was found in (`agent.pattern_run_ids`).

    Only this agent's assistant-sourced runs are loaded; none recorded (NULL or empty) means no evidence."""
    ids = [i for i in (agent.pattern_run_ids or []) if isinstance(i, str)]
    if not ids:
        return []
    rows = db.scalars(
        select(Run).where(Run.id.in_(ids), Run.agent_id == agent.id, Run.source.in_(EVIDENCE_SOURCES))
    )
    by_id = {run.id: run for run in rows}
    return evidence_for_runs(db, [by_id.get(i) for i in ids])


def unchecked_flagged_count(db: Session, agent: Agent) -> int:
    """Assistant-sourced runs saved since the last pattern check (manual runs do not count)."""
    return db.scalar(
        select(func.count())
        .select_from(Run)
        .where(
            Run.agent_id == agent.id,
            Run.position > agent.last_checked_at_run,
            Run.source.in_(EVIDENCE_SOURCES),
        )
    )
