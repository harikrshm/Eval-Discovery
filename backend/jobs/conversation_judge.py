"""Conversation judge job: grade idle Patent Assist conversations through the job queue.

The idle scan (run by the worker only when it has nothing else to do) queues at most one `judge_conversation`
job at a time, and only when chat and the flywheel are quiet and the hourly cap has room. "Evaluate now" in
the console does the same without waiting for idle time. The handler builds the conversation from the stored
question rows (nothing comes from the extension), skips imported sessions before any model call, stores the
verdict and records one telemetry row per model call.
"""

import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.assist.convo_judge import JudgeOutputError, JudgeTruncated, Turn, judge_conversation
from backend.db.assist import SessionConflict, record_judgement, session_is_imported
from backend.db.models import Agent, AssistJudgement, AssistPageView, AssistQuestion, Job, Run, Workspace, utcnow
from backend.db.telemetry import classify_error, record_call
from backend.groq_client import MODEL_JUDGE
from backend.jobs.queue import MAX_QUEUED_JOBS, enqueue, queued_job_count

logger = logging.getLogger(__name__)

JOB_TYPE = "judge_conversation"
JUDGE_IDLE_MINUTES = 3  # a conversation is judged once its last question is this old
JUDGE_MAX_PER_HOUR = 20  # judge jobs created per workspace per hour
CHAT_QUIET_SECONDS = 60  # the idle scan waits this long after the latest live question

_IMPORTED = "imported"
_ACTIVE = ("queued", "running")
_UNREADABLE = "The judge could not read this conversation."
_UNAVAILABLE = "The judge was unavailable. Try again."
_BAD_SESSION = "That conversation no longer exists."
_MAX_SUMMARY_PARTS = 12
_MAX_SUMMARY_PART_CHARS = 40


# ---- handler ---------------------------------------------------------------------


def _skipped(reason: str) -> dict:
    return {"status": "skipped", "reason": reason}


def _page_summary(db: Session, agent_id: str, session_id: str) -> str | None:
    """Patent number and which page parts the brief had, from the session's stored run; None without one."""
    snapshot = db.scalar(select(Run.brief_snapshot).where(Run.agent_id == agent_id, Run.session_id == session_id))
    if not isinstance(snapshot, dict) or not snapshot:
        return None
    parts = []
    number = snapshot.get("patent_number")
    if isinstance(number, str) and number:
        parts.append(f"Patent {number[:_MAX_SUMMARY_PART_CHARS]}.")
    completeness = snapshot.get("completeness")
    if isinstance(completeness, dict) and completeness:
        items = [
            f"{str(name)[:_MAX_SUMMARY_PART_CHARS]} {'present' if value is True else 'missing'}"
            for name, value in list(completeness.items())[:_MAX_SUMMARY_PARTS]
        ]
        parts.append("Page parts: " + ", ".join(items) + ".")
    return " ".join(parts) or None


def _record(db: Session, job: Job, started: float, outcome: str, version: int | None) -> None:
    """One telemetry row for the judge call (never raises). Source is live: imported sessions never get here.

    `version` is the judged session's prompt version, so a version filter in the console keeps the call."""
    record_call(
        db, workspace_id=job.workspace_id, kind="judge", model=MODEL_JUDGE, started=started,
        outcome=outcome, attempts=job.attempts, source="live", prompt_version=version,
    )


def handle_judge_conversation(db: Session, job: Job) -> dict:
    # Function-local: handlers.py imports this module to register the handler, so a module-level import
    # of its exception classes here would be a cycle.
    from backend.jobs.handlers import JobFailed, RetryableJobFailure

    session_id = job.payload.get("session_id") if isinstance(job.payload, dict) else None
    if not isinstance(session_id, str) or not session_id:
        raise JobFailed(_BAD_SESSION)

    workspace = db.get(Workspace, job.workspace_id)
    agent = db.scalar(
        select(Agent)
        .where(Agent.workspace_id == job.workspace_id, Agent.kind == "assist")
        .order_by(Agent.created_at, Agent.id)
        .limit(1)
    )
    if workspace is None or agent is None:
        return _skipped("no_assist_agent")
    questions = list(
        db.scalars(
            select(AssistQuestion)
            .where(AssistQuestion.workspace_id == job.workspace_id, AssistQuestion.session_id == session_id)
            .order_by(AssistQuestion.created_at, AssistQuestion.id)
        )
    )
    if not questions:
        return _skipped("no_questions")
    if any(q.source == _IMPORTED for q in questions) or session_is_imported(db, session_id):
        return _skipped("imported")
    version = questions[-1].assist_version
    already = db.scalar(
        select(AssistJudgement.id).where(
            AssistJudgement.workspace_id == job.workspace_id,
            AssistJudgement.session_id == session_id,
            AssistJudgement.prompt_version == version,
        )
    )
    if already is not None:
        return _skipped("already_judged")

    turns = [Turn(q.id, q.text, q.answer) for q in questions]
    messages = [
        message
        for q in questions
        for message in ({"role": "user", "content": q.text}, {"role": "assistant", "content": q.answer})
    ]
    summary = _page_summary(db, agent.id, session_id)
    # End the read transaction before the slow model call (expire_on_commit=False keeps rows usable).
    db.commit()

    started = time.monotonic()
    try:
        verdict = judge_conversation(turns, summary)
    except JudgeTruncated as exc:  # cut off at the token limit: says nothing about the conversation, so retry
        logger.warning("judge reply truncated for job %s: %s", job.id, exc)
        _record(db, job, started, "error", version)
        raise RetryableJobFailure(_UNAVAILABLE) from exc
    except JudgeOutputError as exc:
        logger.warning("judge output unusable for job %s: %s", job.id, exc)
        _record(db, job, started, "error", version)
        raise JobFailed(_UNREADABLE) from exc
    except Exception as exc:
        outcome = classify_error(exc)
        logger.warning("judge call failed for job %s (%s)", job.id, outcome, exc_info=True)
        _record(db, job, started, outcome, version)
        if outcome in ("rate_limited", "timeout"):
            raise RetryableJobFailure(_UNAVAILABLE) from exc
        raise

    try:
        row = record_judgement(
            db, agent, workspace, session_id, verdict,
            model=MODEL_JUDGE, prompt_version=version, turns_messages=messages,
        )
    except IntegrityError:  # judged by someone else while the model ran: nothing to store, not a failure
        _record(db, job, started, "ok", version)
        return _skipped("already_judged")
    except SessionConflict:  # the session turned out to be imported: never graded
        _record(db, job, started, "ok", version)
        return _skipped("imported")
    except Exception:
        # The model call happened and used tokens: count it before the failure propagates.
        try:
            db.rollback()
        except Exception:
            logger.warning("Rolling back after a failed judgement write failed", exc_info=True)
        _record(db, job, started, "error", version)
        raise
    _record(db, job, started, "ok", version)
    return {"status": "judged", "grade": verdict.grade, "flagged": bool(row.flagged)}


# ---- scan and evaluate -----------------------------------------------------------


def _unjudged(db: Session, workspace_id: str) -> list[tuple[str, datetime]]:
    """(session_id, time of its latest question) of the live sessions not yet judged for their latest version."""
    return [(session_id, last_at) for session_id, last_at, _ in unjudged_conversations(db, workspace_id)]


def unjudged_conversations(db: Session, workspace_id: str) -> list[tuple[str, datetime, int | None]]:
    """(session_id, time of its latest question, that question's version) of the live sessions not yet judged
    for their latest version, oldest first."""
    rows = db.execute(
        select(
            AssistQuestion.session_id, AssistQuestion.source, AssistQuestion.assist_version, AssistQuestion.created_at
        )
        .where(AssistQuestion.workspace_id == workspace_id)
        .order_by(AssistQuestion.created_at, AssistQuestion.id)
    )
    latest: dict[str, tuple[datetime, int]] = {}
    imported: set[str] = set()
    for session_id, source, assist_version, created_at in rows:
        if source == _IMPORTED:
            imported.add(session_id)
        latest[session_id] = (created_at, assist_version)  # oldest first, so the last row wins
    imported.update(
        db.scalars(
            select(AssistPageView.session_id).where(
                AssistPageView.workspace_id == workspace_id, AssistPageView.source == _IMPORTED
            )
        )
    )
    judged = {
        (session_id, version)
        for session_id, version in db.execute(
            select(AssistJudgement.session_id, AssistJudgement.prompt_version).where(
                AssistJudgement.workspace_id == workspace_id
            )
        )
    }
    found = [
        (session_id, last_at, version)
        for session_id, (last_at, version) in latest.items()
        if session_id not in imported and (session_id, version) not in judged
    ]
    return sorted(found, key=lambda item: (item[1], item[0]))


def unjudged_sessions(db: Session, workspace_id: str, idle_before: datetime | None) -> list[str]:
    """Live sessions with a question and no judgement for the version of their latest question, oldest idle first.

    `idle_before` keeps only sessions whose latest question is at or before it (None ignores idleness).
    """
    return [
        session_id
        for session_id, last_at in _unjudged(db, workspace_id)
        if idle_before is None or last_at <= idle_before
    ]


def _judge_jobs(db: Session, workspace_id: str | None = None, statuses: tuple[str, ...] | None = None) -> list[Job]:
    query = select(Job).where(Job.type == JOB_TYPE)
    if workspace_id is not None:
        query = query.where(Job.workspace_id == workspace_id)
    if statuses is not None:
        query = query.where(Job.status.in_(statuses))
    return list(db.scalars(query))


def _session_of(job: Job) -> str | None:
    value = job.payload.get("session_id") if isinstance(job.payload, dict) else None
    return value if isinstance(value, str) else None


def _split(
    db: Session, workspace_id: str, idle_before: datetime | None
) -> tuple[list[tuple[str, datetime]], list[tuple[str, datetime]]]:
    """(pending, unreadable): unjudged sessions, oldest idle first, split by whether they need a new job.

    Never in either list: a session with a judge job in flight. `unreadable` holds a session whose latest
    judge job failed with the fixed unreadable-conversation message after its latest question: the judge
    cannot read it, so retrying would make it first in line on every scan and starve the rest. A new
    question makes it eligible again. A job that failed for any other reason (retries used up after a 429
    or timeout, a crash, a stale job) does not suppress anything: that session stays in `pending`.
    """
    jobs = _judge_jobs(db, workspace_id)
    in_flight = {_session_of(j) for j in jobs if j.status in _ACTIVE}
    latest_job: dict[str, Job] = {}
    for job in sorted(jobs, key=lambda j: (j.created_at, j.id)):
        session_id = _session_of(job)
        if session_id is not None:
            latest_job[session_id] = job
    pending: list[tuple[str, datetime]] = []
    unreadable: list[tuple[str, datetime]] = []
    for session_id, last_at in _unjudged(db, workspace_id):
        if idle_before is not None and last_at > idle_before:
            continue
        if session_id in in_flight:
            continue
        job = latest_job.get(session_id)
        if job is not None and job.status == "failed" and job.error == _UNREADABLE and job.created_at >= last_at:
            unreadable.append((session_id, last_at))
        else:
            pending.append((session_id, last_at))
    return pending, unreadable


def _pending(db: Session, workspace_id: str, idle_before: datetime | None) -> list[tuple[str, datetime]]:
    """Unjudged sessions (oldest idle first) that need a new job (see `_split`)."""
    return _split(db, workspace_id, idle_before)[0]


def _hourly_cap_reached(db: Session, workspace_id: str, now: datetime) -> bool:
    created = db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.type == JOB_TYPE, Job.workspace_id == workspace_id, Job.created_at >= now - timedelta(hours=1))
    )
    return created >= JUDGE_MAX_PER_HOUR


def _judge_job_active(db: Session) -> bool:
    return db.scalar(select(Job.id).where(Job.type == JOB_TYPE, Job.status.in_(_ACTIVE)).limit(1)) is not None


def _enqueue_judge(db: Session, workspace_id: str, session_id: str) -> None:
    enqueue(db, workspace_id, JOB_TYPE, {"session_id": session_id, "system": True})


def scan_and_enqueue(db: Session, now: datetime | None = None) -> int:
    """Queue at most one judge job for the oldest idle unjudged conversation. Returns how many were queued (0 or 1).

    Nothing is queued while any other job is queued or running (the flywheel goes first), while a live
    question is younger than CHAT_QUIET_SECONDS, while a judge job is queued or running, or when the
    backlog is full. A workspace that created JUDGE_MAX_PER_HOUR judge jobs in the last hour is skipped.
    """
    now = now or utcnow()
    other_job = db.scalar(select(Job.id).where(Job.type != JOB_TYPE, Job.status.in_(_ACTIVE)).limit(1))
    if other_job is not None or _judge_job_active(db) or queued_job_count(db) >= MAX_QUEUED_JOBS:
        return 0
    newest_question = db.scalar(
        select(func.max(AssistQuestion.created_at)).where(AssistQuestion.source != _IMPORTED)
    )
    if newest_question is None or newest_question > now - timedelta(seconds=CHAT_QUIET_SECONDS):
        return 0  # no live conversation at all, or chat is still busy

    idle_before = now - timedelta(minutes=JUDGE_IDLE_MINUTES)
    best: tuple[datetime, str, str] | None = None
    for workspace_id in db.scalars(select(AssistQuestion.workspace_id).distinct()):
        if _hourly_cap_reached(db, workspace_id, now):
            continue
        pending = _pending(db, workspace_id, idle_before)
        if pending and (best is None or (pending[0][1], pending[0][0]) < (best[0], best[2])):
            best = (pending[0][1], workspace_id, pending[0][0])
    if best is None:
        return 0
    _enqueue_judge(db, best[1], best[2])
    return 1


def evaluate_now(db: Session, workspace_id: str, now: datetime | None = None) -> dict:
    """Queue one judge job for the oldest waiting conversation, ignoring idle time but not the cap.

    Returns `{"queued": 0 or 1, "waiting": n}`: `waiting` counts the conversations still to be judged after
    this call that have no job yet. One judge job at a time and the hourly cap still apply.
    """
    now = now or utcnow()
    pending = _pending(db, workspace_id, None)
    queued = 0
    if (
        pending
        and not _judge_job_active(db)
        and not _hourly_cap_reached(db, workspace_id, now)
        and queued_job_count(db) < MAX_QUEUED_JOBS
    ):
        _enqueue_judge(db, workspace_id, pending[0][0])
        queued = 1
    return {"queued": queued, "waiting": len(pending) - queued}


def judge_queue_stats(db: Session, workspace_id: str, now: datetime | None = None) -> dict:
    """Counts for the console: sessions waiting for a job, jobs in flight, the oldest wait and the hour's judgements.

    `waiting_sessions` counts unjudged sessions that will get a job. `unreadable_sessions` counts the unjudged
    ones the judge could not read (their last judge job failed with the fixed unreadable message and no
    question was asked since): they are not retried and not in `waiting_sessions`, but never invisible.
    """
    now = now or utcnow()
    pending, unreadable = _split(db, workspace_id, None)
    oldest = None
    if pending:
        oldest = max(0, int((now - pending[0][1]).total_seconds() // 60))
    judged_last_hour = db.scalar(
        select(func.count())
        .select_from(AssistJudgement)
        .where(AssistJudgement.workspace_id == workspace_id, AssistJudgement.created_at >= now - timedelta(hours=1))
    )
    return {
        "waiting_sessions": len(pending),
        "unreadable_sessions": len(unreadable),
        "queued_jobs": len(_judge_jobs(db, workspace_id, _ACTIVE)),
        "oldest_waiting_minutes": oldest,
        "judged_last_hour": judged_last_hour,
        "hourly_cap": JUDGE_MAX_PER_HOUR,
    }

