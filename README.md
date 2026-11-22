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
9. **Take the suite to CI** (Phase 4, see [below](#phase-4-ship)): export the saved tests, and a command-line gate replays them against your prompt file in your own pipeline and fails the check if a behavior you had fixed regresses.

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

Outside the app (Phase 4):
  GET /agents/{id}/export  ->  eval-suite.json  ->  evalgate (CI gate)  ->  Groq API
  simulation/ drives the same app in-process (no server) to compare two arms
  scripts/metrics.py reads the event table
```

| Part | Where | Notes |
|------|-------|-------|
| API routers | `backend/api/` | workspaces, agents (presets, create, state, chat), runs (save, check-pattern), criteria (discover, add, edit), prompt editor, tests (suite, replay, run-all, build-tests, suggest-fix, generate-heldout, job status), prompt versions (list, detail, approve, reject, restore, report), export (`GET /agents/{id}/export`, Phase 4) |
| Pure logic | `backend/core/` | agent config and presets, prompt analysis, pattern detection, criterion generation, judge, replay, test-suite rules, suggester, held-out scenario generator, change-report writer, the promotion rule, prompt builders; no web, DB or jobs imports |
| Job queue | `backend/jobs/` | database-backed queue, handlers (`build_tests`, `replay`, `suggest_fix`, `generate_heldout`, `change_report`) and the worker loop started in the app lifespan; the loop also resolves verifying candidates |
| Persistence | `backend/db/`, `migrations/` | SQLAlchemy models, candidate and reconcile logic (`candidates.py`, `reconcile.py`), Alembic migrations `0001_foundation`, `0002_tests_and_jobs` and `0003_evolve` (Phase 4 adds none) |
| Frontend | `frontend/src/` | `useSession` hook hydrates from the server; `useTests` hook polls jobs; `useReport` and `useVersions` load a change report and the version list; `AgentSetup` screen; chat, history and criteria panels (with the prompt editor, the Tests section, the change review, version history and the readiness chip; Phase 4 adds the Export suite for CI button) |
| CI gate | `evalgate/`, `.github/actions/eval-gate/`, `examples/eval-gate-demo/` | Phase 4: the `evalgate` command, a composite GitHub Action and a worked example; imports `backend.core` and `backend.groq_client`, never FastAPI or SQLAlchemy |
| Simulation | `simulation/` | Phase 4: a fake model client, two scenarios, a synthetic builder, an independent reference judge, an in-process driver of the real app, the two arms, a resumable runner and a results-page generator |
| Scripts | `scripts/metrics.py` | Phase 4: product metrics derived from the event table |

Full document with diagrams: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

## Setup

**Backend** (Python 3.11+):

    pip install -r requirements.txt
    cp .env.example .env          # set GROQ_API_KEY (and ALLOWED_ORIGINS if needed)
    alembic upgrade head          # creates the tables
    uvicorn backend.main:app --reload

After pulling Phase 2, run `alembic upgrade head` **before** starting the backend: migration `0002` adds the job and test tables, and without it the worker loop logs an error every second. The same goes for Phase 3 and migration `0003` (see [Phase 3: evolve](#phase-3-evolve)).

`.env` only supplies `GROQ_API_KEY`, `ALLOWED_ORIGINS`, `WORKER_ENABLED` and the optional `GROQ_TPM_LIMIT` (see [Phase 4: ship](#phase-4-ship)). `GROQ_SKIP_DOTENV=1` makes the backend skip reading `.env` entirely, so it has to be set in the process environment, not inside `.env`. `DATABASE_URL` is read from the process environment, **not** from `.env` (a value there is silently ignored). Unset, the backend uses SQLite at `./dev.db`. To use Postgres instead, run `docker compose up -d`, then export the variable in the same shell before `alembic upgrade head` and `uvicorn`:

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

**Deferred after Phase 2:** suggested prompt fixes, a diff and change report, a held-out/regression promotion gate, prompt version history and restore, and an aggregate readiness chip (all now in [Phase 3](#phase-3-evolve)); the export endpoint and CI gate, event-derived metrics and the simulation script (all now in [Phase 4](#phase-4-ship)); picking a specific run for a manual criterion, deleting a single agent, and sliding expiry for workspace tokens (still deferred, see Phase 4's list).

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
- **Groq free tier.** The free tier limits the 120B model (`gpt-oss-120b`, used by the judge, suggester and generator) to 8000 tokens per minute. One build or a verification fan-out can hit it; the client retries 5 times in a job (2 on a request route, since Phase 4), and a job that still fails reports "The judge was unavailable. Try again." (verification replays are retried by the worker first). Consider a paid tier, or spacing your jobs, if you see this. Since Phase 4 you can also set `GROQ_TPM_LIMIT` to make the client pace itself (see [Phase 4: ship](#phase-4-ship)).

**What was verified, and what was not.** Migration `0003` was applied to a throwaway real Postgres on top of populated Phase 2 rows: the new columns backfilled their defaults, Postgres enforced the unique `(agent_id, number)` constraint, deleting a workspace cascaded cleanly, and downgrade then upgrade round-tripped. A race test ran against a real Postgres with a live backend: 15 rounds each of two simultaneous approvals, approve against a manual prompt edit, approve against reject, and approve against restore. Exactly one request won every time, and the invariants held (at most one version verifying, exactly one promoted, the agent pointing at it). An end-to-end run with real Groq on a local Postgres completed the loop (held-out generated, fix suggested, approved, verified, promoted, explained, restored); one run is not evidence of general quality. **Not yet verified:** a real Render deploy, the quality of the real judge and suggester across many agents (every LLM call is mocked in the tests), and several worker processes (still unsupported).

**Limitations.** A generated held-out scenario can be a light paraphrase of the fix test: the copy check only rejects a scenario whose whole message list repeats the fix test's. The judge sanity check is still weak by construction (see Phase 2). A suggested fix can fail verification, and that is a valid outcome. `GET /agents/{id}/tests` makes many small queries, which is fine at tens of tests.

**Deferred to Phase 4:** the CI eval gate and the export endpoint, a simulated before/after comparison and event-derived metrics (the gate, export, simulation harness and metrics script are built, see [Phase 4: ship](#phase-4-ship); the first real simulation results are on [docs/simulation-results.md](docs/simulation-results.md) and did not show the loop reducing preview runs). The rest of that list is still deferred, see the end of Phase 4.

## Phase 4: ship

Phase 4 makes the loop portable and measurable: the saved suite leaves the app and gates prompt changes in your own repository, a simulation compares the loop with a manual baseline, and the Phase 3 carry-overs that bear on cost and hangs are fixed. Phase 4 adds no migration and no required environment variable (`GROQ_TPM_LIMIT` and `GROQ_SKIP_DOTENV` are optional).

### Export the suite

**Export suite for CI** (a button in the Eval criteria panel, below **Export all**, which is unchanged) downloads `eval-suite.json` from `GET /agents/{id}/export`. The file has the format `eval-discovery.suite`, version `1`: the agent's name and role label, the live prompt's text and version number, and per criterion its name, definition, good and bad example and its tests (`id`, `role`, `origin`, `scripted_user_messages` and `current_verdict`). `current_verdict` is the verdict of the test's latest non-stale replay on the live prompt (`pass`, `fail`, `not_applicable`), or `null` when there is none. The export has no results, transcripts, runs, events or workspace data, and it is scoped to the workspace (another workspace's agent is a 404). The export contains the live prompt text and the scripted customer messages from saved runs, so check it for personal data before committing it to a repository.

### The CI gate: `evalgate`

`evalgate` replays an exported suite against a prompt file and exits non-zero when the pass rate falls below a threshold. It runs from the repository root, imports only `backend.core` and `backend.groq_client` (never FastAPI or the database), and needs `pip install -r requirements-gate.txt` (`groq` and `python-dotenv`):

    export GROQ_API_KEY=...        # PowerShell: $env:GROQ_API_KEY="..."
    python -m evalgate --suite eval-suite.json --prompt prompts/agent.md

For each selected test it replays the saved user messages against the prompt file (the agent name and role label come from the suite) and has the same judge as the app, at temperature 0, grade the transcript. The judge never sees the prompt. **The gate tests the prompt file you give it, not the prompt text stored in the export.**

| Flag | Meaning |
|------|---------|
| `--suite FILE`, `--prompt FILE` | Required. The exported suite and the agent prompt to test (at most 4000 characters). A suite may have at most 60 tests, each with 1 to 20 messages of at most 2000 characters, and a file may be at most 2,000,000 bytes |
| `--threshold FLOAT` | Minimum pass rate from 0 to 1 (default `1.0`) |
| `--all` | Also run tests whose `current_verdict` was not `pass` (see below) |
| `--repeat K` | Run each test K times (1 to 9, default 1); a test passes when a strict majority of its runs pass |
| `--pause SECONDS` | Wait between tests (default 0), a manual way to stay under a rate limit |
| `--max-tests N` | Run only the first N selected tests (a warning says how many were not run) |
| `--format text\|json\|markdown` | Output on stdout (default `text`) |
| `--load-dotenv` | Load the first `.env` found by searching upward from the working directory before the key check. Off by default: CI and tests use the environment only |

**Exit codes.**

| Code | Meaning |
|------|---------|
| `0` | The pass rate is at or above the threshold |
| `1` | The pass rate is below the threshold |
| `2` | Bad usage: invalid arguments, or a missing or invalid suite or prompt file |
| `3` | The gate could not run: `GROQ_API_KEY` is not set (or is a placeholder), a test errored (with `--repeat K`, only when every run of it errored, see below), no test was runnable, or no test was applicable |

The gate never reports success when it could not run: a missing key, a provider outage after retries, an empty selection or an unexpected exception is a `3`, which fails a CI check just like a `1`. With `--repeat K` the rule is per test: a test is `error` only when **every** run of it errored (an agent or judge failure after retries). Two passes and one error out of three runs is a pass (exit `0`); one pass and two errors is a `fail` (a strict majority of passes is needed), so an outage that hits only some runs can show up as exit `1`, a regression, rather than `3`. Use `--repeat 1` or a quiet provider window when that distinction matters. The key is read only from the `GROQ_API_KEY` environment variable and is never printed or written. When `GITHUB_STEP_SUMMARY` is set, a markdown table is appended to it.

**It is a regression gate.** By default the gate runs only tests whose exported `current_verdict` is `pass`: behaviors that worked when you exported must not start failing. Tests that were failing or never graded are skipped and the skip count is printed (if nothing is left to run, the exit code is `3`), so a first run does not fail on a criterion you never fixed. `--all` runs every test. The pass rate is passes divided by passes plus fails; `not_applicable` results are reported separately and left out of the rate.

**Flakiness.** A model's reply varies even at temperature 0, so one run of a borderline test can flip between runs. `--repeat 3` smooths this at three times the model calls. The gate has no other flakiness handling, and a pass is a signal, not proof.

### The Action and the example

`.github/actions/eval-gate/action.yml` is a composite GitHub Action around the same command (inputs `suite`, `prompt`, `groq-api-key`, and optionally `threshold`, `repeat`, `python-version` and `extra-args`). `examples/eval-gate-demo/` holds a support-agent prompt with a refund guardrail, a weakened copy without it, an exported suite, and a README that shows how to wire the Action into a repository and how to try the demo. `.github/workflows/eval-gate-demo.yml` runs the Action on pull requests that change `examples/eval-gate-demo/prompt.md` (and on manual dispatch).

**Not run on GitHub.** The Action, the demo workflow and the CI workflow below have NOT been run on GitHub from this repository's development environment. What was tested: the `evalgate` command with its model calls faked (and, separately, run locally once per example prompt against real Groq models), the structure and security properties of the YAML files (parsed in unit tests) and the example's pass and fail behaviour with faked model calls. Using the Action from another repository is documented but unverified. Before relying on it:

- **The repository owner must add the repository secret `GROQ_API_KEY`** (Settings, Secrets and variables, Actions). Without it the gate exits `3`.
- **Pull requests from forks get no secrets**, so on a fork PR the key is empty and the gate exits `3`: the check fails, which is the safe behaviour. Do not switch the trigger to `pull_request_target` to get around it.
- A first real run on GitHub may reveal problems the local tests cannot.

### Repo CI and the real-Postgres tests

`.github/workflows/ci.yml` runs on every push and pull request with read-only permissions and no secrets: a `backend` job (`python -m pytest -q`), a `postgres` job (a Postgres 16 service container, `alembic upgrade head`, then `python -m pytest tests/pg -q`) and a `frontend` job (`npm ci`, lint, test, build). No real model call is made.

`tests/pg/` is a real-Postgres suite: migrations over populated rows, the fail-fast reconcile on Postgres, and thread-based races between concurrent requests (two approvals; approve against a manual edit). It is skipped unless `TEST_DATABASE_URL` is set. To run it locally against a throwaway database:

    docker compose up -d
    docker compose exec postgres createdb -U postgres eval_test      # a database whose name contains "test"
    export TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/eval_test
    python -m pytest tests/pg -q

The URL must use the `postgresql+psycopg://` driver. **Safety rule:** the suite resets the database's `public` schema (`DROP SCHEMA public CASCADE`), so it refuses to run, and skips with a message that never shows the URL, unless the database name contains `test` or `ALLOW_PG_TEST_RESET=1` is set. Never point it at data you want to keep.

### Fail-fast verification

Verification used to wait for every replay. Since Phase 4 a candidate fails as soon as the stored verdicts decide it (`core/promotion.early_failure`): a fix or held-out verdict that is not `pass`, or any regression `fail`, is decisive even while other replays are still missing. A fix or held-out test whose own replay job failed or was discarded (with none queued or running) is decisive too ("A test could not be run: ..." for a failed job, "A criterion changed during verification." for a discarded one); a missing regression result never is. When a candidate fails, its still-queued replay jobs are cancelled (status `failed`, error "Cancelled: verification already failed.") so no model calls are spent on it. The cancellation is scoped by candidate version and test, so it would also cancel a queued replay that a user started for that same candidate version; that is acceptable and not reachable in normal use, because a verifying candidate is never the current version. A replay that is already running is left alone: the handler skips its model calls for a candidate that is no longer verifying, checked at the start and again after the model calls, and stores nothing. The failure reasons are the ones Phase 3 already had.

### Bounded retries, token pacing and `.env` handling

- **Bounded retries.** The request routes that call the model (chat, create agent, discover, check-pattern) retry at most 2 times, so a rate-limited request fails in seconds. Job handlers keep 5 retries.
- **`GROQ_TPM_LIMIT`** (unset by default, which means off) is optional client-side pacing: a sliding 60-second window per model records each call's `usage.total_tokens`, and a call waits when the window plus an estimate would pass the limit. It waits at most 20 seconds and then proceeds anyway, so it reduces rate-limit errors on the free tier but does not guarantee none. One number applies to a separate window per model; for the free tier's 8000 tokens per minute on the 120B model, `7000` leaves headroom. It is read from the environment or `.env`.
- **`GROQ_SKIP_DOTENV=1`** makes `backend.groq_client` skip `load_dotenv()`. The test suite, the gate (unless you pass `--load-dotenv`) and the simulation set it themselves, so none of them reads `.env` unless you ask. Set it in the process environment, not in `.env`.

### The simulation

`simulation/` measures one thing, in a simulation: **preview runs until the first judged pass**, with and without the flywheel. A **preview** is one conversation: a synthetic builder (a model playing a no-code newcomer, `openai/gpt-oss-20b`) writes two customer messages, each answered by the agent on its current prompt. The preview passes when an **independent reference judge** grades the transcript against a fixed per-scenario goal (for example "never promises a refund; asks for the order number and escalates") and says `pass`. That goal is not the criterion the app discovers, so the measure is not circular. A trial stops at the first pass or after 12 previews; a trial that never passes is reported as not converged, never dropped. Two scenarios ship: `support-refund` and `booking-confirm`.

- **Baseline arm.** After each failing preview the builder rewrites the prompt in one model call (`openai/gpt-oss-120b`, the app suggester's model, so the flywheel does not win by using a stronger one), standing in for the manual prompt editor. No detection, tests or fix loop.
- **Flywheel arm.** The builder previews through the real app, driven in-process (FastAPI's test client, in-memory SQLite, the worker run inline). After two saved runs it runs the app's flow: check-pattern, discover, build tests, confirm the fix test fails, an optional held-out test, suggest, approve (auto-approve stands in for the human gate), verify. It then keeps previewing on the live prompt. It gets **one attempt** at that flow per trial: if any step does not go to plan, or a promoted fix still fails the reference judge, it falls back to the same manual rewrite as the baseline.
- **Paths.** Every flywheel trial records `loop` (the first judged pass came after a promoted fix), `fallback` (after a manual rewrite) or `none`. A converged trial with path `none` is reported as "converged with no fix needed". The results page counts them, so a flywheel number that is really the fallback is visible.

Two modes:

    python -m simulation.run                                       # mock mode (the default), offline
    python -m simulation.run --mode real --trials 3                # real Groq: needs GROQ_API_KEY (or --load-dotenv)
    python -m simulation.run --mode real --trials 3 --resume       # continue a stopped run
    python -m simulation.report --results simulation/results --mode real

- **Mock mode** swaps the model client for a deterministic fake that routes on each call type's system text. It exists to test the harness; **its numbers are meaningless**, its output carries the banner "MOCK MODE: harness test only, the numbers are meaningless.", and the report generator refuses to write it to `docs/simulation-results.md`.
- **Real mode** prints a rough cost and time estimate first, paces itself with `GROQ_TPM_LIMIT` (default 7000 here), and saves every trial as it finishes under `simulation/results/<mode>/`, so a stopped or crashed run continues with `--resume`. It spends the Groq quota of whoever runs it.
- **Results page.** [docs/simulation-results.md](docs/simulation-results.md) is generated from the first real run (2026-11-22, commit `eef7fb6`, 12 trials, 3 per scenario and arm) by `python -m simulation.report`; do not edit it by hand. Every table is labelled simulated, names N, the models and the date, and says the figures are not measurements of real builders. **This run did not show the flywheel reducing preview runs.** On `support-refund` the baseline converged in 2 previews in all 3 trials and the flywheel in 3 in all 3, every flywheel trial through the fallback (the app's fix test did not fail on the current prompt in 3 replays in 2 trials, and the app built no fix test in 1), so the app-driven fix never produced a promoted fix. On `booking-confirm` the weak prompt passed the reference judge with no change in all 3 flywheel trials (1, 2 and 2 previews), so that scenario did not reliably elicit the failure it was built to; the baseline needed 2 and 1 previews and did not converge in one trial (12 previews), whose notes show harness problems (agent replies that could not be generated, prompt rewrites rejected as too long), not a verdict on the method. N = 3 cannot establish any difference, and the app-driven fix path was never exercised end to end with a real model in the simulation. The raw trial files are committed deliberately under `simulation/results/real/` (that directory is otherwise git-ignored; they were added with `git add -f`).

**Limitations of the simulation.** A model, not a human, plays the builder, so it is more patient and literal than a real newcomer. N is tiny (a handful of trials per scenario and arm), so no difference is statistically established. The baseline's rewriter is shown the full goal criterion; the flywheel's app-driven fix (check-pattern, discover, suggest, approve) never is, though its fallback rewrites are, like the baseline's (and the customer-message builder sees the goal in both arms). This favours the baseline. Both judge calls (the reference judge and the app's own) use `MODEL_JUDGE`, so they share model bias; in mock mode independence is structural only. Because of the fallback, a fallback trial starts roughly two previews behind the baseline. The app limits a workspace to 30 jobs per hour, so every trial uses a fresh workspace. Wall times mostly describe free-tier pacing.

### Metrics from events

`scripts/metrics.py` reads the `event` table (read-only; the URL comes from `--database-url` or `DATABASE_URL`, and its password is masked in output) and prints counts per workspace and in total, then rates: acceptance rate (`criterion_created` / `loop_detected`), test conversion (workspaces that have both a criterion and a test / workspaces with a criterion, computed for the total only), fix approval rate (`fix_approved` / `fix_suggested`) and verification pass rate (`fix_verified` / `fix_approved`). It also reports "runs until first pass": the `run_saved` events before the first `replay_passed` of each workspace, with the median across workspaces in the total column. A rate with a zero denominator is `n/a`; `--format json` prints the same data with the definitions. This is derived from the app's own replay verdicts, not from an independent judge, so it is not the simulation's measure.

    python scripts/metrics.py --database-url postgresql+psycopg://...        # or set DATABASE_URL to the same kind of URL

The script passes the URL straight to SQLAlchemy and does not normalise it the way the app does, so use the `postgresql+psycopg://` form (a bare `postgresql://` URL selects a driver this repository does not install).

### Small fixes

The stale-fix message now reads "That fix is based on an older prompt. Reject it and suggest a new fix."; a version whose stored `verification` is not an object no longer raises in the version list; and `npm run lint` reports zero problems (it reported three errors before).

### What was verified, and what was not

**Verified (run in the development environment):** `python -m pytest -q` passes 1404 tests with 19 skipped (the real-Postgres tests, skipped without `TEST_DATABASE_URL`); the frontend suite passes 436 tests in 25 files, `npm run lint` reports no problems and `npm run build` succeeds. The 19 Postgres tests were run twice locally against a throwaway Docker Postgres (before and after the final fix wave) and passed both times; they have never run in GitHub Actions from this environment. The mock-mode simulation was run end to end (both arms, both scenarios) and its report rendered. Every model call in the tests is faked. Two real-model runs were also made on 2026-11-22 against commit `eef7fb6`, with real Groq models and a key read from the environment. **The gate**, one run per prompt on a developer machine (not GitHub Actions; `--repeat` 1): `examples/eval-gate-demo/prompt.md` exited 0 with pass rate 1.0 (2 of 2 tests passed, 1 skipped because its verdict at export was not `pass`); `weakened-prompt.md` exited 1 with pass rate 0.5 (1 of 2: the fix test failed, the model replying "we will issue a full refund", and the held-out test passed). So the weakened prompt turned the gate red, but only one of its two tests failed, and with a real model that outcome is probabilistic. **The simulation**: 12 real trials, which did not show the flywheel reducing preview runs (see [The simulation](#the-simulation) and [docs/simulation-results.md](docs/simulation-results.md)).

**Not yet verified:** the Action and the CI and demo workflows on GitHub (including fork pull requests), the app-driven fix loop inside the simulation with a real model (it was never reached; every flywheel trial that needed a fix used the fallback), the gate on many runs (one run per prompt only), the Phase 4 verification-view changes in a browser, `scripts/metrics.py` on real usage data (it is unit tested only, and nothing is deployed), a real Render deploy, and the layout of the Export suite for CI button in a browser.

### Still deferred

A 401 mid-poll that can create an empty workspace, sliding expiry for workspace tokens, deleting a single agent, `passive_deletes` on the new relationships, picking a saved run for a manual criterion or writing a held-out test by hand, editing a candidate by hand (reject it and use the prompt editor), candidate replay jobs shown under per-test keys, queue fairness of the exempt verification fan-out, the UI poll horizon versus the server timeout, and the suite endpoint's many small queries.

## Tests

    python -m pytest -q                # backend (SQLite, Groq is mocked; tests/pg skips without TEST_DATABASE_URL)
    cd frontend && npm test            # frontend (Vitest)
    cd frontend && npm run lint && npm run build

## Key design notes

- **Anonymous workspaces.** The first API call creates a workspace and returns a random token that the browser keeps in `localStorage`. Only its SHA-256 hash is stored. There are no accounts.
- **30-day expiry.** A workspace and everything under it expires after 30 days and is purged when the backend starts. `DELETE /workspaces/me` deletes it immediately.
- **Behavior map instead of hardcoded keywords.** Each agent has a stored map of behaviors with trigger terms (presets ship theirs; pasted prompts get one from the analysis call). The pattern pre-filter checks those terms in user messages and only asks the LLM when a term appears in at least 2 new runs.
- **Delta batching on the server.** Each agent remembers the last run it checked; a check only sends the runs saved since then. `check_due` in the save-run response tells the UI when to run it.
- **Pasted text is data.** Agent prompts, transcripts and pattern summaries are wrapped in delimiter tags, and the system text says tagged content is data, not instructions. Delimiter tags inside pasted text are neutralized.
- **Limits.** 5 agents and 50 runs per workspace, 4000-character prompts, 2000-character messages, at most 40 messages per request (a saved run may hold one more, the reply to the last message), 30 criteria and 20 prompt versions per agent (rejected and failed-verification candidates do not count; 60 version rows in all), 30 background jobs per workspace per hour (the replays queued by Approve and the report job are exempt), at most 100 queued jobs across all workspaces (beyond that replay, run-all and build-tests get a 429 "The test queue is busy"), a 45 s timeout on each Groq call with 5 retries in a job and 2 on a request route, and 30 LLM-backed calls per minute per client IP (in-memory, per process; the replay, run-all, build-tests, suggest-fix and generate-heldout routes count against it, but Approve and the LLM calls a job makes in the worker do not).
- **Events.** A small product event log (`agent_created`, `run_saved`, `loop_detected`, `criterion_created`, `test_created`, `replay_started`, `replay_passed`, `replay_failed`, and for Phase 3 `test_generated`, `fix_suggested`, `fix_approved`, `fix_rejected`, `fix_verified`, `fix_verification_failed`, `fix_promoted`, `version_restored`, `report_viewed`) is written best-effort: a failed write never fails a request.
- **Models.** `openai/gpt-oss-20b` plays the agent (and runs replays), `openai/gpt-oss-120b` analyzes prompts, detects patterns, judges, suggests fixes (temperature 0.2), generates held-out scenarios (0.7) and writes the change report (0), `qwen/qwen3.8-27b` writes criteria.

## Deploy

**Not yet deployed.** Migrations `0002` and `0003`, the job queue and the approval races were exercised on a throwaway real Postgres (see [Phase 2: tests](#phase-2-tests) and [Phase 3: evolve](#phase-3-evolve)), but nothing has run on Render. The first thing to check after deploying is that `alembic upgrade head` succeeds, then `POST /workspaces` and `GET /presets` respond. Run one backend process only (no `--workers` above 1; the start command below uses one). On Groq's free tier the 120B model is limited to 8000 tokens per minute, so one build or a verification fan-out can hit the limit (jobs then fail with "The judge was unavailable. Try again." or are retried); consider a paid tier, spacing your jobs, or setting `GROQ_TPM_LIMIT` (for example `7000`) so the client paces itself (see [Phase 4: ship](#phase-4-ship)).

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

Phases 1 (persistence, any agent), 2 (tests, replay, judging, manual prompt editor), 3 (suggested fixes, diff and change report, held-out and regression promotion gate, version history and restore, readiness chip) and 4 (suite export, the `evalgate` CI gate with a GitHub Action and example, repo CI with real-Postgres tests, fail-fast verification, bounded retries and optional token pacing, an events metrics script and the simulation harness) are implemented. Phases 2, 3 and 4 are on unmerged branches that build on each other (`feat/phase2-tests`, `feat/phase3-evolve`, `feat/phase4-ship`), so merge them in that order. The first real runs of the gate and of the simulation have been made (2026-11-22); the simulation did not show the loop reducing preview runs, see [The simulation](#the-simulation). Next, without promising results: scenarios that fail more reliably on the weak prompt, a fix-test confirmation step that is robust when the app's fix test does not fail on the current prompt or is not built, fixes for the harness failures seen in the booking-confirm baseline, and a larger N. Not done yet: the first run of the Action and the workflows on GitHub. The deferred items are listed at the end of [Phase 4: ship](#phase-4-ship). The design is in [docs/superpowers/specs/2026-11-21-agent-ready-eval-flywheel-design.md](docs/superpowers/specs/2026-11-21-agent-ready-eval-flywheel-design.md), the Phase 3 addendum [docs/superpowers/specs/2026-11-21-phase3-evolve-design.md](docs/superpowers/specs/2026-11-21-phase3-evolve-design.md) and the Phase 4 addendum [docs/superpowers/specs/2026-11-22-phase4-ship-design.md](docs/superpowers/specs/2026-11-22-phase4-ship-design.md).
