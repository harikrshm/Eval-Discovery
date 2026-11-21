"""Candidate prompt versions: proposal, approval, rejection and restore.

Plain functions over a Session. None of them commits (callers commit). Each one that changes
candidate state first takes the agent row lock (`lock_agent`) and reloads the version it was given,
so two requests racing on the same agent are serialized and the second sees the first one's result.
"""

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.core.promotion import RequiredTest, strength_for
from backend.db.agents import (
    MAX_PROMPT_VERSIONS_PER_AGENT,
    MAX_VERSION_ROWS_PER_AGENT,
    lock_agent,
    prompt_version_count,
    prompt_version_row_count,
)
from backend.db.models import Agent, EvalResult, EvalTest, PromptVersion, Workspace, utcnow
from backend.db.testsuite import agent_tests, current_replay, latest_baseline

OPEN_STATUSES = ("proposed", "approved_verifying")
RESTORABLE_STATUSES = ("promoted", "superseded", "failed_verification")


class CandidateOpen(Exception):
    """The agent already has a `proposed` or `approved_verifying` version."""


class VersionLimitReached(Exception):
    """The agent is at its version cap (counted versions or total rows)."""


def get_owned_version(db: Session, workspace: Workspace, version_id: str) -> PromptVersion:
    """Return the version if its agent belongs to the workspace; LookupError otherwise."""
    version = db.get(PromptVersion, version_id)
    if version is None or version.agent.workspace_id != workspace.id:
        raise LookupError(version_id)
    return version


def open_candidate(db: Session, agent: Agent) -> PromptVersion | None:
    """The agent's `proposed` or `approved_verifying` version, if any."""
    stmt = (
        select(PromptVersion)
        .where(PromptVersion.agent_id == agent.id, PromptVersion.status.in_(OPEN_STATUSES))
        .order_by(PromptVersion.number.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def verifying_version(db: Session, agent: Agent) -> PromptVersion | None:
    """The agent's `approved_verifying` version, if any."""
    stmt = (
        select(PromptVersion)
        .where(PromptVersion.agent_id == agent.id, PromptVersion.status == "approved_verifying")
        .order_by(PromptVersion.number.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def failing_result_for(db: Session, test: EvalTest, current_version_id: str) -> EvalResult | None:
    """The result showing the test fails on the current prompt version, else None.

    A current non-stale replay decides (returned only when it failed). Without one, the test's
    newest baseline counts when it was taken on the current version and failed.
    """
    replay = current_replay(db, test, current_version_id)
    if replay is not None:
        return replay if replay.verdict == "fail" else None
    baseline = latest_baseline(db, test)
    if baseline is not None and baseline.prompt_version_id == current_version_id and baseline.verdict == "fail":
        return baseline
    return None


def _next_number(db: Session, agent: Agent) -> int:
    highest = db.scalar(select(func.max(PromptVersion.number)).where(PromptVersion.agent_id == agent.id))
    return (highest or 0) + 1


def create_proposed_version(
    db: Session, agent: Agent, text: str, source_test_id: str, why: dict
) -> PromptVersion:
    """Store a suggested prompt as the agent's `proposed` version; the current version is unchanged.

    Raises CandidateOpen when a candidate is already open, VersionLimitReached at either cap.
    """
    lock_agent(db, agent)
    if open_candidate(db, agent) is not None:
        raise CandidateOpen(agent.id)
    if (
        prompt_version_count(db, agent) >= MAX_PROMPT_VERSIONS_PER_AGENT
        or prompt_version_row_count(db, agent) >= MAX_VERSION_ROWS_PER_AGENT
    ):
        raise VersionLimitReached(agent.id)
    version = PromptVersion(
        agent_id=agent.id,
        text=text,
        parent_id=agent.current_prompt_version_id,
        origin="suggested",
        status="proposed",
        number=_next_number(db, agent),
        source_test_id=source_test_id,
        change_report={"why": why, "outcome": None},
    )
    db.add(version)
    db.flush()
    db.expire(agent, ["prompt_versions"])
    return version


def compute_required(db: Session, agent: Agent, fix_test: EvalTest) -> list[RequiredTest]:
    """The tests a candidate must pass: fix, the criterion's held-out test, then regressions.

    `before` is the failing verdict for the fix and held-out tests ("fail" when no failing result is
    recorded) and "pass" for regressions, which are every other test whose current non-stale replay
    on the agent's current version passed (failing, stale, never-run and not_applicable are left out).
    """
    current_id = agent.current_prompt_version_id

    def before(test: EvalTest) -> str:
        failing = failing_result_for(db, test, current_id)
        return failing.verdict if failing is not None else "fail"

    required = [RequiredTest(fix_test.id, "fix", before(fix_test))]
    heldout = db.scalars(
        select(EvalTest).where(EvalTest.criterion_id == fix_test.criterion_id, EvalTest.role == "heldout")
    ).first()
    taken = {fix_test.id}
    if heldout is not None and heldout.id != fix_test.id:
        required.append(RequiredTest(heldout.id, "heldout", before(heldout)))
        taken.add(heldout.id)
    for test in agent_tests(db, agent):
        if test.id in taken:
            continue
        replay = current_replay(db, test, current_id)
        if replay is not None and replay.verdict == "pass":
            required.append(RequiredTest(test.id, "regression", "pass"))
    return required


def _lock_and_reload(db: Session, agent: Agent, version: PromptVersion) -> None:
    """Lock the agent row and reload `version`, so its status is the committed one."""
    if version.agent_id != agent.id:
        raise ValueError("The version belongs to another agent.")
    lock_agent(db, agent)
    db.refresh(version)


def approve_candidate(db: Session, agent: Agent, version: PromptVersion) -> list[RequiredTest]:
    """Move a `proposed` version to `approved_verifying` and record the tests it must pass.

    Does not enqueue anything. Raises ValueError unless the version is `proposed`.
    """
    _lock_and_reload(db, agent, version)
    if version.status != "proposed":
        raise ValueError(f"Only a proposed version can be approved, not {version.status!r}.")
    fix_test = db.get(EvalTest, version.source_test_id) if version.source_test_id else None
    if fix_test is None:
        raise LookupError(version.source_test_id)
    required = compute_required(db, agent, fix_test)
    version.verification = {
        "strength": strength_for(required),
        "fix_test_id": fix_test.id,
        "approved_at": utcnow().isoformat(),
        "required": [{"test_id": r.test_id, "role": r.role, "before": r.before} for r in required],
    }
    version.status = "approved_verifying"
    db.flush()
    return required


def reject_candidate(db: Session, version: PromptVersion) -> None:
    """Mark a `proposed` version `rejected`. Raises ValueError for any other status."""
    _lock_and_reload(db, version.agent, version)
    if version.status != "proposed":
        raise ValueError(f"Only a proposed version can be rejected, not {version.status!r}.")
    version.status = "rejected"
    db.flush()


def restore_version(db: Session, agent: Agent, version: PromptVersion) -> PromptVersion:
    """Promote a new `restored` copy of an earlier version's text.

    The previous current version and any `proposed` candidate become `superseded`. Allowed from
    `promoted`, `superseded` and `failed_verification` (ValueError otherwise); refused with
    CandidateOpen while a candidate is being verified.
    """
    _lock_and_reload(db, agent, version)
    if version.status not in RESTORABLE_STATUSES:
        raise ValueError(f"A {version.status!r} version cannot be restored.")
    if verifying_version(db, agent) is not None:
        raise CandidateOpen(agent.id)
    number = _next_number(db, agent)
    db.execute(
        update(PromptVersion)
        .where(PromptVersion.agent_id == agent.id, PromptVersion.status.in_(("promoted", "proposed")))
        .values(status="superseded")
        .execution_options(synchronize_session="fetch")
    )
    restored = PromptVersion(
        agent_id=agent.id,
        text=version.text,
        parent_id=version.id,
        origin="restored",
        status="promoted",
        number=number,
    )
    db.add(restored)
    db.flush()
    agent.current_prompt_version_id = restored.id
    db.expire(agent, ["prompt_versions"])
    return restored
