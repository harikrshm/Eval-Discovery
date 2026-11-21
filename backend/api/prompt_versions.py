"""Prompt editor API: save a manual edit of an agent's system prompt as a new version."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.deps import get_current_workspace
from backend.api.limits import FIX_VERIFYING, check_version_caps
from backend.api.schemas import PromptUpdate, PromptUpdated
from backend.db.agents import (
    current_prompt_version,
    get_owned_agent,
    lock_agent,
    new_prompt_version,
)
from backend.db.candidates import verifying_version
from backend.db.models import Workspace
from backend.db.session import get_db

router = APIRouter(tags=["prompt"])


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
    if verifying_version(db, agent) is not None:
        raise HTTPException(status_code=409, detail=FIX_VERIFYING)
    current = current_prompt_version(agent)
    if body.text == current.text:
        return PromptUpdated(
            version_id=current.id, version_number=current.number, text=current.text, changed=False
        )
    check_version_caps(db, agent)
    try:
        version = new_prompt_version(db, agent, body.text)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return PromptUpdated(
        version_id=version.id, version_number=version.number, text=version.text, changed=True
    )
