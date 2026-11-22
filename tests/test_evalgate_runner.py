"""The CI gate runner and its exit-code policy. No network: replay and judge are patched."""

import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from backend.core.config import AgentConfig, CriterionSpec
from backend.core.judge import JudgeFailed, JudgeResult
from backend.core.replay import ReplayFailed
from evalgate.runner import GateResult, Outcome, exit_code, run_suite
from evalgate.suite import SuiteTest

SECRET = "sk-provider-secret-text"
PROMPT = "You are a careful agent. SECRET-PROMPT-BODY"
TRANSCRIPT = [{"role": "user", "content": "x"}]


def make_test(i: int = 0) -> SuiteTest:
    criterion = CriterionSpec(name=f"crit{i}", definition="d", good_example="g", bad_example="b")
    return SuiteTest(
        id=f"t{i}",
        role="regression",
        origin="human",
        criterion_name=criterion.name,
        criterion=criterion,
        messages=(f"hello {i}", "again"),
        current_verdict="pass",
    )


def suite_tests(n: int) -> list[SuiteTest]:
    return [make_test(i) for i in range(n)]


def verdict(v: str) -> JudgeResult:
    return JudgeResult(v, f"because {v}")


def run(test_list, judge_results, *, repeat=1, replay_side_effect=None, **kwargs):
    """Run the suite with replay/judge patched; judge_results is a list of results or exceptions."""
    with patch("evalgate.runner.replay", side_effect=replay_side_effect, return_value=TRANSCRIPT) as rp, patch(
        "evalgate.runner.judge", side_effect=judge_results
    ) as jd:
        result = run_suite(
            test_list, agent_name="Agent", user_role_label="Caller", prompt_text=PROMPT, repeat=repeat, **kwargs
        )
    return result, rp, jd


def gate(*, outcomes=1, passes=0, fails=0, not_applicable=0, errors=0, pass_rate=None) -> GateResult:
    outs = tuple(Outcome(f"t{i}", "c", "regression", "pass", ("pass",), "", None) for i in range(outcomes))
    return GateResult(outs, 0, passes, fails, not_applicable, errors, pass_rate)


def test_replay_gets_prompt_and_judge_never_does():
    t = make_test()
    _, rp, jd = run([t], [verdict("pass")])
    (replay_config, replay_messages), _ = rp.call_args
    assert replay_config == AgentConfig("Agent", PROMPT, "Caller")
    assert list(replay_messages) == list(t.messages)
    (judge_config, criterion, transcript), _ = jd.call_args
    assert judge_config == AgentConfig("Agent", "", "Caller")
    assert judge_config.system_prompt == ""
    assert criterion is t.criterion
    assert transcript == TRANSCRIPT
    assert "SECRET-PROMPT-BODY" not in repr(jd.call_args)


def test_counts_and_pass_rate():
    result, _, _ = run(suite_tests(4), [verdict("pass"), verdict("fail"), verdict("not_applicable"), verdict("pass")])
    assert [o.verdict for o in result.outcomes] == ["pass", "fail", "not_applicable", "pass"]
    assert (result.passes, result.fails, result.not_applicable, result.errors) == (2, 1, 1, 0)
    assert result.pass_rate == pytest.approx(2 / 3)
    first = result.outcomes[0]
    assert (first.test_id, first.criterion_name, first.role) == ("t0", "crit0", "regression")
    assert first.runs == ("pass",)
    assert first.rationale == "because pass"
    assert first.error is None


def test_pass_rate_none_when_nothing_applicable():
    result, _, _ = run(suite_tests(2), [verdict("not_applicable")] * 2)
    assert result.pass_rate is None
    assert result.not_applicable == 2


def test_skipped_is_carried():
    result, _, _ = run(suite_tests(1), [verdict("pass")], skipped=5)
    assert result.skipped == 5


@pytest.mark.parametrize(
    "runs, expected",
    [
        (["pass", "pass", "fail"], "pass"),
        (["pass", "fail", "pass"], "pass"),
        (["pass", "fail", "fail"], "fail"),
        (["fail", "fail", "fail"], "fail"),
        (["pass", "pass", "pass"], "pass"),
        (["not_applicable", "not_applicable", "not_applicable"], "not_applicable"),
        (["not_applicable", "not_applicable", "pass"], "fail"),  # 1 of 3 passed: not a majority
        (["not_applicable", "fail", "pass"], "fail"),
        (["pass", "pass", "not_applicable"], "pass"),
        (["pass", JudgeFailed(SECRET), "pass"], "pass"),  # an errored run is tolerated
        (["pass", JudgeFailed(SECRET), "fail"], "fail"),  # 1 pass of 3 is not > K/2
        (["fail", JudgeFailed(SECRET), "fail"], "fail"),
        (["not_applicable", JudgeFailed(SECRET), "not_applicable"], "not_applicable"),
        ([JudgeFailed(SECRET)] * 3, "error"),
        ([JudgeFailed(SECRET), JudgeFailed(SECRET), "fail"], "fail"),
    ],
)
def test_majority_vote_k3(runs, expected):
    results = [verdict(r) if isinstance(r, str) else r for r in runs]
    result, _, jd = run(suite_tests(1), results, repeat=3)
    assert jd.call_count == 3
    assert result.outcomes[0].verdict == expected
    assert len(result.outcomes[0].runs) == 3


def test_runs_record_each_run_including_errored():
    result, _, _ = run(suite_tests(1), [verdict("pass"), JudgeFailed(SECRET), verdict("fail")], repeat=3)
    assert result.outcomes[0].runs == ("pass", "error", "fail")


def test_even_k_tie_is_not_a_pass():
    result, _, _ = run(suite_tests(1), [verdict("pass"), verdict("fail")], repeat=2)
    assert result.outcomes[0].verdict == "fail"


def test_replay_failure_is_an_errored_run_with_fixed_message():
    result, _, jd = run(suite_tests(1), [], replay_side_effect=ReplayFailed(f"replay chat call failed: {SECRET}"))
    o = result.outcomes[0]
    assert o.verdict == "error"
    assert o.error == "The agent didn't respond."
    assert jd.call_count == 0
    assert result.errors == 1
    assert result.pass_rate is None


def test_judge_failure_is_an_errored_run_with_fixed_message():
    result, _, _ = run(suite_tests(1), [JudgeFailed(f"judge call failed: {SECRET}")])
    o = result.outcomes[0]
    assert o.verdict == "error"
    assert o.error == "The judge was unavailable."


def test_unexpected_exception_becomes_an_error_outcome():
    result, _, _ = run(suite_tests(2), [RuntimeError(SECRET), verdict("pass")])
    assert result.outcomes[0].verdict == "error"
    assert result.outcomes[0].error == "The test could not be run."
    assert result.outcomes[1].verdict == "pass"


@pytest.mark.parametrize(
    "judge_exc, replay_exc",
    [
        (JudgeFailed(SECRET), None),
        (RuntimeError(SECRET), None),
        (ValueError(SECRET), None),
        (None, ReplayFailed(SECRET)),
        (None, KeyError(SECRET)),
    ],
)
def test_provider_text_never_appears_in_any_outcome_field(judge_exc, replay_exc):
    judge_results = [judge_exc] * 3 if judge_exc else []
    for repeat in (1, 3):
        result, _, _ = run(suite_tests(1), judge_results, replay_side_effect=replay_exc, repeat=repeat)
        assert result.outcomes[0].verdict == "error"
        assert SECRET not in repr(result)
    mixed, _, _ = run(suite_tests(1), [judge_exc, verdict("pass"), judge_exc] if judge_exc else [], replay_side_effect=replay_exc, repeat=3)
    assert SECRET not in repr(mixed)


def test_rationale_comes_from_a_run_with_the_winning_verdict():
    result, _, _ = run(suite_tests(1), [verdict("fail"), verdict("pass"), verdict("fail")], repeat=3)
    assert result.outcomes[0].verdict == "fail"
    assert result.outcomes[0].rationale == "because fail"


def test_pause_sleeps_between_tests_only():
    sleep = MagicMock()
    run(suite_tests(3), [verdict("pass")] * 3, pause=1.5, sleep=sleep)
    assert [c.args for c in sleep.call_args_list] == [(1.5,), (1.5,)]


def test_no_sleep_when_pause_is_zero_or_single_test():
    sleep = MagicMock()
    run(suite_tests(3), [verdict("pass")] * 3, pause=0.0, sleep=sleep)
    run(suite_tests(1), [verdict("pass")], pause=2.0, sleep=sleep)
    sleep.assert_not_called()


def test_max_tests_truncates():
    result, rp, _ = run(suite_tests(5), [verdict("pass")] * 5, max_tests=2)
    assert [o.test_id for o in result.outcomes] == ["t0", "t1"]
    assert rp.call_count == 2


def test_max_tests_larger_than_list_is_harmless():
    result, _, _ = run(suite_tests(2), [verdict("pass")] * 2, max_tests=10)
    assert len(result.outcomes) == 2


def test_empty_suite_runs_nothing():
    result, rp, _ = run([], [])
    assert result.outcomes == ()
    assert result.pass_rate is None
    assert rp.call_count == 0


@pytest.mark.parametrize(
    "result, threshold, expected",
    [
        (gate(outcomes=3, passes=3, pass_rate=1.0), 1.0, 0),
        (gate(outcomes=3, passes=2, fails=1, pass_rate=2 / 3), 1.0, 1),
        (gate(outcomes=2, passes=1, fails=1, pass_rate=0.5), 0.5, 0),
        (gate(outcomes=2, passes=1, fails=1, pass_rate=0.5), 0.51, 1),
        (gate(outcomes=3, passes=2, fails=1, pass_rate=2 / 3), 0.6666666667, 0),
        (gate(outcomes=3, passes=2, fails=1, pass_rate=2 / 3), 0.67, 1),
        (gate(outcomes=3, passes=2, errors=1, pass_rate=1.0), 1.0, 3),
        (gate(outcomes=3, passes=0, errors=3, pass_rate=None), 0.0, 3),
        (gate(outcomes=0, pass_rate=None), 0.0, 3),
        (gate(outcomes=2, not_applicable=2, pass_rate=None), 0.0, 3),
        (gate(outcomes=2, fails=2, pass_rate=0.0), 0.0, 0),
        (gate(outcomes=2, fails=2, pass_rate=0.0), 0.1, 1),
    ],
)
def test_exit_code_table(result, threshold, expected):
    assert exit_code(result, threshold) == expected


def test_exit_code_is_3_on_error_even_if_pass_rate_would_pass():
    assert exit_code(gate(outcomes=2, passes=1, errors=1, pass_rate=1.0), 0.0) == 3


def test_end_to_end_exit_codes():
    ok, _, _ = run(suite_tests(2), [verdict("pass")] * 2)
    assert exit_code(ok, 1.0) == 0
    bad, _, _ = run(suite_tests(2), [verdict("pass"), verdict("fail")])
    assert exit_code(bad, 1.0) == 1
    assert exit_code(bad, 0.5) == 0
    err, _, _ = run(suite_tests(2), [verdict("pass"), JudgeFailed(SECRET)])
    assert exit_code(err, 0.0) == 3
    empty, _, _ = run([], [])
    assert exit_code(empty, 0.0) == 3
    na, _, _ = run(suite_tests(2), [verdict("not_applicable")] * 2)
    assert exit_code(na, 0.0) == 3


def test_runner_does_not_import_the_web_stack():
    code = (
        "import sys, evalgate.runner; "
        "sys.exit(1 if any(m in sys.modules for m in ('fastapi', 'sqlalchemy')) else 0)"
    )
    env = {**os.environ, "GROQ_SKIP_DOTENV": "1"}
    assert subprocess.run([sys.executable, "-c", code], env=env).returncode == 0
