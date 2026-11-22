"""Export API: an agent's suite as a versioned JSON document for the CI gate. Read-only."""

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_workspace
from backend.api.schemas import (
    ExportAgentOut,
    ExportCriterionOut,
    ExportPromptOut,
    ExportTestOut,
    SuiteExportOut,
)
from backend.db.agents import current_prompt_version, get_owned_agent
from backend.db.models import Criterion, Workspace
from backend.db.session import get_db
from backend.db.testsuite import agent_tests, current_replay

router = APIRouter(tags=["export"])


@router.get("/agents/{agent_id}/export", response_model=SuiteExportOut)
def export_suite(
    agent_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> SuiteExportOut:
    try:
        agent = get_owned_agent(db, workspace, agent_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Agent not found.") from None
    try:
        version = current_prompt_version(agent)
    except LookupError:
        raise HTTPException(status_code=409, detail="Agent has no current prompt.") from None

    tests_by_criterion = defaultdict(list)
    for test in agent_tests(db, agent):  # already ordered: criterion position, fix before heldout
        replay = current_replay(db, test, version.id)
        tests_by_criterion[test.criterion_id].append(
            ExportTestOut(
                id=test.id,
                role=test.role,
                origin=test.origin,
                scripted_user_messages=list(test.scripted_user_messages),
                current_verdict=replay.verdict if replay else None,
            )
        )

    criteria = db.scalars(
        select(Criterion).where(Criterion.agent_id == agent.id).order_by(Criterion.position, Criterion.created_at)
    )
    return SuiteExportOut(
        agent=ExportAgentOut(name=agent.name, user_role_label=agent.user_role_label),
        prompt=ExportPromptOut(text=version.text, version_number=version.number),
        criteria=[
            ExportCriterionOut(
                id=c.id,
                name=c.name,
                definition=c.definition,
                good_example=c.good_example,
                bad_example=c.bad_example,
                tests=tests_by_criterion[c.id],
            )
            for c in criteria
        ],
    )
