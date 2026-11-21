# Eval Discovery

Helps voice-agent builders break out of the preview run loop by generating eval criteria grounded in their own test conversations. Works with any agent: paste its system prompt, or start from a preset.

## What it does

1. **Set up an agent**: paste any agent's system prompt (up to 4000 characters) or pick a preset (a sales rep, a support agent, an appointment-booking assistant). For a pasted prompt, an LLM reads it once and builds a map of the behaviors worth testing, the role you play (prospect, customer, caller...) and three starter messages.
2. **Chat** with the agent as that role.
3. Click **End run** after each conversation to save it.
4. When you keep probing the same behavior across runs, a **loop is detected** and a pattern summary with quoted evidence is stored.
5. Click **Discover evals** to turn the pattern into a structured criterion (name, definition, good and bad example, why it was discovered).
6. **Edit** the criteria, add your own, and **Export all** as JSON for your test pipeline.

Everything is saved server-side in an anonymous workspace, so a reload or a return visit picks up where you left off.

## Architecture overview

React 19 + Vite frontend, FastAPI backend, SQLAlchemy 2 on Postgres (SQLite locally), LLM calls through Groq.

```
Browser (React)  ->  FastAPI  ->  Postgres / SQLite
  workspace token      api/        workspace, agent, prompt_version,
  in localStorage      core/       behavior_map, run, criterion, event
                       db/
                          ->  Groq API (chat, prompt analysis, pattern, criteria)
```

| Part | Where | Notes |
|------|-------|-------|
| API routers | `backend/api/` | workspaces, agents (presets, create, state, chat), runs (save, check-pattern), criteria (discover, add, edit) |
| Pure logic | `backend/core/` | agent config and presets, prompt analysis, pattern detection, criterion generation, prompt builders; no web or DB imports |
| Persistence | `backend/db/`, `migrations/` | SQLAlchemy models, Alembic migration `0001_foundation` |
| Frontend | `frontend/src/` | `useSession` hook hydrates from the server; `AgentSetup` screen; chat, history and criteria panels |

Full document with diagrams: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

## Setup

**Backend** (Python 3.11+):

    pip install -r requirements.txt
    cp .env.example .env          # set GROQ_API_KEY (and ALLOWED_ORIGINS if needed)
    alembic upgrade head          # creates the tables
    uvicorn backend.main:app --reload

`.env` only supplies `GROQ_API_KEY` and `ALLOWED_ORIGINS`. `DATABASE_URL` is read from the process environment, **not** from `.env` (a value there is silently ignored). Unset, the backend uses SQLite at `./dev.db`. To use Postgres instead, run `docker compose up -d`, then export the variable in the same shell before `alembic upgrade head` and `uvicorn`:

    export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/eval_discovery    # Git Bash / bash
    $env:DATABASE_URL="postgresql://postgres:postgres@localhost:5432/eval_discovery"    # PowerShell

(The URL matches `docker-compose.yml` and the commented example in `.env.example`.)

**Frontend** (Node 18+):

    cd frontend
    npm install
    npm run dev

Open http://localhost:5173. The frontend talks to `http://localhost:8000` unless `VITE_API_URL` is set at build time.

## Tests

    python -m pytest tests -q          # backend (SQLite, Groq is mocked)
    cd frontend && npm test            # frontend (Vitest)
    cd frontend && npm run lint && npm run build

## Key design notes

- **Anonymous workspaces.** The first API call creates a workspace and returns a random token that the browser keeps in `localStorage`. Only its SHA-256 hash is stored. There are no accounts.
- **30-day expiry.** A workspace and everything under it expires after 30 days and is purged when the backend starts. `DELETE /workspaces/me` deletes it immediately.
- **Behavior map instead of hardcoded keywords.** Each agent has a stored map of behaviors with trigger terms (presets ship theirs; pasted prompts get one from the analysis call). The pattern pre-filter checks those terms in user messages and only asks the LLM when a term appears in at least 2 new runs.
- **Delta batching on the server.** Each agent remembers the last run it checked; a check only sends the runs saved since then. `check_due` in the save-run response tells the UI when to run it.
- **Pasted text is data.** Agent prompts, transcripts and pattern summaries are wrapped in delimiter tags, and the system text says tagged content is data, not instructions. Delimiter tags inside pasted text are neutralized.
- **Limits.** 5 agents and 50 runs per workspace, 4000-character prompts, 2000-character messages, at most 40 messages per request, and 30 LLM-backed calls per minute per client IP (in-memory, per process).
- **Events.** A small product event log (`agent_created`, `run_saved`, `loop_detected`, `criterion_created`) is written best-effort: a failed write never fails a request.
- **Models.** `openai/gpt-oss-20b` plays the agent, `openai/gpt-oss-120b` analyzes prompts and detects patterns, `qwen/qwen3.8-27b` writes criteria.

## Deploy

**Not yet verified on a live Postgres.** The migration and models were checked offline against the Postgres dialect only (generated SQL reviewed, nothing executed). The first thing to check after deploying is that `alembic upgrade head` succeeds, then `POST /workspaces` and `GET /presets` respond.

Backend on Render (web service):

1. Add a Postgres database. Check the provider's current free-tier terms first (expiry, size and connection limits change).
2. Set environment variables: `DATABASE_URL` (the instance's connection string; `postgres://` and `postgresql://` both work), `ALLOWED_ORIGINS` (your frontend origin, comma-separated for several), `GROQ_API_KEY`.
3. Paste this into the service's **Start Command** setting in the Render dashboard. Render ignores `Procfile` (it is there for Procfile-based hosts), so the command has to be set there:

       alembic upgrade head && uvicorn backend.main:app --host 0.0.0.0 --port $PORT

   Do not add `--proxy-headers --forwarded-allow-ips="*"`: with `*` uvicorn takes the left-most `X-Forwarded-For` entry, which the client controls, so anyone could forge a fresh IP per request and dodge the rate limiter. The app reads `X-Forwarded-For` itself instead (`backend/api/limits.py`): it uses the Nth entry from the right, where N is `TRUSTED_PROXY_HOPS` (default `1`, meaning one trusted proxy in front; `0` ignores the header and uses the direct peer address). A missing or too-short header falls back to the peer address.

   Render's real header chain has not been observed, so verify it after deploying. Send `POST /workspaces` 31 times, each with a different left-most forged entry, and expect a 429 by the 21st (that route allows 20 per minute per client):

       for i in $(seq 31); do curl -s -o /dev/null -w "%{http_code} " -X POST -H "X-Forwarded-For: 10.0.0.$i" https://your-backend.onrender.com/workspaces; done

   Seeing 429 means a forged header does not buy a fresh key. If it never returns 429, the header chain is longer than assumed: raise `TRUSTED_PROXY_HOPS`. If ordinary users start getting 429 together, everyone is sharing one key: lower it (or set `0` if the proxy adds no usable header). Each LLM route allows 30 calls per minute per client plus a process-wide `LLM_GLOBAL_PER_MINUTE` budget (default 600).

Frontend (Cloudflare Worker with static assets): rebuild with the backend URL baked in, then deploy.

    cd frontend
    VITE_API_URL=https://your-backend.onrender.com npm run build

## Roadmap

Later phases (saved tests, replay, judging, suggested prompt fixes) are described in [docs/superpowers/specs/2026-11-21-agent-ready-eval-flywheel-design.md](docs/superpowers/specs/2026-11-21-agent-ready-eval-flywheel-design.md). They are not implemented yet.
