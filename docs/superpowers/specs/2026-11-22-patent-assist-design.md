# Patent Assist: a self-evolving assistant for Google Patents (MVP design)

Date: 2026-11-22. Status: approved by the author (scope cuts applied the same day). Branch: `feat/patent-assist` (cut from `main` after Phases 1-4 were merged).
Builds on: `docs/ARCHITECTURE.md` (the Eval Discovery flywheel: discover criteria, tests, suggest and verify fixes behind a human gate, the `evalgate` CI gate) and `docs/superpowers/specs/2026-11-21-agent-ready-eval-flywheel-design.md`.

## 1. Intent and the why

AI products have a steep learning curve: new users take time to find what a product can do and to fit it into their work. The pivot turns the flywheel into an assistant that lives *inside a product's own page*, like Notion AI inside Notion. It helps users understand the page in front of them. For the product builder, the same conversations and behaviours are an honest signal of how the product is actually used and where it confuses people.

The author's role is that of a forward-deployed engineer. Three jobs: (i) increase adoption of the product for its users, (ii) understand the customer better, (iii) build an assistant that improves itself.

This is a **portfolio demo of one workflow, not a product**. The demo target is **Google Patents** (patent pages on `patents.google.com`), chosen because all the knowledge a question needs sits in one public document, answers can be checked against that text, and it needs no login. Google Patents is a stand-in for any product with rich pages. The idea is shown portable by the design, not by a second build.

## 2. Goals, non-goals, anti-goals

**Goals**
- A Chrome extension whose overlay assistant answers questions about the **current US patent page**, grounded in that page, in plain language, with citations to claims and to a small curated law pack.
- Adoption and behaviour signals about **Google Patents itself** (which features users use, where they need help), shown to the "builder" in a console.
- The assistant improves through the existing flywheel (system prompt versions), with a human approving every change.

**Non-goals (MVP)**
Publishing to the Chrome Web Store; non-US patents; search-results pages; memory across pages; streaming answers; simulated users (the author populates demo data); the flywheel editing the law pack, the extraction code or the **context policy** (a documented stretch goal); end-user accounts; an automated browser end-to-end test; an assist-key table.

**Anti-goals** (a failure even if it technically works)
Giving legal opinions on validity, infringement or freedom to operate; capturing search queries; tracking users across pages; measuring the widget itself (opens, click-through) as if it were the product; presenting the author's seeded data as real usage.

## 3. Constraints

- Manifest V3: all extension logic ships in the package, no remote scripts. The backend returns data only.
- Google's terms restrict automated access (secondary sources). The extension reads only the page the user is viewing and never fetches other pages.
- Groq free tier: about 8000 tokens per minute on the 120B model (observed). Context slices must be bounded per intent. A higher-limit key may be needed for demos (the author's decision, outside this design). `GROQ_TPM_LIMIT` pacing already exists.
- The MPEP is published free by the USPTO (revision 01.2024 found). Confirm reuse terms before bundling text. Prefer short hand-written summaries with section references over verbatim text.
- Reuse the stack: FastAPI + SQLAlchemy backend, React/Vite console, Groq models via `groq_chat`.
- Never read or commit the repo-root `.env`; no push or deploy without the author's say-so.

## 4. Approaches considered

1. **Extension plus new assist endpoints on the current backend, reusing the flywheel (chosen).** Smallest change; the loop is real.
2. A separate assistant service importing the flywheel as a library. Cleaner boundaries, a second deploy, more time.
3. Extension with simulated signals. Fastest, but the central claim would be fake.

Widget delivery was also considered: a hosted iframe is not viable under Manifest V3 for an in-page overlay, so the widget is bundled (Shadow DOM).

## 5. The assistant: how knowledge reaches one answer

Three channels feed **one model call per question**:

| Channel | What | Changes when |
|---|---|---|
| System prompt | Role, answer format, citation rules, the refusal line | A fix is approved (the evolving artifact) |
| Page context (per question) | A structured **brief** extracted from the page, wrapped in delimiter tags in the user turn | Every question |
| Law pack (per question) | The provisions mapped to the question's intent, wrapped and labelled with ids | A human edits the pack |

Pipeline on the backend, for `POST /assist/chat`:
1. **Intent** by keyword rules (no extra model call): `claim_explain`, `claim_scope`, `status_family`, `citations_prior_art`, `classification`, `law_concept`, `navigation_help`, `legal_opinion_request`, `other`.
2. **Slices**: a **fixed context policy in code** (intent to brief sections and size caps; abstract and claims always available). It is not versioned and not evolved in the MVP.
3. **Law**: a fixed **intent-to-provision-ids map** over a pack of about 12 hand-curated provisions (35 U.S.C. 101, 102, 103, 112, 271; a few MPEP sections on claim interpretation, definiteness and obviousness; claim-term basics such as "comprising"). No search engine.
4. **One model call**: the active system prompt version plus a user message of tagged blocks (brief slices, law excerpts, conversation).
5. **Citation check** (deterministic code): claim numbers cited exist in the brief; law ids cited were among the provided excerpts. A failure adds a visible "unverified citation" note and logs an event.
6. **Legal-opinion refusal**: `legal_opinion_request` is answered with an explanation of what the claims cover plus a not-legal-advice line, never a validity or infringement conclusion.

The same **assembly function** (pure core module) is used by the chat route and by test replay, so tests exercise exactly what users get.

Only US patents are supported. A page whose number is not US gets a polite "US patents only" answer.

## 6. The extension

- Content script matches `https://patents.google.com/*` and **acts only on `/patent/` routes**; on any other route it records and sends nothing. Google Patents may navigate without reloading, so the script detects route changes and starts a **new session** on each new patent page. Whether the site is a single-page app must be verified first.
- **Extractor** (pure function over a DOM, unit-tested on saved pages): number, title, dates (priority, filing, publication; grant date derived from the legal-event "Application granted"), inventors, assignees, legal status and expiry, classifications (CPC code, title, hierarchy), landscapes (area tags), abstract, description sections, **claims with numbers and a dependency tree** (a claim is dependent if it references another claim), patent citations and cited by, family and country status, legal events. Similar documents, non-patent citations, family citations and images are not extracted. Every field is optional; the brief carries a `completeness` map, and the assistant is told which parts were not found.
- **Widget**: Shadow DOM overlay, quick-action chips that are simply pre-filled questions ("Explain claim 1", "What do these CPC codes mean?", "Is this still in force?"), selection-aware: a selection is written **into the question text** ("Explain this passage: \"...\""), so a question is always one plain string. Thumbs up/down with an optional reason, "didn't answer my question".
- **Service worker** makes all backend calls with a single **assist key** (header `X-Assist-Key`) read from an environment variable on the backend and compared in constant time. It can chat and send signals and cannot read anything. The backend allow-lists the extension origin and rate-limits per session.
- **Sessions**: a random id per page view; no persistent install id; history kept in the page and sent each turn; nothing carried to the next page.
- Distribution for the demo: "load unpacked" in developer mode. Store-policy compliance applies only if it is ever published.

## 7. Signals and the Assist console

Signals are about **Google Patents**, not the widget. Widget opens and click-through are not measured.

**Captured, patent pages only, per page view**
- A **page-view summary**, sent as periodic heartbeats that upsert by session id (so a closed tab loses little): dwell time; per section, time visible (a section counts as *viewed* after about 3 seconds visible); bounce (dwell under about 10 seconds and no section viewed).
- **Question events**: intent, the section or topic the question is about, the text.
- **Rating events**: thumbs, reason, and *re-ask* (a rephrase with the same intent shortly after an answer). These feed the flywheel and the Evolution area only.
- **Not captured**: search queries, anything outside patent pages, link clicks that leave the page, scroll depth, copy counts, any identity.

**Metrics (Adoption area), all per page view**
- **Feature coverage**: share of non-bounce views where each section (claims, description, classifications, citations/cited by, family, legal events, landscapes) was viewed.
- **Help demand**: questions about a section per 100 views of that section.
- **Task mix**: distribution of intents.
- **Struggle signals**: `navigation_help` questions ("where do I find..."), re-ask rate by intent, refusals.
- **Insights brief**: a model-written summary grounded only in the aggregates (plus a few anonymised question texts per hotspot), shown with the counts it used.
Charts are plain HTML bars and tables.

**Console data**: the author populates the database. The import is **one record kind, one JSON Lines record per page view**: `session_id`, `patent_number`, optional `brief`, dwell and per-section visible times, and a list of `questions` (text, intent, section focus, the answer, rating, reask flag, citation-check result, answering `assist_version`). The importer validates and expands each record into events and, for flagged questions, flagged conversations (flywheel runs with their brief snapshot). Imported records carry `source: "imported"`; the console shows that tag, labels the data source (for example "demo dataset") in the UI and in any export, and never counts imported data as measured effect (see section 8).

**How Adoption relates to the flywheel**: the Adoption area is a lens, not an input. A hotspot (for example high help demand on claims, or a high re-ask rate for `claim_scope`) links to the filtered list of flagged conversations behind it, and the author can send those to the existing discovery step. The flywheel always works on conversations, never on aggregates.

## 8. Evolution: what improves and how

The versioned artifact is the **system prompt**, exactly as in the existing flywheel (proposed, approve, verify, promote, restore). The context policy and the law pack do not evolve.

- **Collection**: conversations with a thumbs-down, a "didn't answer", a re-ask or a failed citation check are saved as flywheel *runs*, each with its frozen **brief snapshot**.
- **Discovery, tests, fix**: the existing pattern check, criterion discovery, held-out generation and suggest-fix run on those runs. Fixes edit the system prompt only.
- **Frozen context for tests**: a test stores a brief snapshot beside its scripted questions (plain strings). Replay assembles context with the fixed policy and the same law pack, then calls the model with the *version's* system prompt.
- **What each stage sees**:
  - *Pattern detection, criterion generation, suggester*: the transcript plus an **evidence summary** per conversation (intent, section focus, which slices were included, the brief's completeness map, citation-check result, signal reasons). No full brief text, to respect the token limit.
  - *Judge*: the transcript plus the **brief, claims first, truncated to a fixed token cap**, so grounding criteria ("quotes the claim", "does not invent a claim") can be judged.
  - For the assistant agent kind, **discovery does not use the keyword prefilter** and accepts conversations of **2 turns** (one question, one answer), because conversations are chosen by signals and grouped by (intent, signal reason).
- **Evidence of improvement**: the headline is the existing **replay before/after table** (the same tests under the old and the new version), which is measured. Signal-based before/after (for example re-ask rate by intent per version) is shown **only for events whose `source` is not "imported"**, which carry the answering `assist_version`. Imported data is never presented as measured improvement.
- **Law pack**: edited only by the author. The suggester may add a "possible law-pack gap" note to a change report for the author to act on.
- **Stretch (documented, not built)**: evolving the context policy (which slices per intent), as data with a schema, versioned with the prompt.
- **Demo story**: signals arrive, the Adoption area shows a pattern, a finding and fix go through the gate in the Evolution area, and the next answer in the extension is better.

## 9. Backend and console changes (summary)

- New `assist` API: `POST /assist/chat`, `POST /assist/signals` (page-view heartbeats, ratings), key-authenticated by the environment-variable key; builder-side routes (workspace token) for metrics and the import.
- Data: an agent kind for the assistant; a brief snapshot on tests and on saved runs; signal events in the existing `event` table (new names, each carrying `assist_version` and `source`), or a dedicated table if volume or querying requires it. No key table, no policy column.
- **Ownership and lifetime**: exactly one assistant agent, in a dedicated demo workspace created by a setup script with an expiry far in the future (so the 30-day purge never reaches it). Imports insert directly, so the 50-run cap does not apply to them; the other limits stay.
- Pure core additions: brief schema, assembly, intent rules, law-pack map, citation check.
- Console: an Assist area (Adoption, Evolution) in the existing React app, reading the same data; Evolution reuses the existing flywheel screens.
- `evalgate` gating of the assistant is out of scope for the MVP.

## 10. Testing strategy

- Extractor: unit tests on saved patent pages (fixtures kept minimal); selectors verified against live pages first.
- Backend: pure-core tests (intent, assembly, law map, citation check), API tests with the model faked, flywheel integration tests with frozen briefs.
- Extension: build output checked; route-change handling and heartbeat logic unit-tested; a **manual checklist** for loading the unpacked extension on a real patent page (no automated browser test).
- Console: component tests with an imported example dataset.
- Honesty: the docs state what was and was not verified, as in Phase 4.

## 11. Decisions made with the author

Target: Google Patents (US pages only); approach 1; evolution covers the system prompt only, with the context policy as a stretch and the law pack human-curated; signals measure Google Patents, not the widget; patent pages only, one chat session per page view, transitions not captured; no simulation (the author populates data); the console layout (Adoption and Evolution areas) approved; scope cuts approved (single env-var key, one import record kind, selection inside the question text, fixed law map, trimmed extractor and signals, no automated browser test, plain HTML charts).

## 12. Calls I made (open to change)

Keyword rules for intent; a fixed intent-to-provision map instead of search; heartbeat upserts instead of an exit beacon; no persistent install id; the 3-second and 10-second thresholds; the "unverified citation" note instead of a retry; extension-origin allow-list; US-only refusal; history kept in the page; a "demo dataset" label in the console; one assistant agent in a far-future-expiry demo workspace; the evidence each flywheel stage sees (summary for discovery and suggester, brief for the judge); no prefilter and a 2-turn minimum for the assistant kind; signal before/after only for non-imported events, replay before/after as the headline.

## 13. Left to the builder

The exact DOM selectors and fixtures; the intent keyword lists and context-policy contents; the law-pack file format and provision list (subject to reuse-terms checks); the format of the brief snapshot on tests; whether signal events need a dedicated table; the model choice and token budgets per intent under the rate limit; the import-script details; how heartbeats are batched; the structure of the Assist area; the task breakdown.
