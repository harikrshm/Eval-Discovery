from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.agent import router as agent_router

app = FastAPI(title="Eval Discovery API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_router)
