import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.agents import router as agents_router
from backend.api.criteria import router as criteria_router
from backend.api.runs import router as runs_router
from backend.api.workspaces import router as workspaces_router
from backend.db.session import SessionLocal
from backend.db.workspaces import purge_expired

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Purge expired workspaces once at startup."""
    db = SessionLocal()
    try:
        purged = purge_expired(db)
        if purged:
            logger.info("Purged %d expired workspace(s) at startup", purged)
    finally:
        db.close()
    yield


app = FastAPI(title="Eval Discovery API", lifespan=lifespan)

ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workspaces_router)
app.include_router(agents_router)
app.include_router(runs_router)
app.include_router(criteria_router)
