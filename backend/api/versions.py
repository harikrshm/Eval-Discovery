"""Prompt version history: list an agent's versions and read one with its parent's text."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_workspace
from backend.api.schemas import VersionDetailOut, VersionOut
from backend.db.agents import get_owned_agent
from backend.db.candidates import get_owned_version
from backend.db.models import PromptVersion, Workspace
from backend.db.session import get_db

router = APIRouter(tags=["versions"])


def version_out(version: PromptVersion, current_id: str | None) -> VersionOut:
    return VersionOut(
        id=version.id,
        number=version.number,
        origin=version.origin,
        status=version.status,
        strength=(version.verification or {}).get("strength"),
        created_at=version.created_at,
        is_current=version.id == current_id,
    )


@router.get("/agents/{agent_id}/prompt-versions", response_model=list[VersionOut])
def list_versions(
    agent_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> list[VersionOut]:
    try:
        agent = get_owned_agent(db, workspace, agent_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Agent not found.") from None
    versions = db.scalars(
        select(PromptVersion).where(PromptVersion.agent_id == agent.id).order_by(PromptVersion.number.desc())
    )
    return [version_out(v, agent.current_prompt_version_id) for v in versions]


@router.get("/prompt-versions/{version_id}", response_model=VersionDetailOut)
def get_version(
    version_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> VersionDetailOut:
    try:
        version = get_owned_version(db, workspace, version_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Version not found.") from None
    parent = db.get(PromptVersion, version.parent_id) if version.parent_id else None
    base = version_out(version, version.agent.current_prompt_version_id)
    return VersionDetailOut(
        **base.model_dump(),
        text=version.text,
        parent_text=parent.text if parent is not None and parent.agent_id == version.agent_id else None,
    )
