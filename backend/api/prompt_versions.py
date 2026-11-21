"""Prompt editor API: save a manual edit of an agent's system prompt as a new version."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.deps import get_current_workspace
from backend.api.limits import MAX_PROMPT_VERSIONS_PER_AGENT
from backend.api.schemas import PromptUpdate, PromptUpdated
from backend.db.agents import (
    current_prompt_version,
    get_owned_agent,
    lock_agent,
    new_prompt_version,
    prompt_version_count,
)
from backend.db.models import Workspace
from backend.db.session import get_db

router = APIRouter(tags=["prompt"])

PROMPT_VERSION_LIMIT = "Prompt version limit reached for this agent."


@router.put("/agents/{agent_id}/prompt", response_model=PromptUpdated)
def update_prompt(
    agent_id: str,
    body: PromptUpdate,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> PromptUpdated:
    try:
        agent = get_owned_agent(db, workspace, agent_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Agent not found.") from None
    # Lock first: a concurrent edit must not read the same current version and number.
    lock_agent(db, agent)
    current = current_prompt_version(agent)
    if body.text == current.text:
        return PromptUpdated(
            version_id=current.id, version_number=current.number, text=current.text, changed=False
        )
    if prompt_version_count(db, agent) >= MAX_PROMPT_VERSIONS_PER_AGENT:
        raise HTTPException(status_code=429, detail=PROMPT_VERSION_LIMIT)
    try:
        version = new_prompt_version(db, agent, body.text)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return PromptUpdated(
        version_id=version.id, version_number=version.number, text=version.text, changed=True
    )
