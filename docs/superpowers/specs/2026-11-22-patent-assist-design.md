# Patent Assist: a self-evolving assistant for Google Patents (MVP design)

Date: 2026-11-22. Status: draft for approval. Branch: `feat/patent-assist` (cut from `main` after Phases 1-4 were merged).
Builds on: `docs/ARCHITECTURE.md` (the Eval Discovery flywheel: discover criteria, tests, suggest and verify fixes behind a human gate, the `evalgate` CI gate) and `docs/superpowers/specs/2026-11-21-agent-ready-eval-flywheel-design.md`.

## 1. Intent and the why

AI products have a steep learning curve: new users take time to find what a product can do and to fit it into their work. The pivot turns the flywheel into an assistant that lives *inside a product's own page*, like Notion AI inside Notion. It helps users understand the page in front of them. For the product builder, the same conversations and behaviours are an honest signal of how the product is actually used and where it confuses people.

The author's role is that of a forward-deployed engineer. Three jobs: (i) increase adoption of the product for its users, (ii) understand the customer better, (iii) build an assistant that improves itself.

This is a **portfolio demo of one workflow, not a product**. The demo target is **Google Patents** (patent pages on `patents.google.com`), chosen because all the knowledge a question needs sits in one public document, answers can be checked against that text, and it needs no login. Google Patents is a stand-in for any product with rich pages. The idea is shown portable by the design, not by a second build.

## 2. Goals, non-goals, anti-goals

**Goals**
- A Chrome extension whose overlay assistant answers questions about the **current US patent page**, grounded in that page, in plain language, with citations to claims and to a small curated law pack.
- Adoption and behaviour signals about **Google Patents itself** (which features users use, where they need help), shown to the "builder" in a console.
- The assistant improves through the existing flywheel, with a human approving every change.

**Non-goals (MVP)**
Publishing to the Chrome Web Store; non-US patents; search-results pages; memory across pages; streaming answers; simulated users (the author will populate demo data); the flywheel editing the law pack or the extraction code; end-user accounts.

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

Widget delivery was also considered: a hosted iframe is not viable under Manifest V3 for an in-page overlay, so the widget is bundled (Shadow DOM, built with the repo's Vite/React stack).

## 5. The assistant: how knowledge reaches one answer

Three channels feed **one model call per question**:

| Channel | What | Changes when |
|---|---|---|
| System prompt | Role, answer format, citation rules, the refusal line | A fix is approved |
| Page context (per question) | A structured **brief** extracted from the page, wrapped in delimiter tags in the user turn | Every question |
| Law pack (per question) | 2-4 retrieved excerpts, wrapped and labelled with ids | A human edits the pack |

Pipeline on the backend, for `POST /assist/chat`:
1. **Intent** by keyword rules (no extra model call): `claim_explain`, `claim_scope`, `status_family`, `citations_prior_art`, `classification`, `law_concept`, `navigation_help`, `legal_opinion_request`, `other`.
2. **Slices**: the **context policy** says, per intent, which parts of the brief to include and the size caps. Abstract and claims are always available. The policy is data (JSON), validated against a schema.
3. **Law retrieval** only for intents that need it: keyword (BM25-style) search over about 30 hand-curated provisions (35 U.S.C. 101, 102, 103, 112, 154, 271, 282; 37 CFR 1.75; key MPEP sections; a few landmark cases summarised). No embeddings.
4. **One model call**: fixed system prompt (the active version) plus a user message of tagged blocks (brief slices, law excerpts, selected text, conversation).
5. **Citation check** (deterministic code): claim numbers cited exist in the brief; law ids cited were among the retrieved excerpts. A failure adds a visible "unverified citation" note and logs an event.
6. **Legal-opinion refusal**: `legal_opinion_request` is answered with an explanation of what the claims cover plus a not-legal-advice line, never a validity or infringement conclusion.

The same **assembly function** (pure core module) is used by the chat route and by test replay, so tests exercise exactly what users get.

Only US patents are supported. A page whose number is not US gets a polite "US patents only" answer.

## 6. The extension

- Content script matches `https://patents.google.com/*` and **acts only on `/patent/` routes**; on any other route it records and sends nothing. Google Patents may navigate without reloading, so the script detects route changes and starts a **new session** on each new patent page. Whether the site is a single-page app must be verified first.
- **Extractor** (pure function over a DOM, unit-tested on saved pages): number, title, dates (priority, filing, publication; grant date derived from the legal-event "Application granted"), inventors, assignees, legal status and expiry, classifications (CPC code, title, hierarchy), landscapes (area tags), abstract, description sections, **claims with numbers and a dependency tree** (a claim is dependent if it references another claim), patent and non-patent citations, cited by, family citations, family and country status, legal events, similar documents. Every field is optional; the brief carries a `completeness` map, and the assistant is told which parts were not found.
- **Widget**: Shadow DOM overlay, quick-action chips ("Explain claim 1", "What do these CPC codes mean?", "Is this still in force?"), selection-aware ("explain this"), thumbs up/down with an optional reason, "didn't answer my question".
- **Service worker** makes all backend calls with a limited **assist key** (`X-Assist-Key`): it can chat and send signals, never read anything. Keys are stored hashed; the backend allow-lists the extension origin and rate-limits per session.
- **Sessions**: a random id per page view; no persistent install id; history kept in the page and sent each turn; nothing carried to the next page.
- Distribution for the demo: "load unpacked" in developer mode. Store-policy compliance applies only if it is ever published.

## 7. Signals and the Assist console

Signals are about **Google Patents**, not the widget. Widget opens and click-through are not measured.

**Captured, patent pages only, per page view**
- A **page-view summary**, sent as periodic heartbeats that upsert by session id (so a closed tab loses little): dwell time; per section, time visible (a section counts as *viewed* after about 3 seconds visible); scroll depth; number of text selections; number of copies; bounce (dwell under about 10 seconds and no section viewed).
- **Question events**: intent, the section or topic the question is about, the text.
- **Rating events**: thumbs, reason, copy, and *re-ask* (a rephrase with the same intent shortly after an answer). These feed the flywheel and the Evolution area only.
- **Not captured**: search queries, anything outside patent pages, link clicks that leave the page, any identity.

**Metrics (Adoption area), all per page view**
- **Feature coverage**: share of non-bounce views where each section (claims, description, classifications, citations/cited by, family, legal events, similar documents, landscapes, images) was viewed.
- **Help demand**: questions about a section per 100 views of that section.
- **Task mix**: distribution of intents.
- **Struggle signals**: `navigation_help` questions ("where do I find..."), re-ask rate by intent, refusals.
- **Insights brief**: a model-written summary grounded only in the aggregates (plus a few anonymised question texts per hotspot), shown with the counts it used.

**Console data**: the author populates the database. This design delivers a documented format (JSON Lines), `scripts/` import tooling that validates and inserts with explicit timestamps, and an example file. The format has two record kinds: **events** (page-view summaries, questions, ratings) and **conversations** (the messages of one session, its brief snapshot, the signals attached to it, and the assistant version that answered). Imported records carry `source: "imported"`; the console shows that tag on them, labels the data source (for example "demo dataset") in the UI and in any export, and never counts imported data as measured effect (see section 8).

**How Adoption relates to the flywheel**: the Adoption area is a lens, not an input. A hotspot (for example high help demand on the claims section, or a high re-ask rate for `claim_scope`) links to the filtered list of flagged conversations behind it, and the author can send those to the existing discovery step. The flywheel always works on conversations, never on aggregates.

## 8. Evolution: what improves and how

The versioned artifact becomes **system prompt + context policy**. The flywheel's existing lifecycle (proposed, approve, verify, promote, restore) applies to both together.

- **Collection**: conversations with a thumbs-down, a "didn't answer", a re-ask or a failed citation check are saved as flywheel *runs*, each with its frozen **brief snapshot**.
- **Discovery, tests, fix**: the existing pattern check, criterion discovery, held-out generation and suggest-fix run on those runs. Fixes may edit the system prompt and the context policy, never code, never the law pack.
- **Frozen context for tests**: a test stores its brief snapshot with its scripted turns. Each turn keeps the **question text, the selected text if any, and the chip id if the question came from a quick-action chip** (chips map to fixed intents; otherwise intent is recomputed from the text). For existing agents tests stay lists of strings; the assistant agent kind uses structured turns. Replay assembles context with the *version's* policy and the same law pack, then calls the model with the *version's* system prompt.
- **What each stage sees**:
  - *Pattern detection, criterion generation, suggester*: the transcript plus an **evidence summary** per conversation (intent, section focus, which slices the policy included, the brief's completeness map, citation-check result, signal reasons), and for the suggester the current policy. No full brief text, to respect the token limit.
  - *Judge*: the transcript plus the **brief, claims first, truncated to a fixed token cap**, so grounding criteria ("quotes the claim", "does not invent a claim") can be judged. Because the judge may see more than the policy included, a fix that adds the missing claim to the policy is something the loop can discover.
  - For the assistant agent kind, **discovery does not use the keyword prefilter** and accepts conversations of **2 turns** (one question, one answer), because conversations are chosen by signals and grouped by (intent, signal reason).
- **Evidence of improvement**: the headline is the existing **replay before/after table** (the same tests under the old and the new version), which is measured. Signal-based before/after (for example re-ask rate by intent per version) is shown **only for events with `source` not "imported"**, which carry the answering `assist_version`. Imported data is never presented as measured improvement.
- **Verification and promotion**: unchanged (deterministic rule in code, held-out test, regression tests, human approval).
- **Law pack**: edited only by the author. The suggester may add a "possible law-pack gap" note to a change report for the author to act on.
- **Demo story**: signals arrive, the Adoption area shows a pattern, a finding and fix go through the gate in the Evolution area, and the next answer in the extension is better. The evolution evidence is the existing before/after table plus the re-ask rate by intent before and after.

## 9. Backend and console changes (summary)

- New `assist` API: `POST /assist/chat`, `POST /assist/signals` (page-view heartbeats, ratings), key-authenticated; builder-side routes (workspace token) for keys and metrics.
- Data: an agent kind for the assistant; `context_policy` on prompt versions; a brief snapshot on tests and on saved runs; assist keys (hashed, each mapped to one workspace and one agent); signal events in the existing `event` table (new names, each carrying `assist_version` and `source`) or a dedicated table if volume or querying requires it.
- **Ownership and lifetime**: exactly one assistant agent, in a dedicated **persistent demo workspace** created by a script (not by the public API). That workspace is exempt from the 30-day expiry and from the 50-runs-per-workspace cap (the other limits stay). Imported records with old timestamps are unaffected by the purge, which is workspace-based, not event-age-based.
- Pure core additions: brief schema, assembly, intent rules, policy schema and validator, law retrieval, citation check.
- Console: an Assist area (Adoption, Evolution) in the existing React app, reading the same data.
- `evalgate` can gate the assistant (suite export includes briefs) in a later step; not required for the MVP.

## 10. Testing strategy

- Extractor: unit tests on saved patent pages (fixtures kept minimal); verify selectors against live pages first.
- Backend: pure-core tests (intent, assembly, policy validation, retrieval, citation check), API tests with the model faked, flywheel integration tests with frozen briefs.
- Extension: a build that loads unpacked in a Chromium browser for one end-to-end check against a saved page served locally; route-change handling tested explicitly.
- Console: component tests with an imported example dataset.
- Honesty: the docs state what was and was not verified, as in Phase 4.

## 11. Decisions made with the author

Target: Google Patents (US pages only); approach 1; evolution covers system prompt and context policy, with the law pack human-curated; signals measure Google Patents, not the widget; patent pages only, one chat session per page view, transitions not captured; no simulation (the author populates data); the console layout (Adoption and Evolution areas) approved.

## 12. Calls I made (open to change)

Keyword rules for intent; BM25-style law retrieval over about 30 provisions; heartbeat upserts instead of an exit beacon; no persistent install id; the 3-second and 10-second thresholds; the "unverified citation" note instead of a retry; assist key header and extension-origin allow-list; US-only refusal; history kept in the page; a "demo dataset" label in the console.
Added after the builder check: the import also accepts full conversations (tagged `imported`); Adoption is a lens that links to flagged conversations, not an input to discovery; one assistant agent in a persistent demo workspace exempt from expiry and the run cap; the evidence each flywheel stage sees (summary for discovery and suggester, brief for the judge) and no prefilter or 3-turn minimum for the assistant kind; tests preserve selection text and chip origin; signal before/after only for non-imported events, replay before/after as the headline.

## 13. Left to the builder

The exact DOM selectors and fixtures; the context-policy schema and intent keyword lists; the law-pack file format and provision list (subject to reuse-terms checks); the schema for brief snapshots on tests; whether signal events need a dedicated table; the model choice and token budgets per intent under the rate limit; the import-script format details; how heartbeats are batched; the structure of the Assist area; phase and task breakdown for the implementation plan.
