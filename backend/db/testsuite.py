"""Test-suite queries: ownership, current results, staleness. Pure reads/writes; callers commit."""

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.core.testsuite import criterion_status
from backend.db.models import Agent, Criterion, EvalResult, EvalTest, Workspace


def get_owned_criterion(db: Session, workspace: Workspace, criterion_id: str) -> Criterion:
    """Return the criterion if its agent belongs to the workspace; LookupError otherwise."""
    criterion = db.get(Criterion, criterion_id)
    if criterion is None or criterion.agent.workspace_id != workspace.id:
        raise LookupError(criterion_id)
    return criterion


def get_owned_test(db: Session, workspace: Workspace, test_id: str) -> EvalTest:
    """Return the test if it belongs to the workspace; LookupError otherwise."""
    test = db.get(EvalTest, test_id)
    if test is None or test.criterion.agent.workspace_id != workspace.id:
        raise LookupError(test_id)
    return test


def agent_tests(db: Session, agent: Agent) -> list[EvalTest]:
    """All of the agent's tests, by criterion position, `fix` before `heldout`."""
    stmt = (
        select(EvalTest)
        .join(Criterion, EvalTest.criterion_id == Criterion.id)
        .where(Criterion.agent_id == agent.id)
        .order_by(Criterion.position, EvalTest.role != "fix", EvalTest.created_at)
    )
    return list(db.scalars(stmt))


def current_replay(db: Session, test: EvalTest, prompt_version_id: str) -> EvalResult | None:
    """Newest non-stale replay result of the test for the given prompt version."""
    stmt = (
        select(EvalResult)
        .where(
            EvalResult.test_id == test.id,
            EvalResult.kind == "replay",
            EvalResult.prompt_version_id == prompt_version_id,
            EvalResult.stale.is_(False),
        )
        .order_by(EvalResult.created_at.desc(), EvalResult.id)
        .limit(1)
    )
    return db.scalars(stmt).first()


def latest_baseline(db: Session, test: EvalTest) -> EvalResult | None:
    """Newest baseline result of the test."""
    stmt = (
        select(EvalResult)
        .where(EvalResult.test_id == test.id, EvalResult.kind == "baseline")
        .order_by(EvalResult.created_at.desc(), EvalResult.id)
        .limit(1)
    )
    return db.scalars(stmt).first()


def stale_test_count(db: Session, agent: Agent) -> int:
    """Number of tests with no current replay for the agent's current prompt version."""
    version_id = agent.current_prompt_version_id
    return sum(1 for test in agent_tests(db, agent) if current_replay(db, test, version_id) is None)


def mark_criterion_results_stale(db: Session, criterion: Criterion) -> None:
    """Mark the criterion's replay results stale, clear its judge sanity check and bump its revision.

    The revision lets a job that loaded the criterion earlier notice it was edited and discard its results.
    """
    test_ids = select(EvalTest.id).where(EvalTest.criterion_id == criterion.id)
    db.execute(
        update(EvalResult)
        .where(EvalResult.test_id.in_(test_ids), EvalResult.kind == "replay")
        .values(stale=True)
        .execution_options(synchronize_session="fetch")
    )
    criterion.judge_check = None
    criterion.revision += 1


def criterion_status_for(db: Session, criterion: Criterion, prompt_version_id: str) -> str:
    """`passing | failing | untested` from each test's current replay verdict."""
    verdicts: list[str | None] = []
    tests = list(db.scalars(select(EvalTest).where(EvalTest.criterion_id == criterion.id)))
    for test in tests:
        replay = current_replay(db, test, prompt_version_id)
        verdicts.append(replay.verdict if replay else None)
    return criterion_status(verdicts)
