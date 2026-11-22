"""Job handlers: `build_tests`, `replay`, `suggest_fix`, `generate_heldout` and `change_report`.

A handler does its LLM work first and writes its rows in one commit at the end, so a failure
leaves no partial rows. Handlers raise `JobFailed` with a fixed, user-safe message; provider
errors (which may carry request details) are logged, never copied into the message.
"""

import difflib
import logging
from collections.abc import Callable

from sqlalchemy import delete, select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session

from backend.core.config import AgentConfig, CriterionSpec
from backend.core.heldout import MAX_SCENARIO_MESSAGES, GenerateFailed, generate_scenario
from backend.core.judge import JudgeFailed, JudgeResult, judge, sanity_check
from backend.core.promotion import quote_turn
from backend.core.replay import ReplayFailed, replay, user_messages
from backend.core.report import ReportFailed, write_report
from backend.core.suggest import SuggestFailed, SuggestTooLong, suggest_fix
from backend.core.testsuite import pick_test_runs
from backend.db.candidates import (
    CandidateOpen,
    ParentChanged,
    VersionLimitReached,
    create_proposed_version,
    failing_result_for,
    open_candidate,
)
from backend.db.events import try_log_event
from backend.db.models import Agent, Criterion, EvalResult, EvalTest, Job, PromptVersion, Run

logger = logging.getLogger(__name__)

RECENT_RUNS_TO_JUDGE = 8
MAX_FAILING_RUNS_TO_JUDGE = 2
MAX_RATIONALE_CHARS = 2000
MAX_WHY_FIELD_CHARS = 600  # the suggester's summary fields are untrusted model text
MAX_DIFF_CHARS = 8000
MAX_HELDOUT_ATTEMPTS = 3

_JUDGE_UNAVAILABLE = "The judge was unavailable. Try again."
_AGENT_UNAVAILABLE = "The agent didn't respond. Try again."
_CRITERION_GONE = "That criterion no longer exists."
_TEST_GONE = "That test no longer exists."
_VERSION_GONE = "That prompt version no longer exists."
_NOT_FIX_TEST = "Suggest a fix from the fix test."
_NOT_FAILING = "This test isn't failing on the current prompt."
_CANDIDATE_OPEN = "A fix is already proposed or being verified."
_VERSION_LIMIT = "Prompt version limit reached for this agent."
_SUGGEST_FAILED = "Couldn't write a fix. Try again."
_SUGGEST_TOO_LONG = "The suggested prompt was too long. Try again."
_CRITERION_CHANGED = "That criterion changed while the fix was written. Try again."
_HELDOUT_PRECONDITION = "This criterion needs a fix test and no held-out test."

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


def _revision_changed(db: Session, criterion: Criterion, revision: int) -> bool:
    """True if the criterion was edited since `revision` was read (fresh query: sessions don't expire on commit)."""
    current = db.scalar(select(Criterion.revision).where(Criterion.id == criterion.id))
    return current != revision


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
    revision = criterion.revision
    # The judge only reads the agent's name and role label; it never sees the system prompt.
    config = AgentConfig(agent.name, "", agent.user_role_label)
    recent = list(
        db.scalars(
            select(Run).where(Run.agent_id == agent.id).order_by(Run.position.desc()).limit(RECENT_RUNS_TO_JUDGE)
        )
    )
    # End the read transaction before the slow LLM calls (expire_on_commit=False keeps rows usable).
    db.commit()
    try:
        check = sanity_check(config, spec)

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
    if _revision_changed(db, criterion, revision):  # edited while judging: the verdicts are obsolete
        return {"discarded": True, "tests_created": 0}
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
    # The judge never sees the agent's prompt: give it a config without one.
    judge_config = AgentConfig(agent.name, "", agent.user_role_label)
    spec = _spec(criterion)
    revision = criterion.revision
    needs_check = criterion.judge_check is None
    messages = list(test.scripted_user_messages)
    role = test.role
    workspace_id = job.workspace_id
    # End the read transaction before the slow LLM calls (expire_on_commit=False keeps rows usable).
    db.commit()
    try:
        transcript = replay(config, messages)
        verdict = judge(judge_config, spec, transcript)
        check = sanity_check(judge_config, spec) if needs_check else None
    except ReplayFailed as exc:
        logger.warning("replay failure for job %s: %s", job.id, exc)
        raise JobFailed(_AGENT_UNAVAILABLE) from exc
    except JudgeFailed as exc:
        logger.warning("replay judge failure for job %s: %s", job.id, exc)
        raise JobFailed(_JUDGE_UNAVAILABLE) from exc

    _still_exists(db, test, _TEST_GONE)
    if _revision_changed(db, criterion, revision):  # edited while running: the verdict is obsolete
        return {"discarded": True}
    if check is not None:
        criterion.judge_check = {
            "passed": check.passed,
            "good_verdict": check.good_verdict,
            "bad_verdict": check.bad_verdict,
        }
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
    db.flush()
    # Latest wins: drop earlier replay results (stale or not) for this test and version; baselines stay.
    db.execute(
        delete(EvalResult)
        .where(
            EvalResult.test_id == test.id,
            EvalResult.prompt_version_id == version.id,
            EvalResult.kind == "replay",
            EvalResult.id != result.id,
        )
        .execution_options(synchronize_session="fetch")
    )
    db.commit()

    summary = {"result_id": result.id, "verdict": verdict.verdict}
    if verdict.verdict == "pass":
        try_log_event(db, workspace_id, "replay_passed", {"role": role})
    elif verdict.verdict == "fail":
        try_log_event(db, workspace_id, "replay_failed", {"role": role})
    return summary


def _current_version_id(db: Session, agent: Agent) -> str | None:
    """The agent's current version id from a fresh query (sessions don't expire on commit)."""
    return db.scalar(select(Agent.current_prompt_version_id).where(Agent.id == agent.id))


def handle_suggest_fix(db: Session, job: Job) -> dict:
    test_id = _payload_id(job, "test_id", _TEST_GONE)
    test = db.get(EvalTest, test_id)
    if test is None or test.criterion.agent.workspace_id != job.workspace_id:
        raise JobFailed(_TEST_GONE)
    if test.role != "fix":
        raise JobFailed(_NOT_FIX_TEST)
    criterion = test.criterion
    agent = criterion.agent
    current_id = agent.current_prompt_version_id
    current = db.get(PromptVersion, current_id) if current_id else None
    failing = failing_result_for(db, test, current_id) if current is not None else None
    # The transcript comes only from the failing result: the suggester never sees anything else.
    if failing is None or not failing.transcript:
        raise JobFailed(_NOT_FAILING)
    if open_candidate(db, agent) is not None:
        raise JobFailed(_CANDIDATE_OPEN)

    config = AgentConfig(agent.name, current.text, agent.user_role_label)
    spec = _spec(criterion)
    revision = criterion.revision
    criterion_name = criterion.name
    rationale = failing.rationale
    transcript = [dict(message) for message in failing.transcript]
    workspace_id = job.workspace_id
    # End the read transaction before the slow LLM call (expire_on_commit=False keeps rows usable).
    db.commit()
    try:
        fix = suggest_fix(config, spec, rationale, transcript)
    except SuggestTooLong as exc:
        logger.warning("suggest_fix too long for job %s: %s", job.id, exc)
        raise JobFailed(_SUGGEST_TOO_LONG) from exc
    except SuggestFailed as exc:
        # The exception message can carry provider text, so the job message is always a fixed one.
        logger.warning("suggest_fix failure for job %s: %s", job.id, exc)
        raise JobFailed(_SUGGEST_FAILED) from exc

    _still_exists(db, test, _TEST_GONE)
    if _revision_changed(db, criterion, revision):  # the failing result is stale once the criterion changed
        raise JobFailed(_CRITERION_CHANGED)
    if _current_version_id(db, agent) != current_id:  # the prompt moved on: the fix targets an old one
        raise JobFailed(_NOT_FAILING)
    if open_candidate(db, agent) is not None:
        raise JobFailed(_CANDIDATE_OPEN)

    why = {
        "criterion_name": criterion_name,
        "failing_test_id": test.id,
        "failing_turn_index": fix.failing_turn_index,
        "failing_quote": quote_turn(transcript, fix.failing_turn_index) or "",  # by code, never the model
        "change_summary": fix.change_summary[:MAX_WHY_FIELD_CHARS],
        "touched_section": fix.touched_section[:MAX_WHY_FIELD_CHARS],
        "expected_effect": fix.expected_effect[:MAX_WHY_FIELD_CHARS],
        "risk": fix.risk[:MAX_WHY_FIELD_CHARS],
    }
    try:
        version = create_proposed_version(db, agent, fix.prompt_text, test.id, why, expected_parent_id=current_id)
    except CandidateOpen as exc:
        db.rollback()
        raise JobFailed(_CANDIDATE_OPEN) from exc
    except VersionLimitReached as exc:
        db.rollback()
        raise JobFailed(_VERSION_LIMIT) from exc
    except ParentChanged as exc:  # the prompt moved on after the unlocked check above
        db.rollback()
        raise JobFailed(_NOT_FAILING) from exc
    db.commit()

    summary = {"version_id": version.id, "number": version.number}
    try_log_event(db, workspace_id, "fix_suggested", {"number": version.number})
    return summary


def _heldout_preconditions_hold(db: Session, criterion: Criterion) -> bool:
    roles = set(db.scalars(select(EvalTest.role).where(EvalTest.criterion_id == criterion.id)))
    return "fix" in roles and "heldout" not in roles


def handle_generate_heldout(db: Session, job: Job) -> dict:
    criterion = _owned_criterion(db, job)
    agent = criterion.agent
    if not _heldout_preconditions_hold(db, criterion):
        raise JobFailed(_HELDOUT_PRECONDITION)
    fix_test = db.scalars(
        select(EvalTest).where(EvalTest.criterion_id == criterion.id, EvalTest.role == "fix")
    ).one()
    current_id = agent.current_prompt_version_id
    current = db.get(PromptVersion, current_id) if current_id else None
    if current is None:
        raise JobFailed(_VERSION_GONE)

    config = AgentConfig(agent.name, current.text, agent.user_role_label)
    # The judge never sees the agent's prompt: give it a config without one.
    judge_config = AgentConfig(agent.name, "", agent.user_role_label)
    spec = _spec(criterion)
    revision = criterion.revision
    fix_messages = list(fix_test.scripted_user_messages)
    workspace_id = job.workspace_id
    # End the read transaction before the slow LLM calls (expire_on_commit=False keeps rows usable).
    db.commit()

    avoid: list[list[str]] = []
    kept: tuple[list[str], list[dict], JudgeResult] | None = None
    try:
        for _ in range(MAX_HELDOUT_ATTEMPTS):
            try:
                scenario = generate_scenario(spec, fix_messages, [list(a) for a in avoid])
            except GenerateFailed as exc:  # a bad generation is a spent attempt, not a failed job
                logger.warning("generate_heldout generation failure for job %s: %s", job.id, exc)
                continue
            scenario = scenario[:MAX_SCENARIO_MESSAGES]
            transcript = replay(config, scenario)
            verdict = judge(judge_config, spec, transcript)
            if verdict.verdict == "fail":
                kept = (scenario, transcript, verdict)
                break
            avoid.append(scenario)
    except ReplayFailed as exc:
        logger.warning("generate_heldout replay failure for job %s: %s", job.id, exc)
        raise JobFailed(_AGENT_UNAVAILABLE) from exc
    except JudgeFailed as exc:
        logger.warning("generate_heldout judge failure for job %s: %s", job.id, exc)
        raise JobFailed(_JUDGE_UNAVAILABLE) from exc

    _still_exists(db, criterion, _CRITERION_GONE)
    if _revision_changed(db, criterion, revision) or _current_version_id(db, agent) != current_id:
        return {"created": False, "discarded": True}  # the verdicts belong to an older criterion or prompt
    if kept is None:
        return {"created": False, "reason": "agent_handles_it"}
    if not _heldout_preconditions_hold(db, criterion):  # another worker finished first, or the fix test is gone
        raise JobFailed(_HELDOUT_PRECONDITION)

    scenario, transcript, verdict = kept
    test = EvalTest(
        criterion_id=criterion.id,
        source_run_id=None,
        role="heldout",
        origin="generated",
        scripted_user_messages=scenario,
    )
    db.add(test)
    db.flush()
    db.add(
        EvalResult(
            test_id=test.id,
            prompt_version_id=current_id,
            kind="replay",
            verdict="fail",
            rationale=verdict.rationale[:MAX_RATIONALE_CHARS],
            transcript=transcript,
            stale=False,
        )
    )
    db.commit()

    summary = {"created": True, "test_id": test.id}
    try_log_event(db, workspace_id, "test_generated")
    try_log_event(db, workspace_id, "test_created", {"role": "heldout"})
    return summary


_REPORT_ROW_KEYS = ("test_id", "role", "criterion_name", "before", "after", "rationale")


def _outcome_of(version: PromptVersion) -> dict:
    report = version.change_report
    outcome = report.get("outcome") if isinstance(report, dict) else None
    if not isinstance(outcome, dict):
        raise JobFailed(_VERSION_GONE)
    return outcome


def _with_outcome(version: PromptVersion, changes: dict) -> dict:
    """A NEW change_report dict with `changes` merged into the outcome (never mutate in place)."""
    report = dict(version.change_report)
    report["outcome"] = {**_outcome_of(version), **changes}
    return report


def handle_change_report(db: Session, job: Job) -> dict:
    version_id = _payload_id(job, "prompt_version_id", _VERSION_GONE)
    version = db.get(PromptVersion, version_id)
    if version is None or version.agent.workspace_id != job.workspace_id:
        raise JobFailed(_VERSION_GONE)
    outcome = _outcome_of(version)
    parent = db.get(PromptVersion, version.parent_id) if version.parent_id else None
    diff_text = "\n".join(
        difflib.unified_diff(
            (parent.text if parent is not None else "").splitlines(),
            version.text.splitlines(),
            fromfile="current prompt",
            tofile="candidate prompt",
            lineterm="",
        )
    )[:MAX_DIFF_CHARS]
    table = outcome.get("table")
    rows = [
        {key: row.get(key) for key in _REPORT_ROW_KEYS}
        for row in (table if isinstance(table, list) else [])
        if isinstance(row, dict)
    ]
    status = str(outcome.get("status", ""))
    reason = str(outcome.get("reason", ""))
    # End the read transaction before the slow LLM call (expire_on_commit=False keeps rows usable).
    db.commit()
    try:
        narrative = write_report(diff_text, rows, status, reason)
    except ReportFailed as exc:
        logger.warning("change_report failure for job %s: %s", job.id, exc)
        narrative = None

    _still_exists(db, version, _VERSION_GONE)  # also reloads change_report
    if narrative is None:
        version.change_report = _with_outcome(version, {"explanation_unavailable": True})
    else:
        version.change_report = _with_outcome(
            version,
            {
                "narrative": {
                    "explanation": narrative.explanation,
                    "next_step": narrative.next_step,
                    "cited_test_ids": list(narrative.cited_test_ids),
                },
                "explanation_unavailable": False,
            },
        )
    db.commit()
    return {"explained": narrative is not None}


HANDLERS: dict[str, Callable[[Session, Job], dict]] = {
    "build_tests": handle_build_tests,
    "replay": handle_replay,
    "suggest_fix": handle_suggest_fix,
    "generate_heldout": handle_generate_heldout,
    "change_report": handle_change_report,
}
