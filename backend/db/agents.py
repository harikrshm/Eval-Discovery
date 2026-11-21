"""Agent persistence helpers: creation with its first prompt version, and ownership lookup."""

import hashlib
from dataclasses import asdict

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.core.config import MAX_AGENTS_PER_WORKSPACE, AgentAnalysis, AgentConfig, Behavior
from backend.db.models import Agent, BehaviorMap, PromptVersion, Workspace


class AgentLimitReached(Exception):
    """The workspace already has MAX_AGENTS_PER_WORKSPACE agents."""


def agent_count(db: Session, workspace: Workspace) -> int:
    """Number of agents the workspace currently owns."""
    return db.scalar(select(func.count()).select_from(Agent).where(Agent.workspace_id == workspace.id))


def create_agent(
    db: Session,
    workspace: Workspace,
    config: AgentConfig,
    analysis: AgentAnalysis,
    origin: str = "initial",
) -> Agent:
    """Create an agent, its promoted prompt version and behavior map in one transaction.

    Re-checks the per-workspace quota right before inserting (the caller's early check can be
    stale); raises AgentLimitReached with nothing written.
    """
    try:
        if agent_count(db, workspace) >= MAX_AGENTS_PER_WORKSPACE:
            raise AgentLimitReached(workspace.id)
        agent = Agent(
            workspace_id=workspace.id,
            name=config.name,
            user_role_label=config.user_role_label,
        )
        db.add(agent)
        db.flush()
        version = PromptVersion(
            agent_id=agent.id, text=config.system_prompt, origin=origin, status="promoted", number=1
        )
        db.add(version)
        db.flush()
        agent.current_prompt_version_id = version.id
        db.add(
            BehaviorMap(
                agent_id=agent.id,
                prompt_hash=hashlib.sha256(config.system_prompt.encode("utf-8")).hexdigest(),
                analysis=asdict(analysis),
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return agent


def get_owned_agent(db: Session, workspace: Workspace, agent_id: str) -> Agent:
    """Return the agent if it belongs to the workspace; LookupError otherwise (missing or foreign)."""
    agent = db.get(Agent, agent_id)
    if agent is None or agent.workspace_id != workspace.id:
        raise LookupError(agent_id)
    return agent


def current_prompt(agent: Agent) -> str:
    """Text of the agent's current prompt version."""
    for version in agent.prompt_versions:
        if version.id == agent.current_prompt_version_id:
            return version.text
    raise LookupError(f"agent {agent.id} has no current prompt version")


def prompt_version_count(db: Session, agent: Agent) -> int:
    """Number of prompt versions the agent has."""
    return db.scalar(
        select(func.count()).select_from(PromptVersion).where(PromptVersion.agent_id == agent.id)
    )


def current_prompt_version(agent: Agent) -> PromptVersion:
    """The agent's current prompt version row."""
    for version in agent.prompt_versions:
        if version.id == agent.current_prompt_version_id:
            return version
    raise LookupError(f"agent {agent.id} has no current prompt version")


def new_prompt_version(db: Session, agent: Agent, text: str) -> PromptVersion:
    """Promote `text` as the agent's next manual prompt version; the caller commits.

    The new version's number is max(existing)+1 and its parent is the current version, which
    becomes `superseded`. Existing runs keep pointing at the version they ran on.
    """
    parent_id = agent.current_prompt_version_id
    highest = db.scalar(
        select(func.max(PromptVersion.number)).where(PromptVersion.agent_id == agent.id)
    )
    db.execute(
        update(PromptVersion)
        .where(PromptVersion.agent_id == agent.id, PromptVersion.status == "promoted")
        .values(status="superseded")
        .execution_options(synchronize_session="fetch")
    )
    version = PromptVersion(
        agent_id=agent.id,
        text=text,
        parent_id=parent_id,
        origin="manual",
        status="promoted",
        number=(highest or 0) + 1,
    )
    db.add(version)
    db.flush()
    agent.current_prompt_version_id = version.id
    return version


def latest_analysis(agent: Agent) -> dict:
    """The stored analysis for the agent's current prompt (most recent behavior map)."""
    if not agent.behavior_maps:
        return {"user_role_label": agent.user_role_label, "behaviors": [], "starter_messages": []}
    current_hash = hashlib.sha256(current_prompt(agent).encode("utf-8")).hexdigest()
    matching = [m for m in agent.behavior_maps if m.prompt_hash == current_hash]
    return (matching or agent.behavior_maps)[-1].analysis


def current_behaviors(agent: Agent) -> list[Behavior]:
    """Rebuild `Behavior` objects from the agent's stored analysis JSON."""
    return [
        Behavior(
            name=b["name"],
            description=b["description"],
            trigger_terms=tuple(b.get("trigger_terms", ())),
        )
        for b in latest_analysis(agent)["behaviors"]
    ]
