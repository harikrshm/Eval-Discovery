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
| Persistence | SQLAlchemy 2 + Alembic on Postgres (SQLite for local dev and tests) | Workspaces, agents, runs, criteria, tests and results, jobs, events |
| Background jobs | Database-backed queue, one worker loop inside the web process | Building tests and replaying them, so no LLM chain holds an HTTP request open |
| LLM Services | Groq API (3 models) | Agent simulation and replay, prompt analysis, pattern detection and judging, criteria generation |

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
    System -->|"LLM inference requests<br/>(chat / analyze / pattern / criteria / judge / replay)"| Groq
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
    end

    subgraph Backend["FastAPI Backend (port 8000)"]
        B --> B1["analyze_agent (custom prompt)<br/>or preset analysis"]
        D --> D1["agent prompt + messages"]
        G --> G1["save Run, keyword_prefilter<br/>over the unchecked delta"]
        J --> J1["detect_pattern over the delta"]
        M --> M1["generate_criterion<br/>then enqueue build_tests"]
        Q --> Q1["new prompt version<br/>(chat uses it at once)"]
        R --> R1["enqueue one replay job per test"]
    end

    subgraph Store["Database"]
        B1 --> S1["agent · prompt_version · behavior_map"]
        G1 --> S2["run"]
        J1 --> S3["agent.pattern_summary<br/>agent.last_checked_at_run"]
        M1 --> S4["criterion"]
        M1 --> S5["job"]
        Q1 --> S6["prompt_version"]
        R1 --> S5
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
| Workspace, agents, prompt versions, behavior maps, runs, criteria, tests, results, jobs, events | Database | 30 days from workspace creation |
| In-flight job state in the Tests UI | Browser memory (`useTests`), re-derived from `active_job_id` in `GET /agents/id/tests` after a reload | Not stored |
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
        TestsHook["useTests.js<br/>(Loads the suite · follows jobs by polling GET /jobs/id)"]
        ApiClient["api.js<br/>(Fetch client · adds X-Workspace-Token · creates the workspace on first call)"]
        Lib["lib/workspace.js · draft · patternState<br/>(token storage · chat draft · state derivation)"]

        subgraph Components["UI Components"]
            Setup["AgentSetup.jsx<br/>Preset picker · paste prompt · role label"]
            Header["Header<br/>PatternBadge · Discover evals btn · History + Eval criteria toggles"]
            Chat["ChatInterface.jsx<br/>Message list · Input bar · Typing animation"]
            Eval["EvalCriteriaPanel.jsx<br/>Editable criterion cards · Export · Add · stale-tests banner"]
            PromptEd["PromptEditor.jsx<br/>Edit the prompt, saves a new version"]
            TestsSec["TestsSection.jsx<br/>Per-criterion status · tests · Replay · Build tests"]
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
            TestsRouter["tests.py<br/>GET tests · POST replay · POST run-all · POST build-tests · GET jobs"]
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
            Prompts["prompts.py<br/>(prompt builders · delimiter neutralizing)"]
        end

        subgraph Db["db/ — persistence"]
            DbModels["models.py · session.py"]
            DbHelpers["agents.py · workspaces.py · events.py · testsuite.py"]
        end

        subgraph Jobs["jobs/ — background work, same process"]
            Queue["queue.py<br/>(enqueue · claim_next · requeue_stale)"]
            Handlers["handlers.py<br/>(build_tests · replay)"]
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
        FastAPI --> Worker
        Routers --> ApiSupport
        Routers --> Core
        Routers --> DbHelpers
        Routers --> Queue
        Worker --> Queue
        Worker --> Handlers
        Handlers --> Core
        Handlers --> DbHelpers
        Queue --> DbModels
        DbHelpers --> DbModels
        Core --> Prompts
        Core --> GroqClient
        Core --> Utils
        AgentRouter --> GroqClient
    end

    subgraph GroqAPI["Groq API"]
        Chat20["openai/gpt-oss-20b<br/>(plays the agent under test · replays)"]
        GPT["openai/gpt-oss-120b<br/>(prompt analysis · pattern detection · judge)"]
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
    API->>DB: enqueue one replay job per test (skips tests already queued)
    API-->>Dev: 202 job_ids
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

If the worker dies mid-job, `requeue_stale` puts the job back after 180 s (at most 3 attempts, then it fails with a plain message). A judge or agent failure is recorded on the job and writes no result row.

---

## 4. Data Model

Ten tables (Alembic migrations `migrations/versions/0001_foundation.py` and `0002_tests_and_jobs.py`). Ids are string UUIDs and timestamps are naive UTC so SQLite and Postgres behave alike. Every foreign key is `ON DELETE CASCADE`.

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
        string origin
        string status
        int number
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
| `prompt_version` | The initial version is written when the agent is created. A manual prompt edit (`PUT /agents/{id}/prompt`) adds a row (`origin = "manual"`, `status = "promoted"`; the previous one becomes `superseded`) and points `agent.current_prompt_version_id` at it. `number` is the 1-based per-agent version (the "v2" label); the migration backfilled existing rows to 1. At most 20 versions per agent. |
| `behavior_map` | `analysis` JSON: `user_role_label`, `behaviors` (name, description, trigger terms) and `starter_messages`. `prompt_hash` ties it to the prompt text it was built from. |
| `run` | One saved conversation. `position` is 1-based per agent and unique with `agent_id`, so two concurrent saves cannot take the same slot (the loser gets a 409). `messages` is the transcript JSON. |
| `criterion` | `source` is `discovered` or `manual`; `position` orders them. `judge_check` holds the judge sanity-check outcome (`passed`, `good_verdict`, `bad_verdict`); it is null until tests are built and cleared when the definition or examples are edited. |
| `event` | Product event log, see [6.5](#65-events). |
| `test_case` (model `EvalTest`) | One runnable test for a criterion: `role` is `fix` or `heldout` (unique per criterion), `scripted_user_messages` are the saved run's user messages (first 20). `source_run_id` is a plain id, not a foreign key, so there is no second delete path through `run`. |
| `test_result` (model `EvalResult`) | A judge verdict (`pass`, `fail`, `not_applicable`) with rationale and the transcript judged. `kind` is `baseline` (the original saved run, stored only for runs that became tests) or `replay`; only replays feed status. `prompt_version_id` is a plain id (no FK). `stale` is set when the criterion's judged fields change. |
| `job` | Queue row: `type` (`build_tests` or `replay`), `payload`, `status` (`queued`, `running`, `done`, `failed`), `attempts`, `locked_at`, `result` or a user-safe `error`. Cascades from the workspace. |

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

A minimal product event log records `agent_created`, `run_saved`, `loop_detected`, `criterion_created`, `test_created`, `replay_started`, `replay_passed` and `replay_failed` with small property dicts. Writes go through `try_log_event`, which is best-effort: a failure rolls back, is logged, and never fails the user's request.

### 6.6 Limits

| Limit | Value |
|-------|-------|
| Agents per workspace | 5 (also re-checked inside the create transaction) |
| Runs per workspace | 50 |
| System prompt length | 4000 characters |
| Message length / messages per request | 2000 characters per user message, 16000 per assistant message / 40 (120000 characters total per request). A saved run may hold one more message and one more assistant message of text, so it can include the reply to the last message |
| Criteria per agent | 30 |
| Prompt versions per agent | 20 |
| Background jobs | 30 started per workspace per hour (429 beyond that; discover still returns the criterion, with `job_id: null`) |
| Job queue depth | 100 queued jobs across all workspaces (429 "The test queue is busy" beyond that; run-all is all-or-nothing; discover still returns the criterion, with `job_id: null`) |
| Job staleness / attempts | a `running` job older than 180 s is requeued; at most 3 attempts |
| Replay length | the first 20 user messages of the saved run |
| Runs sent to pattern detection | the most recent 10 unchecked |
| LLM-backed calls | 30 per minute per client, plus a process-wide budget of 600 per minute (`LLM_GLOBAL_PER_MINUTE`). The replay, run-all and build-tests enqueue routes spend one of these per accepted request (a dedupe hit that returns an existing job does not) |
| Groq client | 45 s timeout and 2 retries per call |
| Workspace creation | 20 per minute per client |

The LLM calls a background job makes are NOT counted by the per-client or process-wide limiters above (they happen in the worker, not in a request). They are bounded instead by the hourly per-workspace job cap, the global queue-depth cap and the Groq client timeout, so a single stuck call cannot hold the one worker for more than a few minutes.

Limiters are in-memory sliding windows, per process: they reset on restart and are not shared across instances. The client key is derived from `X-Forwarded-For` by taking the Nth entry from the right, where N is `TRUSTED_PROXY_HOPS` (default 1; 0 uses the direct peer address; a missing or too-short header also falls back to the peer address). Entries to the left of that are client-supplied and ignored.

### 6.7 Model Assignments

| Task | Model | Reasoning |
|------|-------|-----------|
| Conversational chat (plays the agent under test) and test replay | openai/gpt-oss-20b | Low latency for interactive chat; 20B is enough for persona roleplay. Replays use temperature 0 |
| Prompt analysis and pattern detection | openai/gpt-oss-120b | Higher accuracy for classification over a long prompt or several transcripts |
| Judge (verdicts and the sanity check) | openai/gpt-oss-120b, temperature 0 | The larger model for grading against a rubric; temperature 0 for repeatability (not a guarantee) |
| Structured criteria generation | qwen/qwen3.8-27b | Reasoning-capable model for grounded, structured JSON rubrics (reasoning output hidden) |

### 6.8 Judge

`core/judge.py` grades one transcript against one criterion and returns `pass`, `fail` or `not_applicable` with a rationale. The criterion fields and the transcript are wrapped in delimiter tags (`<criterion>`, `<transcripts>`) and neutralized like every other pasted text, and the system text says tagged content is data, never instructions. The judge **never sees the agent's system prompt**, and the judge system text is a fixed string with no agent name or role label (the transcript's speaker labels carry them, as data). Unparseable output, an unknown verdict, an empty rationale or a failed call raises `JudgeFailed`: the job fails with a plain message and no result row is written.

**Sanity check.** When tests are built, the judge must also pass the criterion's own good example and fail its bad example. The outcome is stored in `criterion.judge_check`; if it is wrong either way the UI shows verdicts as "low confidence". This check is weak by construction: the example appears both in the criterion text and as the transcript being graded, so it only catches a judge that cannot read the criterion at all, not one that is subtly miscalibrated.

**Status** (`core/testsuite.criterion_status`, never the LLM): any test whose current replay is `fail` gives `failing`; else any test with no current replay gives `untested`; else any `pass` gives `passing`; else `untested`. "Current" means the newest non-stale `replay` result for the agent's current prompt version.

### 6.9 Background Jobs

`jobs/queue.py` is a plain-function queue over the `job` table. `claim_next` takes the oldest `queued` job with `SELECT ... FOR UPDATE SKIP LOCKED` (a no-op on SQLite), marks it `running`, stamps `locked_at`, counts the attempt and commits before returning. `requeue_stale` runs at the start of every worker pass: a `running` job whose lock is older than 180 s goes back to `queued`, or to `failed` once it has used 3 attempts. `worker_loop` (started in the FastAPI lifespan, stopped on shutdown) runs one job per pass in a thread and sleeps one second when idle.

Handlers do their LLM work first and write rows in one commit at the end, so a failure leaves no partial rows. Provider errors are logged and never copied into the user-facing job error. A criterion, test or prompt version deleted while its job waits fails the job with a clear message; the loop keeps running. Starting the same replay or build twice returns the already-active job instead of queueing a second.

**Operations.**
- Run **one** backend process per deployment (no `uvicorn --workers` above 1, no second instance): a job can legitimately outlive the 180 s window, and a second process would requeue and re-run it. The in-memory rate limiters are per process too.
- A restart or a Render free-tier spin-down interrupts the running job; it is requeued once its lock is 180 s old, up to 3 attempts.
- `WORKER_ENABLED=0` disables the loop. Any other value, or unset, runs it.
- Run `alembic upgrade head` before starting the backend after pulling Phase 2. Without migration `0002` the worker loop logs an error every second. The Render start command already runs it.

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
| `/agents/{id}/prompt` | PUT | Save a manual prompt edit as a new numbered version; returns `{version_id, version_number, text, changed}` (`changed: false` if the text is identical) |
| `/agents/{id}/tests` | GET | The suite: current prompt version, `stale_count`, and per criterion its status, `judge_check`, active build job and tests (baseline and current replay results, active replay job) |
| `/tests/{id}/replay` | POST | Queue a replay of one test on the current prompt version (202, `{job_id}`; returns the active job if one exists) |
| `/agents/{id}/tests/run-all` | POST | Queue a replay for every test that has none active (202, `{job_ids}`) |
| `/criteria/{id}/build-tests` | POST | Queue `build_tests` for a criterion (202, `{job_id}`; 409 if it already has tests) |
| `/jobs/{id}` | GET | Job status: `{id, type, status, result, error}` |

**Error codes:** `400` nothing or too little to check, `401` unknown or expired token, `404` agent, criterion, test or job not found in this workspace, `409` run-save race, no pattern to build from, or tests already exist, `422` invalid input, `429` quota, rate limit or hourly job cap, `502` upstream LLM failure.

---

## 8. Development & Deployment

### Project Structure

```
eval-discovery/
├── backend/
│   ├── main.py               # App init, CORS, routers, startup purge, worker loop in the lifespan
│   ├── api/                  # Routers (workspaces, agents, runs, criteria, prompt_versions, tests), deps, limits, schemas
│   ├── core/                 # Pure logic: config + presets, analyze, pattern, criteria, judge, replay, testsuite, prompts
│   ├── db/                   # SQLAlchemy models, session, agent/workspace/event/testsuite helpers
│   ├── jobs/                 # DB-backed queue, handlers (build_tests, replay), worker loop
│   ├── groq_client.py        # Groq API wrapper, model constants
│   └── utils.py              # JSON extraction from LLM output
├── migrations/               # Alembic environment and versions
├── alembic.ini
├── frontend/src/             # App.jsx, api.js, hooks/useSession.js + useTests.js, components/, lib/
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
alembic upgrade head       # SQLite ./dev.db unless DATABASE_URL is exported in this shell; run before the backend after pulling Phase 2
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

> **Not yet deployed.** Migration `0002` was applied to a throwaway real Postgres on top of populated Phase 1 rows: `prompt_version.number` backfilled to 1, deleting a workspace cascaded through the new tables, and a downgrade then upgrade round trip worked. `claim_next` was raced by 8 threads over 40 jobs with no duplicates. **Not yet verified:** a real deploy on Render, real Groq judge behavior at temperature 0 (every LLM call is mocked in the tests), and multi-process behavior (which is not supported, see [6.9](#69-background-jobs)). Verify first on deploy: `alembic upgrade head` succeeds, `POST /workspaces` returns a token, `GET /presets` returns 3 presets, and deleting a workspace with runs works.

- **Backend (Render web service):** add a Postgres instance (check the provider's current free-tier terms first), set `DATABASE_URL`, `ALLOWED_ORIGINS` and `GROQ_API_KEY`, and paste this into the service's Start Command setting in the Render dashboard (Render ignores `Procfile`; it is for Procfile-based hosts):

  ```
  alembic upgrade head && uvicorn backend.main:app --host 0.0.0.0 --port $PORT
  ```

  Migrations run on every start (this is what applies `0002` on deploy). The command starts one uvicorn process, which is what the in-process job worker needs: do not add `--workers`. A free-tier spin-down interrupts a running job; it is requeued after 180 s. Do not pass `--proxy-headers --forwarded-allow-ips="*"`: uvicorn would then take the left-most `X-Forwarded-For` entry, which the client controls, and a forged value per request would defeat the per-IP limiter. The app derives the client key itself (see [6.6](#66-limits)): the Nth entry from the right of `X-Forwarded-For`, N being `TRUSTED_PROXY_HOPS` (default 1; 0 ignores the header). Render's real header chain was not observed. After deploying, send `POST /workspaces` 31 times with different forged left-most `X-Forwarded-For` values and expect a 429 by the 21st; if it never comes, raise `TRUSTED_PROXY_HOPS`, and if all users are throttled together, lower it.
- **Frontend (Cloudflare Worker, static assets):** rebuild with `VITE_API_URL=<backend url> npm run build`, then deploy `frontend/dist`.
- No CI/CD pipeline is configured.

---

## 9. Roadmap

Phases 1 and 2 (persistence, saved tests, replay, judging and the manual prompt editor) are implemented. The design is in [docs/superpowers/specs/2026-11-21-agent-ready-eval-flywheel-design.md](superpowers/specs/2026-11-21-agent-ready-eval-flywheel-design.md). Deferred to later phases:

- Suggested prompt fixes, and the diff and change report
- The held-out/regression promotion gate
- Prompt version history and restore
- The aggregate readiness chip
- Picking a specific saved run for a manual criterion (D2)
- The export endpoint and CI gate
- Event-derived metrics and the simulation script
- Per-agent delete
- Sliding expiry for workspace tokens

---

*Updated November 2026 for the persisted, agent-ready architecture with saved tests, replay and judging (Phases 1 and 2). For questions, see the project README or open an issue.*
