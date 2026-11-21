"""Agents API: presets, creation (preset or custom prompt), state and chat."""

import logging
from dataclasses import replace

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_workspace
from backend.api.limits import MAX_ASSISTANT_MESSAGE_CHARS, rate_limit_llm
from backend.api.schemas import (
    MAX_ROLE_LABEL_CHARS,
    AgentCreate,
    AgentOut,
    AgentState,
    BehaviorOut,
    ChatRequest,
    ChatResponse,
    CriterionOut,
    PresetOut,
    RunOut,
    sanitize_label,
)
from backend.core.analyze import analyze_agent
from backend.core.config import MAX_AGENTS_PER_WORKSPACE, PRESETS, AgentAnalysis, AgentConfig
from backend.db.agents import (
    AgentLimitReached,
    agent_count,
    create_agent,
    current_prompt,
    current_prompt_version,
    get_owned_agent,
    latest_analysis,
)
from backend.db.events import try_log_event
from backend.db.models import Agent, Criterion, Run, Workspace
from backend.db.session import get_db
from backend.groq_client import groq_chat

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agents"])

ANALYSIS_FAILED = "Couldn't analyze that prompt. Try again."
CHAT_FAILED = "The agent didn't respond. Try again."
CHAT_MAX_TOKENS = 3000  # keeps replies (~4 chars/token) under the assistant message cap
AGENT_LIMIT = "Agent limit reached for this workspace."


def _agent_out(agent: Agent) -> AgentOut:
    analysis = latest_analysis(agent)
    version = current_prompt_version(agent)
    return AgentOut(
        id=agent.id,
        name=agent.name,
        user_role_label=agent.user_role_label,
        system_prompt=version.text,
        prompt_version_id=version.id,
        prompt_version_number=version.number,
        behaviors=[BehaviorOut(**b) for b in analysis["behaviors"]],
        starter_messages=analysis["starter_messages"],
    )


def _owned_or_404(db: Session, workspace: Workspace, agent_id: str) -> Agent:
    try:
        return get_owned_agent(db, workspace, agent_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Agent not found.") from None


def _create_agent_or_429(
    db: Session, workspace: Workspace, config: AgentConfig, analysis: AgentAnalysis
) -> Agent:
    try:
        return create_agent(db, workspace, config, analysis)
    except AgentLimitReached:
        raise HTTPException(status_code=429, detail=AGENT_LIMIT) from None


def _check_agent_quota(db: Session, workspace: Workspace) -> None:
    if agent_count(db, workspace) >= MAX_AGENTS_PER_WORKSPACE:
        raise HTTPException(status_code=429, detail=AGENT_LIMIT)


@router.get("/presets", response_model=list[PresetOut])
def list_presets() -> list[PresetOut]:
    return [
        PresetOut(
            id=p.id,
            name=p.config.name,
            user_role_label=p.config.user_role_label,
            system_prompt=p.config.system_prompt,
            starter_messages=list(p.analysis.starter_messages),
        )
        for p in PRESETS.values()
    ]


@router.post("/agents", status_code=201, response_model=AgentOut)
def create(
    body: AgentCreate,
    request: Request,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> AgentOut:
    if body.preset_id is not None:
        preset = PRESETS.get(body.preset_id)
        if preset is None:
            raise HTTPException(status_code=422, detail="Unknown preset.")
        _check_agent_quota(db, workspace)
        agent = _create_agent_or_429(db, workspace, preset.config, preset.analysis)
        source = "preset"
    else:
        _check_agent_quota(db, workspace)
        rate_limit_llm(request)
        try:
            analysis = analyze_agent(body.system_prompt, body.name)
            label = body.user_role_label
            if label is None:
                label = sanitize_label(analysis.user_role_label, MAX_ROLE_LABEL_CHARS)
        except Exception:
            # Includes AnalysisError and an unusable analyzed label; nothing was written yet.
            db.rollback()
            logger.warning("Prompt analysis failed", exc_info=True)
            raise HTTPException(status_code=502, detail=ANALYSIS_FAILED) from None
        config = AgentConfig(name=body.name, system_prompt=body.system_prompt, user_role_label=label)
        agent = _create_agent_or_429(db, workspace, config, replace(analysis, user_role_label=label))
        source = "custom"
    try_log_event(db, workspace.id, "agent_created", {"source": source})
    return _agent_out(agent)


@router.get("/agents/{agent_id}/state", response_model=AgentState)
def state(
    agent_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> AgentState:
    agent = _owned_or_404(db, workspace, agent_id)
    runs = db.scalars(select(Run).where(Run.agent_id == agent.id).order_by(Run.position)).all()
    criteria = db.scalars(
        select(Criterion).where(Criterion.agent_id == agent.id).order_by(Criterion.position)
    ).all()
    return AgentState(
        agent=_agent_out(agent),
        runs=[
            RunOut(id=r.id, position=r.position, messages=r.messages, created_at=r.created_at)
            for r in runs
        ],
        criteria=[
            CriterionOut(
                id=c.id,
                name=c.name,
                definition=c.definition,
                good_example=c.good_example,
                bad_example=c.bad_example,
                why_discovered=c.why_discovered,
                source=c.source,
            )
            for c in criteria
        ],
        pattern_summary=agent.pattern_summary,
        run_count=len(runs),
        last_checked_at_run=agent.last_checked_at_run,
    )


@router.post("/agents/{agent_id}/chat", response_model=ChatResponse)
def chat(
    agent_id: str,
    body: ChatRequest,
    request: Request,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ChatResponse:
    agent = _owned_or_404(db, workspace, agent_id)
    rate_limit_llm(request)
    system = current_prompt(agent)
    messages = [m.model_dump() for m in body.messages]
    try:
        reply = groq_chat(system, messages, max_tokens=CHAT_MAX_TOKENS)
    except Exception:
        logger.warning("Agent chat failed", exc_info=True)
        raise HTTPException(status_code=502, detail=CHAT_FAILED) from None
    # Replies come back in later requests as assistant turns, so they must fit the cap.
    return ChatResponse(reply=reply[:MAX_ASSISTANT_MESSAGE_CHARS])
