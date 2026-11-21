from datetime import timedelta

from sqlalchemy import select

from backend.core.config import PRESETS
from backend.db import reconcile as reconcile_module
from backend.db.agents import create_agent
from backend.db.models import Criterion, Event, EvalResult, EvalTest, Job, PromptVersion, utcnow
from backend.db.reconcile import VERIFICATION_TIMEOUT_MINUTES, reconcile_verifying
from backend.db.workspaces import create_workspace
from backend.jobs import queue

WHY = {"criterion_name": "Tone", "failing_test_id": "t", "failing_turn_index": 1}
TRANSCRIPT = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
_counter = [0]


def _agent(db):
    workspace, _ = create_workspace(db)
    preset = PRESETS["support"]
    agent = create_agent(db, workspace, preset.config, preset.analysis)
    return workspace, agent


def _test(db, agent, role="fix", name=None):
    _counter[0] += 1
    crit = Criterion(
        agent_id=agent.id,
        position=_counter[0],
        name=name or f"crit{_counter[0]}",
        definition="d",
        good_example="g",
        bad_example="b",
        why_discovered="w",
        source="discovered",
    )
    db.add(crit)
    db.flush()
    test = EvalTest(criterion_id=crit.id, role=role, scripted_user_messages=["hi"])
    db.add(test)
    db.flush()
    return test


def _result(db, test, version_id, verdict="pass", rationale="because", stale=False):
    _counter[0] += 1
    result = EvalResult(
        test_id=test.id,
        prompt_version_id=version_id,
        kind="replay",
        verdict=verdict,
        rationale=rationale,
        transcript=TRANSCRIPT,
        stale=stale,
        created_at=utcnow() + timedelta(seconds=_counter[0]),
    )
    db.add(result)
    db.flush()
    return result


def _candidate(db, agent, tests, approved_ago_minutes=1, number=2, status="approved_verifying"):
    """A verifying candidate; `tests` is a list of (EvalTest, role, before)."""
    approved_at = utcnow() - timedelta(minutes=approved_ago_minutes)
    version = PromptVersion(
        agent_id=agent.id,
        text="candidate prompt",
        parent_id=agent.current_prompt_version_id,
        origin="suggested",
        status=status,
        number=number,
        source_test_id=tests[0][0].id,
        change_report={"why": WHY, "outcome": None},
        verification={
            "strength": "full" if any(role == "heldout" for _, role, _ in tests) else "weaker",
            "fix_test_id": tests[0][0].id,
            "approved_at": approved_at.isoformat(),
            "required": [{"test_id": t.id, "role": role, "before": before} for t, role, before in tests],
        },
    )
    db.add(version)
    db.flush()
    db.commit()
    return version


def _replay_job(db, workspace, test, version, status="queued", result=None, error=None, extra=None):
    payload = {"test_id": test.id, "prompt_version_id": version.id}
    payload.update(extra or {})
    job = queue.enqueue(db, workspace.id, "replay", payload)
    job.status = status
    job.result = result
    job.error = error
    db.commit()
    return job


def _setup_full(db, fix_verdict="pass", heldout_verdict="pass", reg_verdict="pass"):
    workspace, agent = _agent(db)
    fix = _test(db, agent, "fix", name="Politeness")
    held = _test(db, agent, "heldout", name="Politeness held-out")
    reg = _test(db, agent, "fix", name="Accuracy")
    version = _candidate(
        db, agent, [(fix, "fix", "fail"), (held, "heldout", "fail"), (reg, "regression", "pass")]
    )
    _result(db, fix, version.id, fix_verdict, "fix why")
    _result(db, held, version.id, heldout_verdict, "held why")
    _result(db, reg, version.id, reg_verdict, "reg why")
    db.commit()
    return workspace, agent, version, (fix, held, reg)


def _jobs(db, job_type="change_report"):
    return list(db.scalars(select(Job).where(Job.type == job_type)))


def _events(db):
    return [e.name for e in db.scalars(select(Event).order_by(Event.created_at, Event.id))]


# --- promotion ---


def test_all_pass_promotes_and_moves_pointer(db_session):
    workspace, agent, version, (fix, held, reg) = _setup_full(db_session)
    previous_id = agent.current_prompt_version_id

    assert reconcile_verifying(db_session) == [version.id]

    db_session.expire_all()
    assert version.status == "promoted"
    assert agent.current_prompt_version_id == version.id
    assert db_session.get(PromptVersion, previous_id).status == "superseded"
    outcome = version.change_report["outcome"]
    assert version.change_report["why"] == WHY
    assert outcome["status"] == "promoted"
    assert outcome["reason"] == "All required tests passed."
    assert outcome["narrative"] is None
    assert outcome["explanation_unavailable"] is False
    rows = outcome["table"]
    assert [r["test_id"] for r in rows] == [fix.id, held.id, reg.id]
    assert [r["role"] for r in rows] == ["fix", "heldout", "regression"]
    assert [r["before"] for r in rows] == ["fail", "fail", "pass"]
    assert [r["after"] for r in rows] == ["pass", "pass", "pass"]
    assert [r["rationale"] for r in rows] == ["fix why", "held why", "reg why"]
    assert rows[0]["criterion_name"] == "Politeness"
    assert rows[0]["transcript"] == TRANSCRIPT


def test_promotion_queues_one_change_report_job_and_logs_events(db_session):
    workspace, agent, version, _ = _setup_full(db_session)
    reconcile_verifying(db_session)
    jobs = _jobs(db_session)
    assert len(jobs) == 1
    assert jobs[0].payload == {"prompt_version_id": version.id}
    assert jobs[0].workspace_id == workspace.id
    assert jobs[0].status == "queued"
    events = list(db_session.scalars(select(Event)))
    assert sorted(e.name for e in events) == ["fix_promoted", "fix_verified"]
    assert all(e.workspace_id == workspace.id for e in events)


def test_idempotent_second_call_does_nothing(db_session):
    _, _, version, _ = _setup_full(db_session)
    assert reconcile_verifying(db_session) == [version.id]
    events_before = _events(db_session)
    assert reconcile_verifying(db_session) == []
    assert len(_jobs(db_session)) == 1
    assert _events(db_session) == events_before


def test_no_verifying_versions_does_nothing(db_session):
    _agent(db_session)
    assert reconcile_verifying(db_session) == []
    assert _jobs(db_session) == []
    assert _events(db_session) == []


def test_weaker_strength_without_heldout_promotes(db_session):
    _, agent = _agent(db_session)
    fix = _test(db_session, agent)
    version = _candidate(db_session, agent, [(fix, "fix", "fail")])
    _result(db_session, fix, version.id, "pass")
    db_session.commit()
    assert reconcile_verifying(db_session) == [version.id]
    assert version.status == "promoted"
    assert version.verification["strength"] == "weaker"


def test_not_applicable_regression_is_ignored(db_session):
    _, _, version, _ = _setup_full(db_session, reg_verdict="not_applicable")
    assert reconcile_verifying(db_session) == [version.id]
    assert version.status == "promoted"


def test_proposed_sibling_superseded_on_promotion(db_session):
    _, agent, version, _ = _setup_full(db_session)
    sibling = PromptVersion(
        agent_id=agent.id, text="s", origin="suggested", status="proposed", number=3
    )
    db_session.add(sibling)
    db_session.commit()
    reconcile_verifying(db_session)
    db_session.expire_all()
    assert sibling.status == "superseded"


def test_previous_current_only_superseded_when_promoted(db_session):
    _, agent, version, _ = _setup_full(db_session)
    previous = db_session.get(PromptVersion, agent.current_prompt_version_id)
    previous.status = "restored-odd"
    db_session.commit()
    reconcile_verifying(db_session)
    db_session.expire_all()
    assert previous.status == "restored-odd"
    assert agent.current_prompt_version_id == version.id


# --- failures by the rule ---


def _assert_failed(db, version, agent, previous_id, reason):
    db.expire_all()
    assert version.status == "failed_verification"
    assert agent.current_prompt_version_id == previous_id
    assert db.get(PromptVersion, previous_id).status == "promoted"
    outcome = version.change_report["outcome"]
    assert outcome["status"] == "failed_verification"
    assert outcome["reason"] == reason
    assert version.change_report["why"] == WHY
    assert len(_jobs(db)) == 1
    assert _events(db) == ["fix_verification_failed"]
    event = db.scalars(select(Event)).one()
    assert event.properties == {"reason": reason}


def test_fix_fail(db_session):
    _, agent, version, _ = _setup_full(db_session, fix_verdict="fail")
    previous_id = agent.current_prompt_version_id
    assert reconcile_verifying(db_session) == [version.id]
    _assert_failed(db_session, version, agent, previous_id, "The fix test did not pass.")
    rows = version.change_report["outcome"]["table"]
    assert [r["after"] for r in rows] == ["fail", "pass", "pass"]


def test_heldout_fail(db_session):
    _, agent, version, _ = _setup_full(db_session, heldout_verdict="fail")
    previous_id = agent.current_prompt_version_id
    assert reconcile_verifying(db_session) == [version.id]
    _assert_failed(db_session, version, agent, previous_id, "The held-out test did not pass.")


def test_regression_fail(db_session):
    _, agent, version, _ = _setup_full(db_session, reg_verdict="fail")
    previous_id = agent.current_prompt_version_id
    assert reconcile_verifying(db_session) == [version.id]
    _assert_failed(db_session, version, agent, previous_id, "A previously passing test now fails.")


def test_proposed_sibling_untouched_on_failure(db_session):
    _, agent, version, _ = _setup_full(db_session, fix_verdict="fail")
    sibling = PromptVersion(agent_id=agent.id, text="s", origin="suggested", status="proposed", number=3)
    db_session.add(sibling)
    db_session.commit()
    reconcile_verifying(db_session)
    db_session.expire_all()
    assert sibling.status == "proposed"


# --- incomplete results ---


def _partial(db):
    workspace, agent = _agent(db)
    fix = _test(db, agent)
    reg = _test(db, agent)
    version = _candidate(db, agent, [(fix, "fix", "fail"), (reg, "regression", "pass")])
    _result(db, fix, version.id, "pass")
    db.commit()
    return workspace, agent, version, fix, reg


def test_partial_results_with_active_job_waits(db_session):
    workspace, _, version, _, reg = _partial(db_session)
    _replay_job(db_session, workspace, reg, version, status="running")
    assert reconcile_verifying(db_session) == []
    assert version.status == "approved_verifying"
    assert _jobs(db_session) == []
    assert _events(db_session) == []


def test_active_job_wins_over_old_age_and_failed_job(db_session):
    workspace, _, version, _, reg = _partial(db_session)
    _replay_job(db_session, workspace, reg, version, status="failed", error="boom")
    _replay_job(db_session, workspace, reg, version, status="queued")
    version.verification = {
        **version.verification,
        "approved_at": (utcnow() - timedelta(hours=2)).isoformat(),
    }
    db_session.commit()
    assert reconcile_verifying(db_session) == []
    assert version.status == "approved_verifying"


def test_active_job_matched_ignoring_extra_payload_keys(db_session):
    workspace, _, version, _, reg = _partial(db_session)
    _replay_job(db_session, workspace, reg, version, status="queued", extra={"system": True})
    assert reconcile_verifying(db_session) == []
    assert version.status == "approved_verifying"


def test_active_job_for_other_version_or_test_does_not_count(db_session):
    workspace, agent, version, fix, reg = _partial(db_session)
    other = _test(db_session, agent)
    db_session.commit()
    _replay_job(db_session, workspace, other, version, status="queued")
    _replay_job(db_session, workspace, reg, agent.prompt_versions[0], status="queued")
    # Nothing relevant is active, the candidate is young: it still waits (no failure yet).
    assert reconcile_verifying(db_session) == []
    assert version.status == "approved_verifying"


def test_failed_replay_job_fails_candidate_with_its_error(db_session):
    workspace, agent, version, _, reg = _partial(db_session)
    previous_id = agent.current_prompt_version_id
    _replay_job(db_session, workspace, reg, version, status="failed", error="The agent is unavailable.")
    assert reconcile_verifying(db_session) == [version.id]
    _assert_failed(
        db_session, version, agent, previous_id, "A test could not be run: The agent is unavailable."
    )
    rows = version.change_report["outcome"]["table"]
    assert [r["after"] for r in rows] == ["pass", None]
    assert rows[1]["rationale"] == ""
    assert rows[1]["transcript"] == []


def test_failed_job_with_extra_payload_keys_is_matched(db_session):
    workspace, _, version, _, reg = _partial(db_session)
    _replay_job(db_session, workspace, reg, version, status="failed", error="x", extra={"system": True})
    assert reconcile_verifying(db_session) == [version.id]
    assert version.change_report["outcome"]["reason"] == "A test could not be run: x"


def test_discarded_job_fails_with_criterion_reason(db_session):
    workspace, agent, version, _, reg = _partial(db_session)
    previous_id = agent.current_prompt_version_id
    _replay_job(db_session, workspace, reg, version, status="done", result={"discarded": True})
    assert reconcile_verifying(db_session) == [version.id]
    _assert_failed(db_session, version, agent, previous_id, "A criterion changed during verification.")


def test_done_job_without_discard_does_not_fail_young_candidate(db_session):
    workspace, _, version, _, reg = _partial(db_session)
    _replay_job(db_session, workspace, reg, version, status="done", result={"verdict": "pass"})
    assert reconcile_verifying(db_session) == []
    assert version.status == "approved_verifying"


def test_timeout_after_thirty_minutes(db_session):
    assert VERIFICATION_TIMEOUT_MINUTES == 30
    workspace, agent = _agent(db_session)
    fix = _test(db_session, agent)
    version = _candidate(db_session, agent, [(fix, "fix", "fail")], approved_ago_minutes=31)
    previous_id = agent.current_prompt_version_id
    assert reconcile_verifying(db_session) == [version.id]
    _assert_failed(db_session, version, agent, previous_id, "Verification timed out.")


def test_younger_than_timeout_waits(db_session):
    _, agent = _agent(db_session)
    fix = _test(db_session, agent)
    version = _candidate(db_session, agent, [(fix, "fix", "fail")], approved_ago_minutes=29)
    assert reconcile_verifying(db_session) == []
    assert version.status == "approved_verifying"


def test_timeout_uses_injected_now(db_session):
    _, agent = _agent(db_session)
    fix = _test(db_session, agent)
    version = _candidate(db_session, agent, [(fix, "fix", "fail")], approved_ago_minutes=1)
    assert reconcile_verifying(db_session, now=utcnow() + timedelta(minutes=45)) == [version.id]
    assert version.change_report["outcome"]["reason"] == "Verification timed out."


def test_stale_and_foreign_version_results_do_not_count(db_session):
    _, agent = _agent(db_session)
    fix = _test(db_session, agent)
    version = _candidate(db_session, agent, [(fix, "fix", "fail")])
    _result(db_session, fix, version.id, "pass", stale=True)
    _result(db_session, fix, agent.current_prompt_version_id, "pass")
    db_session.commit()
    assert reconcile_verifying(db_session) == []
    assert version.status == "approved_verifying"


def test_deleted_required_test_fails_candidate(db_session):
    _, agent = _agent(db_session)
    fix = _test(db_session, agent)
    reg = _test(db_session, agent)
    version = _candidate(db_session, agent, [(fix, "fix", "fail"), (reg, "regression", "pass")])
    previous_id = agent.current_prompt_version_id
    _result(db_session, fix, version.id, "pass")
    db_session.delete(reg)
    db_session.commit()
    assert reconcile_verifying(db_session) == [version.id]
    _assert_failed(
        db_session, version, agent, previous_id, "A test could not be run: That test no longer exists."
    )
    rows = version.change_report["outcome"]["table"]
    assert rows[1]["criterion_name"] == ""
    assert rows[1]["after"] is None


# --- malformed candidates ---


def test_malformed_verification_fails_with_fixed_reason(db_session):
    _, agent = _agent(db_session)
    fix = _test(db_session, agent)
    version = _candidate(db_session, agent, [(fix, "fix", "fail")])
    previous_id = agent.current_prompt_version_id
    version.verification = {
        **version.verification,
        "required": [{"test_id": fix.id, "role": "bogus", "before": "fail"}],
    }
    _result(db_session, fix, version.id, "pass")
    db_session.commit()
    assert reconcile_verifying(db_session) == [version.id]
    _assert_failed(db_session, version, agent, previous_id, "Verification could not be completed.")
    assert version.change_report["outcome"]["table"] == []


def test_missing_verification_fails_with_fixed_reason(db_session):
    _, agent = _agent(db_session)
    fix = _test(db_session, agent)
    version = _candidate(db_session, agent, [(fix, "fix", "fail")])
    version.verification = None
    db_session.commit()
    assert reconcile_verifying(db_session) == [version.id]
    assert version.status == "failed_verification"
    assert version.change_report["outcome"]["reason"] == "Verification could not be completed."
    assert len(_jobs(db_session)) == 1


def test_missing_change_report_is_tolerated(db_session):
    _, agent = _agent(db_session)
    fix = _test(db_session, agent)
    version = _candidate(db_session, agent, [(fix, "fix", "fail")])
    _result(db_session, fix, version.id, "pass")
    version.change_report = None
    db_session.commit()
    assert reconcile_verifying(db_session) == [version.id]
    assert version.status == "promoted"
    assert version.change_report["why"] is None
    assert version.change_report["outcome"]["status"] == "promoted"


# --- several versions, locking ---


def test_each_agent_resolved_independently(db_session):
    workspace, agent1, v1, _ = _setup_full(db_session)
    _, agent2 = _agent(db_session)
    fix2 = _test(db_session, agent2)
    v2 = _candidate(db_session, agent2, [(fix2, "fix", "fail")])  # waits
    assert reconcile_verifying(db_session) == [v1.id]
    assert v1.status == "promoted"
    assert v2.status == "approved_verifying"


def test_version_resolved_after_lock_is_skipped(db_session, monkeypatch):
    _, agent, version, _ = _setup_full(db_session)
    real_lock = reconcile_module.lock_agent

    def racing_lock(db, locked_agent):
        real_lock(db, locked_agent)
        # Another transaction rejected the version after reconcile selected it.
        db.execute(
            PromptVersion.__table__.update()
            .where(PromptVersion.id == version.id)
            .values(status="rejected")
        )
        db.commit()  # stands in for the other transaction's committed change

    monkeypatch.setattr(reconcile_module, "lock_agent", racing_lock)
    assert reconcile_verifying(db_session) == []
    db_session.expire_all()
    assert version.status == "rejected"
    assert agent.current_prompt_version_id != version.id
    assert _jobs(db_session) == []
    assert _events(db_session) == []


def test_one_failing_version_does_not_block_others(db_session, monkeypatch):
    _, agent1, v1, _ = _setup_full(db_session)
    _, agent2, v2, _ = _setup_full(db_session)
    real = reconcile_module._resolve

    def flaky(db, version, *args, **kwargs):
        if version.id == v1.id:
            raise RuntimeError("boom")
        return real(db, version, *args, **kwargs)

    monkeypatch.setattr(reconcile_module, "_resolve", flaky)
    resolved = reconcile_verifying(db_session)
    assert resolved == [v2.id]
    db_session.expire_all()
    assert v1.status == "approved_verifying"
    assert v2.status == "promoted"
