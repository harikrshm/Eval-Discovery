"""Worker: claim one job, run its handler, record the outcome. One loop runs in the web process."""

import asyncio
import logging
import time
from datetime import datetime

from sqlalchemy.orm import Session

from backend.db.models import Job
from backend.db.reconcile import reconcile_verifying
from backend.db.session import SessionLocal
from backend.jobs.conversation_judge import scan_and_enqueue
from backend.jobs.handlers import HANDLERS, JobFailed, RetryableJobFailure
from backend.jobs.queue import MAX_JOB_ATTEMPTS, claim_next, complete, fail, requeue_stale

logger = logging.getLogger(__name__)

_UNEXPECTED_ERROR = "Something went wrong. Try again."

SCAN_INTERVAL_SECONDS = 10  # the idle judge scan runs at most this often (the worker polls every second)
_clock = time.monotonic  # replaced in tests
_last_scan: float | None = None


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


def reset_scan_throttle() -> None:
    """Forget when the idle scan last ran (for tests)."""
    global _last_scan
    _last_scan = None


def _scan_idle(db: Session) -> None:
    """Queue a conversation judgement when the worker has nothing else to do; never raises.

    Throttled: the scan reads every question and judge job, so it runs at most once per
    SCAN_INTERVAL_SECONDS (a scan that raised counts as a run, so a broken scan is not retried every second).
    """
    global _last_scan
    now = _clock()
    if _last_scan is not None and now - _last_scan < SCAN_INTERVAL_SECONDS:
        return
    _last_scan = now
    try:
        scan_and_enqueue(db)
    except Exception:
        logger.exception("Idle judge scan failed")
        try:
            db.rollback()
        except Exception:
            logger.exception("Rolling back after the idle judge scan failed")


def _run_pass() -> bool:
    db = SessionLocal()
    try:
        worked = run_one(db)
        if not worked:
            _scan_idle(db)
        return worked
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
