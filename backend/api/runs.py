"""Runs API: save test conversations, and check the unchecked ones for a repeated pattern."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.api.deps import get_current_workspace
from backend.api.limits import MAX_PATTERN_RUNS, MAX_RUNS_PER_WORKSPACE, rate_limit_llm
from backend.api.schemas import PatternCheckOut, RunCreate, RunOut, RunSaved
from backend.core.config import AgentConfig
from backend.core.pattern import PatternCheckFailed, TranscriptTooShort, detect_pattern, keyword_prefilter
from backend.db.agents import current_behaviors, current_prompt, get_owned_agent
from backend.db.events import try_log_event
from backend.db.models import Agent, Run, Workspace
from backend.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["runs"])

RUN_LIMIT = "Run limit reached for this workspace."
PATTERN_FAILED = "Pattern check unavailable, try again."
EMPTY_DELTA = "Nothing to check: the set of new runs is empty. Save a run first."
RUN_CONFLICT = "Another run was saved at the same time. Try again."
CHECK_DUE_MIN_NEW_RUNS = 2


def _owned_or_404(db: Session, workspace: Workspace, agent_id: str) -> Agent:
    try:
        return get_owned_agent(db, workspace, agent_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Agent not found.") from None


def _run_count(db: Session, agent: Agent) -> int:
    return db.scalar(select(func.count()).select_from(Run).where(Run.agent_id == agent.id))


def _delta_runs(db: Session, agent: Agent) -> list[Run]:
    """The most recent runs (at most MAX_PATTERN_RUNS) saved since the last pattern check, in position order."""
    newest_first = db.scalars(
        select(Run)
        .where(Run.agent_id == agent.id, Run.position > agent.last_checked_at_run)
        .order_by(Run.position.desc())
        .limit(MAX_PATTERN_RUNS)
    ).all()
    return list(reversed(newest_first))


def _transcripts(runs: list[Run]) -> list[list[dict]]:
    return [[{"role": m["role"], "content": m["content"]} for m in run.messages] for run in runs]


@router.post("/agents/{agent_id}/runs", status_code=201, response_model=RunSaved)
def save_run(
    agent_id: str,
    body: RunCreate,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> RunSaved:
    agent = _owned_or_404(db, workspace, agent_id)
    workspace_runs = db.scalar(
        select(func.count())
        .select_from(Run)
        .join(Agent, Run.agent_id == Agent.id)
        .where(Agent.workspace_id == workspace.id)
    )
    if workspace_runs >= MAX_RUNS_PER_WORKSPACE:
        raise HTTPException(status_code=429, detail=RUN_LIMIT)

    position = _run_count(db, agent) + 1
    run = Run(
        agent_id=agent.id,
        prompt_version_id=agent.current_prompt_version_id,
        position=position,
        messages=[m.model_dump() for m in body.messages],
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=RUN_CONFLICT) from None
    try_log_event(db, workspace.id, "run_saved", {"position": position})

    check_due = False
    unchecked = position - agent.last_checked_at_run
    if unchecked >= CHECK_DUE_MIN_NEW_RUNS:
        window = _delta_runs(db, agent)
        check_due = keyword_prefilter(current_behaviors(agent), _transcripts(window))
        if not check_due and unchecked > MAX_PATTERN_RUNS:
            # Older runs can never be analysed (only the newest MAX_PATTERN_RUNS are sent), so skip
            # past them; otherwise the unchecked backlog would only ever grow.
            agent.last_checked_at_run = window[-1].position
            db.commit()
    return RunSaved(
        run=RunOut(id=run.id, position=run.position, messages=run.messages, created_at=run.created_at),
        run_count=position,
        check_due=check_due,
    )


@router.post("/agents/{agent_id}/check-pattern", response_model=PatternCheckOut)
def check_pattern(
    agent_id: str,
    request: Request,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> PatternCheckOut:
    agent = _owned_or_404(db, workspace, agent_id)
    rate_limit_llm(request)
    runs = _delta_runs(db, agent)
    if not runs:
        raise HTTPException(status_code=400, detail=EMPTY_DELTA)
    config = AgentConfig(
        name=agent.name, system_prompt=current_prompt(agent), user_role_label=agent.user_role_label
    )
    try:
        result = detect_pattern(config, _transcripts(runs))
    except TranscriptTooShort as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except PatternCheckFailed:
        logger.warning("Pattern check failed", exc_info=True)
        raise HTTPException(status_code=502, detail=PATTERN_FAILED) from None

    # Mark only what was analysed: a run saved during the LLM call stays in the next delta.
    agent.last_checked_at_run = runs[-1].position
    if result.found:
        agent.pattern_summary = result.pattern_summary
    db.commit()
    if result.found:
        try_log_event(db, workspace.id, "loop_detected", {})
    return PatternCheckOut(found=result.found, pattern_summary=result.pattern_summary)
