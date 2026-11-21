from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from backend.api.deps import get_current_workspace
from backend.db.models import Workspace
from backend.db.session import get_db
from backend.db.workspaces import create_workspace

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post("", status_code=201)
def create(db: Session = Depends(get_db)) -> dict[str, str]:
    _, token = create_workspace(db)
    return {"token": token}


@router.delete("/me", status_code=204)
def delete_me(
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> Response:
    db.delete(workspace)
    db.commit()
    return Response(status_code=204)
