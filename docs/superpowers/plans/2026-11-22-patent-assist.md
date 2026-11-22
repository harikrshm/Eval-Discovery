# Patent Assist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Chrome extension whose overlay assistant answers questions about the current Google Patents US patent page, grounded in a page-extracted brief and a small law pack, records Google Patents adoption signals, and improves through the existing flywheel (system-prompt versions behind a human gate), with an Assist console (Adoption and Evolution) for the builder.

**Architecture:** New pure core modules under `backend/core/assist/` (brief, intent, assembly, law pack, citation check, adoption metrics) feed two new API routers (`assist` for the extension, key-authenticated; `assist_console` for the builder, workspace-token-authenticated). The assistant is one agent of a new kind in a persistent demo workspace; flagged conversations become flywheel runs carrying a frozen brief snapshot; tests and replay reuse the same assembly function. A new `extension/` package (Manifest V3, Vite, vanilla JS, Shadow DOM widget) extracts the brief and talks to the backend through its service worker.

**Tech Stack:** Python (FastAPI, SQLAlchemy, Alembic, Groq), React 19 + Vite + Vitest (console), Manifest V3 extension (Vite build, Vitest + jsdom tests).

**Spec:** `docs/superpowers/specs/2026-11-22-patent-assist-design.md` (binding; wins where this plan differs). Builds on `docs/ARCHITECTURE.md`. Branch `feat/patent-assist`.

## Global Constraints

- The evolving artifact is the **system prompt only**. The context policy and law pack are fixed in code or data files and never edited by the flywheel.
- The assistant supports **US patents only**; any other number gets a fixed "US patents only" answer without a model call.
- Page text (claims, description, anything from the brief) is **untrusted data**: it only ever appears inside delimiter tags in the user turn, neutralised with the same helper the app uses (`backend.core.prompts._neutralize` style); the system prompt is fixed text with no page data.
- The extension **acts only on `https://patents.google.com/patent/*` routes**; on any other route it reads, records and sends nothing. No search queries, no cross-page identity (a random id per page view, never persisted across pages), no scroll or copy counts, no widget open/click-through metrics.
- The assist key is read from the backend environment variable `ASSIST_KEY` and compared in constant time; it grants chat and signal writes only. The assistant agent is `ASSIST_AGENT_ID`. Neither value is ever logged or returned.
- The legal-opinion rule: validity, infringement and freedom-to-operate questions are answered with what the claims say plus a fixed not-legal-advice line, never a conclusion. Enforced by the system prompt, the intent route and a test.
- Imported (seeded) data carries `source = "imported"`; the console labels it "demo dataset" and **never** presents imported data as measured improvement. Replay before/after (from tests) is the headline evidence; signal before/after uses non-imported records only.
- Existing agents (`kind = "general"`) behave exactly as before: no new gates, same limits, same tests. The assistant kind skips the keyword prefilter and accepts 2-turn conversations.
- No real network in any test (fake `backend.groq_client.client`; the extension tests use saved pages). Tests set `GROQ_SKIP_DOTENV=1`. Never read or commit the repo-root `.env`; never push or deploy.
- Frontend lint stays at zero problems; frontend tests run from PowerShell (`cd frontend; npx vitest run <file>`); extension tests from `extension/` the same way; backend `python -m pytest <file> -q` from the repo root. Commit messages follow `git log` style.
- Groq free tier is about 8000 tokens per minute on the 120B model: assembled prompts are bounded by per-intent character caps (Task 2) and the judge's brief by a fixed cap (Task 12).

## Review Focus

1. **Extractor wrong or brittle on real pages:** missing sections, wrong dependent/independent claim tree, a site that navigates without reloading (new patent page must start a new session), and any capture or request on non-patent routes (Tasks 7, 8, 9).
2. **Privacy and data capture:** nothing captured outside patent pages, no search queries, no persistent id, the assist key unable to read anything, heartbeats containing only the allowed fields (Tasks 5, 6, 8, 9).
3. **Ungrounded or unsafe answers:** page text injecting instructions, a validity or infringement conclusion, non-US numbers, unverified citations presented as verified, an oversized brief breaking the token limit (Tasks 2, 3, 5).
4. **Dishonest numbers:** imported data shown as measured effect, division by zero in metrics, wrong bounce or viewed thresholds, before/after on mixed sources (Tasks 13, 14, 16).
5. **Flywheel regression:** existing agents changed, migrations on populated data, the assistant kind's gates, evidence and judge changes breaking existing tests, run-cap and expiry behaviour for the demo workspace (Tasks 4, 11, 12).

## Decisions made in this plan (my calls beyond the design)

- D1. Dedicated tables `assist_page_view` (one row per session, upserted by heartbeats) and `assist_question` (one row per question) instead of the generic event table: the metrics need per-section and per-intent queries. Both carry `source` and `assist_version`.
- D2. Flagged conversations (thumbs-down, "didn't answer", re-ask, failed citation check) are stored as `run` rows with new columns `source` (`manual|assist|imported`), `session_id`, `brief_snapshot`; one run per session, upserted.
- D3. The extension's service worker calls the backend with `host_permissions`, so no CORS change is needed; the backend still rate-limits `/assist/*` per session id and per IP.
- D4. `ASSIST_MODEL` is a constant defaulting to `MODEL_CHAT`; switching to the 120B model is a one-line change to be judged by the author on real answers.
- D5. The law pack is drafted from the public sources it cites and carries `status: draft` in each file's front matter until the author reviews it; the assistant never claims more than "summary of"; the author reviews all provisions before the demo.
- D6. Console access: the author pastes the demo workspace token into a "Connect" box (kept in browser storage like the app's existing token); the Assist area renders only when connected.
- D7. Fixtures are real patent pages **saved by the author from their own browser** (File > Save page as > Webpage, Complete) into `extension/fixtures/raw/`, trimmed by a script; the controller asks for them before Task 7. Fallback: at most five pages fetched once with `curl`.

## File Structure

```
backend/core/assist/{__init__,brief,intent,assemble,lawpack,citations,evidence,adoption}.py     NEW (pure)
backend/core/assist/lawpack/*.md, backend/core/assist/system_prompt.md                          NEW (data)
backend/api/{assist,assist_console}.py, backend/api/{schemas,main wiring}                        NEW / modified
backend/db/{models,assist}.py, migrations/versions/0004_assist.py                                modified / NEW
backend/core/{pattern,criteria,suggest,judge,prompts}.py, backend/api/{runs,tests}.py,
  backend/jobs/handlers.py, backend/db/{testsuite,agents}.py                                     modified (evidence, kind rules, snapshots)
scripts/{setup_assist,import_page_views}.py, examples/assist/page_views.example.jsonl             NEW
extension/{package.json,vite.config.js,manifest.json}, extension/src/{extractor,content,background,
  session,signals,api}.js, extension/src/widget/*, extension/fixtures/**, extension/CHECKLIST.md   NEW
frontend/src/{api.js,App.jsx}, frontend/src/components/assist/*                                  modified / NEW
tests/{test_assist_*,test_import_page_views,test_flywheel_assist,test_migrations}.py             NEW / modified
README.md, docs/ARCHITECTURE.md, docs/DEMO-ASSIST.md                                             modified / NEW
```

---

### Task 1: Brief schema and claim tree (pure core)

**Files:** Create `backend/core/assist/__init__.py`, `backend/core/assist/brief.py`; test `tests/test_assist_brief.py`.

**Interfaces — Produces:**
- `backend/core/assist/__init__.py` defines `ASSIST_MODEL = MODEL_CHAT` (imported from `backend.groq_client`); every later task imports it from here (decision D4: switching models is a one-line change).
- `Brief` (pydantic v2 model, `extra="forbid"`): `patent_number: str`, `title: str | None`, `abstract: str | None`, `status: str | None`, `expiry: str | None`, `dates: dict[str, str]` (priority, filing, publication, grant), `inventors: list[str]`, `assignees: list[str]`, `classifications: list[ClassificationItem(code, title, path: list[str])]`, `landscapes: list[str]`, `claims: list[Claim(number: int, text: str, depends_on: list[int], independent: bool)]`, `description: list[DescriptionSection(heading, text)]`, `citations: Citations(patent: list[CitationRow], cited_by: list[CitationRow])` with `CitationRow(number, date, assignee, title)` all optional strings, `family: list[FamilyRow(number, country, title, priority_date, status)]`, `legal_events: list[LegalEvent(date, text)]`, `completeness: dict[str, bool]`.
- Caps enforced by validators (constants `MAX_CLAIMS=200`, `MAX_CLAIM_CHARS=8000`, `MAX_DESCRIPTION_SECTIONS=40`, `MAX_SECTION_CHARS=20000`, `MAX_LIST_ITEMS=300`, `MAX_BRIEF_JSON_CHARS=600_000`): over-cap input is rejected with a field-naming `ValueError` (never silently truncated).
- `build_claim_tree(claims: list[tuple[int, str]]) -> list[Claim]`: sets `depends_on` by finding "claim N" / "claims N-M" / "any of claims N, M" references (referenced numbers must be lower than the claim's own), `independent = not depends_on`.
- `is_us_patent(patent_number: str) -> bool` (accepts `US` prefix, granted or application forms such as `US10000000B2`, `US20160266243A1`; rejects others).

- [ ] **Step 1: Write failing tests:** a valid brief round-trips through `model_dump`/`model_validate`; unknown keys, caps exceeded and a claim number below 1 raise `ValueError` naming the field; `build_claim_tree` on real-shape texts: "A method comprising…" independent; "The method of claim 1, wherein…" depends_on `[1]`; "The system of claim 3 or 4" `[3, 4]`; "…of any one of claims 1-3" `[1, 2, 3]`; a forward reference ("claim 5" inside claim 2) ignored; `is_us_patent` true for `US10000000B2`, `US20160266243A1`, `us7629993b2`, false for `EP1234567A1`, `WO2020123456A1`, `CN123456A`, `""`.
- [ ] **Step 2: Run to confirm fail:** `python -m pytest tests/test_assist_brief.py -q` -> FAIL.
- [ ] **Step 3: Implement** the models, caps and both functions (pure, no I/O).
- [ ] **Step 4: Run tests** -> PASS; full suite green.
- [ ] **Step 5: Commit:** `feat(assist): brief schema, claim tree and US-number check`

### Task 2: Intent rules and prompt assembly (pure core)

**Files:** Create `backend/core/assist/intent.py`, `backend/core/assist/assemble.py`; test `tests/test_assist_assemble.py`.

**Interfaces — Produces:**
- `Intent` (str enum): `claim_explain, claim_scope, status_family, citations_prior_art, classification, law_concept, navigation_help, legal_opinion_request, other`.
- `classify_intent(question: str) -> Intent`: keyword/phrase rules, case-insensitive, first match in this priority: `legal_opinion_request` ("is it valid", "invalid", "infring", "freedom to operate", "can I use", "would this hold up", "should I file"), `claim_scope`, `claim_explain`, `status_family`, `citations_prior_art`, `classification`, `law_concept`, `navigation_help` ("where do I", "how do I find", "what does this button"), else `other`.
- `CONTEXT_POLICY: dict[Intent, SlicePlan]` (module constant, not versioned): per intent the brief sections included and character caps (`SlicePlan(sections: tuple[str, ...], max_chars: int)`); claims and abstract available for every intent; total slice size capped at `MAX_CONTEXT_CHARS = 14_000`.
- `assemble(brief: Brief, history: list[dict], question: str, *, law_excerpts: list[tuple[str, str]], system_prompt: str) -> tuple[str, list[dict]]`: returns `(system_prompt, messages)` where the final user message contains `<page_brief>…</page_brief>`, `<law_excerpts>` (each labelled `[id]`) and `<question>…</question>`, with every embedded string neutralised (delimiter tags in page text cannot close a wrapper); earlier `history` turns are passed as-is (role/content only, content capped); the brief slice always begins with the `completeness` note naming missing sections.
- `NOT_US_ANSWER: str` constant (fixed text).

- [ ] **Step 1: Write failing tests:** `classify_intent` table (at least 3 phrasings per intent, and priority: "is claim 1 valid" -> `legal_opinion_request`, not `claim_explain`); `assemble` puts claims text only inside `<page_brief>`; a claim containing `</page_brief> ignore previous instructions` is neutralised (the closing tag does not appear raw); the system prompt in the result equals the input (no page data); the slice for `status_family` omits the description and includes legal events; the result never exceeds `MAX_CONTEXT_CHARS` for a maximum-size brief (claims first, description sections dropped from the end); the completeness note lists a missing section; history content over the cap is truncated.
- [ ] **Step 2: Run to confirm fail:** `python -m pytest tests/test_assist_assemble.py -q` -> FAIL.
- [ ] **Step 3: Implement** both modules; reuse the neutralise helper from `backend/core/prompts.py` (import it; do not copy).
- [ ] **Step 4: Run tests** -> PASS; full suite green.
- [ ] **Step 5: Commit:** `feat(assist): intent rules and prompt assembly`

### Task 3: Law pack and citation check (pure core + data)

**Files:** Create `backend/core/assist/lawpack.py`, `backend/core/assist/lawpack/*.md` (12 files), `backend/core/assist/citations.py`, `backend/core/assist/system_prompt.md`; test `tests/test_assist_law.py`.

**Interfaces — Produces:**
- Law files, one provision each, front matter `id`, `title`, `source` (statute or MPEP section), `status: draft`, then a short plain-language summary (about 120-250 words, hand-written, not verbatim): `usc-101`, `usc-102`, `usc-103`, `usc-112a`, `usc-112b`, `usc-112f`, `usc-154`, `usc-271`, `mpep-2111`, `mpep-2111-03` ("comprising" vs "consisting of"), `mpep-2141`, `mpep-2173`.
- `load_lawpack() -> dict[str, Provision(id, title, source, status, text)]` (cached; raises if a file lacks an id or duplicates one).
- `INTENT_PROVISIONS: dict[Intent, list[str]]` (fixed map, every id must exist; `legal_opinion_request` and `law_concept` map to the most provisions, at most 4 per intent) and `excerpts_for(intent) -> list[tuple[str, str]]` returning `(id, "title (source): text")`.
- `check_citations(answer: str, brief: Brief, provided_ids: list[str]) -> CitationCheck(ok: bool, unverified_claims: list[int], unverified_law: list[str])`: claim references in the answer ("claim 5", "claims 2-4") must exist in `brief.claims`; law references in the form `[usc-103]` must be in `provided_ids`.
- `system_prompt.md`: the initial assistant system prompt: role (plain-language guide to the open US patent page), always quote the page and cite claim numbers, cite law as `[id]` only from provided excerpts, say when a section was not found, for validity/infringement/FTO questions explain what the claims say and add the fixed line "This is not legal advice, and I can't judge validity or infringement." Loaded by `load_system_prompt()`.

- [ ] **Step 1: Write failing tests:** every law file loads, ids unique and match `INTENT_PROVISIONS` targets, no intent maps to more than 4, each text under 1,500 characters and `status == "draft"`; `check_citations`: existing claim ok, "claim 99" flagged, "claims 2-4" with claim 4 missing flags 4, `[usc-103]` provided ok, `[usc-101]` not provided flagged, an answer with no citations is `ok`; `load_system_prompt()` contains the not-legal-advice sentence and no page-data placeholders.
- [ ] **Step 2: Run to confirm fail:** `python -m pytest tests/test_assist_law.py -q` -> FAIL.
- [ ] **Step 3: Implement.** Draft the provision summaries yourself from your knowledge of the statutes and the MPEP sections; keep them conservative and factual, name the source in each file, and say in your report that they need the author's review (decision D5).
- [ ] **Step 4: Run tests** -> PASS; full suite green.
- [ ] **Step 5: Commit:** `feat(assist): law pack, citation check and the assistant system prompt`

### Task 4: Data model, migration and setup script

**Files:** Modify `backend/db/models.py`; create `backend/db/assist.py`, `migrations/versions/0004_assist.py`, `scripts/setup_assist.py`; test `tests/test_assist_db.py`, `tests/test_migrations.py` (extend), `tests/pg/test_migrations_pg.py` (extend).

**Interfaces — Produces:**
- Columns: `agent.kind` (str, default `"general"`), `run.source` (str, default `"manual"`), `run.session_id` (str, null), `run.brief_snapshot` (JSON, null), `test_case.brief_snapshot` (JSON, null); a plain unique constraint on `run (agent_id, session_id)` (NULLs are distinct on both SQLite and Postgres, so manual runs are unaffected).
- Tables: `assist_page_view(session_id PK str, workspace_id FK, patent_number str, started_at, updated_at, dwell_ms int, sections JSON, bounce bool, source str, assist_version int null)` and `assist_question(id PK, session_id, workspace_id FK, patent_number, text, intent, section_focus str null, answer, rating str null, rating_reason str null, reasked bool, citation_ok bool, source str, assist_version int, created_at)`. Deleting a workspace cascades to both (follow the existing cascade pattern).
- `backend/db/assist.py`: `get_assist_agent(db) -> tuple[Agent, Workspace]` (reads `ASSIST_AGENT_ID`; `LookupError` if missing), `upsert_page_view(db, workspace_id, session_id, patent_number, dwell_ms, sections, bounce, assist_version, source)`, `add_question(db, …) -> AssistQuestion`, `set_rating(db, question_id, rating, reason)`, `mark_reasked(db, question_id)`, `save_flagged_run(db, agent, session_id, messages, brief, source) -> Run` (one run per `(agent, session_id)`, upserted; `prompt_version_id` = the agent's current version; position = next).
- `scripts/setup_assist.py`: creates the demo workspace with `expires_at` far in the future (10 years), an agent `kind="assist"` named "Patent Assist" with `load_system_prompt()` as its initial promoted prompt version (use the existing `create_agent` with a fixed `AgentAnalysis` so no model call is made), then prints the workspace token (once; never stored), the agent id and the line to put in `.env`. Idempotent guard: refuses to run if an assist agent already exists unless `--force`.

- [ ] **Step 1: Write failing tests:** new columns default correctly and existing rows keep `kind="general"`/`source="manual"` after upgrading a populated 0003 database (extend the existing migration test); the unique `(agent_id, session_id)` rejects a duplicate but allows several null `session_id`s; `save_flagged_run` upserts (second call updates messages, same row); `upsert_page_view` updates by session id; workspace deletion cascades; setup script run against a temp SQLite DB creates exactly one assist agent with a far-future expiry and refuses a second run without `--force`; the PG migration test (skipped without `TEST_DATABASE_URL`) upgrades a populated 0003 database to head and round-trips down/up.
- [ ] **Step 2: Run to confirm fail:** `python -m pytest tests/test_assist_db.py tests/test_migrations.py -q` -> FAIL.
- [ ] **Step 3: Implement.** The controller runs the PG migration test on the local Docker Postgres afterwards.
- [ ] **Step 4: Run tests** -> PASS; full suite green.
- [ ] **Step 5: Commit:** `feat(assist): assistant agent kind, signal tables, migration and setup script`

### Task 5: Assist chat API

**Files:** Create `backend/api/assist.py`; modify `backend/api/schemas.py`, `backend/main.py` (router), `.env.example` (`ASSIST_KEY`, `ASSIST_AGENT_ID`, commented); test `tests/test_api_assist_chat.py`.

**Interfaces — Produces:**
- Dependency `require_assist_key` reading header `X-Assist-Key` against env `ASSIST_KEY` with `hmac.compare_digest`; missing env var -> every call 503 "Assist is not configured." (never accept an empty key); wrong/absent key -> 401.
- `POST /assist/chat` body `{session_id: str, brief: Brief, history: list[{role, content}], question: str}` -> `{answer: str, question_id: str, intent: str, assist_version: int, citation_check: {ok, unverified_claims, unverified_law}}`. Flow: size limits (question <= 2,000 chars, history <= 20 turns); `is_us_patent(brief.patent_number)` else `NOT_US_ANSWER` with intent `other` and no model call; `classify_intent`; law excerpts via `excerpts_for`; `assemble` with the assist agent's current prompt text; `groq_chat(system, messages, model=ASSIST_MODEL, max_tokens=CHAT_MAX_TOKENS)` inside `request_retries(2)`; `check_citations`; if not ok append the fixed note "Some citations in this answer could not be verified against the page."; store an `assist_question` row (intent, section focus derived from the intent's primary section, `source="live"`, `assist_version` = the current prompt version number); return. A failed model call -> 502 with a fixed message. A failed citation check also upserts a flagged run (Task 6 helper) with reason `citation`.
- Rate limit: per session id (30 questions per hour) and the existing per-IP LLM limiter; limits reuse the app's limiter classes.
- The model is `ASSIST_MODEL` imported from `backend.core.assist`.

- [ ] **Step 1: Write failing tests** (fake `backend.groq_client.client` wholesale; build the briefs from a small hand-written valid `Brief`): 401 without or with a wrong key; 503 when `ASSIST_KEY` is unset (and an empty `ASSIST_KEY` is treated as unset); happy path returns the answer and stores one `assist_question`; the recorded model call used `ASSIST_MODEL`, the assist agent's current system prompt unchanged, and the page text only inside the user turn's `<page_brief>`; a claim containing a fake closing tag does not break out (assert on the captured messages); non-US number -> `NOT_US_ANSWER`, no model call, no row bound to a model reply; a `legal_opinion_request` question gets law excerpts and the captured prompt contains them; an answer citing "claim 99" gets the note and `citation_check.ok == false` and creates a flagged run; model failure -> 502 fixed text with no provider text; oversized question/history -> 422; the 31st question in a session -> 429; the response never contains the key.
- [ ] **Step 2: Run to confirm fail:** `python -m pytest tests/test_api_assist_chat.py -q` -> FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run tests** -> PASS; full suite green.
- [ ] **Step 5: Commit:** `feat(assist): key-authenticated chat endpoint`

### Task 6: Signals API and flagged conversations

**Files:** Modify `backend/api/assist.py`, `backend/api/schemas.py`; test `tests/test_api_assist_signals.py`.

**Interfaces — Produces** (all require `X-Assist-Key`):
- `POST /assist/page-views` body `{session_id, patent_number, dwell_ms, sections: {name: visible_ms}, bounce: bool}` -> 204; upserts by `session_id`; `dwell_ms` and each `visible_ms` must be non-negative; the stored values never decrease (a lower incoming value is ignored and the call still returns 204); only the seven section names `claims, description, classifications, citations, family, legal_events, landscapes` are accepted (unknown names rejected, 422); `source="live"`; only US patent numbers accepted.
- `POST /assist/ratings` body `{question_id, rating: "up"|"down"|"no_answer", reason?: str <= 500, conversation: list[{role, content}], brief: Brief}` -> 204; sets the rating; `down` and `no_answer` flag the conversation via `save_flagged_run(..., source="assist")`.
- `POST /assist/reasks` body `{question_id, conversation, brief}` -> 204; marks the earlier question as re-asked and flags the conversation.
- The widget decides what a re-ask is (Task 10); the backend only records it. No endpoint reads data.

- [ ] **Step 1: Write failing tests:** 401 without key; heartbeat creates then updates one row, a lower `dwell_ms` or `visible_ms` is ignored (still 204, stored value unchanged), an unknown section name is 422, a non-US number is 422; a `down` rating stores the rating and creates exactly one flagged run with the brief snapshot, a second `down` for the same session updates the same run (one run per session); `up` creates no run; a re-ask marks the question and flags; rating a question id that does not exist is 404 with no run; oversized reason or conversation -> 422; the flagged run's `source == "assist"` and `session_id` set.
- [ ] **Step 2: Run to confirm fail:** `python -m pytest tests/test_api_assist_signals.py -q` -> FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run tests** -> PASS; full suite green.
- [ ] **Step 5: Commit:** `feat(assist): page-view, rating and re-ask signals`

### Task 7: Extension scaffold and extractor

**Files:** Create `extension/package.json`, `extension/vite.config.js`, `extension/manifest.json`, `extension/src/extractor.js`, `extension/fixtures/raw/` (author-supplied pages, D7), `extension/scripts/trim-fixture.js`, `extension/fixtures/*.html` (trimmed), tests `extension/src/extractor.test.js`.

**Interfaces — Produces:**
- Package: Vite builds `dist/` with `content.js`, `background.js` and a copy of `manifest.json`; scripts `build`, `test` (vitest, jsdom), `lint`. Manifest V3: `matches` `https://patents.google.com/*` for the content script, `host_permissions` = that origin plus the backend origin placeholder `__BACKEND_ORIGIN__` replaced at build time from env `VITE_BACKEND_ORIGIN`, a service-worker background, **no** other permissions.
- `extractBrief(doc: Document, url: string) -> Brief-shaped object`: every field optional and tolerant (never throws on a missing section); `completeness` marks each of `bibliographic, abstract, claims, description, classifications, landscapes, citations, family, legal_events` true/false; claims returned with numbers and a dependency tree computed by the same rules as `build_claim_tree` (Task 1; port the rule set and test the two against the same cases); US check mirrored from `is_us_patent`; grant date derived from a legal event "Application granted" when no labelled field exists.
- First sub-step (Step 0): the controller supplies saved pages (D7); the implementer inspects their real structure (itemprop/ids/section headings) and records the selectors found, and anything not found, in the report.

- [ ] **Step 1: Write failing tests** against at least three trimmed real fixtures (a recent grant, an older grant, a published application): number, title, abstract, ≥ 1 independent and ≥ 1 dependent claim with correct `depends_on`, classifications with codes and titles, landscapes list, dates and status, citations counts match what the page shows, legal events include an "Application granted"-style event for a granted patent; a fixture with a section removed yields `completeness[section] == false` and no exception; garbage HTML yields a mostly-empty brief with `completeness` all false; the same cases as Task 1's claim-tree tests give identical trees; the produced object validates against the backend `Brief` caps (add a Python test `tests/test_assist_fixtures.py` that loads an exported JSON brief from each fixture and validates it with `Brief`).
- [ ] **Step 2: Run to confirm fail:** `cd extension; npx vitest run` -> FAIL.
- [ ] **Step 3: Implement** the scaffold and `extractBrief`; write `trim-fixture.js` to strip scripts/styles/images/base64 and keep the sections the extractor reads.
- [ ] **Step 4: Run tests** -> PASS; `npm run build` produces `dist/`; backend suite green.
- [ ] **Step 5: Commit:** `feat(extension): scaffold and brief extractor with fixtures`

### Task 8: Widget, session and chat wiring

**Files:** Create `extension/src/content.js`, `extension/src/session.js`, `extension/src/api.js`, `extension/src/background.js`, `extension/src/widget/{Widget.js,styles.css,chips.js}`; tests `extension/src/{session,widget,background}.test.js`.

**Interfaces — Produces:**
- `session.js`: `isPatentRoute(url) -> boolean` (`/patent/…` on `patents.google.com` only), `startSessionController(win, onNewSession)` watching history API calls, `popstate` and DOM mutations to detect a new patent page in a page that does not reload; each new route creates a fresh `session_id` (`crypto.randomUUID()`), a new empty history and a re-extracted brief; on a non-patent route it tears the widget down and records nothing.
- `background.js`: receives `{type: "chat" | "rating" | "reask" | "page_view", payload}` messages from the content script, calls the backend with `X-Assist-Key` (key from the build env `VITE_ASSIST_KEY`, never sent to the page), returns the JSON or a fixed error. The content script never calls the backend directly.
- Widget: Shadow DOM overlay (collapsed button; panel with message list, input, chips "Explain claim 1", "What do these CPC codes mean?", "Is this still in force?", thumbs up/down, "Didn't answer my question" button); the user's text selection is inserted into the question as `Explain this passage: "<selection>"` (selection capped at 800 chars); a non-US patent shows the fixed notice without calling the backend; errors show a fixed retry message.
- Re-ask rule (used with the Task 9 signals): when the answer to a new question arrives within 90 seconds of the previous answer and its returned `intent` equals the previous question's returned `intent`, the widget sends `reask` for the **previous** question; a "Didn't answer my question" click sends the `no_answer` rating (Task 6).

- [ ] **Step 1: Write failing tests:** `isPatentRoute` table; the controller starts one session per route change and none on a search route (assert no messages were sent from a search route); a mocked background receives `chat` with the brief, history (this session only) and question; the history resets on a new patent page; selection text becomes the quoted question; chips send their fixed question text; a non-US brief shows the notice and sends no message; the re-ask rule fires for a same-intent answer within 90 seconds and not for a different intent or a later one; the widget DOM lives in a shadow root and does not leak styles (no global `<style>` added); the page script never reads the key (the key string is absent from `content.js` output: a build test greps `dist/content.js`).
- [ ] **Step 2: Run to confirm fail:** `cd extension; npx vitest run` -> FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run tests** -> PASS; build succeeds; the grep for the key in `dist/content.js` finds nothing.
- [ ] **Step 5: Commit:** `feat(extension): shadow-DOM widget, session handling and chat wiring`

### Task 9: Page-view tracker and rating signals

**Files:** Create `extension/src/signals.js`; modify `extension/src/content.js`, widget; tests `extension/src/signals.test.js`.

**Interfaces — Produces:**
- `createTracker(doc, {sectionSelectors, now, sendHeartbeat, intervalMs = 15000})`: `IntersectionObserver` per section element (names exactly `claims, description, classifications, citations, family, legal_events, landscapes`); accumulates `visible_ms` per section while at least 40% visible and the tab is visible; `dwell_ms` counted while the page is visible; every `intervalMs` and on `pagehide`/`visibilitychange` calls `sendHeartbeat({dwell_ms, sections, bounce})`; `bounce = dwell_ms < 10000 && no section has visible_ms >= 3000`; stops and sends nothing after `destroy()` (called on route change or non-patent route). It captures nothing else (no scroll, no copies, no selection counts, no URLs beyond the patent number in the payload).
- Heartbeats are monotonic (the backend ignores lower values): the tracker keeps running totals per session.

- [ ] **Step 1: Write failing tests** (fake observer, fake clock): visible_ms accumulates only while intersecting and the document is visible; a section visible 3 s counts as viewed in `bounce` logic; heartbeat payload keys are exactly `session_id, patent_number, dwell_ms, sections, bounce` (assert equality, so extra data fails); `destroy()` stops timers and a later route change creates a new tracker with zeros; nothing is sent before the first interval except on pagehide; a hidden tab does not accumulate.
- [ ] **Step 2: Run to confirm fail:** `cd extension; npx vitest run src/signals.test.js` -> FAIL.
- [ ] **Step 3: Implement** and wire it into `content.js` and the widget's rating/re-ask buttons.
- [ ] **Step 4: Run tests** -> PASS; build succeeds.
- [ ] **Step 5: Commit:** `feat(extension): page-view tracker and rating signals`

### Task 10: Manual verification checklist (extension on a real page)

**Files:** Create `extension/CHECKLIST.md`.

- [ ] **Step 1: Write the checklist:** load unpacked from `extension/dist`; backend running with `ASSIST_KEY`/`ASSIST_AGENT_ID`; on a real US patent page: widget appears, a claim question returns a grounded answer, a selection becomes a quoted question, chips work, thumbs-down flags a conversation (visible via the console or the database), navigating to another patent without a reload starts a fresh chat, a search page shows no widget and sends nothing (check the network panel), a non-US patent shows the notice, the heartbeat payload in the network panel contains only the allowed keys. Each item has an expected result and a "result" column left blank for the author. State that this checklist was **not run** by the build agents.
- [ ] **Step 2: Commit:** `docs(extension): manual verification checklist`

### Task 11: Flywheel rules for the assistant kind (evidence, gates, prefilter)

**Files:** Create `backend/core/assist/evidence.py`; modify `backend/core/pattern.py`, `backend/core/criteria.py`, `backend/core/suggest.py`, `backend/core/prompts.py`, `backend/api/runs.py`, `backend/api/criteria.py`, `backend/jobs/handlers.py` (only the call sites); test `tests/test_flywheel_assist.py`.

**Interfaces — Produces:**
- `evidence_summary(run_messages, brief_snapshot, meta: dict) -> str` (intent, section focus, which brief sections were in the context slice, the completeness note, citation-check result, signal reasons; no brief text; at most 1,500 chars).
- Optional keyword-only `evidence: list[str] | None = None` on `detect_pattern`, `generate_criterion` and `suggest_fix`: when given, the summaries are added to their **user** messages inside `<evidence>` tags (neutralised); existing calls without it are byte-identical. `detect_pattern(..., min_turns: int = MIN_TURNS_PER_TRANSCRIPT)`.
- In `backend/api/runs.py`: for an assist-kind agent, `check_due` skips `keyword_prefilter` (due when at least `CHECK_DUE_MIN_NEW_RUNS` new flagged runs exist) and `check-pattern` passes `min_turns=2` and the evidence summaries; criterion discovery and suggest-fix routes pass evidence for assist-kind agents. The suggester's JSON may carry an optional `law_pack_gap` string (shown in the change report when present; ignored otherwise).
- Flagged runs only reach these code paths via `source == "assist"` runs.

- [ ] **Step 1: Write failing tests:** `evidence_summary` is at most 1,500 chars, contains no claim text (feed a marker string in the brief and assert absence), and lists missing sections; `detect_pattern` without `evidence` produces the exact same messages as before (golden test against the existing builder output); with `evidence` the user message contains `<evidence>` and neutralises an injected closing tag; general agents still raise `TranscriptTooShort` below 3 turns and still use the prefilter (existing tests stay green); an assist-kind agent accepts 2-turn runs and `check_due` becomes true after two flagged runs even with no trigger terms; `law_pack_gap` is parsed when present and ignored when absent; the evidence passed to a patched `detect_pattern` for an assist agent contains one summary per run.
- [ ] **Step 2: Run to confirm fail:** `python -m pytest tests/test_flywheel_assist.py -q` -> FAIL.
- [ ] **Step 3: Implement.** Keep changes at the call sites minimal; do not touch behaviour for `kind == "general"`.
- [ ] **Step 4: Run tests** -> PASS; full suite green (all existing flywheel tests unchanged).
- [ ] **Step 5: Commit:** `feat(assist): flywheel evidence and gate rules for the assistant kind`

### Task 12: Tests with brief snapshots, assembled replay and judge evidence

**Files:** Modify `backend/jobs/handlers.py` (`build_tests`, `replay`, `generate_heldout` handlers), `backend/db/testsuite.py`, `backend/core/judge.py`, `backend/core/prompts.py` (`build_judge_messages`), `backend/core/replay.py` (new `replay_assist`); test `tests/test_flywheel_assist_replay.py`.

**Interfaces — Produces:**
- `build_tests` copies the source run's `brief_snapshot` into `test_case.brief_snapshot` for assist-kind agents; held-out tests inherit the fix test's snapshot.
- `replay_assist(system_prompt: str, brief: Brief, questions: list[str]) -> list[dict]`: for each question builds the context with `classify_intent`, `excerpts_for` and `assemble` (the **same** functions Task 5 uses) and calls `groq_chat(..., model=ASSIST_MODEL, temperature=0)`; returns the transcript of the questions and answers only (page text is not in the transcript). The replay handler uses it when the test has a snapshot; otherwise the existing `replay`.
- `judge(config, criterion, transcript, *, evidence=None)`: for assist tests `evidence` is the brief rendered claims-first and truncated to `JUDGE_BRIEF_CHARS = 9_000`, inside `<page_evidence>` tags (neutralised); the judge system text gains one fixed sentence telling it to treat `<page_evidence>` as the page the agent could see; calls without evidence are unchanged.
- `replay_assist` imports `ASSIST_MODEL` from `backend.core.assist` (Task 1), the same constant the chat route uses.

- [ ] **Step 1: Write failing tests** (fake client; no network): a test built from a flagged run stores the snapshot; replay of an assist test calls the model with exactly the messages `assemble` would produce for that question (capture and compare to a direct `assemble` call using the same brief and the version's system prompt); replaying under a promoted version uses that version's prompt and the unchanged fixed policy; the judge message contains `<page_evidence>` with claims first and is capped at 9,000 chars; page text with a closing-tag injection is neutralised in the judge message; general-agent replay and judge messages are byte-identical to before (golden tests); the verification fan-out and reconcile paths work for an assist agent end to end with the fake client (approve, verify, promote), including `heldout` generation inheriting the brief.
- [ ] **Step 2: Run to confirm fail:** `python -m pytest tests/test_flywheel_assist_replay.py -q` -> FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run tests** -> PASS; full suite green; the controller re-runs `tests/pg` on the local Docker Postgres.
- [ ] **Step 5: Commit:** `feat(assist): snapshot-backed tests, assembled replay and judge evidence`

### Task 13: Import tooling

**Files:** Create `scripts/import_page_views.py`, `backend/core/assist/importing.py` (pure validation and expansion), `examples/assist/page_views.example.jsonl`, `docs/assist-import-format.md`; test `tests/test_import_page_views.py`.

**Interfaces — Produces:**
- One JSON Lines record per page view: `{session_id, patent_number, started_at (ISO), dwell_ms, sections: {name: visible_ms}, brief?: Brief, questions: [{text, answer, intent?, section_focus?, rating?: "up"|"down"|"no_answer", rating_reason?, reasked?: bool, citation_ok?: bool, assist_version: int}]}`.
- `validate_record(obj) -> ImportRecord` (field-naming errors, same caps and section names as the live API, US numbers only, a rating of `down`/`no_answer`, `reasked` or `citation_ok == false` requires a `brief` so a flagged conversation can be created) and `expand(record) -> (PageViewRow, list[QuestionRow], flagged: list[FlaggedConversation])`; missing `intent` is computed with `classify_intent`; `bounce` is computed with the same rule as the tracker.
- `scripts/import_page_views.py FILE [--dry-run]`: validates every line first and imports nothing if any line is invalid (prints line numbers and messages); otherwise inserts in one transaction into the demo workspace (`--database-url` or `DATABASE_URL`; password masked in output); every row gets `source="imported"` and the record's timestamps; flagged conversations become runs with `source="imported"` (the 50-run cap does not apply: direct insert). Re-importing the same session ids updates, never duplicates.

- [ ] **Step 1: Write failing tests:** the example file validates and expands (counts of page views, questions, flagged conversations asserted exactly); each validation rule has a failing case with a line number; a failing line aborts the whole import (nothing inserted); re-import is idempotent; every inserted row has `source == "imported"`; timestamps preserved; the password never appears in output; `--dry-run` writes nothing; intents are computed when absent; bounce computed per the rule.
- [ ] **Step 2: Run to confirm fail:** `python -m pytest tests/test_import_page_views.py -q` -> FAIL.
- [ ] **Step 3: Implement** and write `docs/assist-import-format.md` (field table, one worked example, the "demo dataset" labelling rule).
- [ ] **Step 4: Run tests** -> PASS; full suite green.
- [ ] **Step 5: Commit:** `feat(assist): page-view import tooling and format`

### Task 14: Adoption metrics core and console API

**Files:** Create `backend/core/assist/adoption.py`, `backend/api/assist_console.py`; modify `backend/main.py`, `backend/api/schemas.py`; test `tests/test_assist_adoption.py`, `tests/test_api_assist_console.py`.

**Interfaces — Produces:**
- Pure `compute_adoption(page_views: list[PV], questions: list[Q]) -> dict` with a `sources` breakdown (`live`, `imported` counts) and these metrics, each `None` (never an exception) when its denominator is zero: `feature_coverage[section]` = non-bounce views where `visible_ms >= 3000` / non-bounce views; `help_demand[section]` = questions with that `section_focus` per 100 views of the section; `task_mix[intent]` counts and shares; `struggle`: `navigation_help` count, `reask_rate_by_intent`, `refusal_count` (legal-opinion answers), `unverified_citation_rate`; `bounce_rate`; totals.
- `compute_version_comparison(questions) -> dict`: per `assist_version` and intent, re-ask and thumbs-down rates **from questions whose `source != "imported"` only**; returns `{"available": false, "reason": "..."}` when fewer than 2 versions have at least 10 live questions.
- Builder routes (workspace token, scoped to the workspace's assist agent): `GET /assist/console/metrics` (adds `data_source_label`: "demo dataset" whenever any imported rows exist), `GET /assist/console/flagged?intent=&section=&limit=` (flagged runs with their questions, ids usable by the existing discovery step), `GET /assist/console/status` (agent id, current version number, run count).

- [ ] **Step 1: Write failing tests:** a hand-built dataset with exact expected coverage/help-demand/task-mix numbers; zero views -> `None`; bounce excluded from coverage denominators; sections with no views -> `None`; version comparison ignores imported rows and returns `available: false` for sparse data; a dataset that is entirely imported never produces a `version_comparison.available == true`; API: 401/404 for a foreign workspace; metrics include `data_source_label == "demo dataset"` when imported rows exist; flagged list filters by intent and section; responses contain no raw brief text.
- [ ] **Step 2: Run to confirm fail:** `python -m pytest tests/test_assist_adoption.py tests/test_api_assist_console.py -q` -> FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run tests** -> PASS; full suite green.
- [ ] **Step 5: Commit:** `feat(assist): adoption metrics and console API`

### Task 15: Insights brief

**Files:** Modify `backend/core/assist/adoption.py` (prompt builder only), `backend/api/assist_console.py`; test `tests/test_assist_insights.py`.

**Interfaces — Produces:** `POST /assist/console/insights` -> `{brief: str, counts_used: dict, data_source_label}`. Builds a fixed system prompt (no data) and a user message containing the aggregates JSON and at most 3 question texts per hotspot (neutralised, each capped at 300 chars), calls `groq_chat` on the judge-class model inside `request_retries(2)`, and returns text that must reference only numbers present in `counts_used` (post-check: every number in the output must appear in the aggregates or be a count of listed questions; otherwise the response carries `"grounded": false` and the UI shows a warning). Rate-limited with the app's LLM limiter. Never sends full conversations or briefs.

- [ ] **Step 1: Write failing tests** (fake client): the captured prompt contains the aggregates and at most 3 texts per hotspot; a question text with an injected closing tag is neutralised; a model reply containing an invented figure sets `grounded: false`; a reply using only supplied figures is `grounded: true`; model failure -> 502 fixed text; imported data is labelled in `counts_used`; no brief text in the prompt.
- [ ] **Step 2: Run to confirm fail:** `python -m pytest tests/test_assist_insights.py -q` -> FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run tests** -> PASS; full suite green.
- [ ] **Step 5: Commit:** `feat(assist): grounded insights brief`

### Task 16: Assist console (Adoption and Evolution)

**Files:** Create `frontend/src/components/assist/{AssistConsole,AdoptionPanel,Bars,FlaggedList,InsightsBrief,ConnectBox}.jsx` and tests; modify `frontend/src/api.js`, `frontend/src/App.jsx`.

**Interfaces:** API functions `assistStatus`, `assistMetrics`, `assistFlagged`, `assistInsights`. `ConnectBox` takes the demo workspace token (D6; stored like the app's token; cleared by a "Disconnect" button). `AssistConsole` shows: header with the data-source label ("demo dataset" whenever the metrics response says so, plus a plain-text "imported" tag on imported flagged conversations); **Adoption** tab: feature coverage bars, help demand table, task mix bars, struggle numbers, `None` values rendered as "n/a", the insights brief (button "Generate insights", the "not grounded" warning, the counts used); each hotspot row links to the filtered flagged list, whose items offer "Review in Evolution"; **Evolution** tab: the existing eval-criteria panel, tests, change review and version history for the assistant agent (reuse the components, no copies), plus the replay before/after table already produced by verification and a signal comparison block that renders only when `version_comparison.available` (otherwise one sentence explaining why not). Bars are plain HTML elements (no chart library) with text labels and accessible names. Entry point: a "Assist console" button in the app header, visible only after connecting.

- [ ] **Step 1: Write failing tests:** connect flow stores the token and calls status; imported-data label present when the API says so; `None` renders "n/a" and no bar; hotspot link filters the flagged list; "Generate insights" disables while pending and shows the warning when `grounded` is false; the signal comparison block is absent when `available` is false and shows the reason; Evolution tab renders the existing panel for the assistant agent id; every bar has a text value and accessible label; disconnecting clears state.
- [ ] **Step 2: Run to confirm fail** (PowerShell): `cd frontend; npx vitest run src/components/assist` -> FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run tests** -> PASS; `npm test`, `npm run lint` (0 problems), `npm run build`.
- [ ] **Step 5: Commit:** `feat(frontend): Assist console with Adoption and Evolution`

### Task 17: Docs and the demo script

**Files:** Modify `README.md`, `docs/ARCHITECTURE.md`; create `docs/DEMO-ASSIST.md`.

- [ ] **Step 1: Write the docs** in the existing voice, from the real code: a "Patent Assist" README section (what it is, setup: `scripts/setup_assist.py`, `.env` variables, build and load the extension unpacked, import the example data, open the console), the architecture section (components, the three knowledge channels, signal tables, flagged runs, the evidence each flywheel stage sees, the privacy rules, decisions D1-D7), a "verified / not verified" list stating only what was actually run, and `docs/DEMO-ASSIST.md` (a three-minute talk track: ask on a real patent, rate, console shows the pattern, a fix through the gate, a better answer; fallbacks; what to say about the human gate and the demo-dataset label). State plainly: the law pack is a draft until the author reviews it; the extension manual checklist is for the author to run; imported data is a demo dataset.
- [ ] **Step 2: Verify:** every number and name traced to code; `python -m pytest -q`; PowerShell `cd frontend; npm test; npm run lint; npm run build`; `cd extension; npx vitest run; npm run build`; validate any Mermaid by reading.
- [ ] **Step 3: Commit:** `docs: Patent Assist overview, architecture and demo script`

---

## After the tasks (controller, not an implementer task)

1. Ask the author for the saved patent pages before Task 7 (D7) and for review of the draft law pack before the demo.
2. Final whole-branch review (Review Focus above) and one fix wave.
3. Real-Postgres checks: run `tests/pg` (including the extended migration test) on the local Docker Postgres.
4. Real run with the author's Groq key (read by the program, never displayed): start the backend with the setup script's output, feed three saved pages through the extractor and `POST /assist/chat` with real questions, flag two conversations, run discovery, build tests, suggest, approve, verify and promote on the assistant agent; report exactly what happened.
5. The author runs `extension/CHECKLIST.md` in a real browser; results are recorded in the README's verified list. Merge to `main` only on the author's say-so; no push or deploy.

## Deferred beyond this plan

Evolving the context policy (documented stretch), non-US patents, search-results pages, cross-page memory, streaming answers, the `evalgate` gate for the assistant, Chrome Web Store publication, an automated browser end-to-end test.
