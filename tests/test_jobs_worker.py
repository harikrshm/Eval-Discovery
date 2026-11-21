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


def _flushed_result(db, job):
    """Flush (not commit) an EvalResult so only a rollback can remove it."""
    ws_id = job.workspace_id
    agent = Agent(workspace_id=ws_id, name="A", user_role_label="U")
    db.add(agent)
    db.flush()
    crit = Criterion(agent_id=agent.id, position=1, name="n", definition="d", good_example="g",
                     bad_example="b", why_discovered="w", source="discovered")
    db.add(crit)
    db.flush()
    test = EvalTest(criterion_id=crit.id, role="fix", scripted_user_messages=["hi"])
    db.add(test)
    db.flush()
    db.add(EvalResult(test_id=test.id, prompt_version_id="pv", kind="replay", verdict="pass",
                      rationale="half written"))
    db.flush()
    job.result = {"half": "written"}
    db.flush()


def test_run_one_unexpected_error_rolls_back_flushed_writes(db_session, fake_type, caplog):
    ws = _ws(db_session)
    job = enqueue(db_session, ws.id, "fake", {})

    def dirty_flush_then_crash(db, j):
        _flushed_result(db, j)
        raise RuntimeError("kaboom")

    fake_type.side_effect = dirty_flush_then_crash
    with caplog.at_level(logging.ERROR, logger="backend.jobs.worker"):
        run_one(db_session)

    job = db_session.get(Job, job.id)
    assert job.status == "queued" and job.result is None
    assert db_session.scalars(select(EvalResult)).first() is None
    assert db_session.scalars(select(EvalTest)).first() is None


def test_run_one_jobfailed_rolls_back_flushed_writes(db_session, fake_type):
    ws = _ws(db_session)
    job = enqueue(db_session, ws.id, "fake", {})

    def dirty_flush_then_jobfailed(db, j):
        _flushed_result(db, j)
        raise JobFailed("That test no longer exists.")

    fake_type.side_effect = dirty_flush_then_jobfailed
    run_one(db_session)

    job = db_session.get(Job, job.id)
    assert job.status == "failed" and job.error == "That test no longer exists."
    assert job.result is None
    assert db_session.scalars(select(EvalResult)).first() is None
    assert db_session.scalars(select(EvalTest)).first() is None


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


# ---------- reconcile at the end of every pass ----------

APPROVED_AT = datetime(2026, 1, 1, 12, 0, 0)


def _verifying(db, token="rv"):
    """An agent with a one-test `approved_verifying` candidate (the fix test is the only required test)."""
    ws = _ws(db, token)
    agent = Agent(workspace_id=ws.id, name="Alex", user_role_label="Customer")
    db.add(agent)
    db.flush()
    current = PromptVersion(agent_id=agent.id, number=1, text="p", origin="initial", status="promoted")
    db.add(current)
    db.flush()
    agent.current_prompt_version_id = current.id
    crit = Criterion(agent_id=agent.id, position=1, name="Tone", definition="d", good_example="g",
                     bad_example="b", why_discovered="w", source="discovered")
    db.add(crit)
    db.flush()
    test = EvalTest(criterion_id=crit.id, role="fix", scripted_user_messages=["hi"])
    db.add(test)
    db.flush()
    candidate = PromptVersion(
        agent_id=agent.id, number=2, text="candidate", parent_id=current.id, origin="suggested",
        status="approved_verifying", source_test_id=test.id,
        change_report={"why": None, "outcome": None},
        verification={
            "strength": "weaker", "fix_test_id": test.id, "approved_at": APPROVED_AT.isoformat(),
            "required": [{"test_id": test.id, "role": "fix", "before": "fail"}],
        },
    )
    db.add(candidate)
    db.commit()
    return ws, agent, test, candidate


def _store_result(db, test, version_id, verdict="pass"):
    db.add(EvalResult(test_id=test.id, prompt_version_id=version_id, kind="replay", verdict=verdict,
                      rationale="because", transcript=[{"role": "user", "content": "hi"}]))
    db.commit()


def _replay_job(db, ws, test, candidate):
    return enqueue(db, ws.id, "replay", {"test_id": test.id, "prompt_version_id": candidate.id})


def test_success_pass_resolves_candidate_in_the_same_pass(db_session, monkeypatch):
    ws, agent, test, candidate = _verifying(db_session)
    job = _replay_job(db_session, ws, test, candidate)
    candidate_id, test_id = candidate.id, test.id

    def fake_replay(db, j):
        _store_result(db, db.get(EvalTest, test_id), candidate_id, "pass")
        return {"verdict": "pass"}

    monkeypatch.setitem(HANDLERS, "replay", fake_replay)

    assert run_one(db_session, now=APPROVED_AT + timedelta(minutes=1)) is True

    db_session.expire_all()
    assert db_session.get(Job, job.id).status == "done"
    assert db_session.get(PromptVersion, candidate_id).status == "promoted"
    assert db_session.get(Agent, agent.id).current_prompt_version_id == candidate_id


def test_failed_replay_job_fails_candidate_with_exact_reason(db_session, monkeypatch):
    ws, agent, test, candidate = _verifying(db_session)
    job = _replay_job(db_session, ws, test, candidate)
    candidate_id = candidate.id
    monkeypatch.setitem(HANDLERS, "replay", MagicMock(side_effect=JobFailed("The model timed out.")))

    assert run_one(db_session, now=APPROVED_AT + timedelta(minutes=1)) is True

    db_session.expire_all()
    assert db_session.get(Job, job.id).status == "failed"
    version = db_session.get(PromptVersion, candidate_id)
    assert version.status == "failed_verification"
    assert version.change_report["outcome"]["reason"] == "A test could not be run: The model timed out."


def test_idle_pass_resolves_candidate_with_all_results_present(db_session):
    """Restart scenario: the last replay finished, the process died before reconcile ran."""
    ws, agent, test, candidate = _verifying(db_session)
    _store_result(db_session, test, candidate.id, "pass")
    candidate_id = candidate.id

    assert run_one(db_session, now=APPROVED_AT + timedelta(minutes=1)) is False

    db_session.expire_all()
    assert db_session.get(PromptVersion, candidate_id).status == "promoted"


def test_lost_job_candidate_waits_then_times_out(db_session):
    ws, agent, test, candidate = _verifying(db_session)
    candidate_id = candidate.id

    assert run_one(db_session, now=APPROVED_AT + timedelta(minutes=29)) is False
    db_session.expire_all()
    assert db_session.get(PromptVersion, candidate_id).status == "approved_verifying"

    assert run_one(db_session, now=APPROVED_AT + timedelta(minutes=31)) is False
    db_session.expire_all()
    version = db_session.get(PromptVersion, candidate_id)
    assert version.status == "failed_verification"
    assert version.change_report["outcome"]["reason"] == "Verification timed out."


def test_reconcile_error_does_not_change_job_outcome_and_is_logged(db_session, fake_type, monkeypatch, caplog):
    ws = _ws(db_session)
    job = enqueue(db_session, ws.id, "fake", {})
    monkeypatch.setattr(worker, "reconcile_verifying", MagicMock(side_effect=RuntimeError("reconcile boom")))

    with caplog.at_level(logging.ERROR, logger="backend.jobs.worker"):
        assert run_one(db_session) is True

    job = db_session.get(Job, job.id)
    assert job.status == "done" and job.result == {"ok": True}
    assert any(r.exc_info and "reconcile boom" in str(r.exc_info[1]) for r in caplog.records)


def test_reconcile_error_on_idle_pass_is_logged_and_returns_false(db_session, monkeypatch, caplog):
    monkeypatch.setattr(worker, "reconcile_verifying", MagicMock(side_effect=RuntimeError("reconcile boom")))

    with caplog.at_level(logging.ERROR, logger="backend.jobs.worker"):
        assert run_one(db_session) is False

    assert any(r.exc_info and "reconcile boom" in str(r.exc_info[1]) for r in caplog.records)


def test_reconcile_runs_once_per_pass_for_every_outcome(db_session, fake_type, monkeypatch):
    ws = _ws(db_session)
    reconcile = MagicMock(return_value=[])
    monkeypatch.setattr(worker, "reconcile_verifying", reconcile)

    enqueue(db_session, ws.id, "fake", {})  # done
    assert run_one(db_session) is True
    assert reconcile.call_count == 1

    enqueue(db_session, ws.id, "fake", {})  # JobFailed
    fake_type.side_effect = JobFailed("nope")
    assert run_one(db_session) is True
    assert reconcile.call_count == 2

    enqueue(db_session, ws.id, "fake", {})  # unexpected error, requeued
    fake_type.side_effect = RuntimeError("kaboom")
    assert run_one(db_session) is True
    assert reconcile.call_count == 3

    enqueue(db_session, ws.id, "no_such_type", {})  # unknown type
    db_session.query(Job).filter(Job.type == "fake", Job.status == "queued").delete()
    db_session.commit()
    assert run_one(db_session) is True
    assert reconcile.call_count == 4

    assert run_one(db_session) is False  # idle
    assert reconcile.call_count == 5


def test_reconcile_receives_the_pass_now(db_session, monkeypatch):
    reconcile = MagicMock(return_value=[])
    monkeypatch.setattr(worker, "reconcile_verifying", reconcile)

    run_one(db_session, now=T0)

    reconcile.assert_called_once_with(db_session, T0)


def test_idle_pass_returns_false_even_when_reconcile_resolved_something(db_session, monkeypatch):
    monkeypatch.setattr(worker, "reconcile_verifying", MagicMock(return_value=["some-version-id"]))

    assert run_one(db_session) is False


def test_reconcile_still_runs_when_the_job_helper_raises(db_session, monkeypatch, caplog):
    reconcile = MagicMock(return_value=[])
    monkeypatch.setattr(worker, "reconcile_verifying", reconcile)
    monkeypatch.setattr(worker, "_run_job", MagicMock(side_effect=RuntimeError("db is down")))

    with pytest.raises(RuntimeError, match="db is down"):
        run_one(db_session)

    assert reconcile.call_count == 1


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
