from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.core.judge import JudgeCheck, JudgeFailed, JudgeResult
from backend.core.heldout import GenerateFailed
from backend.core.replay import ReplayFailed
from backend.core.report import ReportFailed, ReportNarrative
from backend.core.suggest import SuggestedFix, SuggestFailed, SuggestTooLong
from backend.db.candidates import ParentChanged, VersionLimitReached
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
from backend.jobs.handlers import (
    HANDLERS,
    JobFailed,
    RetryableJobFailure,
    handle_build_tests,
    handle_change_report,
    handle_generate_heldout,
    handle_replay,
    handle_suggest_fix,
)
from backend.db.testsuite import mark_criterion_results_stale
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


@pytest.fixture(autouse=True)
def sanity(monkeypatch):
    # Autouse: handle_replay also runs the sanity check when the criterion has no stored one.
    mock = MagicMock(return_value=GOOD_CHECK)
    monkeypatch.setattr(handlers, "sanity_check", mock)
    return mock


def _events(db, name):
    return list(db.scalars(select(Event).where(Event.name == name)))


def test_handlers_registry():
    assert HANDLERS == {
        "build_tests": handle_build_tests,
        "replay": handle_replay,
        "suggest_fix": handle_suggest_fix,
        "generate_heldout": handle_generate_heldout,
        "change_report": handle_change_report,
    }


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


def test_build_tests_sanity_check_failure_still_builds_the_tests_without_a_judge_check(
    db_session, monkeypatch, caplog
):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    _runs(db_session, agent, pv, 1)
    monkeypatch.setattr(handlers, "sanity_check", MagicMock(side_effect=JudgeFailed("boom")))
    _verdicts(monkeypatch, ["fail"])
    job = _job(db_session, ws, "build_tests", {"criterion_id": crit.id})

    with caplog.at_level("WARNING", logger="backend.jobs.handlers"):
        result = handle_build_tests(db_session, job)

    assert result["tests_created"] == 1
    assert len(list(db_session.scalars(select(EvalTest)))) == 1
    db_session.refresh(crit)
    assert crit.judge_check is None  # the next replay retries the check
    assert any("sanity check failure" in r.message for r in caplog.records)


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


def test_build_tests_holds_no_transaction_during_llm_calls(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    _runs(db_session, agent, pv, 2)
    in_txn = []

    def fake_sanity(config, spec):
        in_txn.append(db_session.in_transaction())
        return GOOD_CHECK

    def fake_judge(config, spec, transcript):
        in_txn.append(db_session.in_transaction())
        return JudgeResult("fail", "r")

    monkeypatch.setattr(handlers, "sanity_check", fake_sanity)
    monkeypatch.setattr(handlers, "judge", fake_judge)
    job = _job(db_session, ws, "build_tests", {"criterion_id": crit.id})

    result = handle_build_tests(db_session, job)

    assert result["tests_created"] == 2
    assert in_txn == [False, False, False]


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
    new_version = PromptVersion(agent_id=agent.id, text="new prompt", origin="edit", status="promoted", number=2)
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


def test_replay_holds_no_transaction_during_llm_calls(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    test = _test(db_session, _criterion(db_session, agent))
    in_txn = []

    def fake_replay(config, messages):
        in_txn.append(db_session.in_transaction())
        return TRANSCRIPT

    def fake_judge(config, spec, transcript):
        in_txn.append(db_session.in_transaction())
        return JudgeResult("pass", "ok")

    monkeypatch.setattr(handlers, "replay", fake_replay)
    monkeypatch.setattr(handlers, "judge", fake_judge)
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": pv.id})

    assert handle_replay(db_session, job)["verdict"] == "pass"
    assert in_txn == [False, False]


def test_replay_judge_never_sees_the_agent_prompt(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    test = _test(db_session, _criterion(db_session, agent))
    replay_mock = _patch_replay(monkeypatch)
    judge_mock = _verdicts(monkeypatch, ["pass"])
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": pv.id})

    handle_replay(db_session, job)

    assert replay_mock.call_args.args[0].system_prompt == "be nice"
    judge_config = judge_mock.call_args.args[0]
    assert judge_config.system_prompt == ""
    assert judge_config.name == "Alex" and judge_config.user_role_label == "Customer"


# ---------- replay: sanity check, latest result wins ----------


def test_replay_runs_sanity_check_once_and_stores_judge_check(db_session, monkeypatch, sanity):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    test = _test(db_session, crit)
    _patch_replay(monkeypatch)
    _verdicts(monkeypatch, ["pass"])
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": pv.id})

    handle_replay(db_session, job)

    assert sanity.call_count == 1
    config, spec = sanity.call_args.args
    assert config.system_prompt == "" and config.name == "Alex" and config.user_role_label == "Customer"
    assert spec.name == "Stays polite"
    db_session.refresh(crit)
    assert crit.judge_check == {"passed": True, "good_verdict": "pass", "bad_verdict": "fail"}


def test_replay_skips_sanity_check_when_judge_check_is_stored(db_session, monkeypatch, sanity):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    crit.judge_check = {"passed": True, "good_verdict": "pass", "bad_verdict": "fail"}
    db_session.commit()
    test = _test(db_session, crit)
    _patch_replay(monkeypatch)
    _verdicts(monkeypatch, ["pass"])
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": pv.id})

    handle_replay(db_session, job)

    sanity.assert_not_called()
    db_session.refresh(crit)
    assert crit.judge_check == {"passed": True, "good_verdict": "pass", "bad_verdict": "fail"}


def test_replay_sanity_check_failure_keeps_the_verdict_and_stores_no_judge_check(
    db_session, monkeypatch, caplog
):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    test = _test(db_session, crit)
    _patch_replay(monkeypatch)
    _verdicts(monkeypatch, ["pass"])
    sanity_mock = MagicMock(side_effect=JudgeFailed("boom"))
    monkeypatch.setattr(handlers, "sanity_check", sanity_mock)
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": pv.id})

    with caplog.at_level("WARNING", logger="backend.jobs.handlers"):
        result = handle_replay(db_session, job)

    row = db_session.scalars(select(EvalResult)).one()
    assert result == {"result_id": row.id, "verdict": "pass"}
    db_session.refresh(crit)
    assert crit.judge_check is None
    assert any("sanity check failure" in r.message for r in caplog.records)

    # The next replay tries the check again and stores it.
    sanity_mock.side_effect = None
    sanity_mock.return_value = GOOD_CHECK
    _verdicts(monkeypatch, ["pass"])
    handle_replay(db_session, _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": pv.id}))
    db_session.refresh(crit)
    assert crit.judge_check == {"passed": True, "good_verdict": "pass", "bad_verdict": "fail"}


@pytest.mark.parametrize("system", [True, False])
@pytest.mark.parametrize(
    "exc, message",
    [
        (ReplayFailed("provider says sk-SECRET"), "The agent didn't respond. Try again."),
        (JudgeFailed("provider says sk-SECRET"), "The judge was unavailable. Try again."),
    ],
)
def test_replay_failure_is_retryable_only_for_system_jobs(db_session, monkeypatch, system, exc, message):
    ws, agent, pv = _agent(db_session)
    test = _test(db_session, _criterion(db_session, agent))
    pv.status = "approved_verifying"  # a system replay only runs for a version still being verified
    db_session.commit()
    if isinstance(exc, ReplayFailed):
        monkeypatch.setattr(handlers, "replay", MagicMock(side_effect=exc))
    else:
        _patch_replay(monkeypatch)
        monkeypatch.setattr(handlers, "judge", MagicMock(side_effect=exc))
    payload = {"test_id": test.id, "prompt_version_id": pv.id}
    if system:
        payload["system"] = True

    with pytest.raises(JobFailed) as err:
        handle_replay(db_session, _job(db_session, ws, "replay", payload))

    assert str(err.value) == message
    assert isinstance(err.value, RetryableJobFailure) is system
    assert db_session.scalars(select(EvalResult)).first() is None


def _verifying_version(db, agent, status="approved_verifying"):
    version = PromptVersion(agent_id=agent.id, text="candidate", origin="suggested", status=status, number=2)
    db.add(version)
    db.commit()
    return version


@pytest.mark.parametrize("status", ["failed_verification", "promoted", "superseded"])
def test_system_replay_for_a_resolved_version_is_skipped_without_model_calls(db_session, monkeypatch, status):
    ws, agent, pv = _agent(db_session)
    test = _test(db_session, _criterion(db_session, agent))
    version = _verifying_version(db_session, agent, status)
    replay_mock = _patch_replay(monkeypatch)
    judge_mock = _verdicts(monkeypatch, ["pass"])
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": version.id, "system": True})

    assert handle_replay(db_session, job) == {"skipped": True}

    replay_mock.assert_not_called()
    judge_mock.assert_not_called()
    assert db_session.scalars(select(EvalResult)).first() is None
    assert _events(db_session, "replay_passed") == []


def test_system_replay_for_a_verifying_version_still_runs(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    test = _test(db_session, _criterion(db_session, agent))
    version = _verifying_version(db_session, agent)
    _patch_replay(monkeypatch)
    _verdicts(monkeypatch, ["pass"])
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": version.id, "system": True})

    result = handle_replay(db_session, job)

    assert result["verdict"] == "pass"
    assert db_session.scalars(select(EvalResult)).one().prompt_version_id == version.id


def test_user_replay_for_a_resolved_version_still_runs(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    test = _test(db_session, _criterion(db_session, agent))
    version = _verifying_version(db_session, agent, "failed_verification")
    replay_mock = _patch_replay(monkeypatch)
    _verdicts(monkeypatch, ["pass"])
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": version.id})

    result = handle_replay(db_session, job)

    assert result["verdict"] == "pass"
    replay_mock.assert_called_once()
    assert db_session.scalars(select(EvalResult)).one().prompt_version_id == version.id


def _resolve_elsewhere_during_judge(db_session, monkeypatch, version_id):
    def judge_while_resolved(*args, **kwargs):
        with sessionmaker(bind=db_session.get_bind())() as other:
            other.get(PromptVersion, version_id).status = "failed_verification"
            other.commit()
        return JudgeResult("pass", "because")

    monkeypatch.setattr(handlers, "judge", judge_while_resolved)


def test_system_replay_resolved_during_the_model_calls_stores_nothing(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    test = _test(db_session, _criterion(db_session, agent))
    version = _verifying_version(db_session, agent)
    _patch_replay(monkeypatch)
    _resolve_elsewhere_during_judge(db_session, monkeypatch, version.id)
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": version.id, "system": True})

    assert handle_replay(db_session, job) == {"skipped": True}

    db_session.expire_all()
    assert db_session.scalars(select(EvalResult)).first() is None
    assert _events(db_session, "replay_passed") == []


def test_user_replay_resolved_during_the_model_calls_still_stores_its_result(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    test = _test(db_session, _criterion(db_session, agent))
    version = _verifying_version(db_session, agent)
    _patch_replay(monkeypatch)
    _resolve_elsewhere_during_judge(db_session, monkeypatch, version.id)
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": version.id})

    assert handle_replay(db_session, job)["verdict"] == "pass"
    assert db_session.scalars(select(EvalResult)).one().verdict == "pass"


def test_retryable_job_failure_is_a_job_failed():
    assert issubclass(RetryableJobFailure, JobFailed)
    assert str(RetryableJobFailure("x")) == "x"


def test_replay_sanity_check_runs_outside_a_transaction(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    test = _test(db_session, _criterion(db_session, agent))
    in_txn = []

    def fake_sanity(config, spec):
        in_txn.append(db_session.in_transaction())
        return GOOD_CHECK

    monkeypatch.setattr(handlers, "sanity_check", fake_sanity)
    _patch_replay(monkeypatch)
    _verdicts(monkeypatch, ["pass"])
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": pv.id})

    handle_replay(db_session, job)

    assert in_txn == [False]


def test_second_replay_for_same_test_and_version_replaces_the_first(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    test = _test(db_session, _criterion(db_session, agent))
    db_session.add(
        EvalResult(test_id=test.id, prompt_version_id=pv.id, kind="baseline", verdict="fail", rationale="b")
    )
    db_session.commit()
    _patch_replay(monkeypatch)
    _verdicts(monkeypatch, ["fail", "pass"])
    payload = {"test_id": test.id, "prompt_version_id": pv.id}

    first = handle_replay(db_session, _job(db_session, ws, "replay", payload))
    second = handle_replay(db_session, _job(db_session, ws, "replay", payload))

    rows = list(db_session.scalars(select(EvalResult)))
    replays = [r for r in rows if r.kind == "replay"]
    assert len(replays) == 1
    assert replays[0].id == second["result_id"] and replays[0].id != first["result_id"]
    assert replays[0].verdict == "pass"
    assert [r.kind for r in rows if r.kind == "baseline"] == ["baseline"]


def test_replay_for_a_different_version_keeps_the_earlier_result(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    test = _test(db_session, _criterion(db_session, agent))
    v2 = PromptVersion(agent_id=agent.id, text="v2", origin="edit", status="promoted", number=2)
    db_session.add(v2)
    db_session.commit()
    _patch_replay(monkeypatch)
    _verdicts(monkeypatch, ["fail", "pass"])

    handle_replay(db_session, _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": pv.id}))
    handle_replay(db_session, _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": v2.id}))

    versions = sorted(r.prompt_version_id for r in db_session.scalars(select(EvalResult)))
    assert versions == sorted([pv.id, v2.id])


def test_replay_replacement_also_removes_stale_results_of_the_same_version(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    test = _test(db_session, _criterion(db_session, agent))
    db_session.add(
        EvalResult(
            test_id=test.id, prompt_version_id=pv.id, kind="replay", verdict="pass", rationale="old", stale=True
        )
    )
    db_session.commit()
    _patch_replay(monkeypatch)
    _verdicts(monkeypatch, ["fail"])

    handle_replay(db_session, _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": pv.id}))

    row = db_session.scalars(select(EvalResult)).one()
    assert row.verdict == "fail" and row.stale is False


# ---------- revision guard: results judged against an edited criterion are discarded ----------


def _edit_criterion_elsewhere(db_session, criterion_id, definition="A different definition."):
    """Edit the criterion the way the PATCH route does, but through ANOTHER session."""
    other = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)()
    try:
        row = other.get(Criterion, criterion_id)
        row.definition = definition
        mark_criterion_results_stale(other, row)
        other.commit()
    finally:
        other.close()


def test_replay_discards_result_when_criterion_is_edited_during_the_job(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    test = _test(db_session, crit)
    crit_id = crit.id
    _patch_replay(monkeypatch)

    def judge_while_edited(config, spec, transcript):
        _edit_criterion_elsewhere(db_session, crit_id)
        return JudgeResult("pass", "r")

    monkeypatch.setattr(handlers, "judge", judge_while_edited)
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": pv.id})

    result = handle_replay(db_session, job)

    assert result == {"discarded": True}
    assert db_session.scalars(select(EvalResult)).first() is None
    assert _events(db_session, "replay_passed") == []
    db_session.refresh(crit)
    assert crit.revision == 1
    assert crit.judge_check is None  # the obsolete sanity check is not written back


def test_replay_discard_keeps_the_existing_result(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    test = _test(db_session, crit)
    db_session.add(
        EvalResult(test_id=test.id, prompt_version_id=pv.id, kind="replay", verdict="pass", rationale="old")
    )
    db_session.commit()
    crit_id = crit.id
    _patch_replay(monkeypatch)

    def judge_while_edited(config, spec, transcript):
        _edit_criterion_elsewhere(db_session, crit_id)
        return JudgeResult("fail", "r")

    monkeypatch.setattr(handlers, "judge", judge_while_edited)
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": pv.id})

    assert handle_replay(db_session, job) == {"discarded": True}
    assert db_session.scalars(select(EvalResult)).one().rationale == "old"


def test_replay_with_unchanged_revision_is_not_discarded(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    crit.revision = 3
    db_session.commit()
    test = _test(db_session, crit)
    _patch_replay(monkeypatch)
    _verdicts(monkeypatch, ["pass"])
    job = _job(db_session, ws, "replay", {"test_id": test.id, "prompt_version_id": pv.id})

    result = handle_replay(db_session, job)

    assert "discarded" not in result
    assert db_session.scalars(select(EvalResult)).one().verdict == "pass"


def test_build_tests_discards_when_criterion_is_edited_during_the_job(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)
    _runs(db_session, agent, pv, 3)
    crit_id = crit.id

    edited = []

    def judge_while_edited(config, spec, transcript):
        if not edited:  # build judges several runs; edit only once
            edited.append(True)
            _edit_criterion_elsewhere(db_session, crit_id)
        return JudgeResult("fail", "r")

    monkeypatch.setattr(handlers, "judge", judge_while_edited)
    job = _job(db_session, ws, "build_tests", {"criterion_id": crit_id})

    result = handle_build_tests(db_session, job)

    assert result == {"discarded": True, "tests_created": 0}
    assert db_session.scalars(select(EvalTest)).first() is None
    assert db_session.scalars(select(EvalResult)).first() is None
    assert _events(db_session, "test_created") == []
    db_session.refresh(crit)
    assert crit.revision == 1
    assert crit.judge_check is None


# ---------- suggest_fix ----------

FAIL_TRANSCRIPT = [
    {"role": "user", "content": "my order is late"},
    {"role": "assistant", "content": "Not my problem."},
]


def _failing(db, transcript=FAIL_TRANSCRIPT, rationale="was rude"):
    """An agent with a fix test that fails on the current version."""
    ws, agent, pv = _agent(db)
    crit = _criterion(db, agent)
    test = _test(db, crit)
    db.add(
        EvalResult(
            test_id=test.id,
            prompt_version_id=pv.id,
            kind="replay",
            verdict="fail",
            rationale=rationale,
            transcript=transcript,
        )
    )
    db.commit()
    return ws, agent, pv, crit, test


def _fix(**overrides):
    values = dict(
        prompt_text="be very nice",
        failing_turn_index=1,
        change_summary="Adds a politeness rule.",
        touched_section="Tone",
        expected_effect="Replies stay polite.",
        risk="May sound stiff.",
    )
    values.update(overrides)
    return SuggestedFix(**values)


def _patch_suggest(monkeypatch, fix=None, exc=None):
    mock = MagicMock(return_value=fix or _fix(), side_effect=exc)
    monkeypatch.setattr(handlers, "suggest_fix", mock)
    return mock


def _proposed(db):
    return list(db.scalars(select(PromptVersion).where(PromptVersion.status == "proposed")))


def _other_session(db_session):
    return sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)()


def test_suggest_fix_creates_one_proposed_version_with_the_why_report(db_session, monkeypatch):
    ws, agent, pv, crit, test = _failing(db_session)
    suggest = _patch_suggest(monkeypatch)
    job = _job(db_session, ws, "suggest_fix", {"test_id": test.id})

    result = handle_suggest_fix(db_session, job)

    version = _proposed(db_session)[0]
    assert len(_proposed(db_session)) == 1
    assert result == {"version_id": version.id, "number": 2}
    assert version.text == "be very nice"
    assert version.origin == "suggested" and version.parent_id == pv.id
    assert version.source_test_id == test.id and version.number == 2
    assert version.change_report == {
        "why": {
            "criterion_name": "Stays polite",
            "failing_test_id": test.id,
            "failing_turn_index": 1,
            "failing_quote": "Not my problem.",  # taken by code from the stored transcript
            "change_summary": "Adds a politeness rule.",
            "touched_section": "Tone",
            "expected_effect": "Replies stay polite.",
            "risk": "May sound stiff.",
        },
        "outcome": None,
    }
    db_session.refresh(agent)
    assert agent.current_prompt_version_id == pv.id  # chat keeps the current version
    config, spec, rationale, transcript = suggest.call_args.args
    assert config.system_prompt == "be nice" and config.name == "Alex"
    assert spec.name == "Stays polite"
    assert rationale == "was rude" and transcript == FAIL_TRANSCRIPT
    assert len(_events(db_session, "fix_suggested")) == 1


def test_suggest_fix_quote_is_empty_when_the_index_is_out_of_range(db_session, monkeypatch):
    ws, agent, pv, crit, test = _failing(db_session)
    _patch_suggest(monkeypatch, _fix(failing_turn_index=9))

    handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test.id}))

    assert _proposed(db_session)[0].change_report["why"]["failing_quote"] == ""


def test_suggest_fix_caps_the_model_summary_fields_at_600_characters(db_session, monkeypatch):
    ws, agent, pv, crit, test = _failing(db_session)
    long = "x" * 5000
    _patch_suggest(monkeypatch, _fix(change_summary=long, touched_section=long, expected_effect=long, risk=long))

    handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test.id}))

    why = _proposed(db_session)[0].change_report["why"]
    for key in ("change_summary", "touched_section", "expected_effect", "risk"):
        assert len(why[key]) == 600


def test_suggest_fix_never_passes_heldout_data_to_the_suggester(db_session, monkeypatch):
    ws, agent, pv, crit, test = _failing(db_session)
    heldout = _test(db_session, crit, messages=("HELDOUT-MSG-ONE", "HELDOUT-MSG-TWO"), role="heldout")
    db_session.add(
        EvalResult(
            test_id=heldout.id,
            prompt_version_id=pv.id,
            kind="replay",
            verdict="fail",
            rationale="HELDOUT-RATIONALE",
            transcript=[{"role": "user", "content": "HELDOUT-TRANSCRIPT"}],
        )
    )
    db_session.commit()
    suggest = _patch_suggest(monkeypatch)

    handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test.id}))

    sent = repr(suggest.call_args)
    for marker in ("HELDOUT-MSG-ONE", "HELDOUT-MSG-TWO", "HELDOUT-RATIONALE", "HELDOUT-TRANSCRIPT"):
        assert marker not in sent
    assert "was rude" in sent  # the fix test's own failing result is what it does get


def test_suggest_fix_reads_the_transcript_only_from_the_failing_result(db_session, monkeypatch):
    ws, agent, pv, crit, test = _failing(db_session)
    test.scripted_user_messages = ["SCRIPTED-ONLY"]
    db_session.commit()
    suggest = _patch_suggest(monkeypatch)

    handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test.id}))

    assert "SCRIPTED-ONLY" not in repr(suggest.call_args)


def test_suggest_fix_refuses_a_heldout_test(db_session, monkeypatch):
    ws, agent, pv, crit, test = _failing(db_session)
    heldout = _test(db_session, crit, role="heldout")
    suggest = _patch_suggest(monkeypatch)

    with pytest.raises(JobFailed) as err:
        handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": heldout.id}))

    assert str(err.value) == "Suggest a fix from the fix test."
    suggest.assert_not_called()


def test_suggest_fix_requires_a_failing_result_on_the_current_prompt(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    test = _test(db_session, _criterion(db_session, agent))  # never run
    suggest = _patch_suggest(monkeypatch)

    with pytest.raises(JobFailed) as err:
        handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test.id}))

    assert str(err.value) == "This test isn't failing on the current prompt."
    suggest.assert_not_called()


def test_suggest_fix_refuses_a_passing_result(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    test = _test(db_session, _criterion(db_session, agent))
    db_session.add(
        EvalResult(
            test_id=test.id,
            prompt_version_id=pv.id,
            kind="replay",
            verdict="pass",
            rationale="ok",
            transcript=FAIL_TRANSCRIPT,
        )
    )
    db_session.commit()
    suggest = _patch_suggest(monkeypatch)

    with pytest.raises(JobFailed) as err:
        handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test.id}))

    assert str(err.value) == "This test isn't failing on the current prompt."
    suggest.assert_not_called()


def test_suggest_fix_refuses_a_failure_on_an_older_version(db_session, monkeypatch):
    ws, agent, pv, crit, test = _failing(db_session)
    new = PromptVersion(agent_id=agent.id, text="edited", origin="edit", status="promoted", number=2)
    db_session.add(new)
    db_session.flush()
    agent.current_prompt_version_id = new.id
    db_session.commit()
    suggest = _patch_suggest(monkeypatch)

    with pytest.raises(JobFailed) as err:
        handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test.id}))

    assert str(err.value) == "This test isn't failing on the current prompt."
    suggest.assert_not_called()


def test_suggest_fix_failing_result_without_a_transcript_fails_the_job(db_session, monkeypatch):
    ws, agent, pv, crit, test = _failing(db_session, transcript=None)
    suggest = _patch_suggest(monkeypatch)

    with pytest.raises(JobFailed) as err:
        handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test.id}))

    assert str(err.value) == "This test isn't failing on the current prompt."
    suggest.assert_not_called()


def test_suggest_fix_fails_while_a_candidate_is_open(db_session, monkeypatch):
    ws, agent, pv, crit, test = _failing(db_session)
    suggest = _patch_suggest(monkeypatch)
    handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test.id}))
    suggest.reset_mock()

    with pytest.raises(JobFailed) as err:
        handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test.id}))

    assert str(err.value) == "A fix is already proposed or being verified."
    suggest.assert_not_called()
    assert len(_proposed(db_session)) == 1


def test_suggest_fix_candidate_opened_during_the_call_fails_the_job(db_session, monkeypatch):
    ws, agent, pv, crit, test = _failing(db_session)
    agent_id = agent.id

    def suggest_while_candidate_appears(config, spec, rationale, transcript):
        other = _other_session(db_session)
        try:
            other.add(PromptVersion(agent_id=agent_id, text="x", origin="suggested", status="proposed", number=2))
            other.commit()
        finally:
            other.close()
        return _fix()

    monkeypatch.setattr(handlers, "suggest_fix", suggest_while_candidate_appears)

    with pytest.raises(JobFailed) as err:
        handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test.id}))

    assert str(err.value) == "A fix is already proposed or being verified."
    assert len(_proposed(db_session)) == 1
    assert _events(db_session, "fix_suggested") == []


def test_suggest_fix_failure_uses_a_fixed_message(db_session, monkeypatch):
    ws, agent, pv, crit, test = _failing(db_session)
    _patch_suggest(monkeypatch, exc=SuggestFailed("suggester call failed: key sk-SECRET rejected"))

    with pytest.raises(JobFailed) as err:
        handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test.id}))

    assert str(err.value) == "Couldn't write a fix. Try again."
    assert _proposed(db_session) == []
    assert _events(db_session, "fix_suggested") == []


def test_suggest_fix_version_limit_maps_to_a_fixed_message(db_session, monkeypatch):
    ws, agent, pv, crit, test = _failing(db_session)
    _patch_suggest(monkeypatch)
    monkeypatch.setattr(handlers, "create_proposed_version", MagicMock(side_effect=VersionLimitReached("a")))

    with pytest.raises(JobFailed) as err:
        handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test.id}))

    assert str(err.value) == "Prompt version limit reached for this agent."
    assert _events(db_session, "fix_suggested") == []


def test_suggest_fix_passes_the_version_it_read_as_the_expected_parent(db_session, monkeypatch):
    ws, agent, pv, crit, test = _failing(db_session)
    _patch_suggest(monkeypatch)
    real = handlers.create_proposed_version
    spy = MagicMock(side_effect=real)
    monkeypatch.setattr(handlers, "create_proposed_version", spy)

    handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test.id}))

    assert spy.call_args.kwargs["expected_parent_id"] == pv.id


def test_suggest_fix_parent_changed_maps_to_a_fixed_message(db_session, monkeypatch):
    ws, agent, pv, crit, test = _failing(db_session)
    _patch_suggest(monkeypatch)
    monkeypatch.setattr(handlers, "create_proposed_version", MagicMock(side_effect=ParentChanged("a")))

    with pytest.raises(JobFailed) as err:
        handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test.id}))

    assert str(err.value) == "This test isn't failing on the current prompt."
    assert _events(db_session, "fix_suggested") == []


def test_suggest_fix_stores_nothing_when_the_prompt_moves_after_the_unlocked_check(db_session, monkeypatch):
    """The race the unlocked check cannot see: the version changes right after it passed."""
    ws, agent, pv, crit, test = _failing(db_session)
    agent_id = agent.id
    _patch_suggest(monkeypatch)
    real_check = handlers._current_version_id

    def check_then_rival_commits(db, agent_row):
        answer = real_check(db, agent_row)  # still the old version: the check passes
        other = _other_session(db_session)
        try:
            new = PromptVersion(agent_id=agent_id, text="edited", origin="manual", status="promoted", number=2)
            other.add(new)
            other.flush()
            other.get(Agent, agent_id).current_prompt_version_id = new.id
            other.commit()
        finally:
            other.close()
        return answer

    monkeypatch.setattr(handlers, "_current_version_id", check_then_rival_commits)

    with pytest.raises(JobFailed) as err:
        handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test.id}))

    assert str(err.value) == "This test isn't failing on the current prompt."
    assert _proposed(db_session) == []


def test_suggest_fix_too_long_has_its_own_fixed_message(db_session, monkeypatch):
    ws, agent, pv, crit, test = _failing(db_session)
    _patch_suggest(monkeypatch, exc=SuggestTooLong("suggested prompt is too long"))

    with pytest.raises(JobFailed) as err:
        handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test.id}))

    assert str(err.value) == "The suggested prompt was too long. Try again."
    assert _proposed(db_session) == []


def test_suggest_fix_rejects_foreign_and_missing_tests(db_session, monkeypatch):
    ws, agent, pv, crit, test = _failing(db_session)
    other_ws = Workspace(token_hash="other", expires_at=utcnow())
    db_session.add(other_ws)
    db_session.commit()
    suggest = _patch_suggest(monkeypatch)

    for ws_, payload in ((other_ws, {"test_id": test.id}), (ws, {"test_id": "nope"}), (ws, {})):
        with pytest.raises(JobFailed) as err:
            handle_suggest_fix(db_session, _job(db_session, ws_, "suggest_fix", payload))
        assert str(err.value) == "That test no longer exists."
    suggest.assert_not_called()


def test_suggest_fix_test_deleted_during_the_call_fails_the_job(db_session, monkeypatch):
    ws, agent, pv, crit, test = _failing(db_session)
    test_id = test.id

    def suggest_while_deleted(config, spec, rationale, transcript):
        other = _other_session(db_session)
        try:
            other.execute(EvalResult.__table__.delete())
            other.execute(EvalTest.__table__.delete().where(EvalTest.id == test_id))
            other.commit()
        finally:
            other.close()
        return _fix()

    monkeypatch.setattr(handlers, "suggest_fix", suggest_while_deleted)

    with pytest.raises(JobFailed) as err:
        handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test_id}))

    assert str(err.value) == "That test no longer exists."
    assert _proposed(db_session) == []


def test_suggest_fix_fails_when_the_criterion_is_edited_during_the_call(db_session, monkeypatch):
    ws, agent, pv, crit, test = _failing(db_session)
    crit_id = crit.id

    def suggest_while_edited(config, spec, rationale, transcript):
        _edit_criterion_elsewhere(db_session, crit_id)
        return _fix()

    monkeypatch.setattr(handlers, "suggest_fix", suggest_while_edited)

    with pytest.raises(JobFailed) as err:
        handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test.id}))

    assert str(err.value) == "That criterion changed while the fix was written. Try again."
    assert _proposed(db_session) == []
    assert _events(db_session, "fix_suggested") == []


def test_suggest_fix_fails_when_the_current_version_changes_during_the_call(db_session, monkeypatch):
    ws, agent, pv, crit, test = _failing(db_session)
    agent_id = agent.id

    def suggest_while_prompt_changes(config, spec, rationale, transcript):
        other = _other_session(db_session)
        try:
            new = PromptVersion(agent_id=agent_id, text="edited", origin="edit", status="promoted", number=2)
            other.add(new)
            other.flush()
            other.get(Agent, agent_id).current_prompt_version_id = new.id
            other.commit()
        finally:
            other.close()
        return _fix()

    monkeypatch.setattr(handlers, "suggest_fix", suggest_while_prompt_changes)

    with pytest.raises(JobFailed) as err:
        handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test.id}))

    assert str(err.value) == "This test isn't failing on the current prompt."
    assert _proposed(db_session) == []


def test_suggest_fix_holds_no_transaction_during_the_llm_call(db_session, monkeypatch):
    ws, agent, pv, crit, test = _failing(db_session)
    in_txn = []

    def fake_suggest(config, spec, rationale, transcript):
        in_txn.append(db_session.in_transaction())
        return _fix()

    monkeypatch.setattr(handlers, "suggest_fix", fake_suggest)

    handle_suggest_fix(db_session, _job(db_session, ws, "suggest_fix", {"test_id": test.id}))

    assert in_txn == [False]


# ---------- generate_heldout ----------


def _fix_only(db):
    ws, agent, pv = _agent(db)
    crit = _criterion(db, agent)
    fix = _test(db, crit, messages=("u1", "u2"))
    return ws, agent, pv, crit, fix


def _patch_generate(monkeypatch, scenarios):
    mock = MagicMock(side_effect=scenarios)
    monkeypatch.setattr(handlers, "generate_scenario", mock)
    return mock


def _heldouts(db):
    return list(db.scalars(select(EvalTest).where(EvalTest.role == "heldout")))


def test_generate_heldout_keeps_the_first_failing_scenario(db_session, monkeypatch):
    ws, agent, pv, crit, fix = _fix_only(db_session)
    generate = _patch_generate(monkeypatch, [["a1", "a2"], ["b1", "b2", "b3"]])
    replay_mock = _patch_replay(monkeypatch)
    judge_mock = _verdicts(monkeypatch, ["pass", "fail"], rationale="r" * 3000)
    job = _job(db_session, ws, "generate_heldout", {"criterion_id": crit.id})

    result = handle_generate_heldout(db_session, job)

    test = _heldouts(db_session)[0]
    assert len(_heldouts(db_session)) == 1
    assert result == {"created": True, "test_id": test.id}
    assert test.criterion_id == crit.id and test.origin == "generated" and test.source_run_id is None
    assert test.scripted_user_messages == ["b1", "b2", "b3"]
    row = db_session.scalars(select(EvalResult).where(EvalResult.test_id == test.id)).one()
    assert row.kind == "replay" and row.verdict == "fail" and row.stale is False
    assert row.prompt_version_id == pv.id and row.transcript == TRANSCRIPT
    assert len(row.rationale) == 2000
    assert generate.call_count == 2 and judge_mock.call_count == 2
    assert generate.call_args_list[0].args[2] == []
    spec, fix_messages, avoid = generate.call_args_list[1].args
    assert spec.name == "Stays polite" and fix_messages == ["u1", "u2"]
    assert avoid == [["a1", "a2"]]
    config, messages = replay_mock.call_args.args
    assert config.system_prompt == "be nice" and messages == ["b1", "b2", "b3"]
    assert judge_mock.call_args.args[0].system_prompt == ""  # blanked judge config
    assert len(_events(db_session, "test_generated")) == 1
    assert [e.properties for e in _events(db_session, "test_created")] == [{"role": "heldout"}]


def test_generate_heldout_stops_after_three_passing_attempts(db_session, monkeypatch):
    ws, agent, pv, crit, fix = _fix_only(db_session)
    generate = _patch_generate(monkeypatch, [["a1", "a2"], ["b1", "b2"], ["c1", "c2"], ["d1", "d2"]])
    _patch_replay(monkeypatch)
    _verdicts(monkeypatch, ["pass", "not_applicable", "pass"])
    job = _job(db_session, ws, "generate_heldout", {"criterion_id": crit.id})

    result = handle_generate_heldout(db_session, job)

    assert result == {"created": False, "reason": "agent_handles_it"}
    assert generate.call_count == 3
    assert _heldouts(db_session) == []
    assert _events(db_session, "test_generated") == [] and _events(db_session, "test_created") == []


def test_generate_heldout_failed_generation_counts_as_an_attempt(db_session, monkeypatch):
    ws, agent, pv, crit, fix = _fix_only(db_session)
    generate = _patch_generate(monkeypatch, [GenerateFailed("x"), GenerateFailed("x"), ["c1", "c2"], ["d1", "d2"]])
    replay_mock = _patch_replay(monkeypatch)
    _verdicts(monkeypatch, ["fail"])
    job = _job(db_session, ws, "generate_heldout", {"criterion_id": crit.id})

    result = handle_generate_heldout(db_session, job)

    assert result["created"] is True
    assert generate.call_count == 3
    assert replay_mock.call_count == 1


def test_generate_heldout_all_generations_failing_creates_nothing(db_session, monkeypatch):
    ws, agent, pv, crit, fix = _fix_only(db_session)
    generate = _patch_generate(monkeypatch, [GenerateFailed("x")] * 3)
    replay_mock = _patch_replay(monkeypatch)
    job = _job(db_session, ws, "generate_heldout", {"criterion_id": crit.id})

    assert handle_generate_heldout(db_session, job) == {"created": False, "reason": "agent_handles_it"}
    assert generate.call_count == 3
    replay_mock.assert_not_called()


def test_generate_heldout_scenario_equal_to_the_fix_messages_is_a_failed_attempt(db_session, monkeypatch):
    from backend.core import heldout as heldout_core

    ws, agent, pv, crit, fix = _fix_only(db_session)
    replies = iter(['{"messages": ["U1", " u2 "]}', '{"messages": ["new one", "new two"]}'])
    monkeypatch.setattr(heldout_core, "groq_chat", lambda *a, **k: next(replies))
    replay_mock = _patch_replay(monkeypatch)
    _verdicts(monkeypatch, ["fail"])
    job = _job(db_session, ws, "generate_heldout", {"criterion_id": crit.id})

    result = handle_generate_heldout(db_session, job)

    assert result["created"] is True
    assert replay_mock.call_count == 1  # the copy of the fix test was never replayed
    assert _heldouts(db_session)[0].scripted_user_messages == ["new one", "new two"]


def test_generate_heldout_needs_a_fix_test_and_no_heldout_test(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    crit = _criterion(db_session, agent)  # no tests yet
    generate = _patch_generate(monkeypatch, [])

    with pytest.raises(JobFailed) as err:
        handle_generate_heldout(db_session, _job(db_session, ws, "generate_heldout", {"criterion_id": crit.id}))
    assert str(err.value) == "This criterion needs a fix test and no held-out test."

    _test(db_session, crit, role="fix")
    _test(db_session, crit, messages=("h1", "h2"), role="heldout")
    with pytest.raises(JobFailed) as err:
        handle_generate_heldout(db_session, _job(db_session, ws, "generate_heldout", {"criterion_id": crit.id}))
    assert str(err.value) == "This criterion needs a fix test and no held-out test."
    generate.assert_not_called()


def test_generate_heldout_rejects_foreign_or_missing_criterion(db_session, monkeypatch):
    ws, agent, pv, crit, fix = _fix_only(db_session)
    other_ws = Workspace(token_hash="other", expires_at=utcnow())
    db_session.add(other_ws)
    db_session.commit()
    generate = _patch_generate(monkeypatch, [])

    for ws_, payload in ((other_ws, {"criterion_id": crit.id}), (ws, {"criterion_id": "nope"}), (ws, {})):
        with pytest.raises(JobFailed) as err:
            handle_generate_heldout(db_session, _job(db_session, ws_, "generate_heldout", payload))
        assert str(err.value) == "That criterion no longer exists."
    generate.assert_not_called()


def test_generate_heldout_replay_failure_uses_a_fixed_message(db_session, monkeypatch):
    ws, agent, pv, crit, fix = _fix_only(db_session)
    _patch_generate(monkeypatch, [["a1", "a2"]])
    monkeypatch.setattr(handlers, "replay", MagicMock(side_effect=ReplayFailed("boom SECRET")))

    with pytest.raises(JobFailed) as err:
        handle_generate_heldout(db_session, _job(db_session, ws, "generate_heldout", {"criterion_id": crit.id}))

    assert str(err.value) == "The agent didn't respond. Try again."
    assert _heldouts(db_session) == []


def test_generate_heldout_judge_failure_uses_a_fixed_message(db_session, monkeypatch):
    ws, agent, pv, crit, fix = _fix_only(db_session)
    _patch_generate(monkeypatch, [["a1", "a2"]])
    _patch_replay(monkeypatch)
    monkeypatch.setattr(handlers, "judge", MagicMock(side_effect=JudgeFailed("boom SECRET")))

    with pytest.raises(JobFailed) as err:
        handle_generate_heldout(db_session, _job(db_session, ws, "generate_heldout", {"criterion_id": crit.id}))

    assert str(err.value) == "The judge was unavailable. Try again."
    assert _heldouts(db_session) == []


def test_generate_heldout_discards_when_the_criterion_is_edited_during_the_job(db_session, monkeypatch):
    ws, agent, pv, crit, fix = _fix_only(db_session)
    crit_id = crit.id
    _patch_generate(monkeypatch, [["a1", "a2"]])
    _patch_replay(monkeypatch)

    def judge_while_edited(config, spec, transcript):
        _edit_criterion_elsewhere(db_session, crit_id)
        return JudgeResult("fail", "r")

    monkeypatch.setattr(handlers, "judge", judge_while_edited)

    result = handle_generate_heldout(db_session, _job(db_session, ws, "generate_heldout", {"criterion_id": crit_id}))

    assert result == {"created": False, "discarded": True}
    assert _heldouts(db_session) == []
    assert _events(db_session, "test_generated") == []


def test_generate_heldout_discards_when_the_current_version_changes_during_the_job(db_session, monkeypatch):
    ws, agent, pv, crit, fix = _fix_only(db_session)
    agent_id = agent.id
    _patch_generate(monkeypatch, [["a1", "a2"]])
    _patch_replay(monkeypatch)

    def judge_while_prompt_changes(config, spec, transcript):
        other = _other_session(db_session)
        try:
            new = PromptVersion(agent_id=agent_id, text="edited", origin="edit", status="promoted", number=2)
            other.add(new)
            other.flush()
            other.get(Agent, agent_id).current_prompt_version_id = new.id
            other.commit()
        finally:
            other.close()
        return JudgeResult("fail", "r")

    monkeypatch.setattr(handlers, "judge", judge_while_prompt_changes)

    result = handle_generate_heldout(db_session, _job(db_session, ws, "generate_heldout", {"criterion_id": crit.id}))

    assert result == {"created": False, "discarded": True}
    assert _heldouts(db_session) == []


def test_generate_heldout_holds_no_transaction_during_llm_calls(db_session, monkeypatch):
    ws, agent, pv, crit, fix = _fix_only(db_session)
    in_txn = []

    def fake_generate(spec, fix_messages, avoid):
        in_txn.append(db_session.in_transaction())
        return ["a1", "a2"]

    def fake_replay(config, messages):
        in_txn.append(db_session.in_transaction())
        return TRANSCRIPT

    def fake_judge(config, spec, transcript):
        in_txn.append(db_session.in_transaction())
        return JudgeResult("fail", "r")

    monkeypatch.setattr(handlers, "generate_scenario", fake_generate)
    monkeypatch.setattr(handlers, "replay", fake_replay)
    monkeypatch.setattr(handlers, "judge", fake_judge)

    result = handle_generate_heldout(db_session, _job(db_session, ws, "generate_heldout", {"criterion_id": crit.id}))

    assert result["created"] is True
    assert in_txn == [False, False, False]


# ---------- change_report ----------

OUTCOME_TABLE = [
    {
        "test_id": "t-fix",
        "role": "fix",
        "criterion_name": "Stays polite",
        "before": "fail",
        "after": "pass",
        "rationale": "now polite",
        "transcript": [{"role": "user", "content": "TRANSCRIPT-NOT-FOR-WRITER"}],
    }
]


def _candidate(db, agent, pv, text="be very nice", outcome="default", number=2):
    if outcome == "default":
        outcome = {
            "status": "promoted",
            "reason": "All required tests passed.",
            "table": OUTCOME_TABLE,
            "narrative": None,
            "explanation_unavailable": False,
        }
    version = PromptVersion(
        agent_id=agent.id,
        text=text,
        parent_id=pv.id,
        origin="suggested",
        status="promoted",
        number=number,
        change_report={"why": {"criterion_name": "Stays polite"}, "outcome": outcome},
    )
    db.add(version)
    db.commit()
    return version


def _patch_report(monkeypatch, narrative=None, exc=None):
    narrative = narrative or ReportNarrative("It now stays polite.", "Keep monitoring.", ("t-fix",))
    mock = MagicMock(return_value=narrative, side_effect=exc)
    monkeypatch.setattr(handlers, "write_report", mock)
    return mock


def test_change_report_stores_the_narrative(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    version = _candidate(db_session, agent, pv)
    write = _patch_report(monkeypatch)
    job = _job(db_session, ws, "change_report", {"prompt_version_id": version.id})

    result = handle_change_report(db_session, job)

    assert result == {"explained": True}
    db_session.refresh(version)
    outcome = version.change_report["outcome"]
    assert outcome["narrative"] == {
        "explanation": "It now stays polite.",
        "next_step": "Keep monitoring.",
        "cited_test_ids": ["t-fix"],
    }
    assert outcome["explanation_unavailable"] is False
    assert outcome["status"] == "promoted" and outcome["table"] == OUTCOME_TABLE
    assert version.change_report["why"] == {"criterion_name": "Stays polite"}
    diff_text, rows, status, reason = write.call_args.args
    assert status == "promoted" and reason == "All required tests passed."
    assert rows == [
        {
            "test_id": "t-fix",
            "role": "fix",
            "criterion_name": "Stays polite",
            "before": "fail",
            "after": "pass",
            "rationale": "now polite",
        }
    ]
    assert "TRANSCRIPT-NOT-FOR-WRITER" not in repr(write.call_args)
    assert "-be nice" in diff_text and "+be very nice" in diff_text


def test_change_report_caps_the_diff_at_8000_characters(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    version = _candidate(db_session, agent, pv, text="\n".join(f"line {i} " + "y" * 50 for i in range(500)))
    write = _patch_report(monkeypatch)

    handle_change_report(db_session, _job(db_session, ws, "change_report", {"prompt_version_id": version.id}))

    assert len(write.call_args.args[0]) == 8000


def test_change_report_failure_marks_the_explanation_unavailable(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    version = _candidate(db_session, agent, pv)
    _patch_report(monkeypatch, exc=ReportFailed("report call failed"))

    result = handle_change_report(db_session, _job(db_session, ws, "change_report", {"prompt_version_id": version.id}))

    assert result == {"explained": False}
    db_session.refresh(version)
    outcome = version.change_report["outcome"]
    assert outcome["explanation_unavailable"] is True and outcome["narrative"] is None
    assert outcome["status"] == "promoted" and outcome["table"] == OUTCOME_TABLE


def test_change_report_rejects_foreign_missing_and_outcomeless_versions(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    version = _candidate(db_session, agent, pv)
    pending = _candidate(db_session, agent, pv, outcome=None, number=3)
    other_ws = Workspace(token_hash="other", expires_at=utcnow())
    db_session.add(other_ws)
    db_session.commit()
    write = _patch_report(monkeypatch)

    for ws_, payload in (
        (other_ws, {"prompt_version_id": version.id}),
        (ws, {"prompt_version_id": "nope"}),
        (ws, {}),
        (ws, {"prompt_version_id": pending.id}),
        (ws, {"prompt_version_id": pv.id}),  # a manual version has no change report
    ):
        with pytest.raises(JobFailed) as err:
            handle_change_report(db_session, _job(db_session, ws_, "change_report", payload))
        assert str(err.value) == "That prompt version no longer exists."
    write.assert_not_called()


def test_change_report_version_deleted_during_the_call_fails_the_job(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    version = _candidate(db_session, agent, pv)
    version_id = version.id

    def write_while_deleted(diff_text, rows, status, reason):
        other = _other_session(db_session)
        try:
            other.execute(PromptVersion.__table__.delete().where(PromptVersion.id == version_id))
            other.commit()
        finally:
            other.close()
        return ReportNarrative("e", "n", ())

    monkeypatch.setattr(handlers, "write_report", write_while_deleted)

    with pytest.raises(JobFailed) as err:
        handle_change_report(db_session, _job(db_session, ws, "change_report", {"prompt_version_id": version_id}))

    assert str(err.value) == "That prompt version no longer exists."


def test_change_report_holds_no_transaction_during_the_llm_call(db_session, monkeypatch):
    ws, agent, pv = _agent(db_session)
    version = _candidate(db_session, agent, pv)
    in_txn = []

    def fake_write(diff_text, rows, status, reason):
        in_txn.append(db_session.in_transaction())
        return ReportNarrative("e", "n", ())

    monkeypatch.setattr(handlers, "write_report", fake_write)

    handle_change_report(db_session, _job(db_session, ws, "change_report", {"prompt_version_id": version.id}))

    assert in_txn == [False]
