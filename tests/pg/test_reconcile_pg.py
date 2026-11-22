"""reconcile_verifying on a real Postgres (row locks, the bulk cancel UPDATE, JSON columns)."""

import pytest
from sqlalchemy import select

from backend.db.models import Agent, Job, PromptVersion, utcnow
from backend.db.reconcile import reconcile_verifying
from tests.test_db_reconcile import _agent, _candidate, _replay_job, _result, _setup_full, _test

pytestmark = pytest.mark.pg

CANCELLED = "Cancelled: verification already failed."


def test_fix_fail_with_two_queued_replays_fails_fast_and_cancels_them(pg_sessions):
    with pg_sessions() as db:
        workspace, agent = _agent(db)
        previous_id = agent.current_prompt_version_id
        fix = _test(db, agent, "fix", name="Politeness")
        held = _test(db, agent, "heldout", name="Politeness held-out")
        reg = _test(db, agent, "fix", name="Accuracy")
        busy = _test(db, agent, "fix", name="Tone")
        version = _candidate(
            db,
            agent,
            [(fix, "fix", "fail"), (held, "heldout", "fail"), (reg, "regression", "pass"), (busy, "regression", "pass")],
        )
        # Another version of the same agent (not verifying): its queued replay is out of scope.
        other = _candidate(db, agent, [(held, "heldout", "fail")], number=3, status="proposed")
        _result(db, fix, version.id, "fail", "still wrong")
        db.commit()
        queued_held = _replay_job(db, workspace, held, version, status="queued", extra={"system": True})
        queued_reg = _replay_job(db, workspace, reg, version, status="queued", extra={"system": True})
        # A replay a worker already claimed: reconcile must not touch it.
        running = _replay_job(db, workspace, busy, version, status="running", extra={"system": True})
        running.locked_at = utcnow()
        other_queued = _replay_job(db, workspace, held, other, status="queued", extra={"system": True})
        db.commit()
        ids = (version.id, agent.id, queued_held.id, queued_reg.id)
        running_id, other_queued_id, locked_at = running.id, other_queued.id, running.locked_at

    with pg_sessions() as db:
        assert reconcile_verifying(db) == [ids[0]]

    with pg_sessions() as db:
        resolved = db.get(PromptVersion, ids[0])
        assert resolved.status == "failed_verification"
        assert resolved.change_report["outcome"]["reason"] == "The fix test did not pass."
        assert db.get(Agent, ids[1]).current_prompt_version_id == previous_id
        for job_id in ids[2:]:
            job = db.get(Job, job_id)
            assert job.status == "failed"
            assert job.error == CANCELLED
            assert job.locked_at is None
        # Negative scope: a running replay of the same version and a queued replay of another version stay as they were.
        still_running = db.get(Job, running_id)
        assert still_running.status == "running"
        assert still_running.error is None
        assert still_running.locked_at == locked_at
        untouched = db.get(Job, other_queued_id)
        assert untouched.status == "queued"
        assert untouched.error is None
        # Only the report job was added; the two queued replays were cancelled, not replaced.
        assert sorted(j.type for j in db.scalars(select(Job))) == ["change_report", "replay", "replay", "replay", "replay"]

    with pg_sessions() as db:
        assert reconcile_verifying(db) == []  # idempotent: a resolved version is never touched again


def test_all_passing_candidate_is_promoted(pg_sessions):
    with pg_sessions() as db:
        _, agent, version, _ = _setup_full(db)
        previous_id = agent.current_prompt_version_id
        ids = (version.id, agent.id)

    with pg_sessions() as db:
        assert reconcile_verifying(db) == [ids[0]]

    with pg_sessions() as db:
        assert db.get(PromptVersion, ids[0]).status == "promoted"
        assert db.get(PromptVersion, previous_id).status == "superseded"
        assert db.get(Agent, ids[1]).current_prompt_version_id == ids[0]
