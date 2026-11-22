"""Fail-fast verification: resolve as soon as the stored verdicts decide the outcome and cancel queued replays."""

from backend.db.models import PromptVersion
from backend.db.reconcile import reconcile_verifying
from tests.test_db_reconcile import (
    _agent,
    _candidate,
    _events,
    _jobs,
    _replay_job,
    _result,
    _test,
)

CANCEL_ERROR = "Cancelled: verification already failed."


def _setup(db, fix=None, heldout=None, reg=None):
    """Candidate with fix + held-out + regression tests; each arg is the stored verdict or None (not run yet)."""
    workspace, agent = _agent(db)
    fix_t = _test(db, agent, "fix", name="Politeness")
    held_t = _test(db, agent, "heldout", name="Politeness held-out")
    reg_t = _test(db, agent, "fix", name="Accuracy")
    version = _candidate(
        db, agent, [(fix_t, "fix", "fail"), (held_t, "heldout", "fail"), (reg_t, "regression", "pass")]
    )
    for test, verdict in ((fix_t, fix), (held_t, heldout), (reg_t, reg)):
        if verdict is not None:
            _result(db, test, version.id, verdict, "why")
    db.commit()
    return workspace, agent, version, (fix_t, held_t, reg_t)


def _reload(db, row):
    db.expire_all()
    return db.get(type(row), row.id)


def _assert_cancelled(db, job):
    job = _reload(db, job)
    assert job.status == "failed"
    assert job.error == CANCEL_ERROR
    assert job.locked_at is None


# --- decisive stored verdicts ---


def test_fix_fail_resolves_now_and_cancels_queued_jobs_but_not_running(db_session):
    workspace, agent, version, (fix, held, reg) = _setup(db_session, fix="fail")
    previous_id = agent.current_prompt_version_id
    queued_held = _replay_job(db_session, workspace, held, version, status="queued", extra={"system": True})
    queued_reg = _replay_job(db_session, workspace, reg, version, status="queued", extra={"system": True})
    running = _replay_job(db_session, workspace, reg, version, status="running")

    assert reconcile_verifying(db_session) == [version.id]

    db_session.expire_all()
    assert version.status == "failed_verification"
    assert agent.current_prompt_version_id == previous_id
    outcome = version.change_report["outcome"]
    assert outcome["status"] == "failed_verification"
    assert outcome["reason"] == "The fix test did not pass."
    assert [r["after"] for r in outcome["table"]] == ["fail", None, None]
    assert [r["test_id"] for r in outcome["table"]] == [fix.id, held.id, reg.id]
    _assert_cancelled(db_session, queued_held)
    _assert_cancelled(db_session, queued_reg)
    running = _reload(db_session, running)
    assert running.status == "running" and running.error is None
    assert len(_jobs(db_session)) == 1
    assert _events(db_session) == ["fix_verification_failed"]


def test_heldout_fail_resolves_early(db_session):
    workspace, agent, version, (fix, held, reg) = _setup(db_session, fix="pass", heldout="fail")
    queued = _replay_job(db_session, workspace, reg, version, status="queued")

    assert reconcile_verifying(db_session) == [version.id]

    db_session.expire_all()
    assert version.status == "failed_verification"
    assert version.change_report["outcome"]["reason"] == "The held-out test did not pass."
    assert [r["after"] for r in version.change_report["outcome"]["table"]] == ["pass", "fail", None]
    _assert_cancelled(db_session, queued)


def test_regression_fail_resolves_early(db_session):
    workspace, agent, version, (fix, held, reg) = _setup(db_session, reg="fail")
    queued_fix = _replay_job(db_session, workspace, fix, version, status="queued")
    queued_held = _replay_job(db_session, workspace, held, version, status="queued")

    assert reconcile_verifying(db_session) == [version.id]

    db_session.expire_all()
    assert version.status == "failed_verification"
    assert version.change_report["outcome"]["reason"] == "A previously passing test now fails."
    assert [r["after"] for r in version.change_report["outcome"]["table"]] == [None, None, "fail"]
    _assert_cancelled(db_session, queued_fix)
    _assert_cancelled(db_session, queued_held)


def test_fail_resolution_does_not_cancel_other_versions_tests_or_done_jobs(db_session):
    workspace, agent, version, (fix, held, reg) = _setup(db_session, fix="fail")
    other_test = _test(db_session, agent)
    db_session.commit()
    other_version = agent.prompt_versions[0]
    unrelated_test = _replay_job(db_session, workspace, other_test, version, status="queued")
    unrelated_version = _replay_job(db_session, workspace, held, other_version, status="queued")
    done = _replay_job(db_session, workspace, held, version, status="done", result={"verdict": "pass"})

    reconcile_verifying(db_session)

    assert _reload(db_session, unrelated_test).status == "queued"
    assert _reload(db_session, unrelated_version).status == "queued"
    assert _reload(db_session, done).status == "done"


def test_second_reconcile_after_early_failure_does_nothing(db_session):
    workspace, agent, version, (fix, held, reg) = _setup(db_session, fix="fail")
    _replay_job(db_session, workspace, held, version, status="queued")
    assert reconcile_verifying(db_session) == [version.id]
    events_before = _events(db_session)

    assert reconcile_verifying(db_session) == []

    assert len(_jobs(db_session)) == 1  # exactly one change_report job
    assert _events(db_session) == events_before


# --- still waiting ---


def test_regression_missing_with_fix_passing_keeps_waiting(db_session):
    workspace, agent, version, (fix, held, reg) = _setup(db_session, fix="pass", heldout="pass")
    queued = _replay_job(db_session, workspace, reg, version, status="queued")

    assert reconcile_verifying(db_session) == []

    version = _reload(db_session, version)
    assert version.status == "approved_verifying"
    assert _reload(db_session, queued).status == "queued"
    assert _jobs(db_session) == []


def test_missing_regression_with_failed_job_and_others_queued_keeps_waiting(db_session):
    workspace, agent, version, (fix, held, reg) = _setup(db_session, fix="pass")
    _replay_job(db_session, workspace, reg, version, status="failed", error="boom")
    queued = _replay_job(db_session, workspace, held, version, status="queued")

    assert reconcile_verifying(db_session) == []

    assert _reload(db_session, version).status == "approved_verifying"
    assert _reload(db_session, queued).status == "queued"


def test_missing_fix_with_a_running_job_keeps_waiting_even_with_an_older_failed_job(db_session):
    workspace, agent, version, (fix, held, reg) = _setup(db_session)
    _replay_job(db_session, workspace, fix, version, status="failed", error="boom")
    _replay_job(db_session, workspace, fix, version, status="running")

    assert reconcile_verifying(db_session) == []
    assert _reload(db_session, version).status == "approved_verifying"


# --- a missing fix / held-out test whose own job failed or was discarded ---


def test_missing_fix_with_failed_job_fails_now_while_another_test_is_queued(db_session):
    workspace, agent, version, (fix, held, reg) = _setup(db_session)
    previous_id = agent.current_prompt_version_id
    _replay_job(db_session, workspace, fix, version, status="failed", error="The model timed out.")
    queued = _replay_job(db_session, workspace, held, version, status="queued")
    queued_reg = _replay_job(db_session, workspace, reg, version, status="queued")

    assert reconcile_verifying(db_session) == [version.id]

    db_session.expire_all()
    assert version.status == "failed_verification"
    assert agent.current_prompt_version_id == previous_id
    outcome = version.change_report["outcome"]
    assert outcome["reason"] == "A test could not be run: The model timed out."
    assert [r["after"] for r in outcome["table"]] == [None, None, None]
    _assert_cancelled(db_session, queued)
    _assert_cancelled(db_session, queued_reg)
    assert len(_jobs(db_session)) == 1


def test_missing_heldout_with_failed_job_fails_now(db_session):
    workspace, agent, version, (fix, held, reg) = _setup(db_session, fix="pass")
    _replay_job(db_session, workspace, held, version, status="failed", error="Judge down.")
    queued = _replay_job(db_session, workspace, reg, version, status="queued")

    assert reconcile_verifying(db_session) == [version.id]

    db_session.expire_all()
    assert version.change_report["outcome"]["reason"] == "A test could not be run: Judge down."
    assert [r["after"] for r in version.change_report["outcome"]["table"]] == ["pass", None, None]
    _assert_cancelled(db_session, queued)


def test_missing_fix_with_discarded_job_fails_now_while_another_test_is_queued(db_session):
    workspace, agent, version, (fix, held, reg) = _setup(db_session)
    _replay_job(db_session, workspace, fix, version, status="done", result={"discarded": True})
    queued = _replay_job(db_session, workspace, held, version, status="queued")

    assert reconcile_verifying(db_session) == [version.id]

    db_session.expire_all()
    assert version.status == "failed_verification"
    assert version.change_report["outcome"]["reason"] == "A criterion changed during verification."
    _assert_cancelled(db_session, queued)


def test_stored_verdict_failure_wins_over_a_failed_job_of_another_test(db_session):
    workspace, agent, version, (fix, held, reg) = _setup(db_session, fix="fail")
    _replay_job(db_session, workspace, held, version, status="failed", error="boom")

    assert reconcile_verifying(db_session) == [version.id]
    assert _reload(db_session, version).change_report["outcome"]["reason"] == "The fix test did not pass."


# --- unchanged paths ---


def test_all_pass_still_promotes_and_leaves_jobs_alone(db_session):
    workspace, agent, version, (fix, held, reg) = _setup(db_session, fix="pass", heldout="pass", reg="pass")
    done = _replay_job(db_session, workspace, fix, version, status="done", result={"verdict": "pass"})

    assert reconcile_verifying(db_session) == [version.id]

    db_session.expire_all()
    assert version.status == "promoted"
    assert agent.current_prompt_version_id == version.id
    assert _reload(db_session, done).status == "done"
    assert len(_jobs(db_session)) == 1


def test_parent_changed_failure_also_cancels_queued_jobs(db_session):
    workspace, agent, version, (fix, held, reg) = _setup(db_session, fix="pass", heldout="pass", reg="pass")
    stale_dup = _replay_job(db_session, workspace, reg, version, status="queued")  # a duplicate replay
    newer = PromptVersion(agent_id=agent.id, text="newer", origin="manual", status="promoted", number=3)
    db_session.add(newer)
    db_session.flush()
    db_session.get(PromptVersion, agent.current_prompt_version_id).status = "superseded"
    agent.current_prompt_version_id = newer.id
    db_session.commit()

    assert reconcile_verifying(db_session) == [version.id]

    assert _reload(db_session, version).change_report["outcome"]["reason"] == "The prompt changed during verification."
    _assert_cancelled(db_session, stale_dup)


def test_malformed_verification_still_fails_with_the_fixed_reason(db_session):
    workspace, agent, version, (fix, held, reg) = _setup(db_session, fix="pass")
    version.verification = None
    db_session.commit()

    assert reconcile_verifying(db_session) == [version.id]

    assert _reload(db_session, version).change_report["outcome"]["reason"] == "Verification could not be completed."
