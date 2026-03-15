from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.agent import router as agent_router
from backend.checker import router as checker_router
from backend.criteria import router as criteria_router

app = FastAPI(title="Eval Discovery API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_router)
app.include_router(checker_router)
app.include_router(criteria_router)
