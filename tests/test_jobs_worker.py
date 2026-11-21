import asyncio
import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.core.judge import JudgeResult
from backend.db.models import Agent, Criterion, EvalResult, EvalTest, Job, PromptVersion, Workspace, utcnow
from backend.jobs import handlers, worker
from backend.jobs.handlers import HANDLERS, JobFailed
from backend.jobs.queue import claim_next, enqueue
from backend.jobs.worker import run_one, worker_loop

T0 = datetime(2026, 1, 1, 12, 0, 0)


def _ws(db, token="h"):
    ws = Workspace(token_hash=token, expires_at=utcnow())
    db.add(ws)
    db.commit()
    return ws


@pytest.fixture
def fake_type(monkeypatch):
    """Register a controllable handler under the job type "fake"."""
    handler = MagicMock(return_value={"ok": True})
    monkeypatch.setitem(HANDLERS, "fake", handler)
    return handler


def test_run_one_returns_false_when_idle(db_session):
    assert run_one(db_session) is False


def test_run_one_completes_job(db_session, fake_type):
    ws = _ws(db_session)
    job = enqueue(db_session, ws.id, "fake", {})

    assert run_one(db_session) is True

    job = db_session.get(Job, job.id)
    assert job.status == "done" and job.result == {"ok": True}
    assert job.attempts == 1 and job.locked_at is None


def test_run_one_marks_failed_on_jobfailed_with_message(db_session, fake_type):
    ws = _ws(db_session)
    job = enqueue(db_session, ws.id, "fake", {})
    fake_type.side_effect = JobFailed("That test no longer exists.")

    assert run_one(db_session) is True

    job = db_session.get(Job, job.id)
    assert job.status == "failed"
    assert job.error == "That test no longer exists."
    assert job.locked_at is None


def test_run_one_unexpected_error_requeues_on_first_attempt(db_session, fake_type, caplog):
    ws = _ws(db_session)
    job = enqueue(db_session, ws.id, "fake", {})
    fake_type.side_effect = RuntimeError("kaboom")

    with caplog.at_level(logging.ERROR, logger="backend.jobs.worker"):
        assert run_one(db_session) is True

    job = db_session.get(Job, job.id)
    assert job.status == "queued" and job.locked_at is None and job.attempts == 1
    assert any(r.exc_info for r in caplog.records)


def test_run_one_unexpected_error_fails_on_last_attempt(db_session, fake_type, caplog):
    ws = _ws(db_session)
    job = enqueue(db_session, ws.id, "fake", {})
    job.attempts = 2  # claim makes this the third attempt
    db_session.commit()
    fake_type.side_effect = RuntimeError("kaboom with secrets")

    with caplog.at_level(logging.ERROR, logger="backend.jobs.worker"):
        run_one(db_session)

    job = db_session.get(Job, job.id)
    assert job.status == "failed" and job.attempts == 3
    assert job.error == "Something went wrong. Try again."
    assert "secrets" not in job.error


def test_run_one_unexpected_error_after_dirty_session_is_rolled_back(db_session, fake_type, caplog):
    ws = _ws(db_session)
    job = enqueue(db_session, ws.id, "fake", {})

    def dirty_then_crash(db, j):
        j.result = {"half": "written"}
        j.status = "done"  # uncommitted change that must not survive
        raise RuntimeError("kaboom")

    fake_type.side_effect = dirty_then_crash
    with caplog.at_level(logging.ERROR, logger="backend.jobs.worker"):
        run_one(db_session)

    job = db_session.get(Job, job.id)
    assert job.status == "queued" and job.result is None


def test_run_one_unknown_type_fails(db_session):
    ws = _ws(db_session)
    job = enqueue(db_session, ws.id, "mystery", {})

    assert run_one(db_session) is True

    job = db_session.get(Job, job.id)
    assert job.status == "failed" and job.error


def test_run_one_requeues_stale_running_job_then_processes_it(db_session, fake_type):
    ws = _ws(db_session)
    job = enqueue(db_session, ws.id, "fake", {})
    claim_next(db_session, now=T0)  # a worker that then vanished
    assert db_session.get(Job, job.id).status == "running"

    assert run_one(db_session, now=T0 + timedelta(seconds=181)) is True

    job = db_session.get(Job, job.id)
    assert job.status == "done" and job.attempts == 2
    assert fake_type.call_count == 1


def test_run_one_leaves_fresh_running_job_alone(db_session, fake_type):
    ws = _ws(db_session)
    job = enqueue(db_session, ws.id, "fake", {})
    claim_next(db_session, now=T0)

    assert run_one(db_session, now=T0 + timedelta(seconds=10)) is False
    assert db_session.get(Job, job.id).status == "running"
    fake_type.assert_not_called()


def test_run_one_survives_job_deleted_mid_handler(db_session, fake_type):
    ws = _ws(db_session)
    job = enqueue(db_session, ws.id, "fake", {})
    job_id = job.id

    def delete_job_then_crash(db, j):
        db.delete(db.get(Job, job_id))
        db.commit()
        raise RuntimeError("kaboom")

    fake_type.side_effect = delete_job_then_crash

    assert run_one(db_session) is True
    assert db_session.get(Job, job_id) is None


def test_deleted_test_fails_job_and_worker_keeps_going(db_session, monkeypatch):
    """Review focus 4: a replay for a deleted test fails cleanly; the next job still runs."""
    ws = _ws(db_session)
    agent = Agent(workspace_id=ws.id, name="Alex", user_role_label="Customer")
    db_session.add(agent)
    db_session.flush()
    pv = PromptVersion(agent_id=agent.id, text="p", origin="initial", status="promoted")
    db_session.add(pv)
    db_session.flush()
    agent.current_prompt_version_id = pv.id
    crit = Criterion(agent_id=agent.id, position=1, name="n", definition="d", good_example="g",
                     bad_example="b", why_discovered="w", source="discovered")
    db_session.add(crit)
    db_session.flush()
    test = EvalTest(criterion_id=crit.id, role="fix", scripted_user_messages=["hi"])
    db_session.add(test)
    db_session.commit()
    first = enqueue(db_session, ws.id, "replay", {"test_id": "deleted-id", "prompt_version_id": pv.id})
    second = enqueue(db_session, ws.id, "replay", {"test_id": test.id, "prompt_version_id": pv.id})
    transcript = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    monkeypatch.setattr(handlers, "replay", MagicMock(return_value=transcript))
    monkeypatch.setattr(handlers, "judge", MagicMock(return_value=JudgeResult("pass", "ok")))

    assert run_one(db_session) is True
    assert run_one(db_session) is True

    first = db_session.get(Job, first.id)
    assert first.status == "failed" and "no longer exists" in first.error
    assert db_session.get(Job, second.id).status == "done"
    assert db_session.scalars(select(EvalResult)).one().verdict == "pass"


# ---------- worker_loop ----------


def _session_factory(db_session):
    return sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)


def test_worker_loop_stops_promptly_when_idle(db_session, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", _session_factory(db_session))
    monkeypatch.setattr(worker, "run_one", MagicMock(return_value=False))

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(worker_loop(stop, poll_seconds=30))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())
    assert worker.run_one.call_count >= 1


def test_worker_loop_does_not_sleep_while_busy_and_closes_sessions(db_session, monkeypatch):
    sessions = []

    class Spy:
        def __init__(self):
            self.closed = False
            sessions.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(worker, "SessionLocal", Spy)
    calls = []
    stop = None

    def fake_run_one(db):
        calls.append(db)
        if len(calls) >= 3:
            stop.set()
        return True

    monkeypatch.setattr(worker, "run_one", fake_run_one)

    async def scenario():
        nonlocal stop
        stop = asyncio.Event()
        await asyncio.wait_for(worker_loop(stop, poll_seconds=30), timeout=2)

    asyncio.run(scenario())

    assert len(calls) == 3
    assert len({id(s) for s in sessions}) == 3  # a fresh session per pass
    assert all(s.closed for s in sessions)


def test_worker_loop_never_raises_and_keeps_polling(db_session, monkeypatch, caplog):
    monkeypatch.setattr(worker, "SessionLocal", _session_factory(db_session))
    calls = []
    stop = None

    def exploding_run_one(db):
        calls.append(1)
        if len(calls) >= 2:
            stop.set()
        raise RuntimeError("db is down")

    monkeypatch.setattr(worker, "run_one", exploding_run_one)

    async def scenario():
        nonlocal stop
        stop = asyncio.Event()
        await asyncio.wait_for(worker_loop(stop, poll_seconds=0.01), timeout=2)

    with caplog.at_level(logging.ERROR, logger="backend.jobs.worker"):
        asyncio.run(scenario())

    assert len(calls) == 2
    assert any("Worker pass failed" in r.message for r in caplog.records)
