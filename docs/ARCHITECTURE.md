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

    Dev -->|"Pastes an agent prompt or picks a preset,<br/>chats, reviews & edits eval criteria"| System
    System <-->|"Persists and reloads state"| DB
    System -->|"LLM inference requests<br/>(chat / analyze / pattern / criteria / judge / replay / suggest fix / generate test / report)"| Groq
    Groq -->|"Generated responses"| System
    System -->|"Exports eval rubric"| Export
    Export -->|"Imported by team"| TestPipeline
```

**Key relationships:**
- The developer is both the conversation participant (playing the user role) and the rubric author
- Groq and the database are the only runtime dependencies — there is no auth service; identity is an anonymous workspace token
- The output (JSON rubric) flows into the team's broader test infrastructure

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
            Promotion["promotion.py<br/>(decide_promotion · build_outcome_rows · quote_turn)"]
            Prompts["prompts.py<br/>(prompt builders · delimiter neutralizing)"]
        end

        subgraph Db["db/ — persistence"]
            DbModels["models.py · session.py"]
            DbHelpers["agents.py · workspaces.py · events.py · testsuite.py"]
            Candidates["candidates.py<br/>(create_proposed_version · approve · reject · restore · compute_required)"]
            Reconcile["reconcile.py<br/>(reconcile_verifying: promote or fail)"]
        end

        subgraph Jobs["jobs/ — background work, same process"]
            Queue["queue.py<br/>(enqueue · claim_next · requeue_stale)"]
            Handlers["handlers.py<br/>(build_tests · replay · suggest_fix · generate_heldout · change_report)"]
            Worker["worker.py<br/>(worker_loop started in the app lifespan)"]
        end

        GroqClient["groq_client.py<br/>(API wrapper · model constants · think-block stripping)"]
        Utils["utils.py<br/>(JSON extraction from LLM output)"]

        FastAPI --> WsRouter
        FastAPI --> AgentRouter
        FastAPI --> RunRouter
        FastAPI --> CriteriaRouter
        FastAPI --> PromptRouter
        FastAPI --> TestsRouter
        FastAPI --> VersionsRouter
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

    subgraph GroqAPI["Groq API"]
        Chat20["openai/gpt-oss-20b<br/>(plays the agent under test · replays)"]
        GPT["openai/gpt-oss-120b<br/>(prompt analysis · pattern detection · judge · suggest fix · generate test · report)"]
        Qwen["qwen/qwen3.8-27b<br/>(criteria generation)"]
    end

    DB[("Postgres / SQLite")]

    ApiClient -->|"HTTP / JSON"| FastAPI
    DbModels --> DB
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

---

## 4. Data Model

Ten tables (Alembic migrations `migrations/versions/0001_foundation.py`, `0002_tests_and_jobs.py` and `0003_evolve.py`; Phase 3 adds columns, no tables). Ids are string UUIDs and timestamps are naive UTC so SQLite and Postgres behave alike. Every foreign key is `ON DELETE CASCADE`.

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
| Groq client | 45 s timeout and 5 retries per call (the SDK backs off between them) |
| Workspace creation | 20 per minute per client |

The LLM calls a background job makes are NOT counted by the per-client or process-wide limiters above (they happen in the worker, not in a request). The jobs a person starts (replay, run-all, build, suggest fix, generate held-out) are bounded by the hourly per-workspace job cap, the global queue-depth cap and the Groq client timeout, so a single stuck call cannot hold the one worker for more than a few minutes. Approve's replays and the report job are exempt from the hourly cap: they are bounded by the 100-job queue cap and by the single worker, which runs one job at a time.

**Operational note: Groq's free tier.** The free tier limits `gpt-oss-120b` to 8000 tokens per minute. One build or one verification fan-out (several judge calls in a row) can hit it. A call that is rate limited is retried by the client (5 retries); if it still fails the job fails with "The judge was unavailable. Try again.", except a verification replay, which the worker retries up to its 3 attempts. Consider a paid tier, or spacing jobs, if you see these. One live run is not a capacity test.

Limiters are in-memory sliding windows, per process: they reset on restart and are not shared across instances. The client key is derived from `X-Forwarded-For` by taking the Nth entry from the right, where N is `TRUSTED_PROXY_HOPS` (default 1; 0 uses the direct peer address; a missing or too-short header also falls back to the peer address). Entries to the left of that are client-supplied and ignored.

### 6.7 Model Assignments

| Task | Model | Reasoning |
|------|-------|-----------|
| Conversational chat (plays the agent under test) and test replay | openai/gpt-oss-20b | Low latency for interactive chat; 20B is enough for persona roleplay. Replays use temperature 0 |
| Prompt analysis and pattern detection | openai/gpt-oss-120b | Higher accuracy for classification over a long prompt or several transcripts |
| Judge (verdicts and the sanity check) | openai/gpt-oss-120b, temperature 0 | The larger model for grading against a rubric; temperature 0 for repeatability (not a guarantee) |
| Fix suggestion, held-out scenario generation, change-report narrative | openai/gpt-oss-120b, temperatures 0.2, 0.7 and 0 | Same model as the judge; the suggester is slightly creative, the generator needs variety, the report writer must be repeatable. None of them can decide a verdict or a promotion |
| Structured criteria generation | qwen/qwen3.8-27b | Reasoning-capable model for grounded, structured JSON rubrics (reasoning output hidden) |

### 6.8 Judge

`core/judge.py` grades one transcript against one criterion and returns `pass`, `fail` or `not_applicable` with a rationale. The criterion fields and the transcript are wrapped in delimiter tags (`<criterion>`, `<transcripts>`) and neutralized like every other pasted text, and the system text says tagged content is data, never instructions. The judge **never sees the agent's system prompt**, and the judge system text is a fixed string with no agent name or role label (the transcript's speaker labels carry them, as data). Unparseable output, an unknown verdict, an empty rationale or a failed call raises `JudgeFailed`: the job fails with a plain message and no result row is written.

**Sanity check.** When tests are built, the judge must also pass the criterion's own good example and fail its bad example. The outcome is stored in `criterion.judge_check`; if the check itself cannot run (for example the judge is rate limited), the build or replay still succeeds without it and the next replay tries again. If it is wrong either way the UI shows verdicts as "low confidence". This check is weak by construction: the example appears both in the criterion text and as the transcript being graded, so it only catches a judge that cannot read the criterion at all, not one that is subtly miscalibrated.

**Status** (`core/testsuite.criterion_status`, never the LLM): any test whose current replay is `fail` gives `failing`; else any test with no current replay gives `untested`; else any `pass` gives `passing`; else `untested`. "Current" means the newest non-stale `replay` result for the agent's current prompt version. Run all (since Phase 3) replays only tests that have no current replay, so the banner "N tests haven't run on prompt v{x} yet." and the button agree.

### 6.9 Background Jobs

`jobs/queue.py` is a plain-function queue over the `job` table. `claim_next` takes the oldest `queued` job with `SELECT ... FOR UPDATE SKIP LOCKED` (a no-op on SQLite), marks it `running`, stamps `locked_at`, counts the attempt and commits before returning. `requeue_stale` runs at the start of every worker pass: a `running` job whose lock is older than 180 s goes back to `queued`, or to `failed` once it has used 3 attempts. `worker_loop` (started in the FastAPI lifespan, stopped on shutdown) runs one job per pass in a thread and sleeps one second when idle. At the end of **every** pass, also an idle one and also when the job step raised, it calls `reconcile_verifying` (errors are logged, never raised), which resolves candidates being verified (see [6.11](#611-the-fix-loop-and-promotion)).

Handlers do their LLM work first and write rows in one commit at the end, so a failure leaves no partial rows. Provider errors are logged and never copied into the user-facing job error. A criterion, test or prompt version deleted while its job waits fails the job with a clear message; the loop keeps running. Starting the same replay, build, suggestion or held-out generation twice returns the already-active job instead of queueing a second. A replay queued by Approve (payload `system: true`) that hits a transient agent or judge failure raises `RetryableJobFailure` (a `JobFailed`): the worker requeues it like a crash while attempts remain (3 in all, no sleep, so the queue is not blocked) and only then fails it with the fixed message, which fails the candidate ("A test could not be run: ..."). A replay a person started fails at once; they can press Replay again. A replay that stores a result deletes older replay results for the same test and version, and a replay or build whose criterion was edited while it ran stores nothing and returns `{"discarded": true}`.

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

Build detail worth knowing: the build job judges at most the 8 most recent saved runs (oldest first) and stops after two failures, so an older failing run outside that window never becomes a test.

Phase 3 folded in four Phase 2 deferrals: M4 (the stale banner now reads "N tests haven't run on prompt v{x} yet."), M5 (Run all only reruns tests without a current replay), M6 (a result judged against a criterion that changed meanwhile is discarded, and the sanity check runs lazily when `judge_check` is null) and M7 (unique `(agent_id, number)` on prompt versions, latest replay result wins). D2 (pick a saved run for a manual criterion) is still deferred.

### 6.11 The Fix Loop and Promotion

**Generate a second test** (`generate_heldout` job). Allowed when a criterion has a `fix` test and no `heldout` test. Up to 3 attempts: the generator returns 2 to 5 customer messages for a scenario that probes the same criterion but is not the fix test's (the fix test's messages and earlier rejected scenarios are given to it as delimited data, and an identical whole message list is rejected); the scenario is replayed on the agent's current prompt and judged. The first scenario the judge fails is stored as a `heldout` test (`origin = generated`) together with that failing replay as a normal current result, so the test shows as failing at once. If none fails the job succeeds with `created: false` and the UI says "Couldn't write a test your current prompt fails, so it may already handle this case." If the criterion or the current prompt changed meanwhile the job returns `discarded`.

**Suggest fix** (`suggest_fix` job). Only from a `fix` test whose latest result on the current prompt is a failure (a current replay, else a baseline taken on the current version). A held-out test is refused with 409 "Suggest a fix from the fix test.", a test that is not failing with 409 "This test isn't failing on the current prompt.", a second open candidate with 409. The suggester sees the current prompt, the criterion, and the failing result's rationale and transcript, never the held-out test. It returns a complete replacement prompt (at most 4000 characters, not identical to the current one), the index of the failing turn and four short fields (change summary, touched section, expected effect, risk). The stored `why` quotes the failing turn **by code** from the stored transcript, so the model cannot fabricate it. The result is a `proposed` version with `origin = suggested`; the failure message is "Couldn't write a fix. Try again.", or "The suggested prompt was too long. Try again." when the prompt cap was exceeded. The version is stored under the agent row lock only if the current version is still the one the text was written against (`ParentChanged`, job message "This test isn't failing on the current prompt."), so a candidate written against v2 can never be stored with parent v3.

**Approve** (`POST /prompt-versions/{id}/approve`). Under the agent row lock it re-reads the version (must be `proposed`, 409 otherwise), refuses a fix whose parent is no longer the current version (409 "That fix is based on an older prompt. Reject it and suggest a new fix."), refuses when another version is verifying, checks the queue cap, then computes the **required set** once and stores it in `verification`: the fix test, the criterion's held-out test if any, and a **regression set** (every other test whose current non-stale replay on the *current* prompt is `pass`). `before` is read from stored results at approval time, never assumed: for the fix and held-out tests it is the verdict of the test's current non-stale replay on the current version, else the baseline's verdict when the baseline was taken on the current version, else null (the report shows "Not run"); it is `pass` for regressions. So a held-out test that already passes on the current prompt is not credited with a change, and the report cannot drift. Strength is `full` with a held-out test, else `weaker`. Status becomes `approved_verifying` and one `replay` job per required test (payload `system: true`, pointing at the candidate version) is enqueued in the same commit. Chat keeps the current version throughout.

**Promotion rule** (`core/promotion.decide_promotion`, pure code): promote if and only if the fix test passes, the held-out test passes when present, and no regression test has verdict `fail`. A `not_applicable` fix or held-out verdict counts as not passing; a `not_applicable` regression verdict is ignored. Reasons: "The fix test did not pass.", "The held-out test did not pass.", "A previously passing test now fails." Reconcile promotes only while the candidate's parent is still the current version; otherwise it fails the candidate with "The prompt changed during verification." (and the same table) instead of replacing the newer prompt. On promotion the previous current version becomes `superseded`, any other `proposed` version too, and the agent points at the candidate. Other failure reasons: "A test could not be run: {job error}", "A criterion changed during verification.", "The prompt changed during verification.", "Verification timed out." and "Verification could not be completed.".

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

**Error codes:** `400` nothing or too little to check, `401` unknown or expired token, `404` agent, criterion, test or job not found in this workspace, `409` run-save race, no pattern to build from, tests already exist, a prompt edit whose `base_version_id` is no longer the current version ("The prompt changed to v{n} while you were editing. Reload the prompt to continue."), a candidate action in the wrong state (approve or reject on a version that is not `proposed`, restore of a version that cannot be restored, which includes a suggestion that was never approved), "That fix is based on an older prompt. Reject it and suggest a new fix." (approve after the prompt moved on), "A fix is already proposed or being verified." (suggest fix while any candidate is open, also a merely proposed one), "This fix can't be approved in its current state." (a second approval) or "A fix is being verified. Wait for it to finish." (manual edit, restore, approving another version, or a judged-field criterion edit while a fix verifies), `422` invalid input, `429` quota, rate limit, hourly job cap, queue cap or version cap, `502` upstream LLM failure.

---

## 8. Development & Deployment

### Project Structure

```
eval-discovery/
├── backend/
│   ├── main.py               # App init, CORS, routers, startup purge, worker loop in the lifespan
│   ├── api/                  # Routers (workspaces, agents, runs, criteria, prompt_versions, tests, versions), deps, limits, schemas
│   ├── core/                 # Pure logic: config + presets, analyze, pattern, criteria, judge, replay, testsuite, suggest, heldout, report, promotion, prompts
│   ├── db/                   # SQLAlchemy models, session, agent/workspace/event/testsuite helpers, candidates, reconcile
│   ├── jobs/                 # DB-backed queue, handlers (build_tests, replay, suggest_fix, generate_heldout, change_report), worker loop
│   ├── groq_client.py        # Groq API wrapper, model constants
│   └── utils.py              # JSON extraction from LLM output
├── migrations/               # Alembic environment and versions
├── alembic.ini
├── frontend/src/             # App.jsx, api.js, hooks/ (useSession, useTests, useReport, useVersions), components/, lib/
├── tests/                    # pytest suite
├── docs/                     # This document, specs and plans
├── docker-compose.yml        # Local Postgres 16
├── Procfile                  # Start command for Procfile-based hosts (Render ignores it)
├── requirements.txt
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
| `WORKER_ENABLED` | No | `0` disables the in-process job worker loop; anything else (default) runs it. Keep one backend process per deployment (may live in `.env`) |
| `VITE_API_URL` | Frontend build | Backend base URL baked into the frontend; default `http://localhost:8000` |

### Running Tests

```bash
python -m pytest tests -q
cd frontend && npm test && npm run lint && npm run build
```

### Deployment

> **Not yet deployed.** Migration `0002` was applied to a throwaway real Postgres on top of populated Phase 1 rows: `prompt_version.number` backfilled to 1, deleting a workspace cascaded through the new tables, and a downgrade then upgrade round trip worked. `claim_next` was raced by 8 threads over 40 jobs with no duplicates. Migration `0003` and the approval races were also run on real Postgres (see [6.13](#613-verified-and-not-verified-phase-3)). **Not yet verified:** a real deploy on Render, the quality of the real judge, suggester and generator across many agents (every LLM call is mocked in the tests; a single end-to-end run with real Groq on a local Postgres completed the loop), and multi-process behavior (which is not supported, see [6.9](#69-background-jobs)). Verify first on deploy: `alembic upgrade head` succeeds, `POST /workspaces` returns a token, `GET /presets` returns 3 presets, and deleting a workspace with runs works.

- **Backend (Render web service):** add a Postgres instance (check the provider's current free-tier terms first), set `DATABASE_URL`, `ALLOWED_ORIGINS` and `GROQ_API_KEY`, and paste this into the service's Start Command setting in the Render dashboard (Render ignores `Procfile`; it is for Procfile-based hosts):

  ```
  alembic upgrade head && uvicorn backend.main:app --host 0.0.0.0 --port $PORT
  ```

  Migrations run on every start (this is what applies `0002` and `0003` on deploy; deploy the backend before the frontend). The command starts one uvicorn process, which is what the in-process job worker needs: do not add `--workers`. A free-tier spin-down interrupts a running job; it is requeued after 180 s. Do not pass `--proxy-headers --forwarded-allow-ips="*"`: uvicorn would then take the left-most `X-Forwarded-For` entry, which the client controls, and a forged value per request would defeat the per-IP limiter. The app derives the client key itself (see [6.6](#66-limits)): the Nth entry from the right of `X-Forwarded-For`, N being `TRUSTED_PROXY_HOPS` (default 1; 0 ignores the header). Render's real header chain was not observed. After deploying, send `POST /workspaces` 31 times with different forged left-most `X-Forwarded-For` values and expect a 429 by the 21st; if it never comes, raise `TRUSTED_PROXY_HOPS`, and if all users are throttled together, lower it.
- **Frontend (Cloudflare Worker, static assets):** deploy after the backend, then rebuild with `VITE_API_URL=<backend url> npm run build`, then deploy `frontend/dist`.
- No CI/CD pipeline is configured.

---

## 9. Roadmap

Phases 1 and 2 (persistence, saved tests, replay, judging and the manual prompt editor) and Phase 3 (suggested fixes, diff and change report, held-out and regression promotion gate, version history and restore, readiness chip) are implemented. Phase 3 builds on the unmerged Phase 2 branch, so merge Phase 2 first. The design is in [docs/superpowers/specs/2026-11-21-agent-ready-eval-flywheel-design.md](superpowers/specs/2026-11-21-agent-ready-eval-flywheel-design.md) and the Phase 3 addendum [docs/superpowers/specs/2026-11-21-phase3-evolve-design.md](superpowers/specs/2026-11-21-phase3-evolve-design.md). Deferred to Phase 4:

- The CI eval gate and the export endpoint
- The simulated before/after comparison and its results table
- Event-derived metrics
- Picking a saved run for a manual criterion or writing a held-out test by hand (options A and B, D2)
- Editing a candidate by hand (reject it and use the prompt editor)
- Per-agent delete
- Sliding expiry for workspace tokens
- M8: a 401 mid-poll can create an empty workspace
- `passive_deletes` on the new relationships

---

*Updated November 2026 for the persisted, agent-ready architecture with saved tests, replay and judging, and the evaluator-driven fix loop (Phases 1 to 3). For questions, see the project README or open an issue.*
