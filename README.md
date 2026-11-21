# Eval Discovery

Helps voice-agent builders break out of the preview run loop by generating eval criteria grounded in their own test conversations. Works with any agent: paste its system prompt, or start from a preset.

## What it does

1. **Set up an agent**: paste any agent's system prompt (up to 4000 characters) or pick a preset (a sales rep, a support agent, an appointment-booking assistant). For a pasted prompt, an LLM reads it once and builds a map of the behaviors worth testing, the role you play (prospect, customer, caller...) and three starter messages.
2. **Chat** with the agent as that role.
3. Click **End run** after each conversation to save it.
4. When you keep probing the same behavior across runs, a **loop is detected** and a pattern summary with quoted evidence is stored.
5. Click **Discover evals** to turn the pattern into a structured criterion (name, definition, good and bad example, why it was discovered).
6. **Edit** the criteria, add your own, and **Export all** as JSON for your test pipeline.
7. **Test your fix** (Phase 2, see [below](#phase-2-tests)): discovered criteria become replayable tests, you edit the agent's prompt, and **Run all tests** shows whether the agent now passes.

Everything is saved server-side in an anonymous workspace, so a reload or a return visit picks up where you left off.

## Architecture overview

React 19 + Vite frontend, FastAPI backend, SQLAlchemy 2 on Postgres (SQLite locally), LLM calls through Groq.

```
Browser (React)  ->  FastAPI  ->  Postgres / SQLite
  workspace token      api/        workspace, agent, prompt_version,
  in localStorage      core/       behavior_map, run, criterion, event
                       db/
                       jobs/    (worker loop, same process)
                          ->  Groq API (chat, prompt analysis, pattern, criteria, judge)
```

| Part | Where | Notes |
|------|-------|-------|
| API routers | `backend/api/` | workspaces, agents (presets, create, state, chat), runs (save, check-pattern), criteria (discover, add, edit), prompt editor, tests (suite, replay, run-all, build-tests, job status) |
| Pure logic | `backend/core/` | agent config and presets, prompt analysis, pattern detection, criterion generation, judge, replay, test-suite rules, prompt builders; no web, DB or jobs imports |
| Job queue | `backend/jobs/` | database-backed queue, handlers (`build_tests`, `replay`) and the worker loop started in the app lifespan |
| Persistence | `backend/db/`, `migrations/` | SQLAlchemy models, Alembic migrations `0001_foundation` and `0002_tests_and_jobs` |
| Frontend | `frontend/src/` | `useSession` hook hydrates from the server; `useTests` hook polls jobs; `AgentSetup` screen; chat, history and criteria panels (with the prompt editor and the Tests section) |

Full document with diagrams: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

## Setup

**Backend** (Python 3.11+):

    pip install -r requirements.txt
    cp .env.example .env          # set GROQ_API_KEY (and ALLOWED_ORIGINS if needed)
    alembic upgrade head          # creates the tables
    uvicorn backend.main:app --reload

After pulling Phase 2, run `alembic upgrade head` **before** starting the backend: migration `0002` adds the job and test tables, and without it the worker loop logs an error every second.

`.env` only supplies `GROQ_API_KEY`, `ALLOWED_ORIGINS` and `WORKER_ENABLED`. `DATABASE_URL` is read from the process environment, **not** from `.env` (a value there is silently ignored). Unset, the backend uses SQLite at `./dev.db`. To use Postgres instead, run `docker compose up -d`, then export the variable in the same shell before `alembic upgrade head` and `uvicorn`:

    export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/eval_discovery    # Git Bash / bash
    $env:DATABASE_URL="postgresql://postgres:postgres@localhost:5432/eval_discovery"    # PowerShell

(The URL matches `docker-compose.yml` and the commented example in `.env.example`.)

**Frontend** (Node 18+):

    cd frontend
    npm install
    npm run dev

Open http://localhost:5173. The frontend talks to `http://localhost:8000` unless `VITE_API_URL` is set at build time.

## Phase 2: tests

Phase 2 closes the loop from a discovered criterion to a verdict on a prompt change.

1. **Discover evals** creates the criterion and queues a background job (`build_tests`). The job has a judge read your most recent saved runs (up to 8, oldest first) and stops after two failures. The first failing run becomes the **fix** test, the next failing run the **held-out** test. With only one failing run the panel says a fix would be verified more weakly; with none, no tests are made and it says there is nothing to test yet. Manual criteria use the **Build tests** button instead.
2. **Edit the prompt** in the Eval criteria panel. Saving creates a new numbered prompt version (v2, v3, ...) that chat uses immediately. At most 20 versions per agent.
3. A banner says how many tests haven't run on the current prompt version yet (no replay on it, or the criterion's definition or examples changed since). Editing only a criterion's name does not invalidate results.
4. **Run all tests** replays each test's saved user messages (the first 20 of them) against the current prompt, then the judge returns **pass**, **fail** or **not applicable** with a rationale. A single test can also be replayed on its own.
5. Each criterion shows a status: **Failing** if any test fails, else **Untested** if any test has no current replay, else **Passing** if at least one passes. The status is computed in code, not by the LLM.

**The judge.** Model `openai/gpt-oss-120b` at temperature 0. It never sees the agent's system prompt, only the criterion and the transcript, both wrapped in delimiter tags and treated as data. The first time tests are built for a criterion, a sanity check makes the judge grade the criterion's own good example (expected pass) and bad example (expected fail). If it gets either wrong the criterion shows "low confidence". Replays run on `openai/gpt-oss-20b` at temperature 0.

**Jobs.** A database-backed queue with one worker loop inside the web process. A job running longer than 180 s is treated as abandoned and requeued; a job gets at most 3 attempts. Each workspace may start at most 30 jobs per hour and at most 100 jobs may be queued across all workspaces (a replay, run-all or build beyond either gets a 429; a discover still returns the criterion, just without a job). The LLM calls a job makes are not counted by the per-IP LLM limiter; they are bounded by those two caps and a 45 s Groq timeout.

Operational notes:

- **Run exactly one backend process per deployment.** Do not start uvicorn with `--workers` above 1 or run several instances: a job can legitimately outlive the 180 s window, and a second process would requeue it and run it twice. The in-memory rate limits are also per process, so more instances would multiply them.
- A restart, or a Render free-tier spin-down, interrupts the running job. It is requeued once its lock is 180 s old, up to 3 attempts.
- `WORKER_ENABLED=0` turns the worker loop off (the API still accepts and lists jobs; nothing runs them).
- Migration `0002` must be applied first (see Setup). The Render start command already runs `alembic upgrade head`.

**What was verified, and what was not.** Migration `0002` was applied to a throwaway real Postgres on top of populated Phase 1 rows: `prompt_version.number` backfilled to 1, deleting a workspace cascades through the new tables, and downgrade then upgrade round-trips. `claim_next` was raced by 8 threads over 40 jobs with no duplicates. Not yet verified: a real deploy on Render, real Groq judge behavior at temperature 0 (every LLM call is mocked in the tests), and any multi-process behavior.

**Limitations.** The judge sanity check is weak by construction: the good and bad examples appear both inside the criterion and as the transcript, so the judge is partly graded on text it was just given. The check is not re-run after you edit a criterion's definition or examples (the old result is cleared instead). A pass is a signal, not proof. LLM output is not deterministic even at temperature 0, so a verdict can flip between runs.

**Deferred to later phases:** suggested prompt fixes, a diff and change report, a held-out/regression promotion gate, prompt version history and restore, an aggregate readiness chip, picking a specific run for a manual criterion, the export endpoint and CI gate, event-derived metrics and the simulation script, deleting a single agent, and sliding expiry for workspace tokens.

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
- **Limits.** 5 agents and 50 runs per workspace, 4000-character prompts, 2000-character messages, at most 40 messages per request (a saved run may hold one more, the reply to the last message), 30 criteria and 20 prompt versions per agent, 30 background jobs per workspace per hour, at most 100 queued jobs across all workspaces (beyond that replay, run-all and build-tests get a 429 "The test queue is busy"), a 45 s timeout on each Groq call, and 30 LLM-backed calls per minute per client IP (in-memory, per process; the replay, run-all and build-tests routes count against it, but the LLM calls a job makes in the worker do not).
- **Events.** A small product event log (`agent_created`, `run_saved`, `loop_detected`, `criterion_created`, `test_created`, `replay_started`, `replay_passed`, `replay_failed`) is written best-effort: a failed write never fails a request.
- **Models.** `openai/gpt-oss-20b` plays the agent (and runs replays), `openai/gpt-oss-120b` analyzes prompts, detects patterns and judges, `qwen/qwen3.8-27b` writes criteria.

## Deploy

**Not yet deployed.** Migration `0002` and the job queue were exercised on a throwaway real Postgres (see [Phase 2: tests](#phase-2-tests)), but nothing has run on Render. The first thing to check after deploying is that `alembic upgrade head` succeeds, then `POST /workspaces` and `GET /presets` respond. Run one backend process only (no `--workers` above 1; the start command below uses one).

Backend on Render (web service):

1. Add a Postgres database. Check the provider's current free-tier terms first (expiry, size and connection limits change).
2. Set environment variables: `DATABASE_URL` (the instance's connection string; `postgres://` and `postgresql://` both work), `ALLOWED_ORIGINS` (your frontend origin, comma-separated for several), `GROQ_API_KEY`.
3. Paste this into the service's **Start Command** setting in the Render dashboard. Render ignores `Procfile` (it is there for Procfile-based hosts), so the command has to be set there:

       alembic upgrade head && uvicorn backend.main:app --host 0.0.0.0 --port $PORT

   Do not add `--proxy-headers --forwarded-allow-ips="*"`: with `*` uvicorn takes the left-most `X-Forwarded-For` entry, which the client controls, so anyone could forge a fresh IP per request and dodge the rate limiter. The app reads `X-Forwarded-For` itself instead (`backend/api/limits.py`): it uses the Nth entry from the right, where N is `TRUSTED_PROXY_HOPS` (default `1`, meaning one trusted proxy in front; `0` ignores the header and uses the direct peer address). A missing or too-short header falls back to the peer address.

   Render's real header chain has not been observed, so verify it after deploying. Send `POST /workspaces` 31 times, each with a different left-most forged entry, and expect a 429 by the 21st (that route allows 20 per minute per client):

       for i in $(seq 31); do curl -s -o /dev/null -w "%{http_code} " -X POST -H "X-Forwarded-For: 10.0.0.$i" https://your-backend.onrender.com/workspaces; done

   Seeing 429 means a forged header does not buy a fresh key. If it never returns 429, the header chain is longer than assumed: raise `TRUSTED_PROXY_HOPS`. If ordinary users start getting 429 together, everyone is sharing one key: lower it (or set `0` if the proxy adds no usable header). Each LLM route allows 30 calls per minute per client plus a process-wide `LLM_GLOBAL_PER_MINUTE` budget (default 600).

Frontend (Cloudflare Worker with static assets): deploy the backend first, and let its start command run `alembic upgrade head`, before rebuilding the frontend. A new frontend against an old backend shows "Chat uses prompt vundefined" and no tests. Rebuild with the backend URL baked in, then deploy.

    cd frontend
    VITE_API_URL=https://your-backend.onrender.com npm run build

## Roadmap

Phases 1 (persistence, any agent) and 2 (tests, replay, judging, manual prompt editor) are implemented. The rest of the plan (suggested prompt fixes, diff and change report, promotion gate, export and CI gate) is described in [docs/superpowers/specs/2026-11-21-agent-ready-eval-flywheel-design.md](docs/superpowers/specs/2026-11-21-agent-ready-eval-flywheel-design.md) and is not implemented yet.
