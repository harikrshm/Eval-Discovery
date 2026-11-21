import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.agent import router as agent_router
from backend.api.agents import router as agents_router
from backend.api.workspaces import router as workspaces_router
from backend.checker import router as checker_router
from backend.criteria import router as criteria_router

app = FastAPI(title="Eval Discovery API")

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

app.include_router(agent_router)
app.include_router(checker_router)
app.include_router(criteria_router)
app.include_router(workspaces_router)
app.include_router(agents_router)
