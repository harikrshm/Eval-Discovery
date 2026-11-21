# Agent-Ready Eval Flywheel — Design

Status: draft for approval · Date: 2026-11-21 · Size: project (phased)

## 1. Intent and why

Eval Discovery today detects that a tester keeps probing the same behavior of one hardcoded agent (Alex, CloudSync SDR) and turns that into an eval criterion. This design turns it into a reusable requirement an FDE (forward deployed engineer) could build for a customer:

> When a builder keeps re-previewing the same agent behavior, the system detects the loop, names the behavior being tested, and saves it as a criterion plus a runnable test. It then re-runs those tests on every agent change and tells the builder when testing is done.

Why this requirement:
- It prototypes Solution 2 of the user's product strategy doc ("detect test cases during preview runs", target: time-to-value for no-code builders). The doc's own stated gap is that a plain "save as test" nudge does not teach why tests matter. Naming the behavior and showing evidence across runs is the answer to that gap.
- It is the portfolio vehicle for three claims: product sense (strategy → requirement → metrics → guardrails), technical strength (a Level 2 self-evolving loop with a human gate), and build/deploy skill (a persisted, production-shaped service).
- The technical concept to project is **evaluator-driven harness evolution where the evaluator is discovered from the builder's own behavior**. In the taxonomy at lsl.zone ("A taxonomy of self-evolving agents") this is Level 2, harness self-improvement: the harness is the agent's system prompt and no weights change. That post does not say where the Level 2 learning signal comes from; this project's answer is that the criterion discovered from repeated testing is the signal. (This framing is the author's inference, not a claim made by the post or by the survey arXiv 2508.07407. Only the survey's abstract was read, which names four loop components: System Inputs, Agent System, Environment, Optimisers.)

## 2. Two critiques that drive the scope

1. "Builder keeps re-previewing the same behavior" is hardcoded (persona, 38 keywords, speaker labels). It must work for any agent.
2. "Saves it as a criterion plus a runnable test" is a production-UI feature. The MVP needs a thin, demonstrable slice of it, but built close to production shape.

## 3. Decisions the user made

- **Bring your own agent.** The viewer pastes a system prompt, or starts from a preset (Alex · Sales, Support, Booking, Blank).
- **Replay is the runnable test.** Take the prospect-side messages of a failing run, replay them against the agent's current prompt, have a judge grade the new transcript against the criterion. The demo moment is Fail → Pass after a prompt change.
- **Suggest-a-fix with a human gate.** A button turns the judge's rationale into a proposed prompt edit. The builder approves or rejects it. Approved edits are re-verified on a held-out failing run, so a prompt cannot pass by overfitting to the one run it was tuned on.
- **Close to production: persisted workspace** (database, background jobs), not a stateless MVP.
- **In scope:** anonymous workspaces; Postgres with migrations; replays as background jobs polled by the UI; an event log for the strategy doc's metrics; a CI eval gate (last phase).
- **An approved fix is held until verification passes.** Approving a suggested prompt does not make it active. It becomes the workspace's current prompt only after the suite verifies it. Until then the agent keeps using the previous version.
- **A judge-written change report** explains the change to the builder: why the prompt was changed before approval, and what actually happened after verification. See section 8a.
- **Simulation depth: a script and a results table** (no in-app metrics page). See section 12.
- **Out of scope:** real login/auth, billing, multi-user teams.

## 4. Goals, non-goals, anti-goals

Goals
- Any agent defined by a system prompt can be tested, with loop detection that is not tied to Alex.
- Criteria become tests; tests re-run on every prompt change; a readiness signal says when testing is done.
- A human-approved, evaluator-driven prompt fix loop, verified on held-out data.
- Production-shaped: persisted, isolated per workspace, resumable jobs, migrations, CI, event logging, abuse limits.
- Existing pieces keep working (chat, history, criteria panel, JSON export).

Non-goals
- Not a general eval platform, no cross-customer scoring or benchmarking.
- No tool-calling agents, voice, or connecting to an external agent endpoint (agent = system prompt plus a model).
- No auto-applying fixes. No model fine-tuning (Level 3).

Anti-goals (failure even if it technically works)
- Presenting simulated or illustrative numbers as measured results. The strategy doc says its own figures are illustrative.
- A fix loop that quietly removes the builder's understanding of why a test exists (the strategy doc's own risk).
- A judge whose verdicts the builder cannot inspect (always show rationale and transcript).

## 5. Constraints

- Frontend: React 19 + Vite + Tailwind on Cloudflare Workers static assets (unchanged). Backend: FastAPI on Render, Groq models (`openai/gpt-oss-20b` chat, `openai/gpt-oss-120b` pattern, `qwen/qwen3.8-27b` criteria).
- Render's free web tier sleeps after about 15 minutes and cold-starts in 30–60 s, so every job must be resumable and the UI must tolerate a slow first response.
- The backend is public and accepts user-pasted prompts: it can be abused as a free LLM proxy and stores user content. Caps, rate limits, retention and a delete path are required, not optional.
- Groq cost and rate limits apply. Replay plus judge plus suggest-fix is several calls per test.
- Existing tests in `tests/` must keep passing or be deliberately updated.

## 6. Approaches considered

1. **Browser orchestrates** (extend current shape). Smallest change, but replay is 4–8 sequential browser round trips, logic lives in React and cannot run in CI. Rejected.
2. **Reusable core module with thin endpoints, state in the browser.** One call per test, CLI/CI-capable core. Recommended as the base.
3. **Persisted workspace (database plus background jobs).** Closest to production, breaks the "no database" decision and adds infrastructure. **Chosen by the user**, built on top of option 2's core so the core stays pure and testable.

## 7. Architecture

```
React (Cloudflare)  ──HTTP, X-Workspace-Token──►  FastAPI (Render)
                                                   ├─ api/      thin routers, validation, limits
                                                   ├─ core/     pure functions, no I/O except the LLM client
                                                   ├─ db/       models, migrations, repositories
                                                   └─ worker/   job loop (same process)
                                                        │
                                              Managed Postgres      Groq
```

`core/` takes the agent config as an argument and never imports a persona. Functions: `analyze_agent`, `detect_pattern`, `generate_criterion`, `replay`, `judge`, `suggest_fix`. A CLI wraps the same functions for CI.

### Data model (names are a starting point; the builder may adjust)
- `workspace` (id, token_hash, created_at, expires_at)
- `agent` (id, workspace_id, name, user_role_label, current_prompt_version_id)
- `prompt_version` (id, agent_id, text, parent_id, origin: manual | suggested, status: proposed | approved_verifying | promoted | failed_verification | rejected | superseded, verification_strength: full | weaker, change_report json, created_at)
- `behavior_map` (agent_id, prompt_hash, behaviors: name, description, trigger_terms)
- `run` (id, agent_id, prompt_version_id, messages, created_at)
- `criterion` (id, agent_id, name, definition, good_example, bad_example, why_discovered, source: discovered | manual)
- `test_case` (id, criterion_id, source_run_id, scripted_user_messages, role: fix | heldout)
- `test_result` (id, test_case_id, prompt_version_id, kind: baseline | replay, verdict: pass | fail | not_applicable | error, rationale, replay_transcript, judge_check, stale, created_at)
- `job` (id, workspace_id, type, payload, status: queued | running | done | failed, attempts, result, locked_at)
- `event` (id, workspace_id, name, properties, created_at)

## 8. Flows

1. **Setup.** Pick a preset or paste a prompt and name. One `analyze_agent` call returns the user-role label (for example "Prospect") and a behavior map: the agent's behaviors and guardrails, each with trigger terms. Cached by prompt hash. This replaces the 38 hardcoded keywords.
2. **Chat and runs.** `/chat` uses the selected agent's prompt version. "End run" saves a run row (min 3 messages, as today).
3. **Detect loop.** After every 2nd run, a cheap prefilter checks the new runs' user messages against the behavior map's trigger terms across at least 2 runs; if it passes, an LLM confirms the pattern on delta transcripts only (as today). A "loop detected" event is logged.
4. **Criterion and tests.** "Discover evals" generates a criterion. The judge then scores the saved runs against it. Failing runs become test cases: the first is the `fix` test, the next distinct failing run is the `heldout` test. If only one failing run exists, there is no held-out test and the UI says verification is weaker. Judging a saved run stores a `baseline` result against that run's prompt version. A run that never exercised the behavior gets `not_applicable` and never becomes a test. Tests are therefore born failing; if no saved run fails, no tests are created and the UI says the agent already handles this behavior in the saved runs. (See section 16.)
5. **Replay.** A job replays the test's scripted user messages turn by turn against the chosen prompt version (agent replies are regenerated, user messages are fixed), then the judge returns pass/fail with a rationale. Known limitation: fixed user messages may read oddly if the agent's new replies diverge. Accepted for the MVP; shown in the transcript.
6. **Suggest fix.** From the failing result (criterion, rationale, current prompt) one call proposes a new prompt, saved as a `proposed` prompt version and shown as a diff next to the "why this change" report (section 8a). Nothing changes until the builder approves.
7. **Approve, then verify, then promote.** Approval moves the version to `approved_verifying`. The agent keeps using the current version, and the UI says so ("Verifying v3 — chat still uses v2"). The system replays the whole saved suite on the candidate: the `fix` test, the `heldout` test, and every previously passing test (regression check). The promotion rule is deterministic code, not the judge: promote only if the `fix` test passes, the `heldout` test passes (when one exists), and no previously passing test regresses. If there is no held-out test, promote on the other two conditions and label the version "weaker verification". On promotion the version becomes `agent.current_prompt_version_id`, which changes the prompt used by chat and by later replays in the workspace. Otherwise the version becomes `failed_verification`, stays inactive, and the report says why. The builder can then suggest again from the new failure, edit the candidate by hand, or discard it. Any earlier version can be restored from version history.
8. **Readiness.** A chip such as "2 of 3 criteria passing" is derived from the latest `replay` result per test on the current prompt version. A criterion passes when all its tests pass (`not_applicable` is ignored); a criterion with no replay yet is "untested" and not counted as passing.

### 8a. The change report (judge assessment)

Purpose: the builder should understand why the prompt changed and what the change did, so the fix teaches the testing mental model instead of acting as a black box. This answers the strategy doc's stated trade-off that a nudge "doesn't teach why tests matter".

The report has two stages, both stored on the `prompt_version` as `change_report` and shown beside the diff.

- **Before approval, "Why this change":**
  1. The criterion that failed, with the quoted failing turn from the transcript.
  2. What the diff changes, in plain words, and which part of the prompt (for example a guardrail) it touches.
  3. The expected effect on the failing test and the risk to other behaviors.
- **After verification, "What happened":**
  1. A table of before → after verdicts for the `fix` test, the `heldout` test and each regression test. These numbers come from stored `test_result` rows, not from the LLM.
  2. The judge's rationale for each changed or failing verdict, with a link to the replay transcript.
  3. The outcome: promoted, or not promoted with the specific reason (which test failed and why), and a suggested next step.

Rules: the narrative is written by the judge model, but it must cite test result ids or transcript turns for every claim, and the UI renders verdict tables from the database, so the model cannot misreport a result. If the narrative cannot be generated, the table and diff still show and the report is marked "explanation unavailable". Judge input is delimited and treated as data, as in the reliability notes below.

### Judge reliability (builder to implement, shape fixed)
- Judge and replay run at temperature 0 where the model allows.
- Judge sanity check per criterion: the judge must fail the criterion's own `bad_example` and pass its `good_example`. The outcome is stored in `judge_check`; a failed check marks verdicts "low confidence" in the UI.
- Judge input is delimited and treated as data. The pasted agent prompt and transcripts cannot instruct the judge.

## 9. API surface (illustrative)

`POST /workspaces` (returns token) · `DELETE /workspaces/me` · `POST /agents` · `GET /agents/{id}` · `POST /agents/{id}/chat` · `POST /agents/{id}/runs` · `POST /agents/{id}/check-pattern` · `POST /agents/{id}/criteria/discover` · `GET /agents/{id}/tests` · `POST /tests/{id}/replay` → job id · `POST /agents/{id}/suggest-fix` → job id · `POST /prompt-versions/{id}/approve|reject|restore` · `GET /prompt-versions/{id}/report` · `GET /jobs/{id}` · `GET /agents/{id}/export` (criteria, tests, prompt as JSON for the CI gate).

All routes except workspace creation require the workspace token. Every query is scoped to the token's workspace.

## 10. UI changes

- Setup screen (preset or paste), shown on first visit and via an "Agent" control in the header.
- Existing chat, history and criteria panel stay; labels come from the agent config, not "Alex"/"Prospect".
- Criteria panel gains a Tests section per criterion: latest verdict, Replay, Suggest fix, readiness chip, prompt version history.
- Change review for a proposed prompt: the diff beside the "Why this change" report, with Approve and Reject. After approval the same view becomes "What happened": a Verifying state, then the before → after verdict table, the judge's explanation and the outcome (promoted, or not promoted and why). Version history lists every prompt version with its status and lets the builder restore an earlier one.
- A clear active-version indicator ("Chat uses v2 · v3 verifying") so the builder always knows which prompt is live.
- Job states (queued, running, done, failed) with polling, a slow-start message (reuse the existing 4 s pattern), and retry.
- Keep the accessibility work already shipped (focus management, live regions, reduced motion, inert panels).

## 11. Production traits

- **Identity.** Anonymous workspace created on first visit; token kept in localStorage, only its hash stored server-side. No login.
- **Retention and privacy.** Workspaces expire after 30 days (builder may adjust), with `DELETE /workspaces/me` and a visible notice that pasted prompts are stored.
- **Limits.** Prompt length cap (about 4k characters), runs per workspace, turns per run, jobs per workspace per hour, and per-IP request rate limits.
- **Jobs.** DB-backed queue with a single worker loop in the web process. A job stuck in `running` past a timeout is requeued (cold starts and redeploys will interrupt jobs). Retries are capped.
- **Migrations** run on deploy. Secrets via environment variables.
- **Event log** (names): `agent_created`, `run_saved`, `loop_detected`, `nudge_shown`, `criterion_created`, `test_created`, `replay_started`, `replay_passed`, `replay_failed`, `fix_suggested`, `fix_approved`, `fix_rejected`, `fix_verified`, `fix_verification_failed`, `fix_promoted`, `version_restored`, `report_viewed`. Derived metrics mirror the strategy doc: acceptance rate, test conversion, preview runs until first passing test, fix approval rate, verification pass rate, held-out pass rate.
- **CI eval gate (last phase).** A CLI runs a saved suite (exported JSON plus the prompt file) and exits non-zero if the pass rate drops below a threshold. A GitHub Action runs it when the prompt file changes. Needs a Groq key as a repository secret.

## 12. Measurement honesty

There are no real users. To report the strategy metric "preview runs until first passing test", run synthetic builders (scripted or LLM-played) with and without the flywheel and label every number "simulated". Do not present the strategy doc's own figures as measured.

Scope (decided): a CLI script and a markdown results table for the docs, with no in-app metrics page. The script runs a fixed number of synthetic builders per arm. The baseline arm keeps previewing until a run passes the judge, with no nudge, tests or fix loop. The flywheel arm uses loop detection, tests and the suggest-fix loop. Both arms report preview runs until the first judged pass, and every table is labeled "simulated". Event-derived metrics for a workspace are available through a query or script, not a UI page. Builder decides the number of trials, how the synthetic builder is scripted or LLM-played, and the exact baseline stop rule.

## 13. Phases

1. **Foundation.** `core/` extraction, agent config and presets, behavior map replacing keywords, Postgres and migrations, workspaces, limits, event log. Existing UI works on the new backend.
2. **Tests.** Criterion → tests, judge with sanity check, replay as jobs, Tests UI.
3. **Evolve.** Suggest fix, diff review, held-out and regression verification, readiness chip.
4. **Ship.** CI eval gate, simulated before/after comparison, docs and architecture update, demo script.

## 14. Left to the builder

ORM and migration tool, worker mechanics, rate-limit library, prompt wording for the new LLM calls, which Groq model serves judge and suggest-fix, diff rendering, exact limits and TTL, whether to majority-vote the judge, test strategy per phase.

## 15. Existing behavior to preserve

Chat flow, End run (min 3 messages), delta batching every 2 runs, FSM states in the UI, criteria edit and JSON export, the accessibility behaviors in the current UI, and the backend test suite in `tests/` (updated where signatures change).

## 16. Calls made after the builder check (author's calls, not the user's)

- **Verdicts.** The judge may return `not_applicable` when a transcript never exercised the behavior. It is ignored by readiness and never creates a test.
- **Baseline vs replay.** Judging an original saved run is stored as a `baseline` result. Only `replay` results count toward readiness and regression checks.
- **Criterion status.** "Passing" = all its tests' latest replay results on the current prompt version pass. No replay yet = "untested". Editing a criterion marks its results stale and clears its `judge_check`.
- **Manual criteria.** A manually added criterion gets tests when the builder picks a saved run for it; that run becomes the `fix` test (a second pick can be the `heldout` test).
- **Re-run policy.** Re-runs are on demand, with one exception: approving a suggested fix automatically runs the whole suite. A simple prompt editor in the Agent drawer creates a `manual` prompt version, marks existing results stale and shows "N tests stale — Run all tests". Saving never auto-runs, which protects the Groq budget and the hourly job cap.
- **Database.** Standard Postgres reached through `DATABASE_URL`, with no provider-specific features. The host is chosen at deploy time after checking current free-tier terms (expiry, sleeping, storage). CI uses a Postgres service container. The 30-day workspace TTL is enforced by the app, not the host.

Decided by the user after the builder check: an approved fix is held until verification passes (section 8, step 7) and comes with a judge-written change report (section 8a); the Phase 4 simulation is a script plus a results table (section 12).

My calls on top of those decisions:
- Promotion after passing verification is automatic. The builder's approval is the human gate, and no second click is required. Any earlier version can be restored from version history.
- The promotion rule is deterministic code (fix passes, held-out passes when present, no regressions), never the judge's opinion. The judge's report only explains.
- With no held-out test, promotion uses the other two conditions and the version is labeled `weaker` verification.
- The report's verdict tables are rendered from stored results. The model writes only the narrative and must cite result ids or transcript turns.
- While a candidate verifies, chat and replays keep using the current version.
