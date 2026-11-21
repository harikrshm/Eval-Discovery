from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from backend.db.models import Workspace
from backend.db.session import get_db
from backend.db.workspaces import find_workspace

UNAUTHORIZED_DETAIL = "Workspace not found or expired. Start a new one."


def get_current_workspace(
    x_workspace_token: str | None = Header(default=None, alias="X-Workspace-Token"),
    db: Session = Depends(get_db),
) -> Workspace:
    workspace = find_workspace(db, x_workspace_token) if x_workspace_token else None
    if workspace is None:
        raise HTTPException(status_code=401, detail=UNAUTHORIZED_DETAIL)
    return workspace
