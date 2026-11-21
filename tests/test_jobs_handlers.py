from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from backend.core.judge import JudgeCheck, JudgeFailed, JudgeResult
from backend.core.replay import ReplayFailed
from backend.db.models import (
    Agent,
    Criterion,
    Event,
    EvalResult,
    EvalTest,
    Job,
    PromptVersion,
    Run,
    Workspace,
    utcnow,
)
from backend.jobs import handlers
from backend.jobs.handlers import HANDLERS, JobFailed, handle_build_tests, handle_replay
from backend.jobs.queue import enqueue

GOOD_CHECK = JudgeCheck(passed=True, good_verdict="pass", bad_verdict="fail")


def _agent(db, token="h"):
    ws = Workspace(token_hash=token, expires_at=utcnow())
    db.add(ws)
    db.flush()
    agent = Agent(workspace_id=ws.id, name="Alex", user_role_label="Customer")
    db.add(agent)
    db.flush()
    pv = PromptVersion(agent_id=agent.id, text="be nice", origin="initial", status="promoted")
    db.add(pv)
    db.flush()
    agent.current_prompt_version_id = pv.id
    db.commit()
    return ws, agent, pv


def _criterion(db, agent):
    crit = Criterion(
        agent_id=agent.id,
        position=1,
        name="Stays polite",
        definition="d",
        good_example="g",
        bad_example="b",
        why_discovered="w",
        source="discovered",
    )
    db.add(crit)
    db.commit()
    return crit


def _runs(db, agent, pv, n):
    runs = []
    for i in range(1, n + 1):
        run = Run(
            agent_id=agent.id,
            prompt_version_id=pv.id,
            position=i,
            messages=[
                {"role": "user", "content": f"u{i}"},
                {"role": "assistant", "content": f"a{i}"},
            ],
        )
        db.add(run)
        runs.append(run)
    db.commit()
    return runs


def _job(db, ws, job_type, payload):
    job = enqueue(db, ws.id, job_type, payload)
    return job


def _verdicts(monkeypatch, verdicts, rationale="because"):
    """Patch judge to return the given verdicts in order; returns the mock."""
    mock = MagicMock(side_effect=[JudgeResult(v, rationale) for v in verdicts])
    monkeypatch.setattr(handlers, "judge", mock)
    return mock


@pytest.fixture
def sanity(monkeypatch):
    mock = MagicMock(return_value=GOOD_CHECK)
    monkeypatch.setattr(handlers, "sanity_check", mock)
    return mock


def _events(db, name):
    return list(db.scalars(select(Event).where(Event.name == name)))


def test_handlers_registry():
    assert HANDLERS == {"build_tests": handle_build_tests, "replay": handle_replay}


# ---------- build_tests ----------


def test_build_tests_creates_fix_and_heldout(db_session, monkeypatch, sanity):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    runs = _runs(db_session, agent, pv, 3)
    judge_mock = _verdicts(monkeypatch, ["pass", "fail", "fail"], rationale="r" * 5000)
    job = _job(db_session, ws, "build_tests", {"criterion_id": crit.id})

    result = handle_build_tests(db_session, job)

    assert result == {
        "judged": 3,
        "tests_created": 2,
        "already_handled": False,
        "weaker_verification": False,
    }
    tests = {t.role: t for t in db_session.scalars(select(EvalTest))}
    assert set(tests) == {"fix", "heldout"}
    assert tests["fix"].source_run_id == runs[1].id
    assert tests["heldout"].source_run_id == runs[2].id
    assert tests["fix"].scripted_user_messages == ["u2"]

    baselines = list(db_session.scalars(select(EvalResult)))
    assert len(baselines) == 2
    for b in baselines:
        assert b.kind == "baseline" and b.verdict == "fail" and b.stale is False
        assert b.prompt_version_id == pv.id
        assert len(b.rationale) == 2000
    fix_baseline = next(b for b in baselines if b.test_id == tests["fix"].id)
    assert fix_baseline.transcript == runs[1].messages

    db_session.refresh(crit)
    assert crit.judge_check == {"passed": True, "good_verdict": "pass", "bad_verdict": "fail"}
    created = _events(db_session, "test_created")
    assert sorted(e.properties["role"] for e in created) == ["fix", "heldout"]
    assert judge_mock.call_count == 3


def test_build_tests_judges_oldest_to_newest_and_stops_after_two_failures(
    db_session, monkeypatch, sanity
):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    _runs(db_session, agent, pv, 6)
    judge_mock = _verdicts(monkeypatch, ["fail", "fail", "fail", "fail", "fail", "fail"])
    job = _job(db_session, ws, "build_tests", {"criterion_id": crit.id})

    result = handle_build_tests(db_session, job)

    assert judge_mock.call_count == 2
    assert result["judged"] == 2
    seen = [call.args[2][0]["content"] for call in judge_mock.call_args_list]
    assert seen == ["u1", "u2"]


def test_build_tests_only_judges_latest_eight_runs(db_session, monkeypatch, sanity):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    _runs(db_session, agent, pv, 10)
    judge_mock = _verdicts(monkeypatch, ["pass"] * 8)
    job = _job(db_session, ws, "build_tests", {"criterion_id": crit.id})

    result = handle_build_tests(db_session, job)

    assert judge_mock.call_count == 8
    seen = [call.args[2][0]["content"] for call in judge_mock.call_args_list]
    assert seen[0] == "u3" and seen[-1] == "u10"
    assert result == {
        "judged": 8,
        "tests_created": 0,
        "already_handled": True,
        "weaker_verification": False,
    }


def test_build_tests_single_failing_run_is_weaker_verification(db_session, monkeypatch, sanity):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    _runs(db_session, agent, pv, 3)
    _verdicts(monkeypatch, ["pass", "fail", "pass"])
    job = _job(db_session, ws, "build_tests", {"criterion_id": crit.id})

    result = handle_build_tests(db_session, job)

    assert result["tests_created"] == 1
    assert result["weaker_verification"] is True
    assert result["already_handled"] is False
    assert [t.role for t in db_session.scalars(select(EvalTest))] == ["fix"]


def test_build_tests_no_failing_run_creates_nothing_but_stores_judge_check(
    db_session, monkeypatch, sanity
):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    _runs(db_session, agent, pv, 2)
    _verdicts(monkeypatch, ["pass", "pass"])
    job = _job(db_session, ws, "build_tests", {"criterion_id": crit.id})

    result = handle_build_tests(db_session, job)

    assert result["tests_created"] == 0 and result["already_handled"] is True
    assert db_session.scalars(select(EvalTest)).first() is None
    assert db_session.scalars(select(EvalResult)).first() is None
    db_session.refresh(crit)
    assert crit.judge_check["passed"] is True
    assert _events(db_session, "test_created") == []


def test_build_tests_not_applicable_never_becomes_a_test(db_session, monkeypatch, sanity):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    _runs(db_session, agent, pv, 3)
    _verdicts(monkeypatch, ["not_applicable", "not_applicable", "fail"])
    job = _job(db_session, ws, "build_tests", {"criterion_id": crit.id})

    result = handle_build_tests(db_session, job)

    assert result["tests_created"] == 1
    test = db_session.scalars(select(EvalTest)).one()
    assert test.role == "fix" and test.scripted_user_messages == ["u3"]


def test_build_tests_skips_runs_without_user_messages(db_session, monkeypatch, sanity):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    db_session.add(
        Run(agent_id=agent.id, prompt_version_id=pv.id, position=1,
            messages=[{"role": "assistant", "content": "hello"}])
    )
    db_session.commit()
    judge_mock = _verdicts(monkeypatch, [])
    job = _job(db_session, ws, "build_tests", {"criterion_id": crit.id})

    result = handle_build_tests(db_session, job)

    judge_mock.assert_not_called()
    assert result["tests_created"] == 0


def test_build_tests_second_call_is_a_noop_without_llm_calls(db_session, monkeypatch, sanity):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    _runs(db_session, agent, pv, 2)
    judge_mock = _verdicts(monkeypatch, ["fail", "fail"])
    job = _job(db_session, ws, "build_tests", {"criterion_id": crit.id})
    handle_build_tests(db_session, job)
    sanity.reset_mock()
    judge_mock.reset_mock()

    again = handle_build_tests(db_session, job)

    assert again["tests_created"] == 0 and again["already_built"] is True
    sanity.assert_not_called()
    judge_mock.assert_not_called()
    assert len(list(db_session.scalars(select(EvalTest)))) == 2
    assert len(_events(db_session, "test_created")) == 2


def test_build_tests_failed_sanity_check_is_stored(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    _runs(db_session, agent, pv, 1)
    monkeypatch.setattr(
        handlers, "sanity_check",
        MagicMock(return_value=JudgeCheck(False, "fail", "fail")),
    )
    _verdicts(monkeypatch, ["fail"])
    job = _job(db_session, ws, "build_tests", {"criterion_id": crit.id})

    handle_build_tests(db_session, job)

    db_session.refresh(crit)
    assert crit.judge_check == {"passed": False, "good_verdict": "fail", "bad_verdict": "fail"}


def test_build_tests_judge_failure_in_runs_fails_job_and_writes_nothing(
    db_session, monkeypatch, sanity
):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    _runs(db_session, agent, pv, 3)
    monkeypatch.setattr(
        handlers, "judge",
        MagicMock(side_effect=[JudgeResult("fail", "r"), JudgeFailed("secret provider detail")]),
    )
    job = _job(db_session, ws, "build_tests", {"criterion_id": crit.id})

    with pytest.raises(JobFailed) as exc_info:
        handle_build_tests(db_session, job)

    assert str(exc_info.value) == "The judge was unavailable. Try again."
    assert "secret" not in str(exc_info.value)
    assert db_session.scalars(select(EvalTest)).first() is None
    assert db_session.scalars(select(EvalResult)).first() is None
    db_session.refresh(crit)
    assert crit.judge_check is None


def test_build_tests_sanity_check_failure_fails_job(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    _runs(db_session, agent, pv, 1)
    monkeypatch.setattr(handlers, "sanity_check", MagicMock(side_effect=JudgeFailed("boom")))
    job = _job(db_session, ws, "build_tests", {"criterion_id": crit.id})

    with pytest.raises(JobFailed, match="The judge was unavailable"):
        handle_build_tests(db_session, job)
    assert db_session.scalars(select(EvalTest)).first() is None


def test_build_tests_rejects_foreign_workspace_criterion(db_session, monkeypatch, sanity):
    _, agent, pv = _agent(db_session, token="a")
    crit = _criterion(db_session, agent)
    _runs(db_session, agent, pv, 1)
    other_ws, _, _ = _agent(db_session, token="b")
    judge_mock = _verdicts(monkeypatch, [])
    job = _job(db_session, other_ws, "build_tests", {"criterion_id": crit.id})

    with pytest.raises(JobFailed, match="That criterion no longer exists."):
        handle_build_tests(db_session, job)
    sanity.assert_not_called()
    judge_mock.assert_not_called()


def test_build_tests_missing_criterion_or_payload(db_session, sanity):
    ws, _, _ = _agent(db_session)
    for payload in ({"criterion_id": "nope"}, {}, {"criterion_id": 5}):
        job = _job(db_session, ws, "build_tests", payload)
        with pytest.raises(JobFailed, match="no longer exists"):
            handle_build_tests(db_session, job)


def test_build_tests_criterion_deleted_during_judging_fails_job(db_session, monkeypatch, sanity):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    _runs(db_session, agent, pv, 1)
    crit_id = crit.id

    def judge_and_delete(config, spec, transcript):
        db_session.delete(db_session.get(Criterion, crit_id))
        db_session.commit()
        return JudgeResult("fail", "r")

    monkeypatch.setattr(handlers, "judge", judge_and_delete)
    job = _job(db_session, ws, "build_tests", {"criterion_id": crit_id})

    with pytest.raises(JobFailed, match="That criterion no longer exists."):
        handle_build_tests(db_session, job)
    assert db_session.scalars(select(EvalTest)).first() is None


# ---------- replay ----------


def _test(db, crit, messages=("hi", "bye"), role="fix"):
    test = EvalTest(criterion_id=crit.id, role=role, scripted_user_messages=list(messages))
    db.add(test)
    db.commit()
    return test


TRANSCRIPT = [
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "hello"},
]


def _patch_replay(monkeypatch, transcript=TRANSCRIPT):
    mock = MagicMock(return_value=transcript)
    monkeypatch.setattr(handlers, "replay", mock)
    return mock


def test_replay_stores_result_for_given_prompt_version(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    test = _test(db_session, crit)
    new_version = PromptVersion(agent_id=agent.id, text="new prompt", origin="edit", status="promoted")
    db_session.add(new_version)
    db_session.commit()
    replay_mock = _patch_replay(monkeypatch)
    _verdicts(monkeypatch, ["pass"], rationale="x" * 3000)
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": new_version.id})

    result = handle_replay(db_session, job)

    row = db_session.scalars(select(EvalResult)).one()
    assert result == {"result_id": row.id, "verdict": "pass"}
    assert row.kind == "replay" and row.stale is False
    assert row.prompt_version_id == new_version.id
    assert row.transcript == TRANSCRIPT
    assert len(row.rationale) == 2000
    config, messages = replay_mock.call_args.args
    assert config.system_prompt == "new prompt"
    assert config.name == "Alex" and config.user_role_label == "Customer"
    assert messages == ["hi", "bye"]
    assert [e.name for e in db_session.scalars(select(Event))] == ["replay_passed"]


def test_replay_failed_verdict_logs_replay_failed(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    test = _test(db_session, crit)
    _patch_replay(monkeypatch)
    _verdicts(monkeypatch, ["fail"])
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": pv.id})

    assert handle_replay(db_session, job)["verdict"] == "fail"
    assert [e.name for e in db_session.scalars(select(Event))] == ["replay_failed"]


def test_replay_not_applicable_logs_no_event(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    test = _test(db_session, crit)
    _patch_replay(monkeypatch)
    _verdicts(monkeypatch, ["not_applicable"])
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": pv.id})

    assert handle_replay(db_session, job)["verdict"] == "not_applicable"
    assert db_session.scalars(select(Event)).first() is None
    assert db_session.scalars(select(EvalResult)).one().verdict == "not_applicable"


def test_replay_failed_raises_jobfailed_and_writes_nothing(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    test = _test(db_session, crit)
    monkeypatch.setattr(handlers, "replay", MagicMock(side_effect=ReplayFailed("api key sk-123")))
    judge_mock = _verdicts(monkeypatch, [])
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": pv.id})

    with pytest.raises(JobFailed) as exc_info:
        handle_replay(db_session, job)

    assert str(exc_info.value) == "The agent didn't respond. Try again."
    judge_mock.assert_not_called()
    assert db_session.scalars(select(EvalResult)).first() is None


def test_replay_judge_failure_raises_jobfailed_and_writes_nothing(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    test = _test(db_session, crit)
    _patch_replay(monkeypatch)
    monkeypatch.setattr(handlers, "judge", MagicMock(side_effect=JudgeFailed("garbage output")))
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": pv.id})

    with pytest.raises(JobFailed) as exc_info:
        handle_replay(db_session, job)

    assert str(exc_info.value) == "The judge was unavailable. Try again."
    assert db_session.scalars(select(EvalResult)).first() is None


def test_replay_missing_test_fails_job(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    replay_mock = _patch_replay(monkeypatch)
    job = _job(db_session, ws, "replay", {"test_id": "gone", "prompt_version_id": pv.id})

    with pytest.raises(JobFailed, match="no longer exists"):
        handle_replay(db_session, job)
    replay_mock.assert_not_called()


def test_replay_test_from_other_workspace_fails_job(db_session, monkeypatch):
    _, agent, pv = _agent(db_session, token="a")
    test = _test(db_session, _criterion(db_session, agent))
    other_ws, _, _ = _agent(db_session, token="b")
    replay_mock = _patch_replay(monkeypatch)
    job = _job(db_session, other_ws, "replay", {"test_id": test.id, "prompt_version_id": pv.id})

    with pytest.raises(JobFailed, match="no longer exists"):
        handle_replay(db_session, job)
    replay_mock.assert_not_called()


def test_replay_prompt_version_from_another_agent_fails_job(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    test = _test(db_session, _criterion(db_session, agent))
    other_agent = Agent(workspace_id=ws.id, name="Other", user_role_label="Caller")
    db_session.add(other_agent)
    db_session.flush()
    foreign = PromptVersion(agent_id=other_agent.id, text="x", origin="initial", status="promoted")
    db_session.add(foreign)
    db_session.commit()
    replay_mock = _patch_replay(monkeypatch)
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": foreign.id})

    with pytest.raises(JobFailed, match="no longer exists"):
        handle_replay(db_session, job)
    replay_mock.assert_not_called()


def test_replay_test_deleted_during_llm_calls_fails_job(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    test = _test(db_session, _criterion(db_session, agent))
    test_id = test.id

    def replay_and_delete(config, messages):
        db_session.delete(db_session.get(EvalTest, test_id))
        db_session.commit()
        return TRANSCRIPT

    monkeypatch.setattr(handlers, "replay", replay_and_delete)
    _verdicts(monkeypatch, ["pass"])
    job = _job(db_session, ws, "replay", {"test_id": test_id, "prompt_version_id": pv.id})

    with pytest.raises(JobFailed, match="That test no longer exists."):
        handle_replay(db_session, job)
    assert db_session.scalars(select(EvalResult)).first() is None


def test_job_rows_are_untouched_by_handlers(db_session, monkeypatch):
    """Handlers never change job status; run_one owns that."""
    ws, agent, pv = _agent(db_session)
    test = _test(db_session, _criterion(db_session, agent))
    _patch_replay(monkeypatch)
    _verdicts(monkeypatch, ["pass"])
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": pv.id})
    handle_replay(db_session, job)
    assert db_session.get(Job, job.id).status == "queued"
