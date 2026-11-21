"""Job handlers: `build_tests` and `replay`.

A handler does its LLM work first and writes its rows in one commit at the end, so a failure
leaves no partial rows. Handlers raise `JobFailed` with a fixed, user-safe message; provider
errors (which may carry request details) are logged, never copied into the message.
"""

import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session

from backend.core.config import AgentConfig, CriterionSpec
from backend.core.judge import JudgeFailed, JudgeResult, judge, sanity_check
from backend.core.replay import ReplayFailed, replay, user_messages
from backend.core.testsuite import pick_test_runs
from backend.db.events import try_log_event
from backend.db.models import Criterion, EvalResult, EvalTest, Job, PromptVersion, Run

logger = logging.getLogger(__name__)

RECENT_RUNS_TO_JUDGE = 8
MAX_FAILING_RUNS_TO_JUDGE = 2
MAX_RATIONALE_CHARS = 2000

_JUDGE_UNAVAILABLE = "The judge was unavailable. Try again."
_AGENT_UNAVAILABLE = "The agent didn't respond. Try again."
_CRITERION_GONE = "That criterion no longer exists."
_TEST_GONE = "That test no longer exists."
_VERSION_GONE = "That prompt version no longer exists."

_ALREADY_BUILT = {
    "judged": 0,
    "tests_created": 0,
    "already_built": True,
    "already_handled": False,
    "weaker_verification": False,
}


class JobFailed(Exception):
    """A job cannot be completed; the message is safe to show the user."""


def _spec(criterion: Criterion) -> CriterionSpec:
    return CriterionSpec(
        name=criterion.name,
        definition=criterion.definition,
        good_example=criterion.good_example,
        bad_example=criterion.bad_example,
    )


def _payload_id(job: Job, key: str, missing_message: str) -> str:
    value = job.payload.get(key) if isinstance(job.payload, dict) else None
    if not isinstance(value, str) or not value:
        raise JobFailed(missing_message)
    return value


def _still_exists(db: Session, row: object, message: str) -> None:
    """Re-read `row` after slow LLM calls; JobFailed if it was deleted meanwhile."""
    try:
        db.refresh(row)
    except InvalidRequestError as exc:
        raise JobFailed(message) from exc


def _has_tests(db: Session, criterion: Criterion) -> bool:
    return db.scalar(select(EvalTest.id).where(EvalTest.criterion_id == criterion.id).limit(1)) is not None


def _owned_criterion(db: Session, job: Job) -> Criterion:
    criterion_id = _payload_id(job, "criterion_id", _CRITERION_GONE)
    criterion = db.get(Criterion, criterion_id)
    if criterion is None or criterion.agent.workspace_id != job.workspace_id:
        raise JobFailed(_CRITERION_GONE)
    return criterion


def handle_build_tests(db: Session, job: Job) -> dict:
    criterion = _owned_criterion(db, job)
    agent = criterion.agent
    if _has_tests(db, criterion):
        return dict(_ALREADY_BUILT)

    spec = _spec(criterion)
    # The judge only reads the agent's name and role label; it never sees the system prompt.
    config = AgentConfig(agent.name, "", agent.user_role_label)
    try:
        check = sanity_check(config, spec)

        recent = list(
            db.scalars(
                select(Run).where(Run.agent_id == agent.id).order_by(Run.position.desc()).limit(RECENT_RUNS_TO_JUDGE)
            )
        )
        judged: list[tuple[Run, JudgeResult]] = []
        failures = 0
        for run in reversed(recent):  # oldest -> newest
            if not user_messages(run.messages):
                continue  # nothing to replay, so it can never become a test
            result = judge(config, spec, run.messages)
            judged.append((run, result))
            if result.verdict == "fail":
                failures += 1
                if failures >= MAX_FAILING_RUNS_TO_JUDGE:
                    break
    except JudgeFailed as exc:
        logger.warning("build_tests judge failure for job %s: %s", job.id, exc)
        raise JobFailed(_JUDGE_UNAVAILABLE) from exc

    _still_exists(db, criterion, _CRITERION_GONE)
    if _has_tests(db, criterion):  # another worker finished first
        return dict(_ALREADY_BUILT)

    criterion.judge_check = {
        "passed": check.passed,
        "good_verdict": check.good_verdict,
        "bad_verdict": check.bad_verdict,
    }
    by_run = {run.id: (run, result) for run, result in judged}
    roles: list[str] = []
    for run_id, role in pick_test_runs([(run.id, result.verdict) for run, result in judged]):
        run, result = by_run[run_id]
        test = EvalTest(
            criterion_id=criterion.id,
            source_run_id=run.id,
            role=role,
            scripted_user_messages=user_messages(run.messages),
        )
        db.add(test)
        db.flush()
        db.add(
            EvalResult(
                test_id=test.id,
                prompt_version_id=run.prompt_version_id,
                kind="baseline",
                verdict="fail",
                rationale=result.rationale[:MAX_RATIONALE_CHARS],
                transcript=run.messages,
                stale=False,
            )
        )
        roles.append(role)
    db.commit()

    workspace_id = job.workspace_id
    summary = {
        "judged": len(judged),
        "tests_created": len(roles),
        "already_handled": not roles,
        "weaker_verification": len(roles) == 1,
    }
    for role in roles:
        try_log_event(db, workspace_id, "test_created", {"role": role})
    return summary


def handle_replay(db: Session, job: Job) -> dict:
    test_id = _payload_id(job, "test_id", _TEST_GONE)
    version_id = _payload_id(job, "prompt_version_id", _VERSION_GONE)

    test = db.get(EvalTest, test_id)
    if test is None or test.criterion.agent.workspace_id != job.workspace_id:
        raise JobFailed(_TEST_GONE)
    criterion = test.criterion
    agent = criterion.agent
    version = db.get(PromptVersion, version_id)
    if version is None or version.agent_id != agent.id:
        raise JobFailed(_VERSION_GONE)
    if not test.scripted_user_messages:
        raise JobFailed("That test has no messages to replay.")

    config = AgentConfig(agent.name, version.text, agent.user_role_label)
    spec = _spec(criterion)
    messages = list(test.scripted_user_messages)
    role = test.role
    workspace_id = job.workspace_id
    try:
        transcript = replay(config, messages)
        verdict = judge(config, spec, transcript)
    except ReplayFailed as exc:
        logger.warning("replay failure for job %s: %s", job.id, exc)
        raise JobFailed(_AGENT_UNAVAILABLE) from exc
    except JudgeFailed as exc:
        logger.warning("replay judge failure for job %s: %s", job.id, exc)
        raise JobFailed(_JUDGE_UNAVAILABLE) from exc

    _still_exists(db, test, _TEST_GONE)
    result = EvalResult(
        test_id=test.id,
        prompt_version_id=version.id,
        kind="replay",
        verdict=verdict.verdict,
        rationale=verdict.rationale[:MAX_RATIONALE_CHARS],
        transcript=transcript,
        stale=False,
    )
    db.add(result)
    db.commit()

    summary = {"result_id": result.id, "verdict": verdict.verdict}
    if verdict.verdict == "pass":
        try_log_event(db, workspace_id, "replay_passed", {"role": role})
    elif verdict.verdict == "fail":
        try_log_event(db, workspace_id, "replay_failed", {"role": role})
    return summary


HANDLERS: dict[str, Callable[[Session, Job], dict]] = {
    "build_tests": handle_build_tests,
    "replay": handle_replay,
}
