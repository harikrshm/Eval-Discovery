"""Run an exported suite against a prompt and turn the result into a CI exit code.

Imports `backend.core.replay`, `backend.core.judge` and `evalgate.suite` only: no web stack.

Safety rule: the gate must never report success when it could not actually run. Any errored test,
an empty run, or a run with nothing applicable yields exit code 3, never 0. Provider exception text
is never stored in an `Outcome`; each failure maps to a fixed, safe message.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from backend.core.config import AgentConfig
from backend.core.judge import JudgeFailed, judge
from backend.core.replay import ReplayFailed, replay
from evalgate.suite import SuiteTest

__all__ = ["GateResult", "Outcome", "exit_code", "run_suite"]

EXIT_PASS = 0
EXIT_BELOW_THRESHOLD = 1
EXIT_COULD_NOT_RUN = 3

ERR_AGENT = "The agent didn't respond."
ERR_JUDGE = "The judge was unavailable."
ERR_OTHER = "The test could not be run."

_GRADED = ("pass", "fail", "not_applicable")


@dataclass(frozen=True)
class Outcome:
    test_id: str
    criterion_name: str
    role: str
    verdict: str  # "pass" | "fail" | "not_applicable" | "error"
    runs: tuple[str, ...]  # one verdict per run, "error" for a run that could not complete
    rationale: str
    error: str | None


@dataclass(frozen=True)
class GateResult:
    outcomes: tuple[Outcome, ...]
    skipped: int
    passes: int
    fails: int
    not_applicable: int
    errors: int
    pass_rate: float | None


def _run_once(test: SuiteTest, agent_name: str, user_role_label: str, prompt_text: str) -> tuple[str, str, str | None]:
    """One replay plus judge. Returns (verdict, rationale, error); verdict is "error" on any failure."""
    try:
        transcript = replay(AgentConfig(agent_name, prompt_text, user_role_label), list(test.messages))
    except ReplayFailed:
        return "error", "", ERR_AGENT
    except Exception:
        return "error", "", ERR_OTHER
    try:
        # The judge never sees the agent prompt.
        result = judge(AgentConfig(agent_name, "", user_role_label), test.criterion, transcript)
    except JudgeFailed:
        return "error", "", ERR_JUDGE
    except Exception:
        return "error", "", ERR_OTHER
    if result.verdict not in _GRADED:
        return "error", "", ERR_OTHER
    return result.verdict, result.rationale, None


def _run_test(test: SuiteTest, repeat: int, agent_name: str, user_role_label: str, prompt_text: str) -> Outcome:
    runs = [_run_once(test, agent_name, user_role_label, prompt_text) for _ in range(repeat)]
    verdicts = tuple(v for v, _, _ in runs)
    completed = [v for v in verdicts if v != "error"]

    if not completed:
        final = "error"
    elif verdicts.count("pass") * 2 > repeat:
        final = "pass"
    elif all(v == "not_applicable" for v in completed):
        final = "not_applicable"
    else:
        final = "fail"

    if final == "error":
        rationale, error = "", runs[0][2]
    else:
        rationale = next((r for v, r, _ in runs if v == final), "")
        error = None
    return Outcome(test.id, test.criterion_name, test.role, final, verdicts, rationale, error)


def run_suite(
    tests: Sequence[SuiteTest],
    *,
    agent_name: str,
    user_role_label: str,
    prompt_text: str,
    repeat: int = 1,
    pause: float = 0.0,
    max_tests: int | None = None,
    sleep: Callable[[float], object] = time.sleep,
    skipped: int = 0,
) -> GateResult:
    """Replay and judge each test `repeat` times; a test passes on a strict majority of passing runs."""
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    selected = list(tests)
    if max_tests is not None:
        selected = selected[: max(max_tests, 0)]

    outcomes: list[Outcome] = []
    for index, test in enumerate(selected):
        if index > 0 and pause > 0:
            sleep(pause)
        outcomes.append(_run_test(test, repeat, agent_name, user_role_label, prompt_text))

    def count(verdict: str) -> int:
        return sum(1 for o in outcomes if o.verdict == verdict)

    passes, fails = count("pass"), count("fail")
    return GateResult(
        outcomes=tuple(outcomes),
        skipped=skipped,
        passes=passes,
        fails=fails,
        not_applicable=count("not_applicable"),
        errors=count("error"),
        pass_rate=passes / (passes + fails) if passes + fails else None,
    )


def exit_code(result: GateResult, threshold: float) -> int:
    """3 when the gate could not run, else 0 if the pass rate meets the threshold, else 1."""
    if result.errors > 0 or not result.outcomes or result.pass_rate is None:
        return EXIT_COULD_NOT_RUN
    return EXIT_PASS if result.pass_rate >= threshold - 1e-9 else EXIT_BELOW_THRESHOLD
