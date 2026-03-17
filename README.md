# Eval Discovery Agent

Helps voice agent builders break out of the preview run loop by auto-generating eval rubrics grounded in real conversation data.

## What It Does

1. You play a prospect and chat with **Alex** — a CloudSync SDR AI agent
2. Click **END** after each conversation to save the run
3. The system automatically detects repeated testing patterns across your runs
4. Click **Discover Evals** to generate a structured evaluation rubric
5. Edit the criteria and **Export All** as JSON for your test pipeline

## Architecture Overview

**Two-tier web app** — React frontend + Python FastAPI backend — with three specialized LLM models via Groq:

```
Browser (React)  →  FastAPI (Python)  →  Groq API
  sessionStorage      3 routers            3 LLM models
  useSession hook     Pydantic schemas     llama / gpt / qwen
```

| Component | Technology | Purpose |
|-----------|-----------|---------|
| UI | React 19 + Vite + Tailwind | Chat, history, criteria panels |
| State | `useSession` hook + sessionStorage | FSM, runs, keyword pattern trigger |
| API | FastAPI + Pydantic | `/chat`, `/check-pattern`, `/generate-criteria` |
| LLM | Groq (llama-3.1-8b / gpt-oss-120b / qwen3-32b) | Chat · pattern detection · rubric gen |

Full architecture document with diagrams: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

## Setup

**Backend** (requires Python 3.11+):

    pip install -r requirements.txt
    cp .env.example .env   # add your GROQ_API_KEY
    uvicorn backend.main:app --reload

**Frontend** (requires Node 18+):

    cd frontend
    npm install
    npm run dev

Open http://localhost:5173.

## Running Tests

    pytest tests/ -v

## Key Design Notes

- **No database** — all state lives in `sessionStorage` for single-session use; JSON export is the durable artifact
- **Delta batching** — only new transcripts since the last check are sent to the pattern API, reducing token usage
- **Keyword pre-filter** — 38 domain keywords must appear in ≥2 runs before any API call is made
- **Three-model strategy** — cheapest model for chat, strongest for pattern analysis, reasoning model for criteria generation
