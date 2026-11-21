"""Criteria API: discover a criterion from the detected pattern, add one manually, edit one."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_workspace
from backend.api.limits import MAX_CRITERIA_PER_AGENT, rate_limit_llm
from backend.api.schemas import (
    MAX_CRITERION_NAME_CHARS,
    CriterionCreate,
    CriterionOut,
    CriterionUpdate,
)
from backend.core.config import AgentConfig
from backend.core.criteria import CriterionGenerationFailed, generate_criterion
from backend.db.agents import current_prompt, get_owned_agent
from backend.db.events import try_log_event
from backend.db.models import Agent, Criterion, Workspace
from backend.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["criteria"])

NO_PATTERN = "No pattern to build from yet."
GENERATION_FAILED = "Criteria generation unavailable, try again."
MANUAL_WHY = "(manually added)"
CRITERIA_LIMIT = "Criteria limit reached for this agent."


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


@router.post("/agents/{agent_id}/criteria/discover", status_code=201, response_model=CriterionOut)
def discover(
    agent_id: str,
    request: Request,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> CriterionOut:
    agent = _owned_or_404(db, workspace, agent_id)
    _check_criteria_cap(db, agent)
    rate_limit_llm(request)
    if not agent.pattern_summary:
        raise HTTPException(status_code=409, detail=NO_PATTERN)
    config = AgentConfig(
        name=agent.name, system_prompt=current_prompt(agent), user_role_label=agent.user_role_label
    )
    try:
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
    return _out(criterion)


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
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(criterion, field, value)
    db.commit()
    return _out(criterion)
