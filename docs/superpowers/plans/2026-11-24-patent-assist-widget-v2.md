# Patent Assist Widget v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Crisp answers with plain formatting, clickable reference pills that scroll Google Patents to the cited claim, paragraph or section (granted patents only), no shortcut collisions, and quieter conversation controls (first-turn chips, one follow-up, one feedback request).

**Architecture:** The backend becomes the only parser of references: it validates claim, paragraph and section citations against the brief and returns them with UTF-16 offsets in the final answer. The extension slices the answer at those offsets, renders a small safe markdown subset and pills as DOM nodes (never HTML), and scrolls the page on click. The crisp prompt ships as a new active prompt version created through the existing version helper.

**Tech Stack:** Python 3.13 / FastAPI / pydantic / pytest (backend); vanilla JS MV3 content script, Vite, Vitest + jsdom (extension).

**Spec:** `docs/superpowers/specs/2026-11-24-patent-assist-widget-v2-design.md` (builds on `docs/superpowers/specs/2026-11-22-patent-assist-design.md`).

## Global Constraints

- Work on branch `feat/widget-v2` created from `main`; never commit to `main`; do not push.
- Widget stays a closed Shadow DOM; everything shown is built with `textContent`/DOM nodes. No `innerHTML`, no HTML from model output, no markdown library.
- `MAX_PROMPT_CHARS = 4000` (`backend/core/config.py`): `load_system_prompt()` must stay at or under it. `CHAT_MAX_TOKENS` stays 3000.
- Backend answer cap `MAX_ASSISTANT_MESSAGE_CHARS`: `finalize_answer` still never returns more.
- The chat response change is additive: new field `references` and two new `citation_check` lists. Existing fields and their meaning do not change.
- Reference tokens (fixed): claims use the existing grammar ("claim 3", "claims 1-3", "claims 1, 2 and 4"); paragraphs `[p0042]` (digits 1 to 6, exactly as the page marker); sections `[section:NAME]` with NAME one of `SECTION_NAMES` (`claims`, `description`, `classifications`, `citations`, `family`, `legal_events`, `landscapes`).
- Pills exist only for granted patents (kind code B or E, e.g. `US12401720B1`). On published applications (`A1`, `A2`, `A9`, no kind code, `S`, `P`) references are shown as plain text.
- At most 50 references per answer. Offsets are UTF-16 code units, measured on the answer string exactly as returned (before `&lt;`/`&gt;`/`&amp;` are undone for display).
- Ratings API, re-ask and citation-failure flagging are unchanged; only when the widget asks changes.
- Commit trailer on every commit: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` (use the model name of the implementing session).
- Test commands: backend `python -m pytest tests/<file> -q`; extension `cd extension && npx vitest run src/<file>`. Full gates at the end: `python -m pytest -q` (about 5 minutes) and `cd extension && npx vitest run`.

## Review Focus

1. A non-BMP character (emoji) before a reference shifts Python code-point offsets away from JS UTF-16 offsets, putting the pill on the wrong text. Expected: pill covers exactly the cited text. Pinned in Task 1 and Task 5.
2. Escaped angle brackets (`&lt;`) earlier in the answer change string length after display unescaping. Expected: offsets still land on the cited text. Pinned in Task 5.
3. Hostile or sloppy model output: a made-up `[p9999]`, `[section:secrets]`, `claims 1-999999`, 500 tokens in one answer. Expected: no pill for anything unverified, the existing citation note appears, at most 50 references, no slowdown. Pinned in Tasks 1 and 2.
4. A pill whose target is no longer on the page (SPA navigation, page differs from capture). Expected: disabled with a tooltip, no exception. Pinned in Task 6.
5. Highlight leaves the page changed (two quick clicks, or the element replaced). Expected: the page's inline styles are restored every time. Pinned in Task 6.
6. Key events: typing `i w s a d` reaches the page's shortcuts; Enter/Escape in the box stop working. Expected: page never sees keys from the box; Enter sends; Escape closes. Pinned in Task 7 and by a real-Chrome checklist row in Task 10.
7. Feedback card returns after more questions, or chips come back after the first turn. Expected: feedback once per page view, chips never again. Pinned in Task 8.

---

### Task 1: Reference extraction (backend, pure)

**Files:**
- Modify: `backend/core/assist/brief.py` (add `iter_claim_ref_spans` next to `iter_claim_ref_ranges`)
- Create: `backend/core/assist/references.py`
- Test: `tests/test_assist_references.py`

**Interfaces:**
- Produces in `brief.py`: `iter_claim_ref_spans(text: str) -> Iterator[tuple[int, int, list[tuple[int, int]]]]` yielding `(start, end, items)` for every reference match in `text` (`start`/`end` are `str` indexes of the whole match, e.g. "claims 1-3"; `items` are the `(lo, hi)` pairs `iter_claim_ref_ranges` would yield for it). Same regex and grammar; `iter_claim_ref_ranges` must keep yielding exactly what it did.
- Produces in `references.py`:
  - `@dataclass(frozen=True) class Reference: start: int; end: int; kind: str; target: str; label: str` (`start`/`end` UTF-16 offsets, end exclusive; `kind` in `"claim" | "paragraph" | "section"`; `target`: first claim number as digits / the marker digits / a section name; `label`: claim = matched text as written, paragraph = `"¶" + digits`, section = `SECTION_LABELS[name]`).
  - `PARAGRAPH_MARKER_RE` (`\[p([0-9]{1,6})\]`), `SECTION_TOKEN_RE` (`\[section:([a-z_]{1,20})\]`), `SECTION_LABELS: dict[str, str]` (`claims`→"Claims", `description`→"Description", `classifications`→"Classifications", `citations`→"Citations", `family`→"Family", `legal_events`→"Legal events", `landscapes`→"Landscapes"), `MAX_REFERENCES = 50`.
  - `paragraph_numbers(brief: Brief) -> set[str]`: the digit strings of every `[pNNNN]` marker found in `brief.description[*].text`.
  - `utf16_offset(text: str, index: int) -> int`: UTF-16 code units in `text[:index]`.
  - `extract_references(answer: str, brief: Brief) -> list[Reference]`: validated references only, sorted by `start`, at most `MAX_REFERENCES`, none overlapping.

- [ ] **Step 1: Write the failing tests** in `tests/test_assist_references.py`. Build a small `Brief` (claims 1-4, description section text `"[p0001] First. [p0002] Second."`). Cases and assertions:
  - `"See claim 1."` → one `Reference(kind="claim", target="1", label="claim 1")` whose `answer[start:end] == "claim 1"`.
  - `"claims 1-3"` and `"claims 1, 2 and 4"` → one reference each, `target` `"1"`, label the matched text; `"claims 1-9"` and `"claim 7"` → none (a number is missing).
  - `"[p0002] says it"` → paragraph `target="0002"`, label `"¶0002"`; `"[p0099]"` → none.
  - `"[section:description]"` → section, label `"Description"`; `"[section:secrets]"` → none.
  - Emoji first: `"😀 see claim 1"` → `start == 7` (the emoji is 2 UTF-16 units, so the word "claim" starts at UTF-16 offset 7) and `utf16_offset("😀 see claim 1", 6) == 7`.
  - 500 repetitions of `"claim 1 "` → exactly `MAX_REFERENCES` references; `"claims 1-999999"` returns in under 1 s with no references.
  - `iter_claim_ref_spans("claims 1-3 and claim 5")` yields two spans whose items are `[(1, 3)]` and `[(5, 5)]`; `list(iter_claim_ref_ranges(...))` is unchanged for the same text (existing tests in `tests/test_assist_brief.py` still pass).
- [ ] **Step 2: Run** `python -m pytest tests/test_assist_references.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `iter_claim_ref_spans` and `references.py` per the Interfaces. A claim reference is valid only when every integer in every `(lo, hi)` item (1 ≤ lo ≤ hi) is a claim number: check each item with `bisect` on the sorted claim numbers (count in range equals `hi - lo + 1`), never by expanding the range.
- [ ] **Step 4: Run** the new tests and `python -m pytest tests/test_assist_brief.py tests/test_assist_assemble.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(assist): extract validated claim, paragraph and section references with UTF-16 offsets`.

---

### Task 2: Citation check and chat response carry the new references

**Files:**
- Modify: `backend/core/assist/citations.py`, `backend/api/schemas.py`, `backend/api/assist.py`
- Test: `tests/test_assist_references.py` (extend), `tests/test_api_assist_chat.py` (extend)

**Interfaces:**
- Consumes: `paragraph_numbers`, `PARAGRAPH_MARKER_RE`, `SECTION_TOKEN_RE`, `extract_references` (Task 1); `SECTION_NAMES` from `backend/core/assist/intent.py`.
- Produces: `CitationCheck` gains `unverified_paragraphs: list[str]` and `unverified_sections: list[str]` (default empty lists); `ok` is false when any of the four lists is non-empty. `CitationCheckOut` gains the same two fields (default `[]`). New `class ReferenceOut(BaseModel): start: int; end: int; kind: Literal["claim","paragraph","section"]; target: str; label: str`. `AssistChatResponse` gains `references: list[ReferenceOut] = []`. `finalize_answer` keeps its signature.

- [ ] **Step 1: Write failing tests.** In `tests/test_assist_references.py`: `check_citations("see [p0099]", brief, [])` has `unverified_paragraphs == ["0099"]` and `ok` false; `[p0001]` in a brief that contains that marker is ok; `[section:nope]` → `unverified_sections == ["nope"]`; `[section:claims]` ok; each list is capped at `MAX_REPORTED`. In `tests/test_api_assist_chat.py` (reuse its faked model): a reply `"Claim 1 covers X, see claim 1 and [p0001]."` against a brief with that marker returns `references` with two claim/paragraph entries whose offsets slice the returned `answer`; a reply with `[p9999]` returns no paragraph reference, `citation_check.ok` false, `unverified_paragraphs == ["9999"]` and the existing citation note appended (and the conversation is flagged as today); a non-US patent answer returns `references == []`.
- [ ] **Step 2: Run** both files. Expected: FAIL.
- [ ] **Step 3: Implement** the check extension in `citations.py` (regexes bounded as the existing ones; read from `normalize_for_check` text like law ids), the schema fields, and in `chat()` set `references=extract_references(answer, brief)` on the response (extend `_response` with a `references` parameter).
- [ ] **Step 4: Run** `python -m pytest tests/test_assist_references.py tests/test_api_assist_chat.py tests/test_assist_assemble.py tests/test_final_fix_wave.py tests/test_real_run_fix_wave.py -q`. Expected: PASS (fix any existing test that constructs `CitationCheckOut` positionally).
- [ ] **Step 5: Commit** `feat(assist): return validated references and check paragraph and section citations`.

---

### Task 3: Extractor keeps paragraph markers (granted patents)

**Files:**
- Modify: `extension/src/extractor.js` (`readDescription`, line ~314)
- Test: `extension/src/extractor.test.js`

**Interfaces:**
- Produces: for every `.description-paragraph` element that has a `num` attribute matching `^[0-9]{1,6}$`, the paragraph text pushed into the section is `"[p" + num + "] " + text`. Elements without `num` (applications, `.description-line`) are unchanged. The marker counts toward `CAPS.sectionChars` like any other text.

- [ ] **Step 1: Write failing tests.** Using the saved pages loaded by the existing fixture helper in `extractor.test.js`: the brief for `us12401720b1.html` has a description whose joined text contains `[p0001]` and the paragraph following it; the brief for `us20250119367a1.html` and `us6756997b1.html` contains no `/\[p[0-9]+\]/` in any description text; a synthetic paragraph `<div class="description-paragraph" num="0042">` yields text starting `[p0042] `; a paragraph with `num="abc"` yields no marker. First open the saved page and confirm whether the paragraph's visible text already begins with its number; if it does, strip it before adding the marker so it is not doubled.
- [ ] **Step 2: Run** `cd extension && npx vitest run src/extractor.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** in `readDescription`.
- [ ] **Step 4: Run** `npx vitest run src/extractor.test.js src/fixtures.test.js src/completeness.test.js`. Expected: PASS; update any fixture expectation that pinned the old text (the committed expected output for `US12401720B1` changes by design).
- [ ] **Step 5: Commit** `feat(extension): keep paragraph numbers as [pNNNN] markers in the captured description`.

---

### Task 4: Crisp prompt and an active prompt version

**Files:**
- Modify: `backend/core/assist/system_prompt.md`
- Create: `scripts/set_assist_prompt.py`
- Test: `tests/test_assist_law.py` (extend the prompt test), `tests/test_set_assist_prompt.py`

**Interfaces:**
- Produces: `scripts/set_assist_prompt.py` with `main(argv: list[str] | None = None) -> int` (0 on success). It uses the app's database and `ASSIST_AGENT_ID` (via `get_assist_agent`), and: if the agent's current prompt text already equals `load_system_prompt()`, prints `Prompt already current (version N).` and changes nothing; otherwise calls `new_prompt_version(db, agent, load_system_prompt())`, commits, prints `Created prompt version N and made it current.` It prints no key, token or database URL. Exit 1 with one clear message when the agent cannot be found.

- [ ] **Step 1: Rewrite rules 2 to 5 of `system_prompt.md`** (everything else stays word for word, including the angle-bracket paragraph, the legal-opinion section and the fixed sentence; in the legal-opinion section change "(quote them, list their elements)" to "(list their elements)"). New rules, exact copy:
  ```
  2. Be brief. Lead with the answer in the first sentence. Use at most four short sentences (about 80 words). No greeting, no restating the question, no closing offer. Plain language for a non-lawyer; explain jargon in a few words.
  3. Plain text only: no markdown, no asterisks, no headings, no tables. Use a short "-" list only when the question asks for a list, with at most five items.
  4. Quote sparingly: at most one short quotation, and only when it carries the point; otherwise paraphrase. Never invent a claim number, date, name or quote.
  5. Point to the page instead of quoting at length. Cite claims as "claim N" (or "claims N-M") only for claims that exist in the supplied page. When the page text shows a paragraph marker such as [p0042], you may cite that paragraph as [p0042], exactly as shown, and only markers that appear. You may point to a page section as [section:NAME], where NAME is one of claims, description, classifications, citations, family, legal_events, landscapes.
  ```
  Renumber the remaining rules 6 to 9 (law citations, completeness, status and dates, block names) unchanged in wording. Keep the file at or under 4000 characters.
- [ ] **Step 2: Write failing tests.** Extend the existing prompt test in `tests/test_assist_law.py` (it already pins `quote`, `claim n`, `[usc-103]`, `plain language`): also assert `"at most four short sentences"`, `"no markdown"` (case-insensitive), `"[p0042]"`, `"[section:"` are present and `len(p) <= MAX_PROMPT_CHARS`. In `tests/test_set_assist_prompt.py` (reuse the assist-agent fixtures in `tests/test_assist_db.py`): running `main` on an agent whose prompt is old creates version 2 as `promoted`/`manual`, makes it current, leaves version 1 `superseded`; running it again changes nothing and exits 0; with `ASSIST_AGENT_ID` unset it exits 1.
- [ ] **Step 3: Run** both test files. Expected: FAIL.
- [ ] **Step 4: Implement** the prompt text and the script (sys.path pattern as in `scripts/setup_assist.py`).
- [ ] **Step 5: Run** `python -m pytest tests/test_assist_law.py tests/test_set_assist_prompt.py tests/test_assist_db.py tests/test_real_run_fix_wave.py -q`. Expected: PASS.
- [ ] **Step 6: Commit** `feat(assist): crisp answer prompt with page references, and a script to make it the live version`.

---

### Task 5: Answer view (markdown subset, references, laws)

**Files:**
- Create: `extension/src/widget/answer-view.js`
- Modify: `extension/src/widget/Widget.js` (re-export `displayText` from the new module; no behaviour change yet)
- Test: `extension/src/answer-view.test.js`

**Interfaces:**
- Produces in `answer-view.js`:
  - `displayText(answer: string): string` (moved from `Widget.js`; `Widget.js` keeps `export { displayText } from './answer-view.js'`).
  - `parseAnswer(text: string): Block[]` where `Block = { type: 'p' | 'ul' | 'ol', items: Inline[][] }` and `Inline = { text: string, start: number, bold: boolean, italic: boolean }`; `start` is the offset of the run's first character in `text`. Markdown handled: blank-line paragraphs; lines starting `- `, `* `, `• ` (bullet) or `1. `/`1) ` (numbered); paired `**x**` bold and paired `*x*` italic; leading `#` run of a line dropped; backticks dropped. Unpaired markers stay as literal text. Nothing else is interpreted.
  - `normalizeReferences(raw: unknown, answerLength: number): Ref[]` where `Ref = { start, end, kind, target, label }`: keeps only well-formed entries (integers, `0 <= start < end <= answerLength`, `kind` in claim/paragraph/section, `target` and `label` non-empty strings of at most 100 characters), sorted by `start`, dropping any that overlap an earlier one, at most 50.
  - `renderAnswer(doc: Document, answer: string, references: Ref[], options: { pills: boolean, onJump: (ref: Ref) => void }): DocumentFragment`. Builds `<p>`/`<ul>`/`<ol>`/`<li>`/`<strong>`/`<em>` nodes with `textContent`. A reference that lies wholly inside one run becomes `<button type="button" class="pill" data-kind data-target>` (text = `label`) when `options.pills`, else a `<span class="ref">` with `label` as its text; clicking a pill calls `onJump(ref)`. A reference that spans two runs or blocks renders as plain text. `[usc-…]`/`[mpep-…]` law ids become `<span class="law">` with their original text. Every emitted text piece goes through `displayText` after slicing.

- [ ] **Step 1: Write failing tests** (`answer-view.test.js`): `parseAnswer("**Hi** there")` is one paragraph with a bold run `Hi` at `start 2` and a plain run ` there`; `"- a\n- b"` is one `ul` with two items; `"1. a\n2. b"` is an `ol`; `"1 * 2 = 2"` and `"a ** b"` keep their asterisks; `"# Title"` loses the `#`; `normalizeReferences` drops overlapping, out-of-range, wrong-type and 60-entry inputs correctly; `renderAnswer(doc, "😀 see claim 1.", [{start: 7, end: 14, kind: "claim", target: "1", label: "claim 1"}], {pills: true, onJump})` produces a button whose text is `claim 1` and, clicked, calls `onJump` once; with `pills: false` the same input has no `button`; a reference after `"a &lt; b "` still covers the right text (offsets are on the raw string); a reference crossing `**` markers renders as plain text; `<img onerror=x>` in the answer appears as literal text and the fragment contains no `img` element; `[usc-103]` becomes a `span.law`.
- [ ] **Step 2: Run** `cd extension && npx vitest run src/answer-view.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** `answer-view.js`; move `displayText` and its `ENTITIES` map; keep `widget.test.js` imports working through the re-export.
- [ ] **Step 4: Run** `npx vitest run src/answer-view.test.js src/widget.test.js`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(extension): render answers as safe formatted text with validated reference pills`.

---

### Task 6: Jump to a reference on the page

**Files:**
- Modify: `extension/src/claimrefs.js` (add `isGrantedPatentNumber`)
- Create: `extension/src/widget/jump.js`
- Test: `extension/src/jump.test.js`, `extension/src/claimrefs.test.js` (extend)

**Interfaces:**
- Consumes: `SECTION_SELECTORS` exported by `extension/src/signals.js`.
- Produces:
  - `isGrantedPatentNumber(patentNumber: unknown): boolean` in `claimrefs.js`: true only for a compact US number whose kind code is `B` or `E` optionally followed by one digit (`US12401720B1`, `US10000000B2`); false for `A1`, `A9`, no kind code, `S`, `P`, other countries and non-strings.
  - `findTarget(doc: Document, ref: Ref): Element | null` in `jump.js`: claim → the leaf `.claim` element whose `num` attribute parses to the claim number; paragraph → `.description-paragraph[num]` whose `num` equals `ref.target` exactly; section → first match of `SECTION_SELECTORS[ref.target]`.
  - `createJumper(doc: Document, options?: { win?: Window, highlightMs?: number, setTimeoutFn?: Function, clearTimeoutFn?: Function }): { canJump(ref): boolean, jump(ref): boolean, clear(): void }`. `jump` scrolls the target into view (`block: 'center'`; `behavior: 'auto'` when `win.matchMedia('(prefers-reduced-motion: reduce)').matches`, else `'smooth'`) and highlights it with inline `outline: 3px solid #f5b400; outline-offset: 2px; background-color: #fff3b0` for `highlightMs` (default 2000). It saves the element's previous `style.cssText` and restores it when the time ends, when `clear()` is called, or when the next `jump` starts; only one highlight is active. Returns `false` (and does nothing, never throws) when the target is missing.

- [ ] **Step 1: Write failing tests:** `isGrantedPatentNumber` for `US12401720B1` true, `US20250119367A1` false, `US6756997B1` true, `EP4517554A1` false, `""`/`null` false. In `jump.test.js` use jsdom pages built from the saved fixtures plus small synthetic markup: each of the three kinds finds its element; a missing target returns `null`/`false`; `jump` calls the (stubbed) `scrollIntoView` with `block: 'center'` and the right `behavior` for both reduced-motion settings; after `highlightMs` (fake timers) the element's `style.cssText` equals what it was before (including a case where it had an inline style); two jumps in a row leave only the second element highlighted and the first restored; `clear()` restores immediately; an element removed from the document before the timer fires does not throw.
- [ ] **Step 2: Run** the two test files. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npx vitest run src/jump.test.js src/claimrefs.test.js src/signals.test.js`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(extension): scroll to and highlight a cited claim, paragraph or section`.

---

### Task 7: Keep keystrokes inside the widget

**Files:**
- Modify: `extension/src/widget/Widget.js`
- Test: `extension/src/widget.test.js`

**Interfaces:**
- Produces: inside the widget's shadow root, `keydown`, `keyup` and `keypress` events reaching the `root` element are stopped with `stopPropagation()` (after the existing Escape handler has run), so no listener on the page's `document` or `window` in the bubble phase sees them.

- [ ] **Step 1: Write failing tests:** with a `keydown` listener on `document` and on `window` (bubble phase), dispatching bubbling, composed `keydown`/`keyup`/`keypress` events for `i`, `w`, `s`, `a`, `d`, `ArrowLeft` on the textarea does not call either listener; the same keys dispatched on `document.body` still do; Enter in the textarea still sends and Escape still closes the panel and focuses the launcher.
- [ ] **Step 2: Run** `npx vitest run src/widget.test.js`. Expected: FAIL on the new tests.
- [ ] **Step 3: Implement** the three listeners on `root`.
- [ ] **Step 4: Run** `npx vitest run src/widget.test.js`. Expected: PASS.
- [ ] **Step 5: Commit** `fix(extension): stop typing in the widget from triggering Google Patents shortcuts`.
- [ ] **Step 6 (record, no code):** jsdom cannot show whether Google Patents listens in the capture phase. Task 10 adds the real-Chrome row. If that row fails, do not improvise a workaround: report it with what the page's listener turned out to be.

---

### Task 8: First-turn chips, one follow-up, one feedback request

**Files:**
- Create: `extension/src/widget/followups.js`
- Modify: `extension/src/widget/Widget.js`, `extension/src/widget/chips.js` (no change expected)
- Test: `extension/src/followups.test.js`, `extension/src/widget.test.js` (rewrite the tests that assume per-answer rating buttons and always-visible chips)

**Interfaces:**
- Consumes: `renderAnswer`, `normalizeReferences` (Task 5), `createJumper`, `isGrantedPatentNumber` (Task 6); the chat response fields `references`, `intent`.
- Produces in `followups.js`:
  - `export const FEEDBACK_AFTER_QUESTIONS = 3`.
  - `pickFollowUp(input: { intent: string, references: Ref[], asked: string[], claimNumbers: number[] }): string | null` with this exact table. Let `n` = smallest claim number in `claimNumbers` greater than the highest claim number among `references` of kind `claim` (use 0 when there are none).
    - `claim_explain`, `claim_scope`: candidates `What does claim ${n} add?` (only if `n` exists), `What do these CPC codes mean?`, `Is this still in force?`
    - `classification`: `Is this still in force?`, `Who cites this patent?`
    - `status_family`: `What do these CPC codes mean?`, `Explain claim 1`
    - `citations_prior_art`: `Explain claim 1`, `Is this still in force?`
    - `legal_opinion_request`: `What does claim 1 cover?`, `Is this still in force?`
    - any other intent (`law_concept`, `navigation_help`, `other`, unknown): `Explain claim 1`, `What do these CPC codes mean?`, `Is this still in force?`
    - Return the first candidate not already in `asked` (compare trimmed, case-insensitive) and, for a claim candidate, whose number exists in `claimNumbers`; otherwise `null`.
- Widget behaviour:
  - The quick-question chip row is visible only until the first question is sent, then hidden for the rest of the page view.
  - After each answer, a single button `.followup` showing the picked question replaces the previous one (hidden when `null`, when busy, or on a non-US page); clicking it sends it as the next question.
  - Answers render through `renderAnswer` with `references` from the response (normalised), `pills: isGrantedPatentNumber(brief.patent_number)` and `onJump` calling the jumper; a disabled pill (`canJump` false) shows the tooltip `Couldn't find this on the page`. `entry.answer` stays the raw string returned (it is what is sent back as history).
  - The per-answer Helpful / Not helpful / Didn't answer buttons are removed. When the number of answered questions reaches `FEEDBACK_AFTER_QUESTIONS` (once, ever, per page view) a feedback block appears under that answer: text `How is this going?`, the three buttons (same labels and aria-labels as today), the optional reason form, and a `Dismiss` button. Rating or dismissing ends it; it never returns. The rating is saved against that latest answer through the existing `rate(entry, …)` path.

- [ ] **Step 1: Write failing tests.** `followups.test.js`: one assertion per table row; an already-asked candidate is skipped; a claim candidate whose number does not exist is skipped; all candidates asked → `null`. `widget.test.js`: chips visible before the first question and absent after it (and after a retry); after an answer a `.followup` button exists and clicking it sends exactly that text; the follow-up is replaced, never duplicated, by the next answer; no rating buttons appear on answers 1 and 2; after answer 3 the feedback block appears once with the three buttons; choosing "Not helpful" with a reason sends one `rating` call carrying the latest `question_id`, the conversation up to that answer and the reason; after answer 4 and 5 it does not return (also after Dismiss); with a `B1` patent a reference in the answer renders a pill and clicking it calls the jumper; with an `A1` patent the same answer has no pill; re-ask signalling still fires as before.
- [ ] **Step 2: Run** `npx vitest run src/followups.test.js src/widget.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** `followups.js` and the widget changes; update the old widget tests that asserted the removed behaviour (do not delete coverage for the rating call itself).
- [ ] **Step 4: Run** `npx vitest run`. Expected: PASS (the whole extension suite).
- [ ] **Step 5: Commit** `feat(extension): chips on the first turn, one follow-up, one feedback request per conversation`.

---

### Task 9: Refined look

**Files:**
- Modify: `extension/src/widget/styles.css`, `extension/src/widget/Widget.js` (header markup only)
- Test: `extension/src/widget.test.js`

**Interfaces:**
- Produces: header shows a patent-number chip `<span class="pn">` with `brief.patent_number` (text only); CSS custom properties on `:host` for colour, radius and spacing used by every rule (no raw hex outside the token block); styles for `.pill`, `.pill:disabled`, `.law`, `.ref`, `.followup`, `.feedback`, lists and paragraphs inside `.text`; `.msg .text` no longer uses `white-space: pre-wrap` (paragraphs come from the renderer); buttons at least 32 px tall; `@media (prefers-reduced-motion: reduce)` removes transitions; visible focus ring kept; all existing roles, labels and button names kept.

- [ ] **Step 1: Write failing tests:** header contains the patent number text; the `style` text contains every `--pa-` token it uses and has no `pre-wrap`; existing accessibility tests (roles, `aria-label`s, focus order) still pass.
- [ ] **Step 2: Run** `npx vitest run src/widget.test.js`. Expected: FAIL on the new assertions.
- [ ] **Step 3: Implement** the CSS and header chip, following the mockup direction A (floating card: dark header with patent chip, grey assistant bubble, blue pills, green section pills).
- [ ] **Step 4: Run** `npx vitest run`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(extension): refine the widget look`.

---

### Task 10: Checklist, docs and final gates

**Files:**
- Modify: `extension/CHECKLIST.md`, `docs/ARCHITECTURE.md`, `docs/DEMO-ASSIST.md`, `README.md` (Patent Assist section, only where it describes the answers, chips or ratings)

**Interfaces:** none.

- [ ] **Step 1: Add a section "K. Widget v2" to `extension/CHECKLIST.md`** (same table format, Result column empty, status line still "NOT RUN") with rows: K1 on `US12401720B1`, a question about claim 1 gives 2 to 4 sentences with no `*`, `#` or `[p…]` text visible; K2 a claim pill scrolls Google Patents to that claim and highlights it for about 2 seconds, then the highlight is gone and the page looks as before; K3 a paragraph pill (`¶NNNN`) does the same for a description paragraph; K4 a section pill does the same for its section; K5 on an application page (`US20250119367A1`) the same kind of question shows no pills and no raw `[section:…]` text; K6 typing `i w s a d` and pressing the arrow keys in the box does nothing to the page, while the same keys outside the box still do (NOT VERIFIED: if this fails record what the page does and stop, see Task 7 step 6); K7 chips show on the first turn only, one follow-up appears after each answer and clicking it asks it; K8 the feedback request appears once after the 3rd question and not again; K9 after `python scripts/set_assist_prompt.py` the console header shows the new version number. Add K to the J-style "not verified" list where relevant.
- [ ] **Step 2: Update `docs/ARCHITECTURE.md` and `docs/DEMO-ASSIST.md`** to state: the response now carries `references`; the citation check covers paragraphs and sections; references are plain text on applications; the crisp prompt is a manual version made by `scripts/set_assist_prompt.py`; the author's one-line instruction to run it. Do not claim answers are better; say they are shorter by construction and unverified until the checklist is run.
- [ ] **Step 3: Full gates.** `python -m pytest -q` (all green apart from the existing skips), `cd extension && npx vitest run && npm run lint && npm run build`, `cd ../frontend && npx vitest run` (unchanged, still green). Report the counts.
- [ ] **Step 4: Commit** `docs: checklist, architecture and demo notes for widget v2`.
- [ ] **Step 5 (author, outside this plan):** `python scripts/set_assist_prompt.py` against the demo database, rebuild the extension with the key and origin, reload the extension and run section K in real Chrome.
