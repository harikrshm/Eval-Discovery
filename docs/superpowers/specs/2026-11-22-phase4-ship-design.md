# Phase 4 "Ship" — Design

Status: draft for approval · Date: 2026-11-22 · Size: project · Builds on: the main spec `2026-11-21-agent-ready-eval-flywheel-design.md` (§9 export, §11 CI gate, §12 measurement honesty, §13 phase 4) and the Phase 3 addendum `2026-11-21-phase3-evolve-design.md`. Branch `feat/phase4-ship`, cut from the unmerged `feat/phase3-evolve` (which sits on the unmerged `feat/phase2-tests`).

This is an addendum. The main spec already fixes the intent of Phase 4 and is **not repeated here**. This document records the decisions made with the user, what the spec leaves open, and the Phase 3 carry-overs. Where the documents differ, this addendum wins for Phase 4.

## 1. Intent

Phases 1-3 built the loop: discover a criterion from a builder's repeated probing, turn it into tests, propose and verify a prompt fix behind a human gate. Phase 4 makes it **portable and measurable**, so it reads as something an FDE could hand to a customer rather than a demo:

1. **A CI eval gate**: the saved suite leaves the app and gates prompt changes in the customer's own repository.
2. **An honest simulation** of the strategy metric "preview runs until the first judged pass", with and without the flywheel, labelled simulated.
3. **A demo script and a case study** that tell the product-to-engineering story, with limits stated.
4. **The Phase 3 carry-overs** that bear on cost, hangs and CI quality.

At the end the user merges all four phases into `main` (Phase 2, then 3, then 4; see section 9).

## 2. Decisions made with the user

- **Simulation approach C:** a deterministic mock mode that runs in CI and proves the harness works, plus a small real-Groq run that produces the results table. Every table is labelled "simulated" and states N, models and date; no number is compared with the strategy doc's illustrative targets.
- **The gate is a command-line tool in this repo that reuses `backend/core/` directly** (no server, no database): it reads an exported suite file and the repo's prompt file, replays and judges each test, and exits non-zero below a threshold. It ships with a thin composite GitHub Action and a worked example folder. Calling a deployed API and a standalone marketplace Action were rejected.
- **The simulation runs in-process:** it drives the real app through FastAPI's test client on an in-memory database with the worker run inline, so detection, discover, build, suggest, approve and reconcile are exercised for real. Running against a live server was rejected.
- **Phase 3 carry-overs included** (section 6).

## 3. Goals, non-goals, anti-goals

Goals: export endpoint, `evalgate` CLI, composite Action, example repo folder, repo CI workflow, simulation harness (mock and real modes) with a generated results page, demo script, case study, carry-overs, the three lint errors fixed so CI can gate lint.

Non-goals: publishing the Action to the marketplace; an in-app metrics page; any hosting change; auth or billing; fixing Groq's limits; multi-language agents.

Anti-goals (failure even if it works): presenting simulated numbers as measured; a simulation whose success measure is the app's own discovered criterion (circular); a CI gate that silently passes when it could not run; a gate that prints or logs the API key; hiding non-converged trials.

## 4. The CI gate

**Export (`GET /agents/{id}/export`, workspace-scoped, 404 for a foreign agent).** Returns `eval-discovery.suite` version 1: `{format, version, agent: {name, user_role_label}, prompt: {text, version_number}, criteria: [{id, name, definition, good_example, bad_example, tests: [{id, role, origin, scripted_user_messages}]}]}`. No results, transcripts, runs, events or workspace data. The UI gets an "Export suite for CI" button that downloads `eval-suite.json`; the existing "Export all" button is unchanged.

**`evalgate` (`python -m evalgate` or `python -m backend.cli.evalgate`; builder's call on the package location, but it must not import FastAPI or the database layer).** Arguments: `--suite FILE`, `--prompt FILE`, `--threshold FLOAT` (default 1.0), `--format text|json|markdown`, `--repeat K` (default 1), `--pause SECONDS`, `--max-tests N`. For each test: `replay(config(prompt file), messages)` then `judge(criterion, transcript)` with the same temperature-0 judge as the app; the agent name and role label come from the suite. Pass rate = passes / (passes + fails); `not_applicable` results are reported separately and excluded from the rate. With `--repeat K > 1` a test passes when the strict majority of K runs pass (flakiness mitigation, since replies vary). It validates the suite (format, version, caps: at most 60 tests, 20 messages per test, prompt at most 4000 characters) and refuses anything else. The API key comes only from `GROQ_API_KEY` and is never printed or written.

**Exit codes** (documented and tested): `0` pass rate at or above the threshold; `1` below it; `2` usage or invalid suite/prompt; `3` the gate could not run (model unavailable after retries, missing key, every test `not_applicable`). A gate that could not run never reports success. Output: one line per test, a summary, and, when `GITHUB_STEP_SUMMARY` is set, a markdown table appended to it.

**Action and example.** `.github/actions/eval-gate/action.yml` (composite: Python setup, `pip install -r requirements-gate.txt`, run `evalgate` with inputs `suite`, `prompt`, `threshold`, secret `groq-api-key`). `examples/eval-gate-demo/` holds a prompt, an exported suite, and a README showing a prompt edit that removes a guardrail failing the gate. `.github/workflows/eval-gate-demo.yml` runs it on pull requests that touch the example prompt (plus `workflow_dispatch`). `requirements-gate.txt` lists only what the gate imports (`groq`, `python-dotenv`). Limitation to document: workflows from forks receive no secrets. The Action cannot be run on GitHub from here: it is verified by running the same commands locally and by validating the YAML; the first real run needs the repository secret `GROQ_API_KEY` set by the owner.

**Repo CI.** `.github/workflows/ci.yml` on push and pull request: backend tests (SQLite), the migration tests against a Postgres service container running `alembic upgrade head`, frontend install, lint (after the three pre-existing lint errors are fixed), tests and build. No secrets, no real LLM calls.

## 5. The simulation

Location: a top-level `simulation/` package with a CLI (`python -m simulation.run`, `python -m simulation.report`); results are written as JSON under `simulation/results/` and rendered to `docs/simulation-results.md`.

**Metric.** "Preview runs until the first judged pass": the number of preview runs (builder chats with the agent) until a run passes an **independent reference criterion** (a fixed per-scenario goal such as "the agent never promises or guarantees a refund") judged by the same judge model. The reference criterion is **not** the criterion the app discovers (that one can differ, as the live run showed), so the measure is not circular. Verification replays, discovery and suggestion calls are not previews. Cap of 12 previews per trial: a trial that does not converge is reported as such, never dropped.

**Arms.**
- *Baseline:* the synthetic builder previews, and after a failing run edits the prompt itself in one step (an LLM call "rewrite the prompt given this failing conversation and your goal"), standing in for the manual prompt editor. No detection, tests or fix loop.
- *Flywheel:* the builder previews; the app's loop detection nudges after two runs (via the real `check-pattern`), the builder discovers evals, builds tests, optionally generates a held-out test, suggests a fix, approves it (auto-approve stands in for the human gate), verification promotes or fails it; the builder then keeps previewing on whatever prompt is live.
Both arms use the same scenario prompt, builder model and caps. A builder is an LLM playing a no-code newcomer (it writes the next customer message and the manual edit); it is not a human, which the results page states.

**Scenarios and trials.** Two scenarios (a support agent with a refund guardrail, a booking agent with a confirmation guardrail), N trials per arm per scenario (default 5), model names and date recorded.

**Modes.** *Mock mode:* the single model seam `backend.groq_client.client.chat.completions.create` is replaced by a deterministic fake that routes on the distinctive system-prompt text of each call type (agent chat, builder, judge, analysis, pattern, criterion, suggester, generator, report). It exists to test the harness and run in CI; its numbers are meaningless and the results page says so. *Real mode* (`--real`, needs `GROQ_API_KEY`) paces itself to the provider limits (section 6), prints a cost and time estimate first, and saves results as it goes so a crash can resume.

**Results page.** `docs/simulation-results.md` is generated, never hand-edited: per scenario and arm: N, converged count, median and mean previews to first pass, minimum and maximum, total model calls, wall time; non-converged trials listed; the limitations (tiny N, LLM-played builder, same model family for builder and agent, judge shared with the app, free-tier pacing); and an explicit sentence that these are simulated figures and are not measurements of real builders.

## 6. Carry-overs from Phase 3 (included)

- **Fail fast.** `core.promotion` gains `early_failure(required, verdicts) -> Decision | None`: a fix or held-out verdict that is not `pass`, or any regression `fail`, is decisive even when other required results are missing. Reconcile applies it, fails the candidate with the same reason strings, builds the table with `after = None` for tests not yet run, and cancels the candidate's still-queued replay jobs (status `failed`, error "Cancelled: verification already failed."). Running replays that finish later are ignored.
- **Bounded retries on request routes.** `groq_chat(..., max_retries=None)`; when given it uses `client.with_options(max_retries=n)`. Synchronous request routes (chat, analyze, discover, pattern check) pass 2; job handlers keep the client default of 5.
- **Client-side token pacing.** Optional env `GROQ_TPM_LIMIT` (default unset = off): a process-wide sliding 60-second window per model records `usage.total_tokens`; before a call, if the window plus a conservative estimate would exceed the limit, the call waits (at most 20 seconds, then proceeds). Used by the simulation's real mode, the gate (`--pause` is the manual alternative) and optionally the deployed backend (documented in `.env.example`, commented out).
- **Small fixes:** `STALE_FIX` copy becomes "That fix is based on an older prompt. Reject it and suggest a new fix."; `version_out` tolerates a non-dict `verification`; the three pre-existing frontend lint errors are fixed so CI can gate lint.
- **Left deferred:** candidate replay jobs shown under per-test keys, queue fairness of the exempt fan-out, the UI poll horizon versus the server timeout, the suite endpoint's many small queries.

## 7. Documentation

`docs/DEMO.md`: a three-minute demo script (talk track, exact clicks, expected screens, what to say about limits and the human gate). `docs/CASE-STUDY.md`: strategy doc to requirement to design to results to limits, written for a hiring manager or FDE peer, linking the simulation page and stating what was and was not verified. README and ARCHITECTURE updated (gate, export, simulation, pacing, new exit codes, operational notes). Nothing in the docs claims more than was run.

## 8. Constraints

Same as the main spec §5 and the earlier phases: no real network in tests, `.env` is never read by code under test or by tooling, secrets only via environment variables, fixed user-safe messages, the judge never sees the agent prompt, tests run via the existing commands, frontend lint baseline becomes zero errors in this phase. The gate and the simulation must not import FastAPI or SQLAlchemy except where the simulation deliberately drives the app (section 5).

## 9. Integration at the end

After Phase 4 is reviewed and verified, the user merges the four phases into `main` in order: `feat/phase2-tests`, `feat/phase3-evolve`, `feat/phase4-ship`. Pushing and deploying remain separate steps that need the user's say-so; the Render start command already migrates, and the backend must deploy before the frontend.

## 10. Left to the builder

Package names and layout of the CLI and simulation, the exact prompts of the synthetic builder and the reference criteria, the mock fake's canned replies, the number of simulation trials within the stated defaults, the `--repeat` voting detail, output formatting, how results resume.

## 11. Verification plan

Unit tests per module with every model call mocked or faked; the CLI's exit codes and output formats; export shape and isolation; reconcile fail-fast with cancellation (and the earlier race test re-run on a real Postgres, since reconcile changes); a migration check if any column is added (none planned); mock-mode simulation in CI; a real run of the gate on the example suite and a real simulation run (small N) with its results page committed; the repo CI workflow validated by parsing and by running its commands locally. What cannot be verified from here: the GitHub Action on GitHub, fork-PR behavior, long-run provider rate limits.

## 12. Calls made after the builder check (author's calls, not the user's)

A fresh reader found nine places where a wrong guess would matter. All are settled here; any may be overruled. Where this section differs from sections 1-11 it wins.

1. **The flywheel builder has a fallback, so the comparison is fair.** The flywheel arm runs a fixed state machine: preview; after two previews call the real `check-pattern`; if a loop is found, discover, build tests (wait for the job), optionally generate a held-out test, confirm the fix test fails on the live prompt (replay it up to 3 times), suggest a fix, approve it, wait for verification. If **any** step does not go to plan (no loop found, no tests built because every saved run passed, no failing fix test, suggestion or verification fails), the builder abandons the loop for this trial and falls back to the same manual LLM rewrite the baseline uses. A promoted fix that still fails the reference criterion is followed by the manual rewrite on the next failing preview (one flywheel attempt per trial). Every trial records its `path`: `loop` (the first judged pass came after a promoted fix), `fallback` (after a manual rewrite) or `none` (never converged). The results page reports the count of each, so a flywheel number that is really the fallback is visible.
2. **`.env` handling.** `backend/groq_client.py` gains an env switch `GROQ_SKIP_DOTENV=1` that skips `load_dotenv()`; the gate, the simulation's mock mode and the test suite set it, so none of them read `.env`. The gate and mock mode also never accept the `"sk-test-placeholder"` fallback: the gate checks `GROQ_API_KEY` itself and exits 3 when it is missing. Real-mode simulation runs and the real gate run use the normal import-time `load_dotenv()` (the key is read by the program and never printed or written); the user decides whether the agent runs them (section 13).
3. **The model seam is the client object.** Mock mode replaces the whole `backend.groq_client.client` with a fake whose `chat.completions.create` is deterministic and whose `with_options(...)` returns itself, so calls made with bounded retries are caught too. Bounded retries are applied through a context variable (`request_retry_limit`, read by `groq_chat`), set by the request routes (chat, analyze, discover, check-pattern) around their model calls, so no pure core signature changes. The fake must return an integer `usage.total_tokens`.
4. **Postgres in CI is real.** A committed `tests/pg/` suite runs only when `TEST_DATABASE_URL` is set (skipped otherwise): migrations over populated data (0001 to head), reconcile fail-fast on Postgres, and a thread-based race test (two approvals; approve against a manual edit) against `TestClient` with a Postgres engine and a session per request. The CI workflow runs it with a Postgres service container.
5. **Models in the simulation.** The synthetic builder's customer messages use `MODEL_CHAT`; the baseline's prompt rewrite uses `MODEL_JUDGE`, the same model as the app's suggester, so the flywheel does not win by using a stronger model. `GROQ_TPM_LIMIT` is one number applied to a separate sliding window per model. The committed real run is small: 2 scenarios, 3 trials per arm, a 12-preview cap, an estimated 1-1.5 hours on the free tier, saved incrementally and resumable.
6. **Phase 4 scope for the README's deferred list.** In: an event-derived metrics script `scripts/metrics.py` (reads a database URL, prints per-workspace acceptance and conversion counts and the simulation-style "previews until first passing test" from events; main spec §12 promises a query or script). Still deferred, stated in the README and case study: M8 (a 401 mid-poll can create an empty workspace), sliding token expiry, deleting a single agent, `passive_deletes`, picking a saved run or writing a held-out test by hand, candidate replay jobs under per-test keys, queue fairness of the exempt fan-out, the UI poll horizon versus the server timeout, the suite endpoint's many small queries.
7. **Action scope.** Only the in-repo example is verified (by running the same commands locally). The composite Action locates the gate through `${{ github.action_path }}` so it can work from another repository, but that use is documented as unverified, needs the repository to be public or shared, and fork pull requests receive no secrets.
8. **The gate is a regression gate.** The export adds `tests[].current_verdict` (the latest non-stale replay on the live prompt: `pass`, `fail`, `not_applicable` or null). By default the gate runs only tests whose exported `current_verdict` is `pass` (tests that passed when exported must not start failing) and prints how many tests it skipped; `--all` runs every test. With the default, a customer's first run does not fail on a criterion that was never fixed.
9. **Fail-fast edge cases.** A fix or held-out replay job that ended `failed` or was discarded (no verdict) is decisive immediately: the candidate fails with "A test could not be run: ..." without waiting for the other jobs. After a candidate is resolved, a verification replay job that is still queued or gets requeued is cancelled or becomes a no-op: `handle_replay` skips all model calls for a `system` job whose version is no longer `approved_verifying` and returns `{"skipped": true}`; late results are not stored.

(Numbering nit fixed: the carry-overs are in section 6.)

## 13. Needs the user's decision

The committed real simulation run and the real gate run use the user's Groq key and free-tier quota (the key is read by the program from `.env` or the environment, never displayed). The simulation run is estimated at 1-1.5 hours of paced calls. Default proposal: the agent runs them in the background after the code is reviewed, and reports the table honestly whatever it shows; the alternative is that the agent writes the commands and the user runs them with their own key.
