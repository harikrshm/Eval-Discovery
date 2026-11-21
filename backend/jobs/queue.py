"""DB-backed job queue: plain functions over a Session, no web imports.

Datetimes are naive UTC (`utcnow()`). Callers may pass `now` explicitly so tests need no sleeping.
"""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models import Job, utcnow

MAX_JOB_ATTEMPTS = 3
JOB_STALE_AFTER_SECONDS = 180
JOBS_PER_WORKSPACE_PER_HOUR = 30
MAX_QUEUED_JOBS = 100  # global backlog cap: one FIFO worker serves every workspace

_ACTIVE_STATUSES = ("queued", "running")
_INTERRUPTED_ERROR = "The job was interrupted too many times. Try again."


def enqueue(db: Session, workspace_id: str, job_type: str, payload: dict, commit: bool = True) -> Job:
    """Add a queued job and commit it (`commit=False` only flushes: the caller commits with other writes)."""
    job = Job(workspace_id=workspace_id, type=job_type, payload=payload)
    db.add(job)
    if commit:
        db.commit()
        db.refresh(job)
    else:
        db.flush()
    return job


def _counts_toward_hourly_cap(job: Job) -> bool:
    """System-queued work (Approve's replays, the change report) is exempt from the hourly cap."""
    if job.type == "change_report":
        return False
    return not (isinstance(job.payload, dict) and job.payload.get("system") is True)


def jobs_started_since(db: Session, workspace_id: str, since: datetime) -> int:
    """Jobs the workspace created at or after `since`, whatever their status (for rate limiting).

    Jobs the system queued itself (payload `"system": true`) and `change_report` jobs are skipped.
    The filter runs in Python: JSON column operators are not portable across databases.
    """
    jobs = db.scalars(select(Job).where(Job.workspace_id == workspace_id, Job.created_at >= since))
    return sum(1 for job in jobs if _counts_toward_hourly_cap(job))


def queued_job_count(db: Session) -> int:
    """Jobs waiting for the worker, across all workspaces (for the global backlog cap)."""
    return db.scalar(select(func.count()).select_from(Job).where(Job.status == "queued"))


def active_job_for(db: Session, workspace_id: str, job_type: str, payload: dict) -> Job | None:
    """The oldest queued or running job of this type whose payload equals `payload`."""
    candidates = db.scalars(
        select(Job)
        .where(
            Job.workspace_id == workspace_id,
            Job.type == job_type,
            Job.status.in_(_ACTIVE_STATUSES),
        )
        .order_by(Job.created_at, Job.id)
    )
    # Payload equality is checked in Python: JSON column equality is not portable across databases.
    return next((job for job in candidates if job.payload == payload), None)


def claim_next(db: Session, now: datetime | None = None) -> Job | None:
    """Claim the oldest queued job: mark it running, lock it and count the attempt.

    `skip_locked` lets concurrent claimers on Postgres pass over a row another transaction holds
    (SQLite ignores it); the claim is committed before returning so a job is never handed out twice.
    """
    job = db.scalars(
        select(Job)
        .where(Job.status == "queued")
        .order_by(Job.created_at, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if job is None:
        return None
    job.status = "running"
    job.locked_at = now or utcnow()
    job.attempts += 1
    db.commit()
    return job


def complete(db: Session, job: Job, result: dict) -> None:
    job.status = "done"
    job.result = result
    job.error = None
    job.locked_at = None
    db.commit()


def fail(db: Session, job: Job, error: str) -> None:
    job.status = "failed"
    job.error = error
    job.locked_at = None
    db.commit()


def requeue_stale(db: Session, now: datetime | None = None) -> int:
    """Recover jobs whose worker vanished: requeue them, or fail them once out of attempts.

    Returns the number of jobs touched.
    """
    cutoff = (now or utcnow()) - timedelta(seconds=JOB_STALE_AFTER_SECONDS)
    stale = db.scalars(
        select(Job)
        .where(Job.status == "running", Job.locked_at < cutoff)
        .with_for_update(skip_locked=True)
    ).all()
    for job in stale:
        job.locked_at = None
        if job.attempts < MAX_JOB_ATTEMPTS:
            job.status = "queued"
        else:
            job.status = "failed"
            job.error = _INTERRUPTED_ERROR
    db.commit()
    return len(stale)
