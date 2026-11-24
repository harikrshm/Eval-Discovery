"""Assist console v2 endpoints (builder side): conversation judging and, in later tasks, telemetry and trends.

Workspace-token auth (`X-Workspace-Token`) and the workspace's own assist agent, exactly like the other
console routes. Responses carry counts, rates, ids and labels only: never question or answer text.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.assist_console import _assist_agent
from backend.api.deps import get_current_workspace
from backend.api.schemas import JudgeEvaluateOut
from backend.db.models import Workspace
from backend.db.session import get_db
from backend.jobs.conversation_judge import evaluate_now

router = APIRouter(prefix="/assist/console", tags=["assist-console"])


@router.post("/judge/evaluate", response_model=JudgeEvaluateOut)
def judge_evaluate(
    workspace: Workspace = Depends(get_current_workspace), db: Session = Depends(get_db)
) -> JudgeEvaluateOut:
    """Queue one judgement for the oldest waiting conversation now, without waiting for idle time.

    The hourly cap and the one-judge-job-at-a-time rule still apply; `waiting` counts the rest.
    """
    _assist_agent(db, workspace)
    return JudgeEvaluateOut(**evaluate_now(db, workspace.id))
