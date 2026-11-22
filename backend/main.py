import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.agents import router as agents_router
from backend.api.assist import router as assist_router
from backend.api.assist_console import router as assist_console_router
from backend.api.criteria import router as criteria_router
from backend.api.export import router as export_router
from backend.api.prompt_versions import router as prompt_router
from backend.api.runs import router as runs_router
from backend.api.tests import router as tests_router
from backend.api.versions import router as versions_router
from backend.api.workspaces import router as workspaces_router
from backend.db.session import SessionLocal
from backend.db.workspaces import purge_expired
from backend.jobs.worker import worker_loop

logger = logging.getLogger(__name__)

WORKER_SHUTDOWN_TIMEOUT_SECONDS = 5


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Purge expired workspaces once at startup, then run the job worker (unless WORKER_ENABLED=0)."""
    db = SessionLocal()
    try:
        purged = purge_expired(db)
        if purged:
            logger.info("Purged %d expired workspace(s) at startup", purged)
    finally:
        db.close()

    stop = asyncio.Event()
    worker_task = None
    if os.getenv("WORKER_ENABLED") != "0":
        worker_task = asyncio.create_task(worker_loop(stop))
    try:
        yield
    finally:
        stop.set()
        if worker_task is not None:
            try:
                await asyncio.wait_for(worker_task, timeout=WORKER_SHUTDOWN_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                logger.warning("Job worker did not stop within %s s", WORKER_SHUTDOWN_TIMEOUT_SECONDS)
            except Exception:
                logger.exception("Job worker stopped with an error")


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
app.include_router(prompt_router)
app.include_router(tests_router)
app.include_router(versions_router)
app.include_router(export_router)
app.include_router(assist_router)
app.include_router(assist_console_router)
