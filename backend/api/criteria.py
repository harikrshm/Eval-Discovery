"""Criteria API: discover a criterion from the detected pattern, add one manually, edit one."""

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_workspace
from backend.api.limits import FIX_VERIFYING, MAX_CRITERIA_PER_AGENT, rate_limit_llm
from backend.api.schemas import (
    MAX_CRITERION_NAME_CHARS,
    CriterionCreate,
    CriterionOut,
    CriterionUpdate,
    DiscoveredCriterionOut,
)
from backend.core.config import AgentConfig
from backend.core.criteria import CriterionGenerationFailed, generate_criterion
from backend.db.agents import current_prompt, get_owned_agent, lock_agent
from backend.db.candidates import verifying_version
from backend.db.events import try_log_event
from backend.db.models import Agent, Criterion, Workspace, utcnow
from backend.db.session import get_db
from backend.db.testsuite import mark_criterion_results_stale
from backend.groq_client import request_retries
from backend.jobs.queue import (
    JOBS_PER_WORKSPACE_PER_HOUR,
    MAX_QUEUED_JOBS,
    enqueue,
    jobs_started_since,
    queued_job_count,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["criteria"])

NO_PATTERN = "No pattern to build from yet."
GENERATION_FAILED = "Criteria generation unavailable, try again."
MANUAL_WHY = "(manually added)"
CRITERIA_LIMIT = "Criteria limit reached for this agent."
# What the judge reads; editing any of these makes earlier verdicts and the sanity check obsolete.
JUDGED_FIELDS = ("definition", "good_example", "bad_example")


def _owned_or_404(db: Session, workspace: Workspace, agent_id: str) -> Agent:
    try:
        return get_owned_agent(db, workspace, agent_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Agent not found.") from None


def _criterion_count(db: Session, agent: Agent) -> int:
    return db.scalar(select(func.count()).select_from(Criterion).where(Criterion.agent_id == agent.id))


def _check_criteria_cap(db: Session, agent: Agent) -> None:
    if _criterion_count(db, agent) >= MAX_CRITERIA_PER_AGENT:
        raise HTTPException(status_code=429, detail=CRITERIA_LIMIT)


def _out(criterion: Criterion) -> CriterionOut:
    return CriterionOut(
        id=criterion.id,
        name=criterion.name,
        definition=criterion.definition,
        good_example=criterion.good_example,
        bad_example=criterion.bad_example,
        why_discovered=criterion.why_discovered,
        source=criterion.source,
    )


def _enqueue_build_tests(db: Session, workspace: Workspace, criterion: Criterion) -> str | None:
    """Queue the test-building job; None when a cap is reached or queueing fails (the criterion still stands)."""
    try:
        used = jobs_started_since(db, workspace.id, utcnow() - timedelta(hours=1))
        if used >= JOBS_PER_WORKSPACE_PER_HOUR or queued_job_count(db) >= MAX_QUEUED_JOBS:
            return None
        return enqueue(db, workspace.id, "build_tests", {"criterion_id": criterion.id}).id
    except Exception:
        logger.warning("Could not queue test building for criterion %s", criterion.id, exc_info=True)
        db.rollback()
        return None


@router.post("/agents/{agent_id}/criteria/discover", status_code=201, response_model=DiscoveredCriterionOut)
def discover(
    agent_id: str,
    request: Request,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> DiscoveredCriterionOut:
    agent = _owned_or_404(db, workspace, agent_id)
    _check_criteria_cap(db, agent)
    rate_limit_llm(request)
    if not agent.pattern_summary:
        raise HTTPException(status_code=409, detail=NO_PATTERN)
    config = AgentConfig(
        name=agent.name, system_prompt=current_prompt(agent), user_role_label=agent.user_role_label
    )
    try:
        with request_retries(2):
            draft = generate_criterion(config, agent.pattern_summary)
    except CriterionGenerationFailed:
        logger.warning("Criterion generation failed", exc_info=True)
        raise HTTPException(status_code=502, detail=GENERATION_FAILED) from None

    criterion = Criterion(
        agent_id=agent.id,
        position=_criterion_count(db, agent) + 1,
        name=draft.name[:MAX_CRITERION_NAME_CHARS],
        definition=draft.definition,
        good_example=draft.good_example,
        bad_example=draft.bad_example,
        why_discovered=draft.why_discovered,
        source="discovered",
    )
    db.add(criterion)
    agent.pattern_summary = None
    db.commit()
    try_log_event(db, workspace.id, "criterion_created", {"source": "discovered"})
    job_id = _enqueue_build_tests(db, workspace, criterion)
    return DiscoveredCriterionOut(**_out(criterion).model_dump(), job_id=job_id)


@router.post("/agents/{agent_id}/criteria", status_code=201, response_model=CriterionOut)
def add_manual(
    agent_id: str,
    body: CriterionCreate,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> CriterionOut:
    agent = _owned_or_404(db, workspace, agent_id)
    _check_criteria_cap(db, agent)
    criterion = Criterion(
        agent_id=agent.id,
        position=_criterion_count(db, agent) + 1,
        name=body.name,
        definition=body.definition,
        good_example=body.good_example,
        bad_example=body.bad_example,
        why_discovered=MANUAL_WHY,
        source="manual",
    )
    db.add(criterion)
    db.commit()
    try_log_event(db, workspace.id, "criterion_created", {"source": "manual"})
    return _out(criterion)


@router.patch("/criteria/{criterion_id}", response_model=CriterionOut)
def update(
    criterion_id: str,
    body: CriterionUpdate,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> CriterionOut:
    criterion = db.scalar(
        select(Criterion)
        .join(Agent, Criterion.agent_id == Agent.id)
        .where(Criterion.id == criterion_id, Agent.workspace_id == workspace.id)
    )
    if criterion is None:
        raise HTTPException(status_code=404, detail="Criterion not found.")
    changes = body.model_dump(exclude_unset=True)
    if any(f in changes for f in JUDGED_FIELDS):
        # Serialize with Approve (it locks the same row): a judged-field edit must not slip in
        # between the approval and the replays that verify the candidate against these definitions.
        lock_agent(db, criterion.agent)
        db.refresh(criterion)
    # Compare before setattr: only a real change to a judged field invalidates results.
    judged_changed = any(getattr(criterion, f) != changes[f] for f in JUDGED_FIELDS if f in changes)
    if judged_changed and verifying_version(db, criterion.agent) is not None:
        raise HTTPException(status_code=409, detail=FIX_VERIFYING)
    for field, value in changes.items():
        setattr(criterion, field, value)
    if judged_changed:
        mark_criterion_results_stale(db, criterion)
    db.commit()
    return _out(criterion)
