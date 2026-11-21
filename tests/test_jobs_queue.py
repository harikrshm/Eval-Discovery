from datetime import datetime, timedelta

from backend.db.models import Job
from backend.db.workspaces import create_workspace
from backend.jobs import queue
from backend.jobs.queue import (
    JOB_STALE_AFTER_SECONDS,
    JOBS_PER_WORKSPACE_PER_HOUR,
    MAX_JOB_ATTEMPTS,
    active_job_for,
    claim_next,
    complete,
    enqueue,
    fail,
    jobs_started_since,
    requeue_stale,
)

T0 = datetime(2026, 1, 1, 12, 0, 0)


def _ws(db):
    workspace, _ = create_workspace(db)
    return workspace.id


def _enqueue_at(db, ws, created_at, job_type="replay", payload=None):
    job = enqueue(db, ws, job_type, payload or {"n": created_at.second})
    job.created_at = created_at
    db.commit()
    return job


def test_constants():
    assert MAX_JOB_ATTEMPTS == 3
    assert JOB_STALE_AFTER_SECONDS == 180
    assert JOBS_PER_WORKSPACE_PER_HOUR == 30
    assert queue.MAX_JOB_ATTEMPTS == 3


def test_enqueue_persists_queued_job(db_session):
    ws = _ws(db_session)
    job = enqueue(db_session, ws, "build_tests", {"criterion_id": "c1"})
    stored = db_session.get(Job, job.id)
    assert stored.status == "queued"
    assert stored.attempts == 0
    assert stored.workspace_id == ws
    assert stored.type == "build_tests"
    assert stored.payload == {"criterion_id": "c1"}
    assert stored.locked_at is None


def test_claim_next_is_fifo(db_session):
    ws = _ws(db_session)
    second = _enqueue_at(db_session, ws, T0 + timedelta(seconds=2))
    first = _enqueue_at(db_session, ws, T0 + timedelta(seconds=1))
    third = _enqueue_at(db_session, ws, T0 + timedelta(seconds=3))
    assert claim_next(db_session, now=T0).id == first.id
    assert claim_next(db_session, now=T0).id == second.id
    assert claim_next(db_session, now=T0).id == third.id


def test_claim_marks_running_and_increments_attempts(db_session):
    ws = _ws(db_session)
    enqueue(db_session, ws, "replay", {"a": 1})
    now = T0 + timedelta(minutes=5)
    job = claim_next(db_session, now=now)
    assert job.status == "running"
    assert job.locked_at == now
    assert job.attempts == 1
    db_session.refresh(job)
    assert job.status == "running"
    assert job.attempts == 1


def test_claim_never_returns_same_job_twice(db_session):
    ws = _ws(db_session)
    only = enqueue(db_session, ws, "replay", {"a": 1})
    assert claim_next(db_session, now=T0).id == only.id
    assert claim_next(db_session, now=T0) is None


def test_claim_empty_queue_returns_none(db_session):
    assert claim_next(db_session) is None


def test_claim_defaults_now_to_utcnow(db_session):
    ws = _ws(db_session)
    enqueue(db_session, ws, "replay", {})
    job = claim_next(db_session)
    assert job.locked_at is not None
    assert job.locked_at.tzinfo is None


def test_complete_sets_result_and_clears_lock(db_session):
    ws = _ws(db_session)
    enqueue(db_session, ws, "replay", {})
    job = claim_next(db_session, now=T0)
    complete(db_session, job, {"ok": True})
    db_session.refresh(job)
    assert job.status == "done"
    assert job.result == {"ok": True}
    assert job.locked_at is None
    assert job.error is None


def test_fail_sets_error_and_clears_lock(db_session):
    ws = _ws(db_session)
    enqueue(db_session, ws, "replay", {})
    job = claim_next(db_session, now=T0)
    fail(db_session, job, "boom")
    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error == "boom"
    assert job.locked_at is None


def test_requeue_stale_requeues_old_running_job(db_session):
    ws = _ws(db_session)
    enqueue(db_session, ws, "replay", {})
    job = claim_next(db_session, now=T0)
    assert job.attempts == 1
    count = requeue_stale(db_session, now=T0 + timedelta(seconds=200))
    assert count == 1
    db_session.refresh(job)
    assert job.status == "queued"
    assert job.locked_at is None
    assert job.attempts == 1
    # Can be claimed again, bumping attempts.
    again = claim_next(db_session, now=T0 + timedelta(seconds=201))
    assert again.id == job.id
    assert again.attempts == 2


def test_requeue_stale_fails_job_out_of_attempts(db_session):
    ws = _ws(db_session)
    job = enqueue(db_session, ws, "replay", {})
    job.status = "running"
    job.attempts = MAX_JOB_ATTEMPTS
    job.locked_at = T0
    db_session.commit()
    count = requeue_stale(db_session, now=T0 + timedelta(seconds=200))
    assert count == 1
    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error == "The job was interrupted too many times. Try again."
    assert job.locked_at is None


def test_requeue_stale_ignores_recent_and_non_running(db_session):
    ws = _ws(db_session)
    enqueue(db_session, ws, "replay", {"a": 1})
    recent = claim_next(db_session, now=T0)
    queued = enqueue(db_session, ws, "replay", {"a": 2})
    done = enqueue(db_session, ws, "replay", {"a": 3})
    done.status = "done"
    db_session.commit()
    assert requeue_stale(db_session, now=T0 + timedelta(seconds=10)) == 0
    # Exactly at the timeout is not yet stale.
    assert requeue_stale(db_session, now=T0 + timedelta(seconds=JOB_STALE_AFTER_SECONDS)) == 0
    db_session.refresh(recent)
    assert recent.status == "running"
    assert queued.status == "queued"
    assert done.status == "done"


def test_requeue_stale_counts_both_outcomes(db_session):
    ws = _ws(db_session)
    a = enqueue(db_session, ws, "replay", {"a": 1})
    b = enqueue(db_session, ws, "replay", {"a": 2})
    for job, attempts in ((a, 1), (b, 3)):
        job.status = "running"
        job.attempts = attempts
        job.locked_at = T0
    db_session.commit()
    assert requeue_stale(db_session, now=T0 + timedelta(seconds=500)) == 2
    assert a.status == "queued"
    assert b.status == "failed"


def test_jobs_started_since_counts_all_statuses_in_window_for_workspace(db_session):
    ws = _ws(db_session)
    other = _ws(db_session)
    since = T0
    inside_queued = _enqueue_at(db_session, ws, T0 + timedelta(minutes=1), payload={"a": 1})
    inside_running = _enqueue_at(db_session, ws, T0 + timedelta(minutes=2), payload={"a": 2})
    inside_done = _enqueue_at(db_session, ws, T0 + timedelta(minutes=3), payload={"a": 3})
    inside_running.status = "running"
    inside_done.status = "done"
    db_session.commit()
    _enqueue_at(db_session, ws, T0 - timedelta(minutes=1), payload={"a": 4})
    _enqueue_at(db_session, other, T0 + timedelta(minutes=1), payload={"a": 5})
    assert inside_queued.status == "queued"
    assert jobs_started_since(db_session, ws, since) == 3
    assert jobs_started_since(db_session, other, since) == 1


def test_active_job_for_matches_payload_and_type(db_session):
    ws = _ws(db_session)
    queued = enqueue(db_session, ws, "replay", {"test_id": "t1", "v": 2})
    assert active_job_for(db_session, ws, "replay", {"v": 2, "test_id": "t1"}).id == queued.id
    assert active_job_for(db_session, ws, "replay", {"test_id": "t2", "v": 2}) is None
    assert active_job_for(db_session, ws, "build_tests", {"test_id": "t1", "v": 2}) is None


def test_active_job_for_includes_running(db_session):
    ws = _ws(db_session)
    enqueue(db_session, ws, "replay", {"a": 1})
    running = claim_next(db_session, now=T0)
    assert active_job_for(db_session, ws, "replay", {"a": 1}).id == running.id


def test_active_job_for_ignores_finished_and_other_workspaces(db_session):
    ws = _ws(db_session)
    other = _ws(db_session)
    done = enqueue(db_session, ws, "replay", {"a": 1})
    failed = enqueue(db_session, ws, "replay", {"a": 1})
    done.status = "done"
    failed.status = "failed"
    db_session.commit()
    enqueue(db_session, other, "replay", {"a": 1})
    assert active_job_for(db_session, ws, "replay", {"a": 1}) is None
    assert active_job_for(db_session, other, "replay", {"a": 1}) is not None
