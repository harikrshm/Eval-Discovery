"""Worker: claim one job, run its handler, record the outcome. One loop runs in the web process."""

import asyncio
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from backend.db.models import Job
from backend.db.reconcile import reconcile_verifying
from backend.db.session import SessionLocal
from backend.jobs.handlers import HANDLERS, JobFailed, RetryableJobFailure
from backend.jobs.queue import MAX_JOB_ATTEMPTS, claim_next, complete, fail, requeue_stale

logger = logging.getLogger(__name__)

_UNEXPECTED_ERROR = "Something went wrong. Try again."


def _reload(db: Session, job_id: str) -> Job | None:
    """Re-read the job after a rollback (the identity map can hold stale state); None if deleted."""
    db.expire_all()
    return db.get(Job, job_id)


def _run_job(db: Session, now: datetime | None) -> bool:
    """Recover stale jobs, then claim and run one job. Returns False when there was nothing to do."""
    requeue_stale(db, now)
    job = claim_next(db, now)
    if job is None:
        return False

    job_id, job_type = job.id, job.type
    handler = HANDLERS.get(job_type)
    if handler is None:
        fail(db, job, "Unknown job type.")
        return True

    try:
        result = handler(db, job)
        complete(db, job, result)
    except RetryableJobFailure as exc:
        # Counted like a crash: requeued while attempts remain, then failed with the fixed message.
        # No sleep here (it would block every other job); the SDK's own backoff already waited.
        db.rollback()
        _requeue_or_fail(db, job_id, str(exc))
    except JobFailed as exc:
        db.rollback()
        job = _reload(db, job_id)
        if job is not None:
            fail(db, job, str(exc))
    except Exception:
        logger.exception("Job %s (%s) crashed", job_id, job_type)
        db.rollback()
        _requeue_or_fail(db, job_id, _UNEXPECTED_ERROR)
    return True


def _requeue_or_fail(db: Session, job_id: str, error: str) -> None:
    job = _reload(db, job_id)
    if job is None:
        return
    if job.attempts < MAX_JOB_ATTEMPTS:
        job.status = "queued"
        job.locked_at = None
        db.commit()
    else:
        fail(db, job, error)


def _reconcile(db: Session, now: datetime | None) -> None:
    """Resolve verifying candidates; never raises, so it cannot change the outcome of the job it follows."""
    try:
        reconcile_verifying(db, now)
    except Exception:
        logger.exception("Reconcile of verifying candidates failed")


def run_one(db: Session, now: datetime | None = None) -> bool:
    """Run one job if there is one, then reconcile verifying candidates (on every pass, also when idle).

    Returns True only when a job was processed. Reconcile runs even if the job step raises, and
    its own errors are logged, never raised, so a candidate is never stranded by a failed pass.
    """
    try:
        return _run_job(db, now)
    finally:
        _reconcile(db, now)


def _run_pass() -> bool:
    db = SessionLocal()
    try:
        return run_one(db)
    finally:
        db.close()


async def worker_loop(stop: asyncio.Event, poll_seconds: float = 1.0) -> None:
    """Run jobs until `stop` is set; never raises. Sleeps only when idle (or after an error)."""
    while not stop.is_set():
        try:
            worked = await asyncio.to_thread(_run_pass)
        except Exception:
            logger.exception("Worker pass failed")
            worked = False
        if worked:
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except asyncio.TimeoutError:
            pass
