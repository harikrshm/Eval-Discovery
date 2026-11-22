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
8. **Let the evaluator fix the prompt** (Phase 3, see [below](#phase-3-evolve)): from a failing test the app suggests a prompt change and explains why, you read the diff and approve, and the change goes live only after it passes the fix test, a held-out test and every previously passing test. Any version can be restored, and a chip says how many criteria pass.

Everything is saved server-side in an anonymous workspace, so a reload or a return visit picks up where you left off.

## Architecture overview

React 19 + Vite frontend, FastAPI backend, SQLAlchemy 2 on Postgres (SQLite locally), LLM calls through Groq.

```
Browser (React)  ->  FastAPI  ->  Postgres / SQLite
  workspace token      api/        workspace, agent, prompt_version,
  in localStorage      core/       behavior_map, run, criterion, event
                       db/
                       jobs/    (worker loop, same process)
                          ->  Groq API (chat, prompt analysis, pattern, criteria, judge,
                                        suggested fix, generated test, change report)
```

| Part | Where | Notes |
|------|-------|-------|
| API routers | `backend/api/` | workspaces, agents (presets, create, state, chat), runs (save, check-pattern), criteria (discover, add, edit), prompt editor, tests (suite, replay, run-all, build-tests, suggest-fix, generate-heldout, job status), prompt versions (list, detail, approve, reject, restore, report) |
| Pure logic | `backend/core/` | agent config and presets, prompt analysis, pattern detection, criterion generation, judge, replay, test-suite rules, suggester, held-out scenario generator, change-report writer, the promotion rule, prompt builders; no web, DB or jobs imports |
| Job queue | `backend/jobs/` | database-backed queue, handlers (`build_tests`, `replay`, `suggest_fix`, `generate_heldout`, `change_report`) and the worker loop started in the app lifespan; the loop also resolves verifying candidates |
| Persistence | `backend/db/`, `migrations/` | SQLAlchemy models, candidate and reconcile logic (`candidates.py`, `reconcile.py`), Alembic migrations `0001_foundation`, `0002_tests_and_jobs` and `0003_evolve` |
| Frontend | `frontend/src/` | `useSession` hook hydrates from the server; `useTests` hook polls jobs; `useReport` and `useVersions` load a change report and the version list; `AgentSetup` screen; chat, history and criteria panels (with the prompt editor, the Tests section, the change review, version history and the readiness chip) |

Full document with diagrams: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

## Setup

**Backend** (Python 3.11+):

    pip install -r requirements.txt
    cp .env.example .env          # set GROQ_API_KEY (and ALLOWED_ORIGINS if needed)
    alembic upgrade head          # creates the tables
    uvicorn backend.main:app --reload

After pulling Phase 2, run `alembic upgrade head` **before** starting the backend: migration `0002` adds the job and test tables, and without it the worker loop logs an error every second. The same goes for Phase 3 and migration `0003` (see [Phase 3: evolve](#phase-3-evolve)).

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
2. **Edit the prompt** in the Eval criteria panel. Saving creates a new numbered prompt version (v2, v3, ...) that chat uses immediately. At most 20 versions per agent in Phase 2; since Phase 3 the cap counts every version except rejected and failed-verification ones, plus a hard cap of 60 version rows.
3. A banner says "N tests haven't run on prompt v{x} yet." (no replay on that version, or the criterion's definition or examples changed since). Editing only a criterion's name does not invalidate results.
4. **Run all tests** replays each test that has no current replay (since Phase 3 it no longer reruns tests that already have one) with its saved user messages (the first 20 of them) against the current prompt, then the judge returns **pass**, **fail** or **not applicable** with a rationale. A single test can also be replayed on its own.
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

**Deferred after Phase 2:** suggested prompt fixes, a diff and change report, a held-out/regression promotion gate, prompt version history and restore, and an aggregate readiness chip (all now in [Phase 3](#phase-3-evolve)); picking a specific run for a manual criterion, the export endpoint and CI gate, event-derived metrics and the simulation script, deleting a single agent, and sliding expiry for workspace tokens (still deferred, see Phase 3's list).

## Phase 3: evolve

Phase 3 closes the loop the project exists to show: an evaluator-driven prompt fix with a human gate. From a failing test the app proposes a prompt edit, you approve it, and it goes live only if it passes tests it was not tuned on.

1. **Generate a second test.** A criterion with a fix test and no held-out test offers **Generate a second test**. An LLM writes a different customer scenario (2 to 5 messages) for the same criterion; the scenario is replayed on the current prompt and kept **only if it fails there**. Up to 3 attempts. If none fails, the UI says "Couldn't write a test your current prompt fails, so it may already handle this case." The kept test is the held-out test and shows as failing at once.
2. **Suggest fix.** A failing **fix** test offers **Suggest fix** (a held-out test never does). The suggester sees the current prompt, the criterion, and the failing result's rationale and transcript; it never sees the held-out test. It returns a complete replacement prompt (at most 4000 characters), stored as a **proposed** version. Chat keeps using the current prompt.
3. **Read the diff and "why this change".** The change review shows a line diff of the current prompt against the candidate (changed lines are marked in text, not only by colour), the criterion, the failing turn **quoted by code** from the stored transcript (the model only returns a turn index, so it cannot invent the quote), a plain-words summary, which part of the prompt changed, the expected effect and the risk to other behaviors.
4. **Approve and verify.** Approving does not make the candidate live: chat keeps the current prompt while the candidate shows as verifying. You can also **Reject** a proposal.
5. **Verification.** The server replays the candidate against the **fix test**, the **held-out test** (if any) and **every previously passing test** (those whose current replay passed). Each is a background replay job, and the UI shows progress.
6. **Promotion is code, not an LLM.** The candidate is promoted if and only if the fix test passes, the held-out test passes when there is one, and no previously passing test fails. A `not_applicable` verdict counts as not passing for the fix and held-out tests and is ignored for the regression tests. The verification strength is **full** with a held-out test and **weaker** without one, and the UI says which. Otherwise the version ends as **failed verification** with the reason ("The fix test did not pass.", "The held-out test did not pass." or "A previously passing test now fails.").
7. **What happened.** A before-and-after table and the status come from stored data (verdicts, rationales and transcripts are snapshotted into the report). A model then writes a short explanation and a next step as a background job; it is only an explanation, and its citations are checked against the table's test ids (unknown ones are dropped). The model cannot change a verdict or the status. If it fails, the table and the diff still show, marked "explanation unavailable".
8. **Version history and restore.** The history lists every version with its origin, status and strength. **Restore** creates a **new** version (origin `restored`, next number, live at once) with the old text. It is allowed from a promoted, superseded or failed-verification version and is refused while a fix is being verified.
9. **Readiness chip.** "N of M criteria passing", where M counts the criteria that have at least one test (criteria without tests are listed separately; with none tested yet it reads "No criteria have tests yet"). "All criteria passing" is the signal that testing is done for your saved tests. The live region is in the app header; the criteria panel shows the same text without a second announcement.

**Rules while a fix verifies.** An agent has at most one open candidate (proposed or verifying). While one is being verified, a manual prompt edit, a restore and edits to a criterion's definition, good example or bad example are refused with 409 "A fix is being verified. Wait for it to finish." (a criterion's name can still be edited), and approving another version while one verifies gets the same message. **Suggest fix** is refused with 409 "A fix is already proposed or being verified." whenever any candidate is open, including one that is merely proposed. A second approval of the same version gets 409 "This fix can't be approved in its current state." because it is no longer proposed. Saving in the prompt editor sends the version the text was written against; if the prompt moved on, the save is refused with 409 "The prompt changed to v{n} while you were editing. Reload the prompt to continue.", and the editor offers to discard the stale edit. A manual edit, a restore or a promotion marks a proposed version **superseded**.

**Limits.** At most 20 prompt versions per agent, not counting rejected and failed-verification ones (and 60 version rows in all); beyond that a manual edit or restore gets a 429 "Prompt version limit reached for this agent.", and a suggestion that hits the cap fails its job with the same text. The replay jobs queued by **Approve** and the explanation job queued when verification ends do **not** count toward the 30 jobs per workspace per hour, because they are bounded and queued by the system. The 100-job queue cap still applies to Approve (429 "The test queue is busy. Try again in a minute." before anything changes). **Suggest fix** and **Generate a second test** count toward both the hourly cap and the queue cap, like Run all.

**A restore is refused for a suggestion that was never approved** (for example a proposal a manual edit superseded): its text was never verified, so the history labels it "never approved" and offers no Restore. Approve is refused (409 "That fix is based on an older prompt. Reject it and suggest a new fix.") when the prompt moved on after the fix was written, and a candidate whose prompt changed during verification fails ("The prompt changed during verification.") instead of replacing the newer prompt. The before column of the report is read from stored results ("Not run" when there is none), never assumed. While a fix verifies, the verifying view refreshes itself every 4 seconds.

**Verification ends by itself while the worker runs.** A candidate resolves to promoted or failed verification even if the worker restarts or a replay job fails, is discarded or is lost, because a reconcile step runs at the end of every worker pass (with the worker disabled, `WORKER_ENABLED=0`, nothing resolves it). A replay job that fails fails the candidate ("A test could not be run: ..."), a criterion edited mid-run fails it ("A criterion changed during verification."), data that cannot be read fails it ("Verification could not be completed."), and, if results are still missing 30 minutes after approval while no replay job for them is queued or running (none failed or was discarded), it fails ("Verification timed out."). A verification replay that fails for a transient reason (a rate limit, a timeout) is retried by the worker up to 3 attempts before it fails the candidate; a replay you start yourself fails at once and you can press Replay again. A replay that is still queued or running keeps the candidate waiting; a stuck job ends only through the worker's stale-job recovery (180 s, 3 attempts). See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the state machine and the sequence.

**Operational notes.**

- After pulling Phase 3, run `alembic upgrade head` **before** starting the backend: migration `0003` adds columns to `prompt_version`, `test_case` and `criterion` and a unique `(agent_id, number)` constraint. The Render start command already runs it.
- Deploy the backend before the frontend (a new frontend against an old backend has no candidate or version routes to call).
- Phase 3 builds on the unmerged Phase 2 branch, so merge Phase 2 first (or include it in the Phase 3 pull request).
- No new environment variables. Still one backend process per deployment.
- **Groq free tier.** The free tier limits the 120B model (`gpt-oss-120b`, used by the judge, suggester and generator) to 8000 tokens per minute. One build or a verification fan-out can hit it; the client retries 5 times, and a job that still fails reports "The judge was unavailable. Try again." (verification replays are retried by the worker first). Consider a paid tier, or spacing your jobs, if you see this.

**What was verified, and what was not.** Migration `0003` was applied to a throwaway real Postgres on top of populated Phase 2 rows: the new columns backfilled their defaults, Postgres enforced the unique `(agent_id, number)` constraint, deleting a workspace cascaded cleanly, and downgrade then upgrade round-tripped. A race test ran against a real Postgres with a live backend: 15 rounds each of two simultaneous approvals, approve against a manual prompt edit, approve against reject, and approve against restore. Exactly one request won every time, and the invariants held (at most one version verifying, exactly one promoted, the agent pointing at it). An end-to-end run with real Groq on a local Postgres completed the loop (held-out generated, fix suggested, approved, verified, promoted, explained, restored); one run is not evidence of general quality. **Not yet verified:** a real Render deploy, the quality of the real judge and suggester across many agents (every LLM call is mocked in the tests), and several worker processes (still unsupported).

**Limitations.** A generated held-out scenario can be a light paraphrase of the fix test: the copy check only rejects a scenario whose whole message list repeats the fix test's. The judge sanity check is still weak by construction (see Phase 2). A suggested fix can fail verification, and that is a valid outcome. `GET /agents/{id}/tests` makes many small queries, which is fine at tens of tests.

**Deferred to Phase 4:** the CI eval gate and the export endpoint, a simulated before/after comparison, event-derived metrics, picking a saved run or writing a held-out test by hand, editing a candidate by hand (reject it and use the prompt editor), deleting a single agent, sliding expiry for workspace tokens, a 401 mid-poll that can create an empty workspace, and `passive_deletes` on the new relationships.

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
- **Limits.** 5 agents and 50 runs per workspace, 4000-character prompts, 2000-character messages, at most 40 messages per request (a saved run may hold one more, the reply to the last message), 30 criteria and 20 prompt versions per agent (rejected and failed-verification candidates do not count; 60 version rows in all), 30 background jobs per workspace per hour (the replays queued by Approve and the report job are exempt), at most 100 queued jobs across all workspaces (beyond that replay, run-all and build-tests get a 429 "The test queue is busy"), a 45 s timeout and 5 retries on each Groq call, and 30 LLM-backed calls per minute per client IP (in-memory, per process; the replay, run-all, build-tests, suggest-fix and generate-heldout routes count against it, but Approve and the LLM calls a job makes in the worker do not).
- **Events.** A small product event log (`agent_created`, `run_saved`, `loop_detected`, `criterion_created`, `test_created`, `replay_started`, `replay_passed`, `replay_failed`, and for Phase 3 `test_generated`, `fix_suggested`, `fix_approved`, `fix_rejected`, `fix_verified`, `fix_verification_failed`, `fix_promoted`, `version_restored`, `report_viewed`) is written best-effort: a failed write never fails a request.
- **Models.** `openai/gpt-oss-20b` plays the agent (and runs replays), `openai/gpt-oss-120b` analyzes prompts, detects patterns, judges, suggests fixes (temperature 0.2), generates held-out scenarios (0.7) and writes the change report (0), `qwen/qwen3.8-27b` writes criteria.

## Deploy

**Not yet deployed.** Migrations `0002` and `0003`, the job queue and the approval races were exercised on a throwaway real Postgres (see [Phase 2: tests](#phase-2-tests) and [Phase 3: evolve](#phase-3-evolve)), but nothing has run on Render. The first thing to check after deploying is that `alembic upgrade head` succeeds, then `POST /workspaces` and `GET /presets` respond. Run one backend process only (no `--workers` above 1; the start command below uses one). On Groq's free tier the 120B model is limited to 8000 tokens per minute, so one build or a verification fan-out can hit the limit (jobs then fail with "The judge was unavailable. Try again." or are retried); consider a paid tier or spacing your jobs.

Backend on Render (web service):

1. Add a Postgres database. Check the provider's current free-tier terms first (expiry, size and connection limits change).
2. Set environment variables: `DATABASE_URL` (the instance's connection string; `postgres://` and `postgresql://` both work), `ALLOWED_ORIGINS` (your frontend origin, comma-separated for several), `GROQ_API_KEY`.
3. Paste this into the service's **Start Command** setting in the Render dashboard. Render ignores `Procfile` (it is there for Procfile-based hosts), so the command has to be set there:

       alembic upgrade head && uvicorn backend.main:app --host 0.0.0.0 --port $PORT

   Do not add `--proxy-headers --forwarded-allow-ips="*"`: with `*` uvicorn takes the left-most `X-Forwarded-For` entry, which the client controls, so anyone could forge a fresh IP per request and dodge the rate limiter. The app reads `X-Forwarded-For` itself instead (`backend/api/limits.py`): it uses the Nth entry from the right, where N is `TRUSTED_PROXY_HOPS` (default `1`, meaning one trusted proxy in front; `0` ignores the header and uses the direct peer address). A missing or too-short header falls back to the peer address.

   Render's real header chain has not been observed, so verify it after deploying. Send `POST /workspaces` 31 times, each with a different left-most forged entry, and expect a 429 by the 21st (that route allows 20 per minute per client):

       for i in $(seq 31); do curl -s -o /dev/null -w "%{http_code} " -X POST -H "X-Forwarded-For: 10.0.0.$i" https://your-backend.onrender.com/workspaces; done

   Seeing 429 means a forged header does not buy a fresh key. If it never returns 429, the header chain is longer than assumed: raise `TRUSTED_PROXY_HOPS`. If ordinary users start getting 429 together, everyone is sharing one key: lower it (or set `0` if the proxy adds no usable header). Each LLM route allows 30 calls per minute per client plus a process-wide `LLM_GLOBAL_PER_MINUTE` budget (default 600).

Frontend (Cloudflare Worker with static assets): deploy the backend first, and let its start command run `alembic upgrade head` (which applies `0003`), before rebuilding the frontend. A new frontend against an old backend shows "Chat uses prompt vundefined" and no tests, and has no fix, version or report routes to call. Rebuild with the backend URL baked in, then deploy.

    cd frontend
    VITE_API_URL=https://your-backend.onrender.com npm run build

## Roadmap

Phases 1 (persistence, any agent), 2 (tests, replay, judging, manual prompt editor) and 3 (suggested fixes, diff and change report, held-out and regression promotion gate, version history and restore, readiness chip) are implemented. Phase 3 builds on the unmerged Phase 2 branch, so merge Phase 2 first. The rest of the plan (export and CI gate, simulated before/after comparison, event-derived metrics) is described in [docs/superpowers/specs/2026-11-21-agent-ready-eval-flywheel-design.md](docs/superpowers/specs/2026-11-21-agent-ready-eval-flywheel-design.md) and the Phase 3 addendum [docs/superpowers/specs/2026-11-21-phase3-evolve-design.md](docs/superpowers/specs/2026-11-21-phase3-evolve-design.md), and is not implemented yet.
