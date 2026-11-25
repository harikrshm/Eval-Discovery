# Eval Discovery — System Architecture

> **Audience:** This document serves both executives and technical stakeholders.
> Executives: focus on the [System Overview](#1-system-overview) and [Context Diagram](#31-system-context-diagram).
> Engineers: the [Component Diagram](#33-component-diagram), [Data Flow Diagram](#32-data-flow-diagram) and [Data Model](#4-data-model) contain the implementation details.

---

## 1. System Overview

**Eval Discovery** is a web application that accelerates the quality-assurance process for conversational AI ("voice agent") products. Developers who build voice agents — like sales bots or support agents — must test them against real-world conversation patterns, then write rubrics that describe what "good" looks like.

Today that process is manual and slow. Eval Discovery automates it:

- A developer pastes **any agent's system prompt** (or picks a built-in preset) and plays the user role in conversations with that agent
- The system watches for repeated testing patterns across those conversations
- Once a pattern is detected, the system uses a large language model to generate an evaluation rubric, grounded in real conversation evidence
- The developer reviews, edits, and exports the rubric as JSON for use in automated test pipelines
- (Phase 2) A background job turns the criterion into replayable tests from the developer's own failing runs. The developer edits the agent's prompt, replays the tests against it, and an LLM judge says pass or fail with a rationale
- (Phase 3) From a failing test the system suggests a prompt fix and explains why. The developer reads the diff and approves, and the candidate goes live only if it passes the fix test, a held-out test and every previously passing test, by a rule written in code. A model-written report explains the result, any version can be restored, and a chip says how many criteria pass
- (Phase 4) The saved suite can be exported as a versioned JSON file, and a command-line gate (`evalgate`) replays it against a prompt file in the team's own CI and fails the check when a behavior that passed at export starts failing. A simulation harness compares "preview runs until the first judged pass" with and without the loop (its first real-model results, which did not show the loop reducing preview runs, are in [simulation-results.md](simulation-results.md) and summarised in [6.14](#614-phase-4-decisions) and [6.15](#615-verified-and-not-verified-phase-4)), verification now fails as soon as the stored verdicts decide it, and an events script reports acceptance and conversion counts
- (Patent Assist) The same loop is pointed at an assistant that lives inside a product's own page: a Chrome extension answers questions about a Google Patents page, usage signals reach a console, and flagged answers go through discover, tests, fix, approve and verify. It is built and tested with faked model calls and was run once with real models (2026-11-23; defects found and fixed, the fixes not re-run), and its law pack is a draft. See [10](#10-patent-assist)

**Business Value:**
- Reduces time-to-rubric from hours of manual analysis to minutes
- Ensures rubrics are grounded in actual conversation data, not guesswork
- Creates an auditable artifact (JSON) that integrates with existing test infrastructure

---

## 2. Architecture Type

**Monolithic Two-Tier Web Application** with a relational database and LLM backend services

| Layer | Technology | Role |
|-------|-----------|------|
| Frontend | React 19 + Vite + Tailwind CSS | User interface, chat draft, hydration from the server |
| Backend | Python FastAPI | Routing, workspace auth, limits, prompt engineering, LLM orchestration |
| Persistence | SQLAlchemy 2 + Alembic on Postgres (SQLite for local dev and tests) | Workspaces, agents, prompt versions (including candidates and their reports), runs, criteria, tests and results, jobs, events |
| Background jobs | Database-backed queue, one worker loop inside the web process | Building tests, replaying them, suggesting fixes, generating held-out tests and writing change reports, so no LLM chain holds an HTTP request open; the loop also resolves verifying candidates |
| LLM Services | Groq API (3 models) | Agent simulation and replay, prompt analysis, pattern detection and judging, criteria generation, fix suggestion, held-out scenario generation, change-report narrative |
| Tooling (Phase 4) | `evalgate/`, `simulation/`, `scripts/metrics.py`, `.github/` | Not part of the web process. The CI gate (imports `backend.core` and `backend.groq_client` only), the simulation harness (drives the real app in-process), the events metrics script, and the repo workflows. See [3.7](#37-export-and-the-ci-gate-phase-4) and [3.8](#38-the-simulation-phase-4) |

---

## 3. Architecture Diagrams

### 3.1 System Context Diagram

Shows the system as a whole and every external actor it communicates with.

```mermaid
graph TB
    Dev["Voice Agent Developer<br/>(User / Tester)"]
    System["Eval Discovery App<br/>localhost:5173"]
    DB[("Postgres / SQLite<br/>(workspaces, agents, runs, criteria, tests, jobs)")]
    Groq["Groq API<br/>(LLM Cloud Service)"]
    Export["JSON Export<br/>(Eval Rubric File)"]
    TestPipeline["External Test Pipeline<br/>(CI / Eval Runner)"]
    SuiteFile["Suite export<br/>(eval-suite.json, committed to the team's repo)"]
    Gate["evalgate CI gate<br/>(runs in the team's repository)"]

    Dev -->|"Pastes an agent prompt or picks a preset,<br/>chats, reviews & edits eval criteria"| System
    System <-->|"Persists and reloads state"| DB
    System -->|"LLM inference requests<br/>(chat / analyze / pattern / criteria / judge / replay / suggest fix / generate test / report)"| Groq
    Groq -->|"Generated responses"| System
    System -->|"Exports eval rubric"| Export
    Export -->|"Imported by team"| TestPipeline
    System -->|"Export suite for CI"| SuiteFile
    SuiteFile -->|"Read by"| Gate
    Gate <-->|"Replay and judge requests"| Groq
    Gate -->|"Exit code 0, 1, 2 or 3"| TestPipeline
```

**Key relationships:**
- The developer is both the conversation participant (playing the user role) and the rubric author
- Groq and the database are the only runtime dependencies — there is no auth service; identity is an anonymous workspace token
- The output (JSON rubric) flows into the team's broader test infrastructure
- Since Phase 4 the gate is the only part that runs outside the app: it needs a suite file, a prompt file and a Groq key, not the app, its database or a workspace token

---

### 3.2 Data Flow Diagram

Shows how data moves through the system from agent setup to the final eval rubric export.

```mermaid
flowchart TD
    subgraph Browser["Developer (Browser)"]
        A["Agent setup: preset or pasted prompt"] --> B["POST /agents"]
        C["Types a message"] --> D["POST /agents/id/chat"]
        D --> E["ChatInterface renders reply<br/>(draft kept in sessionStorage)"]
        E --> F["End run"]
        F --> G["POST /agents/id/runs"]
        G --> H{"check_due?"}
        H -->|No| I["Wait for more runs"]
        H -->|Yes| J["POST /agents/id/check-pattern"]
        J --> K{"pattern found?"}
        K -->|No| I
        K -->|Yes| L["Show 'Discover evals'"]
        L --> M["POST /agents/id/criteria/discover"]
        M --> N["EvalCriteriaPanel<br/>(editable cards)"]
        N --> O["PATCH /criteria/id"]
        N --> P["Export all → JSON file"]
        N --> Q["PUT /agents/id/prompt"]
        N --> R["POST /agents/id/tests/run-all"]
        R --> T["Poll GET /jobs/id and GET /agents/id/tests"]
        N --> U["POST /tests/id/suggest-fix<br/>POST /criteria/id/generate-heldout"]
        U --> T
        T --> V["Read the diff and report<br/>GET /prompt-versions/id and /report"]
        V --> W["POST /prompt-versions/id/approve, reject or restore"]
        W --> T
    end

    subgraph Backend["FastAPI Backend (port 8000)"]
        B --> B1["analyze_agent (custom prompt)<br/>or preset analysis"]
        D --> D1["agent prompt + messages"]
        G --> G1["save Run, keyword_prefilter<br/>over the unchecked delta"]
        J --> J1["detect_pattern over the delta"]
        M --> M1["generate_criterion<br/>then enqueue build_tests"]
        Q --> Q1["new prompt version<br/>(chat uses it at once)"]
        R --> R1["enqueue a replay job per test<br/>without a current replay"]
        U --> U1["enqueue a suggest_fix or<br/>generate_heldout job"]
        W --> W1["approve: lock agent, required set,<br/>one replay job each<br/>reject and restore: version rows"]
    end

    subgraph Store["Database"]
        B1 --> S1["agent · prompt_version · behavior_map"]
        G1 --> S2["run"]
        J1 --> S3["agent.pattern_summary<br/>agent.last_checked_at_run"]
        M1 --> S4["criterion"]
        M1 --> S5["job"]
        Q1 --> S6["prompt_version"]
        R1 --> S5
        U1 --> S5
        W1 --> S5
        W1 --> S6
    end

    subgraph Groq["Groq API"]
        B1 --> X1["gpt-oss-120b"]
        D1 --> X2["gpt-oss-20b"]
        J1 --> X1
        M1 --> X3["qwen3.8-27b"]
    end
```

**Where state lives:**

| Data | Location | Lifetime |
|------|----------|----------|
| Workspace token, active agent id | Browser `localStorage` (`ed_token`, `ed_agent_id`) | Until the workspace expires or is deleted |
| Chat draft of the run in progress | Browser `sessionStorage`, one key per agent | Until End run saves it or the tab session ends |
| Workspace, agents, prompt versions (candidates, verification data, change reports), behavior maps, runs, criteria, tests, results, jobs, events | Database | 30 days from workspace creation |
| In-flight job state in the Tests UI | Browser memory (`useTests`), re-derived from the active job ids in `GET /agents/id/tests` after a reload | Not stored |
| Change report on screen, version list | Browser memory (`useReport`, `useVersions`), loaded from the server | Not stored |
| Pattern-detection UI state | Derived on load from the server (see [5](#5-pattern-detection-state-machine)) | Not stored |

---

### 3.3 Component Diagram

Internal structure of both the frontend application and backend API.

```mermaid
graph TB
    subgraph Browser["Browser — React App (port 5173)"]
        direction TB
        Main["main.jsx<br/>(React root)"]
        App["App.jsx<br/>(Layout orchestrator)"]
        Hook["useSession.js<br/>(Hydrates from GET state; run, pattern and criteria logic)"]
        TestsHook["useTests.js<br/>(Loads the suite · follows jobs by polling GET /jobs/id · suggest, approve, reject, restore actions)"]
        ReportHooks["useReport.js · useVersions.js<br/>(Change report, polled until its narrative arrives · version list)"]
        ApiClient["api.js<br/>(Fetch client · adds X-Workspace-Token · creates the workspace on first call)"]
        Lib["lib/workspace.js · draft · patternState<br/>(token storage · chat draft · state derivation)"]

        subgraph Components["UI Components"]
            Setup["AgentSetup.jsx<br/>Preset picker · paste prompt · role label"]
            Header["Header<br/>PatternBadge · Discover evals btn · History + Eval criteria toggles"]
            Chat["ChatInterface.jsx<br/>Message list · Input bar · Typing animation"]
            Eval["EvalCriteriaPanel.jsx<br/>Editable criterion cards · Export · Add · stale-tests banner"]
            PromptEd["PromptEditor.jsx<br/>Edit the prompt, saves a new version"]
            TestsSec["TestsSection.jsx<br/>Per-criterion status · tests · Replay · Build tests · Suggest fix · Generate a second test"]
            Review["ChangeReview.jsx<br/>Diff · why this change · Approve and verify · Reject · what happened"]
            VersionHist["VersionHistory.jsx<br/>Versions with status and strength · Restore"]
            Chip["ReadinessChip.jsx<br/>N of M criteria passing"]
            History["HistoryPanel.jsx<br/>Completed run transcripts · slide-in drawer"]
            Controls["RunControls.jsx<br/>End run button · progress hint"]
        end

        Main --> App
        App --> Hook
        Hook --> ApiClient
        TestsHook --> ApiClient
        Hook --> Lib
        ApiClient --> Lib
        App --> Setup
        App --> Header
        App --> Chat
        App --> Eval
        App --> TestsHook
        Eval --> PromptEd
        Eval --> TestsSec
        Eval --> Review
        Eval --> VersionHist
        Eval --> Chip
        Header --> Chip
        Review --> ReportHooks
        VersionHist --> ReportHooks
        ReportHooks --> ApiClient
        App --> History
        App --> Controls
    end

    subgraph BackendAPI["FastAPI Backend (port 8000)"]
        direction TB
        FastAPI["main.py<br/>(CORS · routers · startup purge of expired workspaces · starts the job worker)"]

        subgraph Routers["api/ — routers"]
            WsRouter["workspaces.py<br/>POST /workspaces · DELETE /workspaces/me"]
            AgentRouter["agents.py<br/>GET /presets · POST /agents · GET state · POST chat"]
            RunRouter["runs.py<br/>POST runs · POST check-pattern"]
            CriteriaRouter["criteria.py<br/>POST discover (returns job_id) · POST criteria · PATCH criteria"]
            PromptRouter["prompt_versions.py<br/>PUT agents/id/prompt"]
            TestsRouter["tests.py<br/>GET tests · POST replay · POST run-all · POST build-tests · POST suggest-fix · POST generate-heldout · GET jobs"]
            VersionsRouter["versions.py<br/>GET prompt-versions · GET prompt-versions/id · POST approve · reject · restore · GET report"]
            ExportRouter["export.py<br/>GET agents/id/export (the suite for the CI gate, read-only)"]
        end

        ApiSupport["api/deps.py · limits.py · schemas.py<br/>(workspace auth · quotas and rate limiter · strict Pydantic schemas)"]

        subgraph Core["core/ — pure logic, no web, DB or jobs imports"]
            Config["config.py<br/>(AgentConfig · AgentAnalysis · Behavior · presets · limits)"]
            Analyze["analyze.py<br/>(prompt → behavior map)"]
            Pattern["pattern.py<br/>(keyword_prefilter · detect_pattern)"]
            Criteria["criteria.py<br/>(generate_criterion)"]
            Judge["judge.py<br/>(judge · sanity_check)"]
            Replay["replay.py<br/>(user_messages · replay)"]
            Suite["testsuite.py<br/>(pick_test_runs · criterion_status)"]
            Suggest["suggest.py<br/>(suggest_fix)"]
            Heldout["heldout.py<br/>(generate_scenario)"]
            Report["report.py<br/>(write_report)"]
            Promotion["promotion.py<br/>(decide_promotion · early_failure · build_outcome_rows · quote_turn)"]
            Prompts["prompts.py<br/>(prompt builders · delimiter neutralizing)"]
        end

        subgraph Db["db/ — persistence"]
            DbModels["models.py · session.py"]
            DbHelpers["agents.py · workspaces.py · events.py · testsuite.py"]
            Candidates["candidates.py<br/>(create_proposed_version · approve · reject · restore · compute_required)"]
            Reconcile["reconcile.py<br/>(reconcile_verifying: promote or fail · fails fast and cancels queued replays)"]
        end

        subgraph Jobs["jobs/ — background work, same process"]
            Queue["queue.py<br/>(enqueue · claim_next · requeue_stale)"]
            Handlers["handlers.py<br/>(build_tests · replay · suggest_fix · generate_heldout · change_report)"]
            Worker["worker.py<br/>(worker_loop started in the app lifespan)"]
        end

        GroqClient["groq_client.py<br/>(API wrapper · model constants · think-block stripping · bounded request retries · optional token pacing)"]
        Utils["utils.py<br/>(JSON extraction from LLM output)"]

        FastAPI --> WsRouter
        FastAPI --> AgentRouter
        FastAPI --> RunRouter
        FastAPI --> CriteriaRouter
        FastAPI --> PromptRouter
        FastAPI --> TestsRouter
        FastAPI --> VersionsRouter
        FastAPI --> ExportRouter
        FastAPI --> Worker
        Routers --> ApiSupport
        Routers --> Core
        Routers --> DbHelpers
        Routers --> Candidates
        Routers --> Queue
        Worker --> Queue
        Worker --> Handlers
        Worker --> Reconcile
        Handlers --> Core
        Handlers --> DbHelpers
        Handlers --> Candidates
        Reconcile --> Core
        Reconcile --> DbHelpers
        Reconcile --> Queue
        Queue --> DbModels
        DbHelpers --> DbModels
        Candidates --> DbModels
        Reconcile --> DbModels
        Core --> Prompts
        Core --> GroqClient
        Core --> Utils
        AgentRouter --> GroqClient
    end

    subgraph Tools["Phase 4 tools — run from the repo root, outside the web process"]
        direction TB
        GateCli["evalgate/cli.py<br/>(flags · exit codes 0, 1, 2, 3 · text, json, markdown output · GITHUB_STEP_SUMMARY)"]
        GateSuite["evalgate/suite.py<br/>(load and validate eval-discovery.suite v1 · select tests)"]
        GateRunner["evalgate/runner.py<br/>(replay and judge each test · --repeat majority vote · exit_code)"]
        SimRun["simulation/run.py · report.py<br/>(resumable runner · results page generator)"]
        SimArms["simulation/arms.py<br/>(baseline and flywheel arms · path recording)"]
        SimDriver["simulation/appdriver.py<br/>(the real app through a test client, in-memory SQLite, worker run inline)"]
        SimRef["simulation/reference.py<br/>(independent reference judge)"]
        SimFake["simulation/fakeclient.py · clients.py<br/>(deterministic fake model for mock mode · counting wrapper)"]
        MetricsScript["scripts/metrics.py<br/>(read-only event metrics)"]

        GateCli --> GateSuite
        GateCli --> GateRunner
        GateRunner --> Replay
        GateRunner --> Judge
        SimRun --> SimArms
        SimRun --> SimFake
        SimArms --> SimDriver
        SimArms --> SimRef
        SimArms --> GroqClient
        SimRef --> Judge
        SimDriver --> FastAPI
        SimFake -.->|"replaces the client in mock mode"| GroqClient
    end

    subgraph GroqAPI["Groq API"]
        Chat20["openai/gpt-oss-20b<br/>(plays the agent under test · replays)"]
        GPT["openai/gpt-oss-120b<br/>(prompt analysis · pattern detection · judge · suggest fix · generate test · report)"]
        Qwen["qwen/qwen3.8-27b<br/>(criteria generation)"]
    end

    DB[("Postgres / SQLite")]

    ApiClient -->|"HTTP / JSON"| FastAPI
    DbModels --> DB
    MetricsScript -->|"reads the event table"| DB
    GroqClient --> Chat20
    GroqClient --> GPT
    GroqClient --> Qwen
```

---

### 3.4 Build-Tests and Replay Sequence

How a discovered criterion becomes tests, and how a prompt edit is checked. The request returns at once; the worker does the LLM work and the browser polls.

```mermaid
sequenceDiagram
    actor Dev as Developer (browser)
    participant API as FastAPI routes
    participant DB as Database
    participant W as Worker loop (jobs/)
    participant J as Judge (gpt-oss-120b, temp 0)
    participant A as Agent model (gpt-oss-20b, temp 0)

    Dev->>API: POST /agents/id/criteria/discover
    API->>DB: insert criterion, enqueue build_tests job
    API-->>Dev: 201 criterion + job_id
    W->>DB: claim_next (queued to running, attempts + 1)
    W->>J: sanity_check: grade the criterion's own good and bad example
    loop up to the 8 most recent runs, oldest first, stop after 2 failures
        W->>J: judge(criterion, saved run transcript)
        J-->>W: pass, fail or not_applicable + rationale
    end
    W->>DB: tests (first fail = fix, next = heldout), baseline results, judge_check, job done
    Dev->>API: GET /jobs/id (polled until done)
    Dev->>API: GET /agents/id/tests

    Dev->>API: PUT /agents/id/prompt
    API->>DB: new numbered prompt version (current)
    API-->>Dev: version number
    Dev->>API: POST /agents/id/tests/run-all
    API->>DB: enqueue one replay job per test with no current replay on this version and no active job
    API-->>Dev: 202 job_ids (empty when every test already has a current replay)
    loop each replay job
        W->>DB: claim_next
        W->>A: replay the test's saved user messages against the current prompt
        A-->>W: new transcript
        W->>J: judge(criterion, new transcript)
        J-->>W: verdict + rationale
        W->>DB: replay result for this prompt version, job done
    end
    Dev->>API: GET /jobs/id, then GET /agents/id/tests (status per criterion)
```

If the worker dies mid-job, `requeue_stale` puts the job back after 180 s (at most 3 attempts, then it fails with a plain message). A judge or agent failure is recorded on the job and writes no result row. The one exception to "fails at once" is a verification replay (queued by Approve, payload `system: true`): a transient agent or judge failure there raises `RetryableJobFailure`, which the worker requeues like a crash (at most 3 attempts, no sleep) before failing the job with the fixed message.

---

### 3.5 Candidate State Machine (Phase 3)

A suggested prompt is stored as a candidate version and moves through these states. Only `reconcile_verifying` (code, see [6.11](#611-the-fix-loop-and-promotion)) leaves `approved_verifying`, so a candidate can never be stranded there while the worker runs. A manual edit also creates a `promoted` version (origin `manual`), and the first version of an agent is `promoted` with origin `initial`.

```mermaid
stateDiagram-v2
    [*] --> proposed : suggest_fix job stores a suggested version
    [*] --> promoted : initial version, manual edit, or restore (origin restored, a NEW version)
    proposed --> approved_verifying : approve
    proposed --> rejected : reject
    proposed --> superseded : manual edit, restore or another promotion
    approved_verifying --> promoted : reconcile, the promotion rule passes
    approved_verifying --> failed_verification : reconcile, the rule fails or verification cannot finish
    promoted --> superseded : a newer version goes live
    rejected --> [*]
    failed_verification --> [*]
    superseded --> [*]
```

`failed_verification` and `superseded` versions can still be restored, except a suggestion that was never approved (it has no verification record, for example a proposal a manual edit superseded: its text was never checked, so restore refuses it and the history labels it "never approved"). Restore copies the text into a new `promoted` version and never changes the old row. Chat uses `agent.current_prompt_version_id`, which moves only on promotion, manual edit or restore.

---

### 3.6 Suggest, Approve and Verify Sequence (Phase 3)

How a failing test becomes a promoted version. Every LLM step is a job; Approve itself makes no LLM call.

```mermaid
sequenceDiagram
    actor Dev as Developer (browser)
    participant API as FastAPI routes
    participant DB as Database
    participant W as Worker loop (jobs/)
    participant R as reconcile_verifying (db/)
    participant A as Agent model (gpt-oss-20b, temp 0)
    participant J as Judge (gpt-oss-120b, temp 0)
    participant S as Suggester and report writer (gpt-oss-120b)

    Dev->>API: POST /tests/id/suggest-fix (fix test, failing on the current prompt)
    API->>DB: enqueue suggest_fix job
    W->>S: current prompt, criterion, failing rationale and transcript (never the held-out test)
    S-->>W: replacement prompt, failing turn index, why fields
    W->>DB: proposed version, why report with the quote cut from the stored transcript by code
    Dev->>API: GET /prompt-versions/id and GET /prompt-versions/id/report
    Dev->>API: POST /prompt-versions/id/approve
    API->>DB: lock agent row, re-read the version (must be proposed)
    API->>DB: compute required set, store verification, status approved_verifying
    API->>DB: enqueue one replay job per required test (payload system true), one commit
    API-->>Dev: 202 version_id, status, jobs
    loop each replay job
        W->>DB: claim_next
        W->>A: replay the test's saved user messages against the candidate prompt
        A-->>W: new transcript
        W->>J: judge(criterion, transcript)
        J-->>W: verdict and rationale
        W->>DB: replay result for the candidate version, job done
    end
    Note over W,R: every worker pass ends with reconcile_verifying, also an idle one
    W->>R: reconcile_verifying
    R->>DB: lock agent row, re-check status is still approved_verifying
    Note over R,DB: if the candidate's parent is no longer the current version it fails instead of replacing it
    alt every required test has a current replay
        R->>R: decide_promotion (pure code, no LLM)
        R->>DB: promoted (agent moves to it, the old version is superseded) or failed_verification
    else stored verdicts already decide failure, before every replay has finished
        R->>DB: failed_verification with the reason, queued replays of that version cancelled
    else a required job failed or was discarded, data is malformed, or results are still missing 30 minutes after approval with no replay queued or running
        R->>DB: failed_verification with the reason
    end
    R->>DB: before and after table stored, change_report job queued
    W->>S: diff, table and rationales as delimited data
    S-->>W: explanation, next step and cited test ids
    W->>DB: narrative stored (citations not in the table are dropped)
    Dev->>API: GET /agents/id/tests and GET /prompt-versions/id/report (polled)
```

Reconcile also covers the cases no single job can: a restart between the last replay and the reconcile (the next pass resolves it), a replay job that failed, was discarded (the criterion changed) or was lost, and malformed `verification` data ("Verification could not be completed."). With results still missing, no replay job queued or running and none failed or discarded, the candidate fails 30 minutes after approval ("Verification timed out."); a queued or running replay keeps it waiting (a stuck one ends through stale-job recovery), and with the worker disabled nothing resolves it. It is idempotent and takes the agent row lock, so overlapping passes cannot resolve a version twice.

**Fail fast (Phase 4).** Reconcile no longer waits for every replay when the stored verdicts already decide the outcome. `core/promotion.early_failure(required, verdicts)` takes the verdicts present so far (a missing verdict never decides anything) and returns a failing decision when the fix or held-out verdict is not `pass`, or any regression verdict is `fail`; it never returns a promotion. A fix or held-out test whose own replay jobs failed or were discarded, with none queued or running, is decisive as well ("A test could not be run: ..." or "A criterion changed during verification."); a missing regression result never is. A failed candidate gets the same reason strings as before and a table with `after = null` for the tests that never ran, and its still-queued replay jobs (matched on the candidate version and the required test ids) are set to `failed` with the error "Cancelled: verification already failed." by an update that re-checks `status = 'queued'`, so a job a worker claims at the same moment is not overwritten. A replay that is already running is not interrupted: `handle_replay` returns `{"skipped": true}` for a `system` job whose version is no longer `approved_verifying`, checked before the model calls and again after them, and stores nothing, so a late result never lands on a resolved version. The cancellation is scoped by candidate version and test, so it would also cancel a queued replay that a user started for that same candidate version; that is acceptable and not reachable in normal use, because a verifying candidate is never the current version.

---

### 3.7 Export and the CI Gate (Phase 4)

The export is a read-only route, `GET /agents/{id}/export` (workspace-scoped; 404 for another workspace's agent, 409 "Agent has no current prompt." for an agent without one). It returns the `eval-discovery.suite` v1 document and nothing else (no results, transcripts, runs, events or workspace data):

```json
{
  "format": "eval-discovery.suite",
  "version": 1,
  "agent": {"name": "...", "user_role_label": "..."},
  "prompt": {"text": "...", "version_number": 3},
  "criteria": [
    {
      "id": "...", "name": "...", "definition": "...", "good_example": "...", "bad_example": "...",
      "tests": [
        {"id": "...", "role": "fix", "origin": "run", "scripted_user_messages": ["..."], "current_verdict": "pass"}
      ]
    }
  ]
}
```

`current_verdict` is the verdict of the test's newest non-stale replay on the live prompt version (`pass`, `fail`, `not_applicable`), or `null` when there is none. The frontend's "Export suite for CI" button saves the response as `eval-suite.json`. The export contains the live prompt text and the scripted customer messages from saved runs, so check it for personal data before committing it to a repository.

`evalgate` (`python -m evalgate`, run from the repository root) reads that file and a prompt file. It imports only `backend.core` and `backend.groq_client` (a test imports it in a subprocess and asserts that neither FastAPI nor SQLAlchemy is loaded). Flow:

```mermaid
flowchart TD
    Start["python -m evalgate --suite FILE --prompt FILE"] --> Valid{"Suite and prompt valid?"}
    Valid -->|No| X2["exit 2"]
    Valid -->|Yes| Select["Select tests: current_verdict is pass<br/>(every test with --all), count the skipped"]
    Select --> Any{"Any test selected?"}
    Any -->|No| X3a["exit 3: nothing runnable"]
    Any -->|Yes| Key{"GROQ_API_KEY set and not a placeholder?"}
    Key -->|No| X3b["exit 3: no key"]
    Key -->|Yes| Run["Per test, K runs (--repeat):<br/>replay on the prompt file, then judge at temperature 0"]
    Run --> Vote["Per test: a strict majority of passes is a pass;<br/>error only when every run errored"]
    Vote --> Ran{"Any test errored, or no pass or fail verdict at all?"}
    Ran -->|Yes| X3c["exit 3: the gate could not run"]
    Ran -->|No| Rate{"passes / (passes + fails)<br/>at or above the threshold?"}
    Rate -->|Yes| X0["exit 0"]
    Rate -->|No| X1["exit 1"]
```

| Exit | Meaning |
|------|---------|
| `0` | Pass rate at or above the threshold (default 1.0) |
| `1` | Pass rate below the threshold |
| `2` | Usage error, or an invalid or missing suite or prompt file (checked before anything else) |
| `3` | The gate could not run: no test selected (after skipping), `GROQ_API_KEY` missing or a placeholder, any test errored after the client's retries (with `--repeat K` a test is `error` only when every run of it errored), no test applicable, or an unexpected exception |

**Errors under `--repeat K`.** The error rule is per test, not per run (decision P3): a test is `error` only when every one of its runs errored. Two passes and one error counts as a pass and the gate exits 0; one pass and two errors is a `fail` (a strict majority of passes is required) and exits 1, so a provider outage that hits only some runs can be reported as a regression instead of a `3`. `runner.exit_code` also returns 3 when any outcome has verdict `error`, independent of the counters.

Design rules, each visible in the code: the gate never reports success when it could not run (`runner.exit_code` returns 3 for any error, an empty run or no graded verdict); `not_applicable` is reported separately and left out of the rate; the judge is called with an empty agent prompt, so it never sees the prompt; provider exception text is never stored (each failure maps to a fixed message such as "The judge was unavailable."); the key comes only from the environment mapping and no output path formats it; a `.env` file is read only with `--load-dotenv` (the module sets `GROQ_SKIP_DOTENV` before the Groq client can be imported). The suite loader refuses anything over 60 tests, 20 messages per test, 2000 characters per message, a 4000-character prompt file or a 2,000,000-byte file, and its error messages name the field and never echo input values. Output is text, JSON or markdown on stdout, and when `GITHUB_STEP_SUMMARY` is set a markdown table is appended to it.

**Regression semantics.** By default the gate selects only tests whose exported `current_verdict` is `pass` (the behaviors that worked at export) and prints how many it skipped; `--all` runs every test. With the default, a customer's first run does not fail on a criterion that was never fixed. **Flakiness:** replies vary even at temperature 0, so `--repeat K` (1 to 9) takes a strict majority of K runs per test; there is no other mitigation, and `--pause` only spaces calls out.

**The Action and workflows.** `.github/actions/eval-gate/action.yml` is a composite Action: Python setup, `pip install -r requirements-gate.txt` (`groq`, `python-dotenv`), then `python -m evalgate ... --format markdown` with `PYTHONPATH` set to the Action's own repository root, the key passed through `env`, and the other inputs passed through `env` rather than interpolated into the script. `.github/workflows/eval-gate-demo.yml` runs it on pull requests that change `examples/eval-gate-demo/prompt.md`. `.github/workflows/ci.yml` runs on push and pull request with read-only permissions: a `backend` job (`python -m pytest -q`), a `postgres` job (Postgres 16 service container, `alembic upgrade head`, `python -m pytest tests/pg -q`) and a `frontend` job (`npm ci`, lint, test, build). None of these workflows has been run on GitHub from the development environment (see [6.15](#615-verified-and-not-verified-phase-4)). Pull requests from forks receive no secrets, so the gate exits 3 there.

`tests/pg/` is the real-Postgres suite (migrations over populated rows, reconcile fail-fast, thread-based approval races through `TestClient` with a session per request). It is skipped unless `TEST_DATABASE_URL` is set, and `tests/pg/guard.py` refuses a database whose name does not contain `test` unless `ALLOW_PG_TEST_RESET=1`, because the fixtures run `DROP SCHEMA public CASCADE`.

---

### 3.8 The Simulation (Phase 4)

`simulation/` measures "preview runs until the first judged pass" with and without the loop. It is a harness for a comparison, not a result: what it prints is simulated. The first real-model run is on [simulation-results.md](simulation-results.md) (generated by `simulation.report`; its raw trial files are committed under `simulation/results/real/`, added with `git add -f` because that directory is otherwise ignored) and did not show the loop reducing preview runs; see [6.15](#615-verified-and-not-verified-phase-4).

**Preview and judge.** A preview is two customer messages written by a synthetic builder (`MODEL_CHAT`, a model playing a no-code newcomer who has not seen the agent's instructions), each answered by the agent on its current prompt with the same model, token cap and retry bound as the app's chat route; then the **reference judge** (`simulation/reference.py`) grades the transcript against the scenario's own goal through the app's `core.judge.judge` with an empty agent prompt. Only `pass` converges. The goal is a fixed per-scenario criterion, never the criterion the app discovers, and nothing the app produces feeds the judge. A trial stops at the first pass or at 12 previews and returns a result either way (`converged`, `previews`, `path`, model calls and tokens per model through a `CountingClient`, wall time, notes); a trial that never passes is reported, never dropped or raised.

```mermaid
flowchart TD
    Run["simulation.run: per scenario, trial and arm<br/>(each result file saved as the trial finishes)"] --> Arm{"Arm"}
    Arm -->|baseline| ML["Manual loop: preview, reference judge;<br/>after a fail rewrite the prompt (MODEL_JUDGE, shown the goal), preview again,<br/>stop at the first pass or at 12 previews"]
    Arm -->|flywheel| WS["Fresh workspace in the in-process app"]
    WS --> FP["Preview through the app's chat route, then save the run"]
    FP --> FJ{"Reference judge: pass?"}
    FJ -->|Yes| Done["Trial ends: record converged, previews, path,<br/>model calls, tokens, wall time, notes"]
    FJ -->|No| Cap{"12 previews used?"}
    Cap -->|Yes| Capped["Trial ends not converged: 12 previews without a pass,<br/>path none, still recorded"]
    Cap -->|No| Tried{"App flow already attempted?"}
    Tried -->|Yes| FB["Fall back to the manual loop (path fallback)"]
    Tried -->|No| Two{"Two runs saved?"}
    Two -->|No| FP
    Two -->|Yes| Flow["App flow, one attempt: check-pattern, discover, build tests,<br/>fix test fails on the live prompt, held-out (optional),<br/>suggest, auto-approve, verify"]
    Flow --> Prom{"Candidate promoted?"}
    Prom -->|Yes| FP
    Prom -->|No| FB
    FB --> ML
    ML --> Done
    ML -->|"12 previews without a pass"| Capped
```

Every flywheel trial records its `path`: `loop` (the first judged pass came after a promoted fix), `fallback` (after a manual rewrite) or `none` (never converged; or, for a converged trial, it passed with no prompt change and no app fix, which the page reports as "converged with no fix needed"). A converged baseline trial records `fallback`, because manual rewrites are its only mechanism; the page shows path counts for the flywheel only. If the app flow raises or a step does not go to plan, the arm falls back, so a failure of the product cannot silently drop a trial.

**Modes and seams.** The one model seam is the client object `backend.groq_client.client`. In **mock mode** it is replaced by a deterministic fake whose `chat.completions.create` routes on the distinctive system text of each call type (agent chat, builder, judge, analysis, pattern, criterion, suggester, generator, report) and whose `with_options(...)` returns itself, so retry-bounded calls are caught too; a system text nobody wrote a reply for raises instead of getting a default. In **real mode** (`--mode real`, needs `GROQ_API_KEY`) the real client is used, `GROQ_TPM_LIMIT` pacing is on (default 7000), a rough estimate is printed first, and every trial is written to `simulation/results/<mode>/` as it finishes so `--resume` continues a stopped run. The runner sets `GROQ_SKIP_DOTENV`, so `.env` is read only with `--load-dotenv`.

**Measurement-honesty rules.** Both arms share the scenario prompt, builder model, caps and reference judge. Customer messages use `MODEL_CHAT`; the baseline's rewrite uses `MODEL_JUDGE`, the app suggester's model, so the loop does not win by using a stronger one. Non-converged trials count at the cap in the statistics and are listed. Every table says "simulated" and names N (requested and on file), the models and the date, and an incomplete run (missing trials, unbalanced arms) gets a visible warning. Mock output carries the banner "MOCK MODE: harness test only, the numbers are meaningless." and `simulation.report` refuses to write it to `docs/simulation-results.md`. The page never compares a number with the strategy document's illustrative targets.

**Limitations (stated on the results page too).**

- The baseline's rewriter is shown the full goal criterion; the flywheel's app-driven fix (check-pattern, discover, suggest, approve) never is, though its fallback rewrites are, like the baseline's (and the customer-message builder sees the goal in both arms). This favours the baseline.
- Both judge calls, the reference judge and the app's own, use `MODEL_JUDGE`, so they share model bias. In mock mode independence is structural only: the reference judge is a separate code path, but the fake answers both judge calls with the same rule (does the transcript contain the scenario's violation text).
- The flywheel gets one attempt at the app flow and then falls back to manual rewrites, so a fallback trial starts roughly two previews behind the baseline.
- A flywheel trial that passes its first preview with no fix is "converged with no fix needed", not a win for the loop.
- The app limits a workspace to 30 jobs per hour, so each trial uses a fresh workspace; the arms are not rate-limited by it.
- An LLM plays the builder (more patient and literal than a person), the same model family plays builder and agent, N is a handful of trials, and free-tier pacing dominates wall time.

---

## 4. Data Model

Ten tables (Alembic migrations `migrations/versions/0001_foundation.py`, `0002_tests_and_jobs.py` and `0003_evolve.py`; Phase 3 adds columns, no tables; Phase 4 adds neither). Ids are string UUIDs and timestamps are naive UTC so SQLite and Postgres behave alike. Every foreign key is `ON DELETE CASCADE`.

```mermaid
erDiagram
    workspace ||--o{ agent : owns
    workspace ||--o{ event : logs
    workspace ||--o{ job : queues
    agent ||--o{ prompt_version : has
    agent ||--o{ behavior_map : has
    agent ||--o{ run : has
    agent ||--o{ criterion : has
    prompt_version ||--o{ run : "run used"
    criterion ||--o{ test_case : has
    test_case ||--o{ test_result : has

    workspace {
        string id PK
        string token_hash UK
        datetime created_at
        datetime expires_at
    }
    agent {
        string id PK
        string workspace_id FK
        string name
        string user_role_label
        string current_prompt_version_id
        int last_checked_at_run
        text pattern_summary
    }
    prompt_version {
        string id PK
        string agent_id FK
        text text
        string parent_id
        string origin
        string status
        int number
        json change_report
        json verification
        string source_test_id
    }
    behavior_map {
        string id PK
        string agent_id FK
        string prompt_hash
        json analysis
    }
    run {
        string id PK
        string agent_id FK
        string prompt_version_id FK
        int position
        json messages
    }
    criterion {
        string id PK
        string agent_id FK
        int position
        string name
        text definition
        text good_example
        text bad_example
        text why_discovered
        string source
        json judge_check
        int revision
    }
    event {
        string id PK
        string workspace_id FK
        string name
        json properties
    }
    test_case {
        string id PK
        string criterion_id FK
        string source_run_id
        string role
        string origin
        json scripted_user_messages
    }
    test_result {
        string id PK
        string test_id FK
        string prompt_version_id
        string kind
        string verdict
        text rationale
        json transcript
        boolean stale
    }
    job {
        string id PK
        string workspace_id FK
        string type
        json payload
        string status
        int attempts
        json result
        text error
        datetime locked_at
    }
```

| Table | Notes |
|-------|-------|
| `workspace` | Only the SHA-256 hash of the token is stored (`token_hash`, unique). `expires_at` is 30 days after creation. |
| `agent` | `current_prompt_version_id` is deliberately not a foreign key (it would be circular with `prompt_version`). `last_checked_at_run` is the delta cursor; `pattern_summary` holds the unconsumed pattern and is cleared when a criterion is discovered from it. |
| `prompt_version` | The initial version is written when the agent is created. A manual prompt edit (`PUT /agents/{id}/prompt`) adds a row (`origin = "manual"`, `status = "promoted"`; the previous one becomes `superseded`) and points `agent.current_prompt_version_id` at it. `number` is the 1-based per-agent version (the "v2" label), unique per agent (`uq_prompt_version_agent_number`, migration `0003`); a candidate gets its number when it is proposed (`max + 1`). `origin` is `initial`, `manual`, `suggested` or `restored`; `status` is `proposed`, `approved_verifying`, `promoted`, `superseded`, `rejected` or `failed_verification` (see [3.5](#35-candidate-state-machine-phase-3)). `parent_id` is the version a suggestion or restore was made from. Phase 3 adds `change_report` (`{"why": ..., "outcome": ...}`), `verification` (`{"strength", "fix_test_id", "approved_at", "required": [{"test_id", "role", "before"}]}`) and `source_test_id` (the failing test that motivated a suggestion, a plain id and not a foreign key so it adds no second delete path). Counted versions are capped at 20 per agent, see [6.6](#66-limits). |
| `behavior_map` | `analysis` JSON: `user_role_label`, `behaviors` (name, description, trigger terms) and `starter_messages`. `prompt_hash` ties it to the prompt text it was built from. |
| `run` | One saved conversation. `position` is 1-based per agent and unique with `agent_id`, so two concurrent saves cannot take the same slot (the loser gets a 409). `messages` is the transcript JSON. |
| `criterion` | `source` is `discovered` or `manual`; `position` orders them. `judge_check` holds the judge sanity-check outcome (`passed`, `good_verdict`, `bad_verdict`); it is null until tests are built and cleared when the definition or examples are edited. `revision` (migration `0003`, default 0) is bumped whenever `definition`, `good_example` or `bad_example` changes; jobs record it when they start and discard their result if it changed. |
| `event` | Product event log, see [6.5](#65-events). |
| `test_case` (model `EvalTest`) | One runnable test for a criterion: `role` is `fix` or `heldout` (unique per criterion), `scripted_user_messages` are the saved run's user messages (first 20). `source_run_id` is a plain id, not a foreign key, so there is no second delete path through `run` (null for a generated test). `origin` (migration `0003`, default `run`) is `run` or `generated`. |
| `test_result` (model `EvalResult`) | A judge verdict (`pass`, `fail`, `not_applicable`) with rationale and the transcript judged. `kind` is `baseline` (the original saved run, stored only for runs that became tests) or `replay`; only replays feed status. `prompt_version_id` is a plain id (no FK). `stale` is set when the criterion's judged fields change. The replay handler keeps only the latest replay result per test and prompt version (it deletes older replays for the same pair when it stores a new one; baselines are untouched, and migration `0003` itself deletes nothing). |
| `job` | Queue row: `type` (`build_tests`, `replay`, `suggest_fix`, `generate_heldout` or `change_report`), `payload`, `status` (`queued`, `running`, `done`, `failed`), `attempts`, `locked_at`, `result` or a user-safe `error`. Cascades from the workspace. |

---

## 5. Pattern-Detection State Machine

The UI runs an explicit finite-state machine for the pattern-detection lifecycle. It is **no longer persisted**: on load the frontend derives the starting state from what the server returns (`derivePatternState`): a stored `pattern_summary` gives `pattern_found`, otherwise any existing criteria give `complete`, otherwise `idle`. States such as `checking`, `generating` and `error` are transient and live only in the page.

```mermaid
stateDiagram-v2
    [*] --> idle
    idle --> checking : saveRun() returns check_due=true
    checking --> pattern_found : API returns found=true
    checking --> pattern_not_found : API returns found=false
    checking --> error : API fails
    error --> idle : allows retry next save
    pattern_not_found --> idle
    pattern_found --> generating : user clicks Discover evals
    generating --> complete : criterion returned
    generating --> error : API fails
    complete --> generating : user clicks Discover evals again
```

(This is the pattern-detection lifecycle only. The lifecycle of a suggested prompt version is in [3.5](#35-candidate-state-machine-phase-3).)

The decision to check is made by the server, not the browser: `check_due` in the save-run response is true when at least 2 runs are new since the last check **and** a behavior trigger term appears in user messages of at least 2 of them (see [6.3](#63-delta-batching-and-keyword-pre-filter)).

---

## 6. Key Design Decisions

### 6.1 Anonymous Workspaces

There are no accounts. The first API call (`POST /workspaces`) returns a random token; the browser keeps it in `localStorage` and sends it as `X-Workspace-Token`. The backend stores only its SHA-256 hash, and every agent, run and criterion is reached through a workspace the token proves ownership of (a foreign or missing agent is a 404, an unknown or expired token a 401).

**Why:** A portfolio-scale tool should not need sign-up to try, but state must survive reloads and return visits. A bearer token gives that with no identity data to protect.

**Retention:** a workspace expires 30 days after creation. Expired workspaces are purged when the backend starts (one pass in the app lifespan), and `DELETE /workspaces/me` removes a workspace and all its data at once.

### 6.2 Agent Config and Presets

An agent is a name, a system prompt, a user-role label and an analysis. Presets (`alex-sales`, `support`, `booking`) **ship their analysis** with them, so choosing one needs no LLM call. A pasted prompt is analyzed once by `analyze_agent` (gpt-oss-120b), which returns 3–8 behaviors with trigger terms, the role label and exactly three starter messages; the result is validated strictly and stored as a `behavior_map`.

**Why:** This replaces the earlier hardcoded keyword list that only suited one persona. Testing logic is now data derived per agent.

### 6.3 Delta Batching and Keyword Pre-filter

Each agent keeps `last_checked_at_run`. A pattern check only sends runs with `position` above it, and after a successful check it advances only to the last run it analyzed, so a run saved during the LLM call stays in the next delta. Before offering a check, the server runs `keyword_prefilter`: some trigger term from the agent's behavior map must appear in user messages of at least 2 different new runs.

**Why:** Fewer pointless LLM calls and smaller prompts as a session grows, with no keyword list baked into the code.

### 6.4 Prompt-Injection Handling

Pasted agent prompts, transcripts and pattern summaries are untrusted. Prompt builders wrap them in delimiter tags (`<agent_prompt>`, `<transcripts>`, `<pattern_summary>`, `<criterion>`), neutralize any such tag inside the content (the angle brackets are escaped, so it cannot close the wrapper), and the system text states that tagged content is data to analyze, never instructions.

### 6.5 Events

A minimal product event log records `agent_created`, `run_saved`, `loop_detected`, `criterion_created`, `test_created`, `replay_started`, `replay_passed` and `replay_failed` with small property dicts. Phase 3 adds `test_generated`, `fix_suggested`, `fix_approved`, `fix_rejected`, `fix_verified`, `fix_verification_failed`, `fix_promoted`, `version_restored` and `report_viewed`. Writes go through `try_log_event`, which is best-effort: a failure rolls back, is logged, and never fails the user's request.

### 6.6 Limits

| Limit | Value |
|-------|-------|
| Agents per workspace | 5 (also re-checked inside the create transaction) |
| Runs per workspace | 50 |
| System prompt length | 4000 characters |
| Message length / messages per request | 2000 characters per user message, 16000 per assistant message / 40 (120000 characters total per request). A saved run may hold one more message and one more assistant message of text, so it can include the reply to the last message |
| Criteria per agent | 30 |
| Prompt versions per agent | 20 counted (every version except `rejected` and `failed_verification`), and a hard cap of 60 rows including those. A manual edit or restore beyond either gets a 429 "Prompt version limit reached for this agent."; a suggestion that hits it fails its job with the same message |
| Open candidates per agent | 1 (`proposed` or `approved_verifying`; a second suggestion fails with "A fix is already proposed or being verified.") |
| While a fix verifies | Manual prompt edit, restore and edits to a criterion's `definition`, `good_example` or `bad_example` get a 409 "A fix is being verified. Wait for it to finish." (a `name` edit is allowed), and so does approving another version while one verifies. A second approval of the same version gets 409 "This fix can't be approved in its current state." (it is no longer `proposed`). Suggest fix gets the open-candidate 409 above |
| Stale draft | `PUT /agents/{id}/prompt` with an optional `base_version_id` that is no longer current gets a 409 "The prompt changed to v{n} while you were editing. Reload the prompt to continue." |
| Background jobs | 30 started per workspace per hour (429 beyond that; discover still returns the criterion, with `job_id: null`). Exempt: the replay jobs queued by Approve (payload `system: true`) and `change_report` jobs. Suggest fix and generate held-out count |
| Job queue depth | 100 queued jobs across all workspaces (429 "The test queue is busy" beyond that; run-all is all-or-nothing; discover still returns the criterion, with `job_id: null`). Approve is checked against it before any state changes; the report job queued by reconcile ignores it |
| Job staleness / attempts | a `running` job older than 180 s is requeued; at most 3 attempts |
| Replay length | the first 20 user messages of the saved run |
| Generated held-out scenario | 2 to 5 messages of at most 2000 characters, up to 3 attempts per job |
| Suggested prompt | at most 4000 characters (otherwise the job fails) |
| Verification | a candidate with missing results and no queued or running replay job fails 30 minutes after approval. A queued or running replay keeps it waiting, and with the worker disabled nothing resolves it |
| Runs sent to pattern detection | the most recent 10 unchecked |
| LLM-backed calls | 30 per minute per client, plus a process-wide budget of 600 per minute (`LLM_GLOBAL_PER_MINUTE`). The replay, run-all, build-tests, suggest-fix and generate-heldout enqueue routes spend one of these per accepted request (a dedupe hit that returns an existing job does not). Approve, reject and restore do not |
| Groq client | 45 s timeout per call. 5 retries in a job handler (the SDK backs off between them); the request routes that call the model (chat, create agent, discover, check-pattern) run inside `request_retries(2)`, so they retry at most 2 times (Phase 4) |
| Token pacing | Optional, off unless `GROQ_TPM_LIMIT` is a positive integer: a sliding 60 s window per model records `usage.total_tokens` (when the reply carries none, an estimate of the prompt's characters divided by 4 plus the call's `max_tokens`, or 1000 if it sets none); before a call, if the window plus an estimate would pass the limit the call waits for the oldest record to leave the window, at most 20 s, then proceeds (Phase 4) |
| Workspace creation | 20 per minute per client |

The LLM calls a background job makes are NOT counted by the per-client or process-wide limiters above (they happen in the worker, not in a request). The jobs a person starts (replay, run-all, build, suggest fix, generate held-out) are bounded by the hourly per-workspace job cap, the global queue-depth cap and the Groq client timeout, so a single stuck call cannot hold the one worker for more than a few minutes. Approve's replays and the report job are exempt from the hourly cap: they are bounded by the 100-job queue cap and by the single worker, which runs one job at a time.

**Operational note: Groq's free tier.** The free tier limits `gpt-oss-120b` to 8000 tokens per minute. One build or one verification fan-out (several judge calls in a row) can hit it. A call that is rate limited is retried by the client (5 retries in a job, 2 on a request route since Phase 4); if it still fails the job fails with "The judge was unavailable. Try again.", except a verification replay, which the worker retries up to its 3 attempts. Consider a paid tier, spacing jobs, or setting `GROQ_TPM_LIMIT` (for example `7000`) so the client paces itself; pacing is a bounded wait (20 s at most), so it lowers the chance of a 429 and does not remove it. One live run is not a capacity test.

Limiters are in-memory sliding windows, per process: they reset on restart and are not shared across instances. The client key is derived from `X-Forwarded-For` by taking the Nth entry from the right, where N is `TRUSTED_PROXY_HOPS` (default 1; 0 uses the direct peer address; a missing or too-short header also falls back to the peer address). Entries to the left of that are client-supplied and ignored.

### 6.7 Model Assignments

| Task | Model | Reasoning |
|------|-------|-----------|
| Conversational chat (plays the agent under test) and test replay | openai/gpt-oss-20b | Low latency for interactive chat; 20B is enough for persona roleplay. Replays use temperature 0 |
| Prompt analysis and pattern detection | openai/gpt-oss-120b | Higher accuracy for classification over a long prompt or several transcripts |
| Judge (verdicts and the sanity check) | openai/gpt-oss-120b, temperature 0 | The larger model for grading against a rubric; temperature 0 for repeatability (not a guarantee) |
| Fix suggestion, held-out scenario generation, change-report narrative | openai/gpt-oss-120b, temperatures 0.2, 0.7 and 0 | Same model as the judge; the suggester is slightly creative, the generator needs variety, the report writer must be repeatable. None of them can decide a verdict or a promotion |
| Structured criteria generation | qwen/qwen3.8-27b | Reasoning-capable model for grounded, structured JSON rubrics (reasoning output hidden) |
| Simulation (Phase 4) | customer messages: openai/gpt-oss-20b; baseline prompt rewrite and the reference judge: openai/gpt-oss-120b | The rewrite uses the app suggester's model so the loop does not win by using a stronger one; the reference judge is the app's judge function with a different criterion and an empty agent prompt |
| CI gate (Phase 4) | replay: openai/gpt-oss-20b, judge: openai/gpt-oss-120b, temperature 0 | The same functions and models the app uses for a replay and a verdict |

### 6.8 Judge

`core/judge.py` grades one transcript against one criterion and returns `pass`, `fail` or `not_applicable` with a rationale. The criterion fields and the transcript are wrapped in delimiter tags (`<criterion>`, `<transcripts>`) and neutralized like every other pasted text, and the system text says tagged content is data, never instructions. The judge **never sees the agent's system prompt**, and the judge system text is a fixed string with no agent name or role label (the transcript's speaker labels carry them, as data). Unparseable output, an unknown verdict, an empty rationale or a failed call raises `JudgeFailed`: the job fails with a plain message and no result row is written.

**Sanity check.** When tests are built, the judge must also pass the criterion's own good example and fail its bad example. The outcome is stored in `criterion.judge_check`; if the check itself cannot run (for example the judge is rate limited), the build or replay still succeeds without it and the next replay tries again. If it is wrong either way the UI shows verdicts as "low confidence". This check is weak by construction: the example appears both in the criterion text and as the transcript being graded, so it only catches a judge that cannot read the criterion at all, not one that is subtly miscalibrated.

**Status** (`core/testsuite.criterion_status`, never the LLM): any test whose current replay is `fail` gives `failing`; else any test with no current replay gives `untested`; else any `pass` gives `passing`; else `untested`. "Current" means the newest non-stale `replay` result for the agent's current prompt version. Run all (since Phase 3) replays only tests that have no current replay, so the banner "N tests haven't run on prompt v{x} yet." and the button agree.

### 6.9 Background Jobs

`jobs/queue.py` is a plain-function queue over the `job` table. `claim_next` takes the oldest `queued` job with `SELECT ... FOR UPDATE SKIP LOCKED` (a no-op on SQLite), marks it `running`, stamps `locked_at`, counts the attempt and commits before returning. `requeue_stale` runs at the start of every worker pass: a `running` job whose lock is older than 180 s goes back to `queued`, or to `failed` once it has used 3 attempts. `worker_loop` (started in the FastAPI lifespan, stopped on shutdown) runs one job per pass in a thread and sleeps one second when idle. At the end of **every** pass, also an idle one and also when the job step raised, it calls `reconcile_verifying` (errors are logged, never raised), which resolves candidates being verified (see [6.11](#611-the-fix-loop-and-promotion)).

Handlers do their LLM work first and write rows in one commit at the end, so a failure leaves no partial rows. Provider errors are logged and never copied into the user-facing job error. A criterion, test or prompt version deleted while its job waits fails the job with a clear message; the loop keeps running. Starting the same replay, build, suggestion or held-out generation twice returns the already-active job instead of queueing a second. A replay queued by Approve (payload `system: true`) that hits a transient agent or judge failure raises `RetryableJobFailure` (a `JobFailed`): the worker requeues it like a crash while attempts remain (3 in all, no sleep, so the queue is not blocked) and only then fails it with the fixed message, which fails the candidate ("A test could not be run: ..."). A replay a person started fails at once; they can press Replay again. Since Phase 4 a `system` replay for a version that is no longer `approved_verifying` returns `{"skipped": true}` without model calls, and one that was running when its version resolved stores nothing (see the fail-fast paragraph under [3.6](#36-suggest-approve-and-verify-sequence-phase-3)). A replay that stores a result deletes older replay results for the same test and version, and a replay or build whose criterion was edited while it ran stores nothing and returns `{"discarded": true}`.

**Operations.**
- Run **one** backend process per deployment (no `uvicorn --workers` above 1, no second instance): a job can legitimately outlive the 180 s window, and a second process would requeue and re-run it. The in-memory rate limiters are per process too.
- A restart or a Render free-tier spin-down interrupts the running job; it is requeued once its lock is 180 s old, up to 3 attempts.
- `WORKER_ENABLED=0` disables the loop. Any other value, or unset, runs it.
- Run `alembic upgrade head` before starting the backend after pulling Phase 2 or Phase 3. Without migration `0002` the worker loop logs an error every second, and without `0003` the new columns are missing. The Render start command already runs it. Deploy the backend before the frontend.

### 6.10 Phase 2 Decisions

Calls made in the Phase 2 plan beyond the design spec:

- **D1.** "Build tests" (judge saved runs, create tests, sanity check) is a job. `discover` returns the criterion immediately plus `job_id`, so no LLM chain holds an HTTP request open.
- **D2.** The spec's "pick a saved run for a manual criterion" is deferred. Manual criteria use the same automatic build through a "Build tests" button.
- **D3.** Failures live on the job (`status = failed`, `error`); there are no error verdict rows.
- **D4.** Baseline results exist only for runs that become tests.
- **D5.** A prompt edit does not re-run `analyze_agent`; the behavior map keeps working through the latest-analysis fallback. `prompt_version.number` gives "v2" labels.
- **D6.** There is no agent drawer. The prompt editor and the stale-tests banner sit at the top of the Eval criteria panel.
- **D7.** Replays use the first 20 user messages of the saved run (a 40-message cap).
- **D8.** Editing a criterion's `definition`, `good_example` or `bad_example` marks its replay results stale and clears `judge_check`; editing only `name` does not.

Build detail worth knowing: the build job judges at most the 8 most recent saved runs (oldest first) and stops after two failures, so an older failing run outside that window never becomes a test. For the assistant agent, when it has live (`source` `assist`) runs, those are judged first (newest first) and imported runs only fill what is left of the 8, so a demo import cannot push a live flagged conversation out of the window.

Phase 3 folded in four Phase 2 deferrals: M4 (the stale banner now reads "N tests haven't run on prompt v{x} yet."), M5 (Run all only reruns tests without a current replay), M6 (a result judged against a criterion that changed meanwhile is discarded, and the sanity check runs lazily when `judge_check` is null) and M7 (unique `(agent_id, number)` on prompt versions, latest replay result wins). D2 (pick a saved run for a manual criterion) is still deferred.

### 6.11 The Fix Loop and Promotion

**Generate a second test** (`generate_heldout` job). Allowed when a criterion has a `fix` test and no `heldout` test. Up to 3 attempts: the generator returns 2 to 5 customer messages for a scenario that probes the same criterion but is not the fix test's (the fix test's messages and earlier rejected scenarios are given to it as delimited data, and an identical whole message list is rejected); the scenario is replayed on the agent's current prompt and judged. The first scenario the judge fails is stored as a `heldout` test (`origin = generated`) together with that failing replay as a normal current result, so the test shows as failing at once. If none fails the job succeeds with `created: false` and the UI says "Couldn't write a test your current prompt fails, so it may already handle this case." If the criterion or the current prompt changed meanwhile the job returns `discarded`.

**Suggest fix** (`suggest_fix` job). Only from a `fix` test whose latest result on the current prompt is a failure (a current replay, else a baseline taken on the current version). A held-out test is refused with 409 "Suggest a fix from the fix test.", a test that is not failing with 409 "This test isn't failing on the current prompt.", a second open candidate with 409. The suggester sees the current prompt, the criterion, and the failing result's rationale and transcript, never the held-out test. It returns a complete replacement prompt (at most 4000 characters, not identical to the current one), the index of the failing turn and four short fields (change summary, touched section, expected effect, risk). The stored `why` quotes the failing turn **by code** from the stored transcript, so the model cannot fabricate it. The result is a `proposed` version with `origin = suggested`; the failure message is "Couldn't write a fix. Try again.", or "The suggested prompt was too long. Try again." when the prompt cap was exceeded. The version is stored under the agent row lock only if the current version is still the one the text was written against (`ParentChanged`, job message "This test isn't failing on the current prompt."), so a candidate written against v2 can never be stored with parent v3.

**Approve** (`POST /prompt-versions/{id}/approve`). Under the agent row lock it re-reads the version (must be `proposed`, 409 otherwise), refuses a fix whose parent is no longer the current version (409 "That fix is based on an older prompt. Reject it and suggest a new fix."), refuses when another version is verifying, checks the queue cap, then computes the **required set** once and stores it in `verification`: the fix test, the criterion's held-out test if any, and a **regression set** (every other test whose current non-stale replay on the *current* prompt is `pass`). `before` is read from stored results at approval time, never assumed: for the fix and held-out tests it is the verdict of the test's current non-stale replay on the current version, else the baseline's verdict when the baseline was taken on the current version, else null (the report shows "Not run"); it is `pass` for regressions. So a held-out test that already passes on the current prompt is not credited with a change, and the report cannot drift. Strength is `full` with a held-out test, else `weaker`. Status becomes `approved_verifying` and one `replay` job per required test (payload `system: true`, pointing at the candidate version) is enqueued in the same commit. Chat keeps the current version throughout.

**Promotion rule** (`core/promotion.decide_promotion`, pure code): promote if and only if the fix test passes, the held-out test passes when present, and no regression test has verdict `fail`. A `not_applicable` fix or held-out verdict counts as not passing; a `not_applicable` regression verdict is ignored. Reasons: "The fix test did not pass.", "The held-out test did not pass.", "A previously passing test now fails." Reconcile promotes only while the candidate's parent is still the current version; otherwise it fails the candidate with "The prompt changed during verification." (and the same table) instead of replacing the newer prompt. On promotion the previous current version becomes `superseded`, any other `proposed` version too, and the agent points at the candidate. Other failure reasons: "A test could not be run: {job error}", "A criterion changed during verification.", "The prompt changed during verification.", "Verification timed out." and "Verification could not be completed.". Since Phase 4 the same rule also runs on partial results as `early_failure` (see [3.6](#36-suggest-approve-and-verify-sequence-phase-3)): only a failure can be decided early, never a promotion.

**The report.** Stage 1 (`why`) is written with the suggestion. Stage 2 is the `outcome`: the status, the reason and the before-to-after table (per required test: role, criterion name, before and after verdicts, the judge rationale capped at 600 characters and the replay transcript truncated to 6000 characters) are built in code from stored results, so a stored report is self-contained. A `change_report` job then asks the report writer (which may see the prompt diff, unlike the verdict judge) for an explanation and a next step; the diff, table and rationales are delimited data, it cannot decide a verdict or status, and any cited test id that is not in the table is dropped. If the call fails the outcome carries `explanation_unavailable: true` and the table and diff still show. `GET /prompt-versions/{id}/report` returns `{why, outcome}`; it logs `report_viewed` only for `?view=1`, which the page sends on its first fetch and not on its polls.

**Reject, restore.** Reject works from `proposed` only. Restore creates a **new** `promoted` version (`origin = restored`, text copied, `parent_id` = the restored version, next number), supersedes the current version and any `proposed` candidate, and is allowed from `promoted`, `superseded` or `failed_verification` (a version in any other state gets 409 "This version can't be restored." before the verifying check runs). A `suggested` version without a verification record (never approved) is refused with the same 409, because restoring it would put unverified model text live; the history labels it "never approved" and shows no Restore button. Approve, reject, restore, manual edit and the judged-field edit of a criterion all take the same agent row lock, so racing requests are serialized and exactly one wins.

**Readiness.** The suite response carries per-criterion `can_generate_heldout`, per-test `can_suggest_fix` and `origin`, active job ids (`active_job_id`, `heldout_job_id`, `suggest_job_id`) and a `candidate`: the open candidate (with per-test progress while verifying), else the latest suggested version that ended `promoted` or `failed_verification`, so the outcome stays visible. The chip reads "N of M criteria passing", where M counts criteria with at least one test (the rest are shown as "without tests", or "No criteria have tests yet" when none has a test); "All criteria passing" (M > 0 and N = M) is the testing-is-done signal for the saved tests. While a candidate is `approved_verifying` the verifying view reloads the suite every 4 s (not overlapping, for at most 30 minutes, and once more about 2 s after a tracked job ends), so a reload, a second tab or a late commit never leaves it on "Verifying".

### 6.12 Phase 3 Decisions

Calls made in the Phase 3 plan (P1 to P6), the design addendum (section 11) and the review that followed:

- **P1.** The diff is a small in-house line diff (`frontend/src/lib/diff.js`), not a new dependency. Prompts are at most 4000 characters.
- **P2.** `reconcile_verifying` runs at the end of every worker pass, also when idle, so a restart or a failed job cannot strand a candidate. A candidate whose results are still missing 30 minutes after approval fails ("Verification timed out.") only when no replay job for them is queued or running and none failed or was discarded; a running job keeps it waiting until the worker's stale-job recovery ends it. The guarantee holds while the worker runs.
- **P3.** `verification.required[*].before` records, at approval time, the verdict found in stored results (or null), so the report's "before" column cannot drift or invent a change.
- **P4.** A generated scenario has 2 to 5 messages, so one `generate_heldout` job makes at most 3 x (1 + 5 + 1) = 21 LLM calls.
- **P5.** `useSession.refreshAgent()` reloads only the agent's prompt fields after a promotion or restore, so the prompt editor never shows stale text.
- **P6.** Approve returns `{version_id, status, jobs: [{test_id, job_id}]}` so the client can follow the replays under the existing `replay:<testId>` keys.
- **Addendum.** The held-out test comes from generation and is kept only if it fails on the current prompt (options A and B deferred); verification fans out as replay jobs plus a reconcile step; suggest fix never starts from a held-out test; criteria cannot change under a verifying candidate; the chip counts criteria that have tests; Approve's replays and the report job are exempt from the hourly cap but not the queue cap; the version cap excludes rejected and failed-verification versions, with a 60-row hard cap; the report is self-contained and cites test ids; the report writer may see the prompt diff; the suite response carries the open or most recently resolved candidate.
- **Later calls.** The candidate in the suite response is the open one, else the latest suggested version that is `promoted` or `failed_verification`. Restore checks the version's status before it checks for a verifying candidate. The stale-draft protection exists at both layers: the server refuses a save whose `base_version_id` is no longer current, and the editor shows a banner and offers to discard its draft. The live readiness region lives in the app header (one `role="status"` chip); the panel's chip is plain text so a change is announced once.

### 6.13 Verified and Not Verified (Phase 3)

Verified on a throwaway real Postgres: migration `0003` applied over populated Phase 2 rows (defaults backfilled, the unique `(agent_id, number)` constraint enforced by Postgres, workspace delete cascading cleanly, downgrade then upgrade round trip). A race test with a live backend on real Postgres: 15 rounds each of two simultaneous approvals, approve against a manual prompt edit, approve against reject, and approve against restore. Exactly one request won every round, and the invariants held (at most one version verifying, exactly one promoted, the agent pointing at it).

An end-to-end run with real Groq on a local Postgres completed the loop (held-out generated, fix suggested, approved, verified, promoted, explained, restored); one run is not evidence of general quality.

**Not yet verified:** a real Render deploy; the quality of the real judge and suggester across many agents (every LLM call is mocked in the tests); several worker processes (still unsupported, one process per deployment).

**Known weaknesses.** The judge sanity check is weak by construction (carried from Phase 2). A generated held-out scenario can be a light paraphrase of the fix test, because the copy check compares whole message lists. `GET /agents/{id}/tests` makes many small queries, which is fine at tens of tests.

### 6.14 Phase 4 Decisions

Decisions made with the user (design addendum, section 2): the simulation has a deterministic mock mode that proves the harness plus a small real-Groq run for the results table (every table labelled simulated, with N, models and date); the gate is a command-line tool in this repository that reuses `backend/core/` directly, with no server or database, shipped with a thin composite Action and a worked example; the simulation runs in-process against the real app; the Phase 3 carry-overs are included.

Calls made in the Phase 4 plan (P1 to P6):

- **P1.** `evalgate/` and `simulation/` are top-level packages run from the repository root (`python -m evalgate`, `python -m simulation.run`); the Action sets `PYTHONPATH` to the repository root.
- **P2.** `evalgate --load-dotenv` (off by default) loads `.env` for local convenience; CI and mock runs use the environment only.
- **P3.** `--repeat K` is a majority vote: a test passes when `passes * 2 > K`, and is `error` only when every run of it errored.
- **P4.** The simulation counts model calls through a `CountingClient` wrapped around whatever client is installed (fake or real) and records wall time per trial.
- **P5.** A preview is two builder-written customer messages (`MODEL_CHAT`) answered by the agent, then the reference judge; the cap is 12 previews per trial.
- **P6.** The real simulation run and the real gate run are executed after review, and their results are committed to `docs/simulation-results.md` and quoted in the case study. Both were then executed on 2026-11-22 against commit `eef7fb6`, with the user's Groq key read from the environment by the programs themselves: one real gate run per example prompt, and one real simulation run of 12 trials (2 scenarios, 2 arms, 3 trials each). The outcomes are in [6.15](#615-verified-and-not-verified-phase-4); the simulation did not show the flywheel reducing preview runs.

Author's calls in the design addendum (section 12), made after a fresh reader found places where a wrong guess would matter; any may be overruled:

1. **The flywheel builder has a fallback.** The arm is a fixed state machine with one attempt at the app flow; any step that does not go to plan, or a promoted fix that still fails the reference judge, hands the trial to the same manual rewrite the baseline uses. Every trial records its `path` (`loop`, `fallback`, `none`) and the results page reports the counts.
2. **`.env` handling.** `GROQ_SKIP_DOTENV=1` makes `groq_client` skip `load_dotenv()`. The test suite, the gate and the simulation set it, so none of them reads `.env` unless `--load-dotenv` is passed. The gate checks `GROQ_API_KEY` itself and exits 3 when it is missing or a placeholder, so the `"sk-test-placeholder"` fallback in `groq_client` is never accepted.
3. **The model seam is the client object.** The fake replaces `backend.groq_client.client` whole, and `with_options(...)` returns itself. Bounded retries use a context variable (`request_retry_limit`, set by `request_retries(n)` around the routes' model calls and read by `groq_chat`), so no pure core signature changed.
4. **Postgres in CI is real.** `tests/pg/` runs only when `TEST_DATABASE_URL` is set; the CI `postgres` job sets it against a service container.
5. **Models in the simulation.** Customer messages use `MODEL_CHAT`; the baseline's rewrite uses `MODEL_JUDGE`; `GROQ_TPM_LIMIT` is one number applied to a separate window per model. The real run was small: 2 scenarios, 3 trials per arm, a 12-preview cap, paced at 7000 tokens per minute, saved incrementally and resumable (it was estimated at 1 to 1.5 hours on the free tier; the 12 trials took about 24 minutes of summed trial wall time, per the results page).
6. **Scope of the deferred list.** The events script `scripts/metrics.py` is in. Still deferred: a 401 mid-poll that can create an empty workspace (M8), sliding token expiry, deleting a single agent, `passive_deletes`, picking a saved run or writing a held-out test by hand, candidate replay jobs under per-test keys, queue fairness of the exempt fan-out, the UI poll horizon versus the server timeout, and the suite endpoint's many small queries.
7. **Action scope.** Only the in-repo example is verified (by running the same commands locally, with model calls faked and, once per prompt, with real models; raw output in `docs/gate-real-run/`). The Action locates the gate through `github.action_path`, so it could work from another repository, but that use is unverified, and fork pull requests receive no secrets.
8. **The gate is a regression gate.** The export adds `tests[].current_verdict`; by default the gate runs only tests whose `current_verdict` is `pass` and prints the skip count; `--all` runs everything.
9. **Fail-fast edge cases.** A fix or held-out replay job that ended `failed` or was discarded is decisive immediately; after a candidate resolves, a still-queued verification replay is cancelled, and a running or requeued one becomes a no-op in `handle_replay`.

Small fixes: the stale-fix text is now "That fix is based on an older prompt. Reject it and suggest a new fix."; `version_out` tolerates a non-dict `verification` (strength `null`); `npm run lint` reports zero problems (it reported three errors before).

### 6.15 Verified and Not Verified (Phase 4)

**Verified (actually run in the development environment):**

- `python -m pytest -q` from the repository root: 1404 passed, 19 skipped (the real-Postgres tests, skipped without `TEST_DATABASE_URL`). Every model call in these tests is faked at `backend.groq_client.client` or at the function under test.
- Frontend: `npm test` 436 tests in 25 files passed, `npm run lint` reports no problems, `npm run build` succeeds.
- The 19 real-Postgres tests in `tests/pg/` (migrations over populated rows, reconcile fail-fast with cancellation, approval races) were run twice locally against a throwaway Docker Postgres (before and after the final fix wave) and passed both times. They have never run in GitHub Actions from this environment.
- The mock-mode simulation ran end to end (both arms, both scenarios, one trial each) and its report rendered. This shows the harness works; the numbers it prints are meaningless.
- The YAML of the Action and the workflows is parsed in unit tests (structure and security properties: read-only permissions, no secrets in the CI workflow, inputs passed through `env` and never interpolated into a script, the key reaching only the gate step, actions pinned to a major version), and the example suite's pass and fail behaviour is tested with the gate's model calls faked.
- A real gate run (`python -m evalgate --suite examples/eval-gate-demo/suite.json --prompt <prompt> --load-dotenv --pause 30 --format json`, real Groq models, commit `eef7fb6`, one run per prompt, `--repeat` 1, on a developer machine, not in GitHub Actions; the raw JSON output is committed in `docs/gate-real-run/`). With `examples/eval-gate-demo/prompt.md` it exited 0 with pass rate 1.0 (2 of 2 runnable tests passed, the fix test and the held-out test; 1 test was skipped because its verdict at export was not `pass`). With `examples/eval-gate-demo/weakened-prompt.md` it exited 1 with pass rate 0.5 (1 of 2): the fix test failed (the judge's rationale quotes the reply as "... we will issue a full refund") and the held-out test passed. So the weakened prompt did turn the gate red, but only one of its two tests failed, and with a real model that outcome is probabilistic; one run each is not a measurement of how often it happens.
- A real simulation run (`python -m simulation.run --mode real --trials 3 --load-dotenv`, 12 trials, commit `eef7fb6`, paced at 7000 tokens per minute), reported in [simulation-results.md](simulation-results.md) with the raw trial files committed under `simulation/results/real/`. **It did not show the flywheel reducing preview runs.** `support-refund`: the baseline converged in 2 previews in all 3 trials; the flywheel converged in 3 previews in all 3 trials, every one by the fallback path (`path` fallback, loop 0): in 2 trials the app's fix test did not fail on the current prompt in 3 replays, and in 1 trial the app built no fix test for the discovered criterion. The flywheel never produced a promoted fix and cost one extra preview here. `booking-confirm`: the flywheel converged in 1, 2 and 2 previews, all with "no fix needed" (the weak prompt passed the reference judge with no change), so the scenario did not reliably elicit the failure it was designed to; the baseline converged in 2 and 1 previews and did not converge in one trial (12 previews), whose notes show failures in the harness's model calls (the agent reply could not be generated on 3 previews, and prompt rewrites were rejected as too long 3 times; the cause is not established), not a verdict on the method. In the flywheel's `booking-confirm` trials 2 and 3 the first preview did not pass and the second passed with no change at all, and baseline trial 3 passed in 1 preview with no change, which fits judge and reply variance and a scenario that does not reliably elicit the failure. With N = 3 per arm nothing here is statistically established, and the app-driven fix path (path `loop`) was never exercised end to end in the simulation with a real model.

**Not yet verified:**

- The GitHub Action, the demo workflow and the CI workflow on GitHub, including the Postgres service container and fork pull requests (which get no secrets, so the gate exits 3).
- The layout of the "Export suite for CI" button in a browser (the component is unit tested; nobody has looked at it rendered).
- `scripts/metrics.py` on real usage data (unit tested with fake rows; there is no deployed database).
- A real Render deploy, and several worker processes (still unsupported).

**Known limitations and weaknesses.**

- The simulation's builder is a model, N is tiny, and the same model family plays builder and agent (see [3.8](#38-the-simulation-phase-4)).
- The baseline's rewriter is shown the full goal criterion; the flywheel's app-driven fix never is, though its fallback rewrites are, like the baseline's (and the customer-message builder sees the goal in both arms). This favours the baseline.
- Both judge calls use `MODEL_JUDGE` and so share model bias; in mock mode independence is structural only.
- The flywheel gets one attempt at the app flow and then falls back, so a fallback trial starts roughly two previews behind the baseline; a flywheel trial that passes its first preview with no fix is reported as "converged with no fix needed".
- The app allows 30 jobs per workspace per hour, so each simulated trial uses a fresh workspace.
- The gate samples a stochastic model: a single run of a borderline test can flip, and `--repeat` only reduces that. Token pacing is a bounded wait, not a guarantee against a 429. The events metrics use the app's own replay verdicts, not an independent judge.
- The carried-over weaknesses of Phases 2 and 3 (a weak judge sanity check, a held-out scenario that can be a light paraphrase of the fix test, many small queries in the suite endpoint) are unchanged.

---

## 7. Backend API Reference

**Base URL:** `http://localhost:8000`. Every endpoint except `POST /workspaces` and `GET /presets` requires the `X-Workspace-Token` header.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/workspaces` | POST | Create a workspace; returns `{token}` |
| `/workspaces/me` | DELETE | Delete the workspace and all its data (204) |
| `/presets` | GET | The 3 built-in agent presets |
| `/agents` | POST | Create an agent from `{preset_id}` or `{name, system_prompt, user_role_label?}` |
| `/agents/{id}/state` | GET | Agent, runs, criteria, pattern summary, run count, last checked run |
| `/agents/{id}/chat` | POST | `{messages}` → `{reply}` from the agent under test |
| `/agents/{id}/runs` | POST | Save a run (3–40 messages); returns `{run, run_count, check_due}` |
| `/agents/{id}/check-pattern` | POST | Check the unchecked delta; returns `{found, pattern_summary}` |
| `/agents/{id}/criteria/discover` | POST | Generate a criterion from the stored pattern (409 if none); also queues a `build_tests` job and returns its `job_id` (null if the hourly job cap is reached) |
| `/agents/{id}/criteria` | POST | Add a criterion manually |
| `/criteria/{id}` | PATCH | Edit a criterion (partial update); changing the definition or an example marks its replay results stale |
| `/agents/{id}/prompt` | PUT | Save a manual prompt edit as a new numbered version; returns `{version_id, version_number, text, changed}` (`changed: false` if the text is identical). An optional `base_version_id` is the version the edit was written against: if the agent's current version differs, the save is refused with 409 and nothing is written |
| `/agents/{id}/tests` | GET | The suite: current prompt version, `stale_count`, `candidate` (the open candidate with progress, else the latest suggested version that ended promoted or failed verification), and per criterion its status, `judge_check`, `can_generate_heldout`, active build and held-out job ids, and tests (`role`, `origin`, baseline and current replay results, `can_suggest_fix`, active replay and suggest job ids) |
| `/tests/{id}/replay` | POST | Queue a replay of one test on the current prompt version (202, `{job_id}`; returns the active job if one exists) |
| `/agents/{id}/tests/run-all` | POST | Queue a replay for every test that has no current replay on this version and no active job (202, `{job_ids}`, empty when nothing is pending) |
| `/criteria/{id}/build-tests` | POST | Queue `build_tests` for a criterion (202, `{job_id}`; 409 if it already has tests) |
| `/criteria/{id}/generate-heldout` | POST | Queue `generate_heldout` (202, `{job_id}`; 409 "This criterion needs a fix test and no held-out test." otherwise) |
| `/tests/{id}/suggest-fix` | POST | Queue `suggest_fix` (202, `{job_id}`; 409 for a held-out test, a test that is not failing on the current prompt, or an open candidate) |
| `/agents/{id}/prompt-versions` | GET | The agent's versions, newest first: number, origin, status, strength, created_at, is_current |
| `/prompt-versions/{id}` | GET | One version with its text and its parent's text (for the diff) |
| `/prompt-versions/{id}/approve` | POST | Approve a `proposed` version: queues the verification replays (202, `{version_id, status, jobs: [{test_id, job_id}]}`) |
| `/prompt-versions/{id}/reject` | POST | Reject a `proposed` version |
| `/prompt-versions/{id}/restore` | POST | Create a new promoted version from an earlier version's text (from `promoted`, `superseded` or `failed_verification`) |
| `/prompt-versions/{id}/report` | GET | The change report `{version_id, number, status, strength, why, outcome}`; logs `report_viewed` only with `?view=1` |
| `/jobs/{id}` | GET | Job status: `{id, type, status, result, error}` |
| `/agents/{id}/export` | GET | The agent's suite as an `eval-discovery.suite` v1 document for the CI gate (see [3.7](#37-export-and-the-ci-gate-phase-4)); read-only, workspace-scoped, 404 for another workspace's agent, 409 "Agent has no current prompt." for an agent without one |

**Error codes:** `400` nothing or too little to check, `401` unknown or expired token, `404` agent, criterion, test or job not found in this workspace, `409` run-save race, no pattern to build from, tests already exist, a prompt edit whose `base_version_id` is no longer the current version ("The prompt changed to v{n} while you were editing. Reload the prompt to continue."), a candidate action in the wrong state (approve or reject on a version that is not `proposed`, restore of a version that cannot be restored, which includes a suggestion that was never approved), "That fix is based on an older prompt. Reject it and suggest a new fix." (approve after the prompt moved on), "A fix is already proposed or being verified." (suggest fix while any candidate is open, also a merely proposed one), "This fix can't be approved in its current state." (a second approval) or "A fix is being verified. Wait for it to finish." (manual edit, restore, approving another version, or a judged-field criterion edit while a fix verifies), `422` invalid input, `429` quota, rate limit, hourly job cap, queue cap or version cap, `502` upstream LLM failure.

---

## 8. Development & Deployment

### Project Structure

```
eval-discovery/
├── backend/
│   ├── main.py               # App init, CORS, routers, startup purge, worker loop in the lifespan
│   ├── api/                  # Routers (workspaces, agents, runs, criteria, prompt_versions, tests, versions, export), deps, limits, schemas
│   ├── core/                 # Pure logic: config + presets, analyze, pattern, criteria, judge, replay, testsuite, suggest, heldout, report, promotion, prompts
│   ├── db/                   # SQLAlchemy models, session, agent/workspace/event/testsuite helpers, candidates, reconcile
│   ├── jobs/                 # DB-backed queue, handlers (build_tests, replay, suggest_fix, generate_heldout, change_report), worker loop
│   ├── groq_client.py        # Groq API wrapper, model constants, bounded request retries, optional token pacing
│   └── utils.py              # JSON extraction from LLM output
├── evalgate/                 # The CI gate: suite.py (load and validate), runner.py (replay, judge, exit code), cli.py
├── simulation/               # Fake model, scenarios, builder, reference judge, in-process app driver, arms, run, report
├── scripts/metrics.py        # Events metrics (read-only)
├── examples/eval-gate-demo/  # Worked example: prompt, weakened prompt, exported suite, README
├── .github/                  # actions/eval-gate/action.yml, workflows/ci.yml, workflows/eval-gate-demo.yml
├── migrations/               # Alembic environment and versions
├── alembic.ini
├── frontend/src/             # App.jsx, api.js, hooks/ (useSession, useTests, useReport, useVersions), components/, lib/
├── tests/                    # pytest suite; tests/pg/ is the real-Postgres suite (skipped without TEST_DATABASE_URL)
├── docs/                     # This document, specs and plans
├── docker-compose.yml        # Local Postgres 16
├── Procfile                  # Start command for Procfile-based hosts (Render ignores it)
├── requirements.txt
├── requirements-gate.txt     # What the gate imports: groq, python-dotenv
└── README.md
```

### Local Development

```bash
# Terminal 1 — Backend
pip install -r requirements.txt
cp .env.example .env       # set GROQ_API_KEY (and ALLOWED_ORIGINS if needed); not DATABASE_URL
alembic upgrade head       # SQLite ./dev.db unless DATABASE_URL is exported in this shell; run before the backend after pulling Phase 2 or 3
uvicorn backend.main:app --reload    # http://localhost:8000 (one process: the job worker runs inside it)

# Optional: local Postgres. DATABASE_URL is read from the process environment, NOT from .env
# (a value in .env is silently ignored), so export it in the shell that runs alembic and uvicorn:
docker compose up -d
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/eval_discovery   # Git Bash / bash
# $env:DATABASE_URL="postgresql://postgres:postgres@localhost:5432/eval_discovery"  # PowerShell
alembic upgrade head && uvicorn backend.main:app --reload

# Terminal 2 — Frontend
cd frontend
npm install
npm run dev                # http://localhost:5173
```

### Environment Variables

| Variable | Required | Description |
|----------|---------|-------------|
| `GROQ_API_KEY` | Yes | API key from console.groq.com (may live in `.env`) |
| `DATABASE_URL` | In production | `postgresql://…` (a `postgres://` URL is normalized to the psycopg driver). Unset means SQLite at `./dev.db`. Read from the process environment only, **not** from `.env` |
| `ALLOWED_ORIGINS` | In production | Comma-separated CORS origins; default `http://localhost:5173` (may live in `.env`) |
| `ASSIST_KEY` | For Patent Assist | The secret the extension sends in `X-Assist-Key`; the backend compares it in constant time and answers 503 on every `/assist` extension call when it is unset or empty (may live in `.env`; the extension build reads its own copy as `VITE_ASSIST_KEY`) |
| `ASSIST_AGENT_ID` | For Patent Assist | The id of the assistant agent `scripts/setup_assist.py` printed; the assist routes, the console and the importer use it to find the demo workspace (may live in `.env`) |
| `WORKER_ENABLED` | No | `0` disables the in-process job worker loop; anything else (default) runs it. Keep one backend process per deployment (may live in `.env`) |
| `VITE_API_URL` | Frontend build | Backend base URL baked into the frontend; default `http://localhost:8000` |
| `GROQ_TPM_LIMIT` | No | Optional token pacing (Phase 4): tokens per minute per model; unset means off. May live in `.env` (see [6.6](#66-limits)) |
| `GROQ_SKIP_DOTENV` | No | `1` makes the Groq client skip `load_dotenv()`. Set it in the process environment, not in `.env`. The tests, the gate and the simulation set it themselves |
| `TEST_DATABASE_URL` | Only for `tests/pg` | A `postgresql+psycopg://` URL of a throwaway database whose name contains `test`; without it the real-Postgres tests are skipped |
| `ALLOW_PG_TEST_RESET` | No | `1` lets `tests/pg` reset a database whose name does not contain `test`. The suite runs `DROP SCHEMA public CASCADE`, so leave it unset unless you mean it |

### Running Tests

```bash
python -m pytest -q                       # backend, SQLite, every model call faked; tests/pg skips without TEST_DATABASE_URL
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/eval_test python -m pytest tests/pg -q   # a throwaway database
cd frontend && npm test && npm run lint && npm run build
```

Phase 4 tools, from the repository root:

```bash
python -m evalgate --suite eval-suite.json --prompt prompts/agent.md      # the gate (needs GROQ_API_KEY)
python -m simulation.run                                                  # mock mode, offline (numbers meaningless)
python -m simulation.run --mode real --trials 3 --resume                  # real Groq (spends quota)
python -m simulation.report --results simulation/results --mode real      # writes docs/simulation-results.md
python scripts/metrics.py --database-url postgresql+psycopg://...         # events metrics (does not normalise URLs like the app)
```

### Deployment

> **Not yet deployed.** Migration `0002` was applied to a throwaway real Postgres on top of populated Phase 1 rows: `prompt_version.number` backfilled to 1, deleting a workspace cascaded through the new tables, and a downgrade then upgrade round trip worked. `claim_next` was raced by 8 threads over 40 jobs with no duplicates. Migration `0003` and the approval races were also run on real Postgres (see [6.13](#613-verified-and-not-verified-phase-3)). **Not yet verified:** a real deploy on Render, the quality of the real judge, suggester and generator across many agents (every LLM call is mocked in the tests; a single end-to-end run with real Groq on a local Postgres completed the loop), and multi-process behavior (which is not supported, see [6.9](#69-background-jobs)). Verify first on deploy: `alembic upgrade head` succeeds, `POST /workspaces` returns a token, `GET /presets` returns 3 presets, and deleting a workspace with runs works.

- **Backend (Render web service):** add a Postgres instance (check the provider's current free-tier terms first), set `DATABASE_URL`, `ALLOWED_ORIGINS` and `GROQ_API_KEY`, and paste this into the service's Start Command setting in the Render dashboard (Render ignores `Procfile`; it is for Procfile-based hosts):

  ```
  alembic upgrade head && uvicorn backend.main:app --host 0.0.0.0 --port $PORT
  ```

  Migrations run on every start (this is what applies `0002` and `0003` on deploy; deploy the backend before the frontend). The command starts one uvicorn process, which is what the in-process job worker needs: do not add `--workers`. A free-tier spin-down interrupts a running job; it is requeued after 180 s. Do not pass `--proxy-headers --forwarded-allow-ips="*"`: uvicorn would then take the left-most `X-Forwarded-For` entry, which the client controls, and a forged value per request would defeat the per-IP limiter. The app derives the client key itself (see [6.6](#66-limits)): the Nth entry from the right of `X-Forwarded-For`, N being `TRUSTED_PROXY_HOPS` (default 1; 0 ignores the header). Render's real header chain was not observed. After deploying, send `POST /workspaces` 31 times with different forged left-most `X-Forwarded-For` values and expect a 429 by the 21st; if it never comes, raise `TRUSTED_PROXY_HOPS`, and if all users are throttled together, lower it.
- **Frontend (Cloudflare Worker, static assets):** deploy after the backend, then rebuild with `VITE_API_URL=<backend url> npm run build`, then deploy `frontend/dist`.
- **CI:** `.github/workflows/ci.yml` runs the backend tests, the real-Postgres tests (service container) and the frontend lint, test and build on every push and pull request; `.github/workflows/eval-gate-demo.yml` runs the gate Action on the example. Neither workflow has been run on GitHub from the development environment, and the gate Action needs the repository secret `GROQ_API_KEY` set by the owner (see [3.7](#37-export-and-the-ci-gate-phase-4)). There is no CD: deploys are manual.

---

## 9. Roadmap

Phases 1 and 2 (persistence, saved tests, replay, judging and the manual prompt editor), Phase 3 (suggested fixes, diff and change report, held-out and regression promotion gate, version history and restore, readiness chip) and Phase 4 (suite export, the `evalgate` CI gate with a GitHub Action and example, repo CI with real-Postgres tests, fail-fast verification, bounded retries and optional token pacing, the events metrics script and the simulation harness) are implemented. Phases 2, 3 and 4 are on unmerged branches that build on each other (`feat/phase2-tests`, `feat/phase3-evolve`, `feat/phase4-ship`), so merge them in that order. The design is in [docs/superpowers/specs/2026-11-21-agent-ready-eval-flywheel-design.md](superpowers/specs/2026-11-21-agent-ready-eval-flywheel-design.md), the Phase 3 addendum [docs/superpowers/specs/2026-11-21-phase3-evolve-design.md](superpowers/specs/2026-11-21-phase3-evolve-design.md) and the Phase 4 addendum [docs/superpowers/specs/2026-11-22-phase4-ship-design.md](superpowers/specs/2026-11-22-phase4-ship-design.md).

The first real runs have been made (see [6.15](#615-verified-and-not-verified-phase-4)): one real gate run per example prompt, and a 12-trial real simulation whose results are in [simulation-results.md](simulation-results.md). The simulation did not show the loop reducing preview runs. Next, in this order, without promising results: scenarios that fail more reliably on the weak prompt; a fix-test confirmation step that is robust when the app's fix test does not fail on the current prompt or is not built at all; fixing the harness failures seen in the booking-confirm baseline (agent replies that could not be generated, rewrites rejected as too long); and a larger N, so that a difference could be measured at all. Not done yet: the first run of the Action and the workflows on GitHub. Still deferred:

- Picking a saved run for a manual criterion or writing a held-out test by hand (options A and B, D2)
- Editing a candidate by hand (reject it and use the prompt editor)
- Per-agent delete
- Sliding expiry for workspace tokens
- M8: a 401 mid-poll can create an empty workspace
- `passive_deletes` on the new relationships
- Candidate replay jobs shown under per-test keys, queue fairness of the exempt verification fan-out, the UI poll horizon versus the server timeout, and the suite endpoint's many small queries

---

## 10. Patent Assist

Patent Assist applies the flywheel to an assistant that lives inside a product's own page. The demo target is Google Patents (US patent pages on `patents.google.com`): a Chrome extension answers questions about the page in front of the reader, signals about how the page is used feed a console for the product builder, and answers that went wrong become conversations the existing loop (discover, test, suggest, approve, verify, promote) works on. The versioned artifact is the assistant's **system prompt**, exactly as for any other agent. The design is in [the Patent Assist spec](superpowers/specs/2026-11-22-patent-assist-design.md) and the task plan in [the plan](superpowers/plans/2026-11-22-patent-assist.md); this section describes what the code does. The console v2 work (call telemetry, the conversation judge, the demo dataset script, the five-tab console) is described in [10.11](#1011-console-v2); its design is in [the console v2 spec](superpowers/specs/2026-11-24-patent-assist-console-v2-design.md) and its plan in [the console v2 plan](superpowers/plans/2026-11-24-patent-assist-console-v2.md).

> **Status, stated plainly.**
> - The law pack (12 provisions in `backend/core/assist/lawpack/*.md`, each `status: draft`) is a **draft until the author reviews it**. It is hand-written summary text, not statute text, and has had no legal review.
> - [extension/CHECKLIST.md](../extension/CHECKLIST.md) is for the author to run in real Chrome and **has not been run**. The extension's behaviour on live Google Patents pages is verified only against saved fixtures and in jsdom.
> - Imported page-view data is a **demo dataset**, labelled so in the console, and is never counted as a measured effect of an evolved prompt.
> - The console's **demo dataset** (`scripts/seed_demo_console.py`) is generated data: its trends, its quality dip, its latency bump and its version markers are a scripted storyline, not usage, and never evidence that a prompt version improved the assistant.
> - The **conversation judge** is a language model's opinion of a conversation. It has not been validated against human raters and has **not been run against real conversations** with a real Groq key; the console v2 checklist ([CONSOLE-CHECKLIST.md](CONSOLE-CHECKLIST.md)) has not been run, and nothing in the console has been looked at in a real browser.
> - Console v2 is local on `feat/console-v2`; I did not push it, and nothing is deployed. One real Groq run for Patent Assist was made on 2026-11-23 and the fixes after it were not re-run; this section makes no claim that the flywheel improves Patent Assist answers, or that the judge's grades measure answer quality (see [10.9](#109-verified-and-not-verified-patent-assist) and [10.11](#1011-console-v2)).

### 10.1 Components

```mermaid
flowchart LR
    subgraph Chrome["Chrome extension (Manifest V3, load unpacked)"]
        direction TB
        CS["Content script (content.js)<br/>session and route handling, brief extractor,<br/>page-view tracker, Shadow DOM widget"]
        SW["Service worker (background.js)<br/>the only code that calls the backend,<br/>holds the assist key"]
        CS -->|"chrome.runtime messages"| SW
    end

    subgraph Backend["FastAPI backend"]
        direction TB
        AssistApi["api/assist.py (X-Assist-Key)<br/>POST /assist/chat, /assist/page-views,<br/>/assist/ratings, /assist/reasks"]
        ConsoleApi["api/assist_console.py and assist_console_views.py (X-Workspace-Token)<br/>GET /assist/console/status, /metrics, /flagged, /trends, /overview,<br/>/quality, /operations, /pipeline<br/>POST /assist/console/insights, /judge/evaluate"]
        Worker["Job worker (jobs/)<br/>idle scan, conversation judge job"]
        AssistCore["core/assist/<br/>intent, assemble, lawpack, answer, citations,<br/>evidence, adoption, insights, importing, brief,<br/>convo_judge, console_filters, alerts"]
        Flywheel["Existing flywheel<br/>check-pattern, discover, build tests, replay,<br/>suggest fix, approve, verify, reconcile"]
    end

    DB[("Database<br/>assist_page_view, assist_question,<br/>assist_call, assist_judgement,<br/>run (source assist or imported),<br/>test_case.brief_snapshot")]
    Groq["Groq API<br/>gpt-oss-20b answers and replays,<br/>gpt-oss-120b judge, suggester, insights"]
    ConsoleUi["Assist console (React, lazy chunk)<br/>Overview, Adoption, Quality,<br/>Evolution and Operations tabs"]
    Importer["scripts/import_page_views.py<br/>examples/assist/page_views.example.jsonl"]
    Seeder["scripts/seed_demo_console.py<br/>30-day demo dataset, source imported"]
    Setup["scripts/setup_assist.py<br/>creates the demo workspace and agent"]

    SW -->|"HTTP, X-Assist-Key"| AssistApi
    AssistApi --> AssistCore
    AssistApi --> DB
    AssistApi -->|"one model call per question"| Groq
    ConsoleUi -->|"HTTP, workspace token"| ConsoleApi
    ConsoleApi --> AssistCore
    ConsoleApi --> DB
    ConsoleApi -->|"insights brief"| Groq
    ConsoleUi -->|"existing routes"| Flywheel
    Flywheel --> AssistCore
    Flywheel --> DB
    Flywheel --> Groq
    Worker -->|"one verdict per conversation"| Groq
    Worker --> DB
    ConsoleApi -->|"Evaluate now queues a judge job"| Worker
    AssistApi -->|"records every model call"| DB
    Importer -->|"source imported"| DB
    Seeder -->|"source imported, never calls a model"| DB
    Setup --> DB
```

| Part | Where | Notes |
|------|-------|-------|
| Extractor and session | `extension/src/extractor.js`, `session.js`, `claimrefs.js` | A pure function over the DOM builds the brief (every field optional; a `completeness` map says what was found). Google Patents is a single-page app, so `session.js` starts a **new session** (fresh random id, empty history, freshly extracted brief) on each patent route and starts nothing off `/patent/` routes. It waits until the page's number, title and claims match the URL (a URL number without a kind code accepts the page's number only when the rest is a kind code such as `B2`, never more digits). The brief is extracted once per session, so a long session can answer from a stale brief. A section that was capped or shrunk to fit the size limits (300 list rows, 200 claims, a claim or the abstract cut at its cap, anything dropped to fit the total) is reported as **incomplete**, so the completeness note tells the model that section is partial |
| Widget | `extension/src/widget/` | Shadow DOM overlay; the header shows the patent number; three chips (pre-filled questions) show on the first turn only; after each answer one follow-up button is picked by code from a fixed table by the answer's intent (never a question already asked, never a claim the patent does not have, no claim follow-up when the answer cited no claim); a text selection is written into the question text; answers render as safe formatted text (bold, italic, bullets, numbered lists) built from DOM nodes, with reference pills (see 10.2); one "How is this going?" card (Helpful, Not helpful with an optional reason, Didn't answer my question, Dismiss) appears once per conversation after the 3rd answered question (`FEEDBACK_AFTER_QUESTIONS` in `extension/src/widget/followups.js`) and the rating is recorded against the newest answer together with the whole conversation, so there are no per-answer rating buttons; keystrokes inside the widget are stopped in the bubble phase so they do not reach the page's own shortcuts; a re-ask is the same intent within 90 s of the previous answer; a page that is not a US utility patent or application (a non-US number, or a US design, reissue or plant patent) gets a notice and no request |
| Tracker | `extension/src/signals.js` | Per-section visible time (a section counts when about 40% of it is visible, or when it fills the middle band of the window), a heartbeat about every 15 s while the tab is visible, whole-number milliseconds only. A backwards clock step is rebased (accrual carries on), and a heartbeat request that has not settled after 30 s is given up so the next one can go |
| Service worker | `extension/src/background.js` | Four routes only (chat, rating, reask, page-view); answers with the parsed JSON or a **fixed** error message, never provider or backend text and never the key; 60 s timeout. It forwards a message only when the sender is this extension's content script on the `https://patents.google.com` origin (the path is not checked, so the final heartbeat of a patent view still arrives after the tab has moved to the search page; any other sender of the extension gets one fixed error) and only when the payload is at most 1,000,000 characters |
| Assist API | `backend/api/assist.py` | Key check (503 when `ASSIST_KEY` is unset, 401 when wrong, constant-time compare), strict request schemas, rate limits, then the pipeline in 10.2 |
| Pure core | `backend/core/assist/` | No web, DB or jobs imports. The one answer pipeline is `answer.py` (`build_answer_request`, `finalize_answer`), shared by the chat route and by test replay |
| Console API | `backend/api/assist_console.py`, `backend/api/assist_console_views.py` | Workspace-token auth; responses carry counts, rates, ids and labels, never question or answer text (the insights brief is the one place question samples reach a model). The old routes (`status`, `metrics`, `flagged`, `insights`) are in the first file; the v2 read routes (`trends`, `overview`, `quality`, `operations`, `pipeline`) and `POST judge/evaluate` are in the second. Every route reads only the workspace's own assist agent. Listed in [10.11](#1011-console-v2) |
| Console UI | `frontend/src/components/assist/` | `AssistConsole` (connect, then five tabs: `OverviewTab`, `AdoptionPanel`, `QualityTab`, `EvolutionPanel`, `OperationsTab`), global `ConsoleControls` (range, source, version, refresh), `AlertStrip`, `LoopStepper`, `RefreshFrame`, `ConsoleLoader` and `ConsoleBoundary`; the chart kit in `charts/` (Recharts, one palette). `AdoptionPanel` keeps `VersionComparison` and `FlaggedList`; `InsightsBrief` now lives on the Quality tab; `EvolutionPanel` still reuses the existing criteria, tests and change-review components, with explicit "Check flagged conversations for a pattern" and "Discover evals" buttons, and adds the pipeline stepper, `VersionTimeline` and `ReplayChart`. Structure in [10.11](#1011-console-v2) |
| Call telemetry | `backend/db/telemetry.py`, `AssistCall` in `backend/db/models.py`, migration `0006` | One row per model call the service makes for the assistant (chat, judge, insights): kind, model, latency, outcome, tokens, prompt version, source. A failed record never breaks the call |
| Conversation judge | `backend/core/assist/convo_judge.py`, `rubric.md`, `backend/jobs/conversation_judge.py`, `AssistJudgement`, migration `0007` | A model grades whole finished live conversations; a low grade flags the conversation into the improvement loop. Runs as a job on the existing queue, on an idle scan or on "Evaluate now" |
| Demo dataset | `scripts/seed_demo_console.py` | Writes about 30 days of generated rows, all `imported`, and never calls a model |
| Setup and import | `scripts/setup_assist.py`, `scripts/import_page_views.py`, [assist-import-format.md](assist-import-format.md) | The setup script creates one assistant agent (kind `assist`) in a dedicated workspace that expires in 10 years (`WORKSPACE_TTL`); the importer validates every line before writing anything, in one transaction |

### 10.2 One question: the three knowledge channels

```mermaid
sequenceDiagram
    actor User as Reader (Chrome)
    participant CS as Content script
    participant SW as Service worker
    participant API as POST /assist/chat
    participant Core as core/assist
    participant DB as Database
    participant M as Groq (gpt-oss-20b)

    User->>CS: a question, a chip, or selected text plus a question
    CS->>SW: chat message with session id, brief, history and question
    SW->>API: HTTP with the X-Assist-Key header
    API->>API: key check, schema caps, per-client and per-session limits
    API->>DB: the assist agent and its current prompt version
    API->>Core: classify_intent, excerpts_for_question, assemble
    Core-->>API: system prompt plus one tagged user turn
    API->>M: one model call, at most 3000 tokens
    M-->>API: raw reply
    API->>Core: finalize_answer (length cap, not-legal-advice sentence, citation check)
    API->>DB: assist_question row, and a flagged run when the citation check failed
    API-->>SW: answer, question_id, intent, assist_version, citation_check, references
    SW-->>CS: the answer
    CS-->>User: the answer with reference pills (granted patents) and one follow-up button; the feedback card once, after the 3rd answer
```

Three channels feed **one model call per question**:

| Channel | What | Changes when |
|---------|------|--------------|
| System prompt | Role, answer format (at most four short sentences, plain text, at most one short quote), citation rules (claims, `[pNNNN]` paragraph markers shown on the page, `[section:NAME]`, law ids), the legal-opinion rule (`backend/core/assist/system_prompt.md`, at most 4000 characters like any prompt). The live prompt is a prompt version in the database, not the file: `python scripts/set_assist_prompt.py` makes the file's text the active manual version of the demo assistant (idempotent; it prints the version number) | A fix is approved and promoted (the evolving artifact), or the author edits it |
| Page context, per question | A **slice** of the brief, inside `<page_brief>` in the final user turn | Every question |
| Law pack, per question | The provisions mapped to the question, inside `<law_excerpts>`, each labelled `[id]` | A human edits the pack |

The order in `/assist/chat`: key check, request validation, the per-client and process-wide LLM limits and the per-session limit, then the agent lookup, then a US-patent check (a number that is not a US utility patent or application, which includes US design, reissue and plant patents, gets the fixed "Patent Assist currently supports US utility patents and applications only..." answer, stored, with no model call), then **intent** (keyword rules, no model call, first match wins: `legal_opinion_request`, `claim_scope`, `claim_explain`, `status_family`, `citations_prior_art`, `classification`, `law_concept`, `navigation_help`, else `other`), the **slice** (`CONTEXT_POLICY` in `assemble.py`, a fixed table in code, not versioned and not evolved), the **law excerpts**, the model call, and the answer checks.

| Intent | Brief sections in the slice besides `meta`, `abstract` and `claims` | Cap (characters) |
|--------|--------------------------------------------------------------------|------------------|
| `claim_explain`, `claim_scope` | description | 12,000 |
| `other` | description | 8,000 |
| `status_family` | legal events, family | 8,000 |
| `citations_prior_art` | citations | 8,000 |
| `classification` | classifications, landscapes | 6,000 |
| `law_concept` | none | 6,000 |
| `navigation_help` | none | 3,000 |
| `legal_opinion_request` | description, legal events | 12,000 |

When a slice is over its cap (measured on the escaped text actually sent, and never above 14,000), description sections are dropped from the end first. For an intent about a list (status and family, citations, classifications) the extra claims go next, then the longest text or list among the sections the question does not need, then the ones it does. The completeness note and the short bibliographic block (number, title, status, expiry, dates, inventors, assignees) are cut only as a last resort. Lists are cut on whole-row boundaries and end with `[list truncated: showing N of M rows]`, and the completeness note then names every section that was trimmed to fit, next to the sections the page reported missing, so the model can say "partial" instead of "the page shows two". Claims come before description. This was added after the real run on 2026-11-23, in which the old cut-down-to-200-characters rule lost the Status and Expiry lines and most citation rows (offline tests only; not re-run with the real model).

**Law pack.** `lawpack.py` loads 12 provisions, each a front-matter file (`id`, `title`, `source`, `status`) plus a plain-language summary of under 1500 characters (a test checks it): `usc-101`, `usc-102`, `usc-103`, `usc-112a`, `usc-112b`, `usc-112f`, `usc-154`, `usc-271`, `mpep-2111`, `mpep-2111-03`, `mpep-2141`, `mpep-2173`. All 12 say `status: draft`. A fixed `INTENT_PROVISIONS` map gives each intent at most 4 ids (for example `claim_scope`: `mpep-2111`, `mpep-2111-03`, `usc-112f`, `usc-112b`; `status_family`: `usc-154`; `law_concept` and `legal_opinion_request` four each; `classification`, `navigation_help` and `other` none), and `excerpts_for_question` puts first the provisions that fixed keyword rules find in the question (indefinite, means for, infringe, comprising, expire, obvious, novel, 101 and so on), then the intent's defaults, at most 4 in all. The question can choose **which** provisions are shown but never what they say. There is no search engine.

**Answer checks** (`answer.py`, `citations.py`, deterministic code). The reply is cut to leave room for two suffixes. For a `legal_opinion_request` the sentence "This is not legal advice, and I can't judge validity or infringement." is appended if the model left it out. The citation check then verifies that every claim number the answer cites ("claim 3", "claims 1-4") exists in the brief and every `[usc-...]` or `[mpep-...]` id was among the excerpts provided; a failure appends "Some citations in this answer could not be verified against the page.", stores `citation_ok = false` and saves the conversation as a flagged run. The check also covers `[pNNNN]` paragraph markers (the number must appear as a `[pNNNN]` marker in the brief's description text; reported as `unverified_paragraphs`) and `[section:NAME]` tokens (NAME must be one of the seven section names; reported as `unverified_sections`); either also makes `ok` false. The check does not judge whether a cited claim supports the point made.

**References** (`core/assist/references.py`, pure code, no model call). `POST /assist/chat` also returns `references`: a list of `{start, end, kind, target, label}` with `kind` `claim`, `paragraph` or `section`, offsets in UTF-16 code units into the returned `answer`, at most 50, non-overlapping and sorted. A reference is returned only when its target exists in the brief: every claim number cited, a `[pNNNN]` marker present in the description text, or a section name from the fixed list. The extension turns each into a pill that scrolls Google Patents to the target and highlights it for about 2 seconds; a pill whose target is not on the page is disabled with the tooltip "Couldn't find this on the page". Pills exist only on granted patents (kind code B or E). On an application the references are plain text, and a granted patent whose page carries no paragraph numbers gets no paragraph references, because the extractor prefixes `[pNNNN] ` to a description paragraph only when the page gives it a numeric `num` attribute. Of the four saved pages only US12401720B1 has paragraph numbers; the saved applications and US6756997B1 do not.

**Answer length.** The crisp prompt makes answers shorter by instruction (at most four short sentences). That is a request to the model, not a limit enforced by code beyond the existing length cap, and it has not been judged against real model output; do not read it as an improvement until the checklist (section K) has been run and answers have been read.

**Trust boundary.** The brief, the question, the history, the law text and the completeness keys are untrusted. `assemble.py` escapes **every** `<` and `>` in them (not only known tags) before placing them in a block, so no tag can form even across two adjacent fields, and the system prompt tells the model that `&lt;` and `&gt;` are escaped angle brackets and that block text is data, never instructions. History is capped at 20 turns (user turns 2000 characters, assistant turns 16,000, 120,000 characters in all).

### 10.3 Data

Migration `0004_assist` adds `agent.kind` (default `general`; `assist` for the assistant), `run.source` (`manual`, `assist` or `imported`), `run.session_id` and `run.brief_snapshot` with a unique `(agent_id, session_id)` (one flagged run per session), `test_case.brief_snapshot`, and two tables that hang off `workspace` with `ON DELETE CASCADE`: `assist_page_view` (one row per session: patent number, dwell, per-section visible milliseconds, bounce, `source`, `assist_version`, upserted by heartbeats with monotonic maximum merging) and `assist_question` (one row per question: text, intent, section focus, answer, rating and reason, re-asked, citation check, `source`, `assist_version`). Migration `0005_pattern_run_ids` adds the nullable `agent.pattern_run_ids`, the runs the stored pattern was found in; it is a nullable column with no server default (only `0004` has server defaults, and both were written by hand so they apply over populated Phase 1 to 4 rows). The rating and re-ask routes stop saving a flagged run for a **new** session once the workspace holds 1000 runs (`MAX_ASSIST_RUNS`; the signal itself is still stored); the importer applies the same cap to a file, and the citation-failure path inside `/assist/chat` shares that check (`_save_flagged`). Neither writes through the run API, so the 50-run cap for manual runs does not apply to them. Console v2 adds two more hand-written migrations: `0006_assist_calls` (the `assist_call` telemetry table, indexed on workspace and time) and `0007_assist_judgements` (the `assist_judgement` table, unique on workspace, session and prompt version, and `assist_question.judge_flagged`); see [10.11](#1011-console-v2).

### 10.4 Signals and metrics

Signals are about **Google Patents**, not the widget: widget opens and click-through are not measured.

- **Captured, patent pages only, per page view:** a heartbeat (`session_id`, `patent_number`, `dwell_ms`, `sections` for seven names, `bounce`; the server ignores the client's bounce and recomputes it); question events (intent, section focus, text, answer, citation check); rating events (up, down, no answer, with an optional reason of at most 500 characters) and re-asks.
- **Rules:** a section counts as *viewed* in the metrics after 3 s visible; a bounce is dwell under 10 s with no section at 3 s or more (`core/assist/bounce.py` is the one function the server and the importer use; the extension tracker mirrors the same two thresholds).
- **Not captured:** search queries, anything outside `/patent/` routes, links that leave the page, scroll depth, copy counts, any identity, any persistent install id.
- **Metrics** (`core/assist/adoption.py`, pure, a zero denominator gives `null`, never `0`): feature coverage (share of non-bounce views with a section visible for 3 s or more), views by section, help demand (questions about a section per 100 non-bounce views in which it was visible; it counts all questions, including from bounced and imported sessions, so it can exceed 100), task mix (intents), struggle signals (navigation questions, re-ask rate and thumbs-down rate by intent (a "didn't answer" rating is not counted), refusal count which is the count of `legal_opinion_request` questions, unverified-citation rate) and `sources` (live versus imported counts). The adoption numbers **include imported rows**; when any exist the console shows "Data source: demo dataset" with the counts.
- **Rating protocol since widget v2.** Ratings are no longer collected per answer. The widget shows one "How is this going?" card once per page view, after the 3rd answered question, and records the rating against the **newest** answered question (its `question_id`, so its intent and assistant version) together with the whole conversation so far. So at most one question per conversation can carry a rating, only conversations of 3 or more questions have one, and the thumbs-down rate (down over **all** questions of the intent, `adoption.py`) is structurally lower than before and is not comparable with data from before widget v2. Because the crisp prompt version and this protocol change shipped together, a before/after comparison of thumbs-down rates across that release is confounded. A click on the code-picked follow-up button is never counted as a re-ask (typed text and chip clicks still are).
- **Version comparison** (`compute_version_comparison`): re-ask and thumbs-down rates per assistant version and intent, from questions whose `source` is not `imported` only. A version needs at least 10 such questions, and at least two versions must qualify, otherwise the console shows one sentence saying why. This is the only signal-based before/after, and it is not the headline.
- **Insights brief** (`POST /assist/console/insights`, `core/assist/insights.py`): the 120B model writes a short summary from the aggregates plus at most 3 question samples per hotspot (the top 2 sections by help demand and top 2 intents by re-ask rate, each strictly positive; an intent hotspot also needs at least `HOTSPOT_MIN_QUESTIONS` = 2 questions). The route returns the figures it was given (`counts_used`). A best-effort check flags any figure it cannot explain from them (`grounded: false`, shown as "Check these figures..."). The check does not verify that a figure is attached to the right metric, trends or causes, and the redaction of question samples (email addresses, phone-like digit runs) is regex only.

### 10.5 Flagged runs and what each flywheel stage sees

A conversation is **flagged** when a question gets a thumbs-down or "didn't answer" rating, is re-asked, or fails the citation check (a thumbs-up does not flag). The conversation, with a snapshot of its brief, is saved as the session's run (`source` `assist`, or `imported` for the importer). For the assistant agent kind the rest of the loop differs from the general agent in these ways: `check_due` counts the runs whose source is `assist` or `imported` (`EVIDENCE_SOURCES`; a manual run never counts) and uses **no keyword prefilter**; pattern detection accepts conversations of **2 turns** (one question and one answer); the Evolution tab has explicit buttons to run the pattern check and Discover, because the console has no chat to trigger them.

| Stage | Sees | Does not see |
|-------|------|--------------|
| Pattern check (`check-pattern`) | Transcripts of the newest unchecked runs (at most 10), plus an **evidence summary** per assistant-sourced run | Brief text |
| Criterion generation (`discover`) | The stored pattern summary plus the evidence summaries of exactly the runs the pattern was found in (`agent.pattern_run_ids`) | Brief text |
| Test building (`build_tests`) | The most recent saved runs, judged against the criterion and, for an assistant run that has a brief snapshot, **with the page** (the same plain-text rendering the replay judge gets; a missing or invalid snapshot is judged on the conversation alone); each test stores its source run's **brief snapshot** beside its scripted questions | The system prompt |
| Replay | The test's questions asked one by one through the same `build_answer_request` and `finalize_answer` as the chat route, with the frozen snapshot, the same law pack and the version's system prompt, at temperature 0 | Anything the live route would not have (but see the two differences below) |
| Judge (replays, held-out, verification) | The criterion and the transcript, plus the page as plain text: title, number, abstract, **claims first**, then description, followed by the facts an answer can rest on (status, expiry, dates, assignees, classifications with their titles, legal events, family, citation counts and the first rows); the facts keep a reserved share of the cap (at most half), each cut section ends with `[truncated]`, every angle bracket is escaped first and the escaped text is then capped at 9000 characters, never inside an entity | The system prompt |
| Suggester | The current prompt, the criterion, the failing result's rationale and transcript, and the evidence summary of the failing test's source run. It may add a "possible law-pack gap" note to the change report for the author to act on | The held-out test, brief text |

**Replay versus live, two differences.** The assembled prompt is the same function either way (a committed test runs a two-turn script through `/assist/chat` and through `replay_assist` and compares the model inputs). Replay passes up to 38 earlier turns (20 scripted questions) to `assemble`, while a live request is capped at 20 history turns by its schema; and replay asks at temperature 0, while the live route uses the provider default. Neither is changed, so a live answer can differ from its replay in wording.

An **evidence summary** (`evidence.py`, at most 1500 characters per question block, at most 2 blocks and 1500 characters per run, at most 10 runs per call) lists the intent, the section focus, which brief sections the intent's slice included, which sections the page reported missing, the citation-check result, the signal reasons, the rating, the assist version and the turn count. It contains no brief, question or answer text, and every name that came from a request is checked against a fixed allowlist (anything else is written as "other"), so no free text passes through it. The transcripts themselves (the questions and answers) do go to the pattern, criterion, judge and suggester models: that is the conversation being analysed.

The headline evidence of improvement is the existing **replay before/after table** (the same tests under the old and the new version), which is the evidence the console will show, and the promotion rule is the same code as before: the fix test, the held-out test and every previously passing test must pass, and a human approves the candidate first. A promoted version is used by the next `/assist/chat` call. What the table can show is limited by the usual caveats: a model judges it, and a replay of a handful of tests is a signal, not proof.

### 10.6 Privacy rules

1. The content script acts only on `https://patents.google.com/patent/<number>`. On any other route it reads, records and sends nothing, and the extension never fetches another page. Its manifest requests host access to `patents.google.com` and the backend origin only (no `tabs` or `storage` permission, and the code uses no extension storage). Whether this holds in real Chrome is on the unrun checklist (D1, F5).
2. A session is one patent page view: a random id (`crypto.randomUUID`, with a `getRandomValues` fallback), no persistent install id, history kept in the page and sent each turn, nothing carried to the next page.
3. What the backend stores: the question text and the answer, a rating and its optional reason, the patent number, the session id, per-section visible times, and for flagged conversations the page text of a public patent document as the brief snapshot. Question text is free text, so a reader can type something personal; it is **not redacted at write time**.
4. Console responses (`status`, `metrics`, `flagged`) contain counts, rates, ids and labels, never question or answer text. The exception is the insights route, which sends up to 3 regex-redacted question samples per hotspot to the model.
5. The assist key is compared in constant time, never logged and never returned. It is compiled into `dist/background.js` only; a build check (`extension/scripts/check-build-secret.js`, also run by `extension/src/build.test.js`) asserts that it is absent from `content.js` and `manifest.json`. **Anyone who holds the built extension holds the key**, so a key in a distributed extension is not a secret. The mitigations are per-client, per-session and process-wide rate limits, and fixed error text; that is acceptable for a local, load-unpacked demo and would not be for a published extension.
6. The demo workspace expires after 10 years, and the 30-day purge does not reach it. While the console is connected the chat app, with its "delete my data" control, is unmounted, and the console refuses to delete workspace data.
7. Imported rows are tagged `source: "imported"`, labelled in the console and never counted as a measured effect. The example file's session ids start with `demo-`, but the importer does not enforce a prefix. A live heartbeat or question for a session id that is stored as imported is refused with a 409, so imported data cannot turn live. Imported flagged conversations are recorded against the agent's current prompt version, not the file's `assist_version`. The importer reads a file of at most 20 MiB line by line and does not sanity-check timestamps.

### 10.7 The console

The **Assist console** button in the app header opens a box for the demo workspace token (the one `scripts/setup_assist.py` printed once). The console first validates the token with `GET /assist/console/status`, then swaps the browser's stored workspace token and agent id for the demo workspace's and backs up the real ones; **Disconnect** restores them. A reload mid-connect restores an interrupted swap, and another tab follows the connected state through the `storage` event. After connecting the console has five tabs, **Overview**, **Adoption**, **Quality**, **Evolution** and **Operations**, under global controls for the time range, the data source (all, live only, demo dataset) and the prompt version, an always-visible source badge and a Refresh button. **Overview** is the landing tab: alerts, headline numbers, quality over time and the improvement loop. **Adoption** shows the metrics of 10.4 as charts, the version comparison and the flagged conversations behind each hotspot (a bar opens the list). **Quality** shows the automatic judge's results and the insights brief. **Evolution** shows the improvement loop as a pipeline with a version timeline and reuses the existing flywheel screens, with the human approval gate unchanged. **Operations** shows latency, outcomes, tokens and the judge queue. Imported and generated data carries the "demo dataset" label on every view that includes it. The endpoints, the chart kit and the limits are in [10.11](#1011-console-v2). Focus handling and the cross-tab behaviour are tested in jsdom only; nothing in the console has been looked at in a real browser.

### 10.8 Decisions

The task plan records seven decisions of its own (D1 to D7) on top of the decisions in the spec (Google Patents with US pages only; the extension plus new assist endpoints on the current backend; evolution covers the system prompt only, with the context policy a documented stretch goal and the law pack human-curated; signals measure Google Patents, not the widget; patent pages only, one session per page view; no simulation, the author populates demo data; plain HTML charts for the first console (console v2 replaced them with the Recharts chart kit, see [10.11](#1011-console-v2)); no automated browser end-to-end test).

| ID | Decision | Where in the code |
|----|----------|-------------------|
| D1 | Dedicated tables `assist_page_view` and `assist_question` instead of the generic event table, because the metrics need per-section and per-intent queries; both carry `source` and `assist_version` | migration `0004`, `backend/db/assist.py` |
| D2 | Flagged conversations are `run` rows with new columns `source` (`manual`, `assist`, `imported`), `session_id` and `brief_snapshot`, one run per session, upserted | `save_flagged_run` |
| D3 | The service worker calls the backend with `host_permissions`. The plan assumed that needs no CORS change, but the first real Chrome run (2026-11-23) sent a preflight from the `chrome-extension://` origin and got `Disallowed CORS origin`, so `backend/main.py` now allows extension origins (`EXTENSION_ORIGIN_REGEX`: 32 letters a-p); the assist key, not CORS, authorises every call. The backend still rate-limits `/assist/*` per session and per client | `extension/manifest.json`, `backend/main.py`, `backend/api/limits.py` |
| D4 | `ASSIST_MODEL` is a single constant defaulting to `MODEL_CHAT` (`openai/gpt-oss-20b`); switching to the 120B model is a one-line change the author judges on real answers | `backend/core/assist/__init__.py` |
| D5 | The law pack is drafted from public sources it names and carries `status: draft` until the author reviews it; the assistant claims no more than "summary of" | `backend/core/assist/lawpack/*.md` |
| D6 | Console access: the author pastes the demo workspace token into a Connect box; the Assist area renders only when connected | `frontend/src/components/assist/ConnectBox.jsx`, `frontend/src/lib/workspace.js` |
| D7 | The test fixtures are real patent pages saved by the author from their own browser, then trimmed by a script (raw saves are git-ignored; four trimmed fixtures are committed) | `extension/fixtures/`, `extension/scripts/trim-fixture.js` |

### 10.9 Verified and not verified (Patent Assist)

**Verified (run in the development environment):**

- `python -m pytest -q`: 2644 passed, 24 skipped (console v2 included; no wall-clock test failed in that run). Every model call is faked. The 24 skipped are the real-Postgres suite; it passed on a local Postgres (including migrations `0004` and `0005`) when the migrations were added and was not re-run for the documentation commit.
- Extension: 471 tests in 16 files passed under Vitest and jsdom (extractor against four saved pages, session and route handling, tracker, widget including answer rendering, pills, jump and follow-ups, service worker, build and key-placement checks); `npm run lint` reports no problems and `npm run build` succeeds.
- Frontend: 783 tests in 44 files passed (the first full run of the final pass had one timeout in `VersionHistory.test.jsx` under load, which passed on the rerun), lint reported no problems at the last task, the build succeeds (sizes in [10.11](#1011-console-v2)).
- Console v2: telemetry, the judge (parsing, hostile text, one verdict per version, idle scan, caps, failures), the seed script (determinism, idempotence, isolation, `--remove`), the filters, the read endpoints (including the aggregates-only and empty-database cases), alerts, the pipeline derivation, the chart kit and every tab are covered by offline tests with every model call faked. The chart text equivalents, states, filter and drill-down behaviour are asserted in jsdom.
- The example import validates (`--dry-run`: 12 page views, 17 questions, 5 flagged conversations).

**Not verified:**

- **Console v2 in a real browser, and the checklist.** [CONSOLE-CHECKLIST.md](CONSOLE-CHECKLIST.md) has not been run. jsdom has no layout, so the 360 px width, label collisions on the 24 hourly and 30 daily axes, marker labels, chart sizing after a tab switch, focus rings, screen-reader output and the lazy-chunk failure path are unchecked.
- **The conversation judge on real conversations.** Never run with a real Groq key: whether its output parses, its grades, its token use and latency against the 8000 tokens-per-minute limit and its agreement with a person are unknown. It is an LLM's opinion, unvalidated against human raters. The console v2 checklist rows E1 to E7 are for this.
- **Generated data is not usage.** The seeded dataset is a scripted story; nothing in it measures real use or an improvement.
- **Real run, 2026-11-23: one run, defects found, fixes not re-run.** 14 chat calls with the real Groq models on 3 trimmed fixture pages (US12401720B1, US20250119367A1, US6756997B1), not live Google Patents pages. The extractor output matched the committed expected output. My judgement of the first 12 answers, before the fixes: 5 correct, 1 mostly correct, 3 partly correct, 3 wrong (the wrong ones were all list or table answers). The run found: slice fitting that cut the Status, Expiry and citation rows a question needed (R1); look-alike dashes and curly apostrophes that defeated the citation check and the not-legal-advice test (R2); an internal tag name in an answer (R3); a judge that could not see classifications, citations, status or legal events (R4); imported demo runs crowding live flagged runs out of `build_tests` (R5); a suggester that wrapped its prompt in `<agent_prompt>` tags and named a `<classifications>` block that does not exist (R6); an insights brief that failed its own grounding check on a correct 20% and was cut off mid-bullet (R7). Each was then fixed and is covered by **offline tests only**; the real model was not re-run against the fixes. **The flywheel did not improve the assistant in that run:** the discovery path found a pattern about legal-opinion refusals from imported demo runs whose tests already passed, so no fix was suggested, and the manual-criterion path produced a v2 that failed verification, for a judge-blind-spot reason that R4 addresses but that is not re-verified. **No claim is made that the flywheel improves answers on Patent Assist.**
- **Real run: a second real run after these fixes has not been done.**
- **Not exercised in the real run:** a 429 from the provider (pacing was on throughout) and adversarial input (injection in a question or in page text). The Phase 4 simulation, which did not show the loop reducing preview runs (see [3.8](#38-the-simulation-phase-4) and [6.15](#615-verified-and-not-verified-phase-4)), is unchanged and is not evidence about this product either way.
- **Widget v2 (references, pills, follow-ups, feedback card, keystroke isolation) in real Chrome.** Checklist section K is new and not run. Whether Google Patents handles keystrokes in the capture phase, which the widget's bubble-phase fix cannot stop, is unverified. Paragraph pills depend on the live page carrying paragraph numbers. The crisp prompt has not been judged against real model output, and its legal-opinion rule ("list their elements" plus the fixed sentence) can sit awkwardly with "at most four short sentences", so the model may resolve it either way. The feedback timing (after the 3rd answered question) is one named setting. To put the prompt live the author runs `python scripts/set_assist_prompt.py` against the demo database.
- **Real Chrome.** `extension/CHECKLIST.md` has not been run. Its section J lists the likeliest failures: messaging between the content script and the service worker, the closed shadow root's focus handling, the `?raw` CSS import, the host permission with a port, `IntersectionObserver` timing and the final heartbeat on `pagehide`.
- **Google Patents layout changes** after the fixtures were saved (the checklist notes the build as 2026-11-06). The selectors were not re-verified against live pages.
- **The law pack's legal accuracy.** Draft, unreviewed by the author.
- **Frontend focus behaviour and cross-tab `storage` events** are verified only in jsdom, not in a real browser.

**Known limitations at this commit:**

- Near-identical questions on the same page can get answers of different quality (seen in the real run: a wrong and a right answer to the same question on one page). No code was changed for this.
- A slice that is trimmed to fit can still lack what a question needs; the model is told which sections were trimmed, but it can still answer from a partial list.
- The cut-off check on the insights brief (finish reason `length`, or a last bullet that ends on a dangling mark or is empty) is a heuristic.
- The brief is extracted once per session; sections that load later are not re-read, so a long session can answer from a stale brief. The completeness map is honest about size caps (a capped or shrunk section is reported incomplete), but it describes the brief as extracted at the start.
- Intent and the legal-opinion refusal rest on keyword rules plus the system prompt's backstop, so a phrasing neither catches depends on the model following its prompt. The rules also over-trigger: words such as "valid" or "infring" in another sense, or a question like "what does enforceable mean", can be refused or routed to the wrong intent.
- The per-session limiter can be sidestepped by rotating session ids; the per-client and process-wide limiters are the backstop.
- The unverified-citation check cannot tell whether a correctly cited claim supports the point.
- When a brief is too large the extractor halves lists (down to nothing on the biggest pages) rather than trimming row by row, so a shrunk section can lose more than it needs to; it is reported incomplete either way.
- The insights grounding check is conservative and loose in places: derived figures are flagged ("Check these figures"), a count of listed items is accepted anywhere, and the phone-number redaction is broad enough to redact dates and long numbers.
- Console v2 limits are listed in [10.11](#1011-console-v2): chat and insights `attempts` are always 1, **auto-judge flagged runs carry no page brief**: tests built from them cannot be replayed and held-out generation fails for them, so such a criterion stalls (details in 10.11), demo version numbers are labels, and the endpoints were not timed on a large dataset.
- A "didn't answer" rating is not counted in the thumbs-down rate; imported flagged conversations are recorded against the agent's current prompt version, not the file's `assist_version`; the importer does not check timestamps.

### 10.10 Deferred

Evolving the context policy (which slices per intent; a documented stretch goal), non-US patents, search-results pages, memory across pages, streaming answers, `evalgate` gating of the assistant, Chrome Web Store publication and an automated browser end-to-end test. Console v2 leaves these, none started:

- A batch endpoint for replay reports: the Evolution replay chart reads up to 8 reports one request at a time, and fails as a whole when one fails.
- The "unjudged" line does not refresh after Evaluate now (the job runs later; the builder presses Refresh, and the console says so).
- `GET /assist/console/metrics` is requested twice per filter change (once by the shell for the amber note and counts line, once by the Adoption tab).
- Extracting the anchor-scroll and open-tab code of `AssistConsole.jsx` into a `useAnchorScroll` hook, and moving the pure helpers of `backend/api/assist_console_views.py` (about 630 lines, four domains) into `backend/core/assist/`.
- A fix for **auto-judge flagged runs carrying no page brief** (the judge job could store the brief it had, or Build tests could skip runs whose snapshot does not validate); until then see the limitation in 10.9 and 10.11.
- A per-model or per-version view of imported telemetry (the seeded rows all carry the model name `demo-seed`).
- `npm audit` in `frontend/` reports 14 findings (1 low, 3 moderate, 10 high) and `npm audit --omit=dev` reports none, so they are in dev tooling only. They were not investigated one by one and no `npm audit fix` pass has been made.
- Validating the judge against human raters, calibrating its flag threshold (2 or lower), and a second judge or rubric version; exporting to an external monitoring system, real-time streaming, notifications, accounts and roles, saved views, export and a dark theme (the spec's non-goals).

### 10.11 Console v2

The console of 10.7 was rebuilt as a five-tab monitoring and observability console. The design is [the console v2 spec](superpowers/specs/2026-11-24-patent-assist-console-v2-design.md), the build plan [the console v2 plan](superpowers/plans/2026-11-24-patent-assist-console-v2.md), and the manual verification list [CONSOLE-CHECKLIST.md](CONSOLE-CHECKLIST.md), which has **not been run**. It added four things on the backend (call telemetry, a conversation judge, a demo dataset script, read endpoints with alerts) and replaced the face. The honest limits are at the end of this section; read them before reading any number the console shows.

#### Call telemetry

Migration `0006_assist_calls` adds the table `assist_call` (`AssistCall`). One row per model call the service makes for the assistant: `kind` (`chat`, `judge` or `insights`, `CALL_KINDS`), `model`, `latency_ms`, `outcome` (`ok`, `error`, `rate_limited` or `timeout`, `OUTCOMES`), `attempts`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `prompt_version` (null for insights), `source` (`live` or `imported`; only the demo script writes `imported`) and `created_at`. It holds no question or answer text.

- **Who writes it.** `POST /assist/chat` (kind `chat`, with the prompt version that answered), `POST /assist/console/insights` (kind `insights`, model `MODEL_JUDGE`, no prompt version) and the judge job (kind `judge`). The service's own rate-limit 429s (`rate_limit_llm`, which refuse a request before any model call) are not recorded.
- **Tokens.** `backend/groq_client.py` exposes `last_usage`, a context variable reset at the start of every `groq_chat` call and set after a successful response; each count is a real integer or `None`. It follows the pattern of the finish-reason value that is shared the same way. A failed call stores `None` tokens. A reply that is then rejected as blank is stored as `error` but keeps its token counts, because the call succeeded and spent them.
- **Latency is the total time the user waited**, measured with `time.monotonic()` from before the model call to after it, so it includes the provider SDK's own retries.
- **Attempts are always 1 for chat and insights.** The SDK retries are not observable from the service, so a retried call looks like one slow attempt. The judge job stores its job's attempt count, which is the queue's retry count, not the SDK's.
- **Outcome.** A Groq rate-limit error is `rate_limited`, a timeout is `timeout`, anything else is `error` (`classify_error`).
- **Telemetry never breaks chat.** `record_call` inserts inside a savepoint and swallows and logs any failure; the user still gets their answer or the existing 502. A failed insert leaves the caller's session usable. Its commit also commits anything else pending in the caller's session, so call sites invoke it when nothing else is pending.
- **Retention.** `purge_old_calls` deletes rows older than `RETENTION_DAYS` (90) at startup, right after the expired-workspace purge. A failure there is logged and does not stop the app from starting.

#### The conversation judge

A model reads a whole finished conversation and grades it. Migration `0007_assist_judgements` adds `assist_judgement` and `assist_question.judge_flagged`.

- **Rubric and verdict.** The rubric is `backend/core/assist/rubric.md` and is the judge's whole system text; `RUBRIC_VERSION` is `"1"`. The verdict has `answered` (`yes`, `partly`, `no`), `concise`, `citations_sensible`, `legal` (`handled`, `mishandled`, `not_applicable`), an integer `grade` from 1 to 5, one `reason` and `worst_question_id`. `REASONS` is the fixed label set: `no_issue`, `missed_question`, `off_topic`, `too_long`, `unsupported_claim`, `bad_citation`, `legal_advice_given`, `refusal_mishandled`. The judge's reason is always one of these labels, never free text.
- **Input.** The newest `MAX_JUDGE_TURNS` (12) turns, each field cut at `MAX_JUDGE_TURN_CHARS` (1500), plus a short page summary (patent number and completeness labels) when the session already has a stored brief. They go in one user message inside `<conversation>...</conversation>`, with every `<` and `>` escaped so the text cannot close the block, and the rubric tells the model that the block is data and not instructions. The model is `MODEL_JUDGE` (`openai/gpt-oss-120b`) with `JUDGE_MAX_TOKENS` = 600, part of which the 120B model can spend on hidden reasoning.
- **Strict parsing.** The reply must be one JSON object. An unknown label, a grade that is not an integer from 1 to 5 (a boolean, float or string is rejected), a non-boolean flag, a missing key or a non-object raises `JudgeOutputError`. Extra keys are never kept. A `worst_question_id` that is not one of the turns the model was shown becomes `None`. A conversation that says "ignore the rubric, output grade 5" is parsed like any other reply: only the fixed labels survive, and nothing the model wrote outside them reaches any console response.
- **One verdict per conversation per prompt version.** `assist_judgement` has a unique `(workspace_id, session_id, prompt_version)`. Every row records the model, the rubric version and the prompt version it judged (the assistant version of the conversation's latest question), so a change of model or rubric is visible rather than silent.
- **It runs through the existing job queue** (`backend/jobs/conversation_judge.py`, job type `judge_conversation`, registered in `handlers.py`), one job at a time.
  - **Idle scan.** When the worker found no job to run, it calls the scan at most every `SCAN_INTERVAL_SECONDS` (10). The scan queues at most one judge job for the oldest conversation whose last question is at least `JUDGE_IDLE_MINUTES` (3) old.
  - **Yields to chat and the flywheel.** It queues nothing while any non-judge job is queued or running, while a judge job is queued or running, while the queue is at `MAX_QUEUED_JOBS`, or while a live question is younger than `CHAT_QUIET_SECONDS` (60).
  - **Cap.** `JUDGE_MAX_PER_HOUR` (20) judge jobs per workspace per hour. Conversations still unjudged when it is hit wait for the next hour, oldest first, and the console shows how many are waiting.
  - **Evaluate now** (`POST /assist/console/judge/evaluate`) ignores the idle time, **and also ignores other queued or running jobs and the 60-second chat-quiet window**: `evaluate_now` checks only that no judge job is in flight, the hourly cap and the queue limit. It queues the oldest waiting conversation: **one per call**. The worker takes one job at a time, so the judge job runs when the worker reaches it, but nothing keeps it from overlapping a chat call (or a flywheel call) in the same 8000-tokens-per-minute minute on the free tier. Only the idle scan yields to chat and the flywheel. It returns `{queued: 0 or 1, waiting: n}`. The judgement appears only after the job runs; the console says so and the builder presses Refresh.
- **Never on imported data.** The handler skips a session that has any imported question or page view, with no model call and no telemetry, and `record_judgement` refuses an imported session. The judge therefore spends no tokens on demo text. The demo script writes its own judgements directly (model `demo-seed`, rubric `demo`).
- **Failure.** A conversation the judge cannot parse (`JudgeOutputError`) is recorded as an `error` call, fails its job with the fixed message "The judge could not read this conversation.", and is not queued again until a new question arrives; it is counted as `unreadable_sessions` in the judge queue panel and is not counted as waiting. A provider rate limit or timeout is retried by the queue; any other provider error is recorded as an `error` call and left to the worker's generic failure path (the message "Something went wrong. Try again."). Neither suppresses the session: it is retried under the hourly cap. A lost race that produces a second verdict for the same version is skipped as `already_judged`. A judge failure never blocks chat.

**How a low grade flags a conversation.** A grade at or below `FLAG_GRADE_AT_OR_BELOW` (2) stores `AssistJudgement.flagged = true` (which means the grade was low, not that a run exists: the 1000-run cap can skip the run), sets `judge_flagged` on the worst question (the one the verdict names, else the session's latest question) and saves the conversation as the session's flagged run through the same function a thumbs-down uses. `FLAG_REASONS` has a fifth reason, `auto_judge`, which shows in the console as "Auto-judge", and the flywheel's evidence summary gets the fixed signal "auto-judge flagged the conversation". Two consequences, from reading the code, not from a run with a real model:

- The run has `source` `assist` (it is a live conversation, not an imported one), and a conversation that was never flagged before has **no stored page brief**: `record_judgement` saves it with an empty brief (`existing or {}` in `backend/db/assist.py`), while a session that already had a run keeps that run's brief. `patent_number` is required in a brief, so `Brief.model_validate({})` fails. What follows is from reading the code (no test runs this path end to end, and it has not been run with a real model). The pattern check and criterion discovery work from the evidence summary and the transcript, so they can use such a run. **Build tests does not skip it**: `handle_build_tests` copies the snapshot without validating it, so tests are created from the run (their judge falls back to judging the conversation alone, because `_page_evidence` swallows the invalid snapshot). But those tests **cannot be replayed**, and **held-out generation fails** for them: `handle_replay` and `handle_generate_heldout` both go through `_snapshot_brief`, which raises the fixed job message "That test's saved page is not valid.". So a criterion whose fix test comes from an auto-judge-only conversation **stalls** at replay and held-out generation, and a verification replay would fail the same way. Named known limitation: **auto-judge flagged runs carry no page brief**. Take fix tests from conversations that have a real brief (a thumbs-down, re-ask or citation-failure conversation).
- The adoption numbers and the version comparison are unchanged by `judge_flagged` (a test pins this).

#### The demo dataset

`python scripts/seed_demo_console.py --confirm [--end YYYY-MM-DD] [--seed N] [--database-url URL]` writes about 30 days of generated rows into the workspace of the configured assist agent; `--remove --confirm` deletes them. It is never run silently: without `--confirm` it prints the plan and exits 2.

- **Imported rows only.** Page views, questions, model calls, judgements and flagged runs all have `source` `imported`; session ids start with `demo30-`. With the default seed and `--end 2026-11-24` it writes 428 page views, 477 questions, 719 model calls, 217 judgements and 25 flagged runs.
- **No `PromptVersion` rows.** The script never creates or changes a prompt version, so restore, promote and the "live version" KPI are untouched. Version numbers 1, 2 and 3 in demo rows are **demo-only labels**: version 2 from 12:00 UTC on day 20 of the span and version 3 from 12:00 UTC on day 27. The console draws them as markers labelled "(demo)".
- **The storyline** is scripted: a weekday and weekend rhythm, a latency bump on days 8 to 10 (about 2.2 times), nine rate-limited calls on day 15, a quality dip on days 14 to 20 (lower grades and thumbs-up share, more re-asks) and a version marker after it. It is described as demo data wherever it appears and is **never evidence of improvement**: the version comparison keeps using live questions only, and the Evolution before/after chart shows real replay results only.
- **Deterministic and idempotent.** The same `--seed`, `--end` and agent give the same rows (apart from generated run ids). A re-run deletes the previous seed first and inserts again in one transaction (a failure leaves the database as it was); `--remove` deletes the `demo30-` rows and all imported call rows of the workspace. It never touches live rows, the hand-written example import, or rows without the prefix; a live row that uses the prefix makes it refuse. Question and answer text are short canned templates (the script never calls a model) and are never shown in the console.
- **Imported runs do feed the live pattern check.** `EVIDENCE_SOURCES` is `("assist", "imported")`, so the seeded flagged runs (canned text) are evidence for **Check flagged conversations for a pattern**, and `--remove` does not reset `last_checked_at_run` or `pattern_run_ids`. Only the 25 runs of low-graded sessions exist; the other thumbs-down, re-ask and citation-failure questions in the data have no run, so the console's flagged-question count exceeds the run count. The pipeline's first step ("Flagged conversations") counts runs of any evidence source, demo included.
- **Future-dated rows.** The default `--end` is today (UTC), and the seeded timestamps for today run to about 22:50 UTC, later than "now". Those rows are left out of 24 hours, 7 days and 30 days but counted in All. Use `--end` with yesterday's date.
- **A guard.** If live runs were saved after the seed, so that one sits above a seed run, re-seeding and `--remove` exit 2 before deleting anything.

#### The read endpoints

All are under `/assist/console`, authenticated with `X-Workspace-Token`, and read only the workspace's own assist agent. **They return counts, rates, ids and labels only**: never question text, answer text, ratings reasons, briefs, summaries, criterion names or keys. Tests plant marker text in questions, answers, briefs, messages, the pattern summary and job payloads and assert that none appears in any route. A stored judge `reason` outside `REASONS` is reported as `other`; an unknown `answered` or `legal` value is counted nowhere.

| Route | Payload |
|-------|---------|
| `GET /status` | agent id and name, current version number, run, flagged-run and imported-run counts (unchanged) |
| `GET /metrics` | the adoption metrics of 10.4 and the version comparison, filtered; `data_source_label` ("demo dataset") when imported rows are in the result (the version comparison ignores the filters and stays live only) |
| `GET /flagged` | the flagged conversations as ids, the patent number, intents, sections, reason labels, a source and per-question labels and flags; params `intent`, `section`, `limit`, `reason` (one of `FLAG_REASONS`), and the shared filters. The range applies to the run's creation time; a version filter keeps a run when any question in its pool has that version |
| `POST /insights` | the insights brief of 10.4 (unchanged) |
| `GET /trends` | `{bucket, totals, previous, points, version_markers}`: counts of page views, questions, re-asks and thumbs-downs per bucket, the totals and the same for the previous period |
| `GET /overview` | `{versions, kpis, alerts, loop, quality_trend, version_markers}`: four KPIs (`questions`, `ok_share`, `open_flagged`, `live_version`) with delta and sparkline, the active alerts, the pipeline and the grade trend |
| `GET /quality` | `{judged, unjudged, grade_trend, answered, concise_share, citations_share, legal, reasons, ratings, bucket, version_markers}` |
| `GET /operations` | `{bucket, latency, outcomes, requests, tokens_by_kind, totals, judge_queue, recent_failed_jobs}`: p50 and p95 per bucket, calls by outcome, requests, tokens by kind, the queue (`waiting_sessions`, `queued_jobs`, `oldest_waiting_minutes`, `judged_last_hour`, `hourly_cap`, `unreadable_sessions`) and the newest 10 failed jobs (id, type, time, the stored fixed message) |
| `GET /pipeline` | `{steps, cycle_note}`: eight steps with `done`, `current` or `todo` and a fixed-phrase detail |
| `POST /judge/evaluate` | `{queued, waiting}` |

- **Shared filters** (`console_filters.py`): `range` (`24h`, `7d`, `30d`, `all`), `source` (`all`, `live`, `demo`; `demo` means `source = imported`) and `version` (a positive integer). An invalid value is a 422 "Invalid filter.". The **backend defaults** for a call without parameters are `range=all`, `source=all`, no version; the **UI defaults** are 30 days, All, All. Changing the source in the UI resets the version, because a version number means something only inside one source.
- **Half-open ranges.** A row belongs to a range when `since <= time < until`.
- **Buckets** are counted back from the request time: 24 hourly buckets for `24h`, 7 daily for `7d`, 30 daily for `30d`, zero-filled. They are **not calendar aligned**: an hourly label carries the minute offset of the request, so charts label the x axis with the real UTC time, never a calendar day. `all` is calendar days from the oldest row to now (an empty list on an empty database), and its `previous` is null. Rows stamped after "now" (seeded rows for today) are excluded from the bounded ranges and included in `all`.
- **Zero denominators are `null`**, never `0`: every rate, share, percentile and average grade of an empty set is `null`, drawn as a gap or "n/a" and never as "0%". Counts are 0.
- **What ignores the filters:** `GET /pipeline`, `judge_queue`, `recent_failed_jobs` (they describe the workspace now), the version comparison, and the open-flagged KPI's range and version (it honours the source). The Overview `loop` is the same payload as `/pipeline`.
- **Operations scope.** Percentiles use chat calls only (nearest rank); `error_rate` is (`error` + `timeout`) / all calls of every kind; `rate_limit_rate` is `rate_limited` / all calls.
- **KPI units.** A `percent` value is 0 to 100 and its delta is in points; a `count` delta is the relative change in percent; `open_flagged` and `live_version` have no delta.

#### Alerts

Rules in code (`backend/core/assist/alerts.py`), thresholds in one dict, `THRESHOLDS`:

| Key | Value | Rule |
|-----|-------|------|
| `error_rate` | 0.05 | error rate at or above it, with at least `min_calls` calls (alert `error_rate`, to Operations `ops-errors`) |
| `min_calls` | 20 | minimum sample for the error-rate and p95 rules |
| `p95_latency_ms` | 8000 | chat p95 latency at or above it, with at least `min_calls` chat calls (`p95_latency`, `ops-latency`) |
| `reask_rate_rise_pts` | 5 | re-ask rate up at least 5 points on the previous period, with at least `min_questions` questions in both periods (`reask_rise`, Adoption `adoption-reasks`) |
| `min_questions` | 20 | minimum sample for the re-ask rule |
| `flagged_backlog` | 5 | at least 5 flagged conversations newer than the assistant's last pattern check (`flagged_backlog`, Evolution `evolution-loop`) |
| `judge_backlog` | 10 | at least 10 conversations waiting for the judge (`judge_backlog`, Quality `quality-judge`) |

A rule fires **at or above** its threshold; an unknown (`None`) input never fires; a value at twice the threshold or more (`CRITICAL_FACTOR`) is `critical`, otherwise `warning`. Titles and details are fixed strings built from numbers only. An alert computed from rows that include demo data carries `demo: true` and is tagged "demo" in the strip; the judge-backlog alert is never demo, because the judge sees only live conversations. "Unreviewed" means flagged after the last pattern check; no review status exists. The strip shows only active alerts, each a button that opens its tab and scrolls to its anchor. Nothing is sent anywhere.

#### The pipeline derivation

`GET /pipeline` shows the state of the assistant's current improvement cycle in eight steps: Flagged conversations, Pattern found, Criterion written, Tests built, Fix suggested, Fix reviewed, Fix verified, Fix promoted. It is derived from what the Evolution tab already reads: flagged = runs with an evidence source; pattern = `agent.pattern_summary` present; criterion = criteria count; tests = `EvalTest` count; fix, review, verify and promoted from the newest suggested prompt version and its status (`proposed` means review is current, `approved_verifying` means verify is current, `promoted`, or `superseded` with a verification record, means all done; `rejected`, `failed_verification` and an unverified `superseded` send the cycle back to "Fix suggested" with a fixed note).

**Backward-implied rule.** After the per-step flags are computed, a step is done if any later step has evidence (`done[i] = done[i] or any(done[i+1:])`). Without it, discovering a criterion (which clears the stored pattern summary) put "Pattern found" back as current and everything after it as to do, contradicting the Evolution screens. The consequence, by design: "Flagged conversations" can show done with no detail when only later evidence exists. The first step that is not done is `current`; when all are done there is none.

#### The frontend

- **Lazy chunk.** `App.jsx` renders `ConsoleLoader`, which `React.lazy`-loads `AssistConsole` inside `Suspense` and a `ConsoleBoundary` error boundary. A failed chunk load shows "Couldn't load the console." with **Retry** (a fresh lazy component, so the import is attempted again) and **Disconnect**, and the rest of the app stays standing. A browser may keep a failed dynamic import cached; reloading the page is then the fallback (there is no "Reload page" button).
- **Data hook.** `useConsoleQuery(fetcher, filters, reloadKey, { isEmpty, quietKey })` returns `{ state, data, error, refreshing, retry }`. It reads filters by value, ignores stale responses, keeps the last good data while refetching (`refreshing`) and reports `loading`, `ready`, `empty` or `error`. `quietKey` is part of the fetch key but does not make `refreshing` true: the Evolution tab uses it so that a loop move (a pattern found, a candidate changing status) refreshes the stepper and the timeline without dimming the tab or moving the approval buttons.
- **`RefreshFrame`** wraps a tab's body: while old data is on screen and a refetch is in flight it sets `aria-busy`, dims the content and shows "Updating…". The source badge changes at once, so a stale number is never shown under a truthful badge without the cue.
- **States.** Every panel renders through `PanelState`: "Loading…", "Not enough data yet." and "Couldn't load ...: <message>" with a Retry button.
- **Chart kit** (`components/assist/charts/`, Recharts pinned at 3.10.1): `ChartCard` (title, "View as table" toggle, a real `<table>` with caption and column and row headers, a one-sentence summary on the chart wrapper), `KpiCard` (value, change in words with "better" or "worse", a decorative sparkline), `TrendChart` (lines with gaps for `null`, dashed version markers with a text label, a text legend always; by convention at most 4 series, which nothing enforces), `BarsChart` (grouped or stacked, value labels, an `onSelect` that makes every bar a focusable button with a visible focus ring), `PanelState`. Axis and tooltip times are UTC (`HH:MM` for hourly buckets, a date for daily ones). A chart that has clickable bars sits in a `role="group"` wrapper instead of `role="img"`, because an image role would hide the buttons from assistive technology.
- **Palette.** `palette.js` holds six series colours (the first six of the `dataviz` skill's reference palette), status colours, grid and text colours. Colour never carries meaning alone: every series is named in a text legend and a table, and status is given in words. The validator's own result says the first six colours pass the lightness, chroma, adjacent-colour and colour-blind separation checks, with a contrast warning (below). Its all-pairs check fails, which is why the palette is for neighbouring series only, charts should carry at most 4 series (a convention: a comment in `TrendChart.jsx` and `palette.js` says so, `MAX_SERIES` is 6 and nothing enforces 4), and a seventh series would be neutral grey rather than a repeat.
- **Shell.** `AssistConsole` holds the five tabs with arrow, Home and End keys, the global `ConsoleControls` (the source badge, the range group, the source and version selects, "All times are UTC.", "Refresh data"), and the alert-to-anchor scrolling (`onOpenTab(tab, anchorId)`, which retries for about 2 seconds while a tab loads). Tabs other than Overview mount on first open and stay mounted, hidden. Every tab receives `{ filters, reloadKey, onOpenTab }`.
- **Tabs.** *Overview*: alert strip, four KPI cards, quality over time with version markers, the loop stepper. *Adoption*: four KPI cards, page views and questions over time, four clickable bar charts (feature coverage, help demand, task mix, re-ask and thumbs-down by task) that scope the flagged list, the struggle numbers, definitions, the version comparison (live only) and the flagged list. *Quality*: the judge disclaimer, Evaluate now, KPI cards, the unjudged count, the grade trend, verdicts, reasons and ratings, the insights brief, the flagged list with reason chips. *Evolution*: the pipeline stepper, the version timeline, the replay before/after chart and the actions of 10.5 unchanged, including the human approval gate. *Operations*: latency, outcomes, requests, tokens, the judge queue and recent failed jobs.
- **Evolution is not filtered.** It describes the whole assistant. The replay chart reads real stored replay results through `GET /prompt-versions/{id}/report` (up to 8 reads per load) and draws nothing from demo data. The version timeline lists the real prompt versions; nothing in it is labelled demo.

#### Build facts

- **Dataviz validator** (`validate_palette.js`, light mode, surface `#ffffff`, palette `#2a78d6,#eb6834,#1baf7a,#eda100,#e87ba4,#008300`), run in the chart kit task and pasted at the top of `palette.js`: lightness band PASS (all 6 inside L 0.43 to 0.77); chroma floor PASS; colour-blind separation PASS (worst adjacent `#eda100` against `#1baf7a`, dE 9.1 protanopia, tritan 5.8); normal-vision floor PASS (worst adjacent `#e87ba4` against `#eda100`, dE 19.6); contrast against the surface **WARN** (`#1baf7a` 2.82, `#eda100` 2.17, `#e87ba4` 2.69; "relief required: visible labels or table view", which the text legends, value labels and tables provide); overall "ALL CHECKS PASS" (exit 0). With `--pairs all` the run fails (colour-blind `#008300` against `#eb6834` dE 3.2; normal-vision `#e87ba4` against `#eb6834` dE 12.9), hence the 4-series convention. Only a light run exists: the app has no dark theme.
- **Bundle size** (`npm run build`, gzip). Frontend `npm run build`: entry JS 326.35 kB raw / **99.61 kB gzip**, lazy console chunk (`AssistConsole-*.js`, with Recharts) 462.12 kB raw / **131.22 kB gzip**, CSS 19.74 kB / 4.82 kB gzip. Before the chart kit (the chart-kit task's base, console in the entry bundle): entry 344.36 kB / **103.04 kB gzip**, no console chunk. Importing the kit statically into the entry measured +114.7 kB gzip (217.78 kB gzip in all), so the console is loaded lazily and the chat app's entry bundle is 3.43 kB gzip smaller than before; the console chunk is fetched only when the console route opens. The before figures were measured while the chart kit and the console shell were built and cannot be reproduced from the repository now; the final figures come from a fresh `npm run build`.

#### Honest limits

- **The judge is a language model's opinion.** Its grades, verdicts and reasons have not been validated against human raters, and the console says so on the Quality tab. A grade measures what the model thought of a conversation under rubric version 1; it does not measure answer quality, and **no claim is made that the assistant's answers got better** (or worse) because of anything in this section.
- **The judge has not been run against real conversations.** Everything about it is tested with the model faked. The real Groq run of the judge, its rubric against real conversations and its real token and latency numbers against the 8000 tokens-per-minute limit are rows E1 to E7 of the console checklist, and have not been done. Whether the 600-token cap is enough, how often it returns unparseable output, and how its grades compare with a person's are unknown.
- **Generated data is not usage.** The demo dataset is a scripted story. Its trends, dip, latency bump, rate-limit burst and version markers say nothing about real use or about an improvement. The console labels it ("demo dataset" in the badge, the amber notes and the "(demo)" markers and alerts); a console with demo rows included is not a measurement.
- **Nothing was verified in a real browser.** Layout at laptop and 360 px widths, label collisions (marker labels, the 120 px category column, 24 hourly and 30 daily axis labels), the sizing of charts after switching tabs, focus rings, screen-reader output and the lazy-chunk failure path were checked only in jsdom, which has no layout. [CONSOLE-CHECKLIST.md](CONSOLE-CHECKLIST.md) lists the rows; none has been run.
- **The real-Groq judge run is not done** (see above), and neither is a second real run of the rest of Patent Assist after the 2026-11-23 fixes.
- **Behaviour with a large live dataset is not measured.** The read endpoints load a workspace's rows and compute aggregates in Python per request (telemetry is bounded by the 90-day purge, judgements by the hourly cap, runs by the 1000-run cap); no timing was taken.
- **Smaller known limits.** Chat and insights `attempts` are always 1 (above). The "unreviewed flagged" count is "flagged since the last pattern check", not a review state. Percentiles are chat-only. **Auto-judge flagged runs carry no page brief**: Build tests creates tests from them that cannot be replayed or used for held-out generation ("That test's saved page is not valid."), so a criterion whose fix test comes from one stalls (above). The seeded model name is `demo-seed`, so a per-model view of imported data shows one model. The flagged list's version filter can match a run through a different question than the one its intent and section came from.

---

*Updated November 2026 for the persisted, agent-ready architecture with saved tests, replay and judging, the evaluator-driven fix loop, and the CI gate, simulation harness and fail-fast verification (Phases 1 to 4), and the Patent Assist extension, console and assistant agent (section 10, including the console v2 telemetry, conversation judge and five-tab console, built and tested with faked model calls, and run once with real models on 2026-11-23, after which defects were fixed and tested offline only). For questions, see the project README or open an issue.*
