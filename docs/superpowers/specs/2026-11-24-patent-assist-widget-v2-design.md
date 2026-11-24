# Patent Assist widget v2: design

Status: draft for the author's approval. Project 1 of 2 (the console is `2026-11-24-patent-assist-console-v2-design.md`). Builds on `2026-11-22-patent-assist-design.md`; where this document is silent, that one holds.

## Intent and why

The widget works (real-Chrome checks passed on 2026-11-23) but is not demo-ready. Answers are long and quote the page at length, the model's markdown (`*`, `**`) shows as raw characters, typing in the box triggers Google Patents' own shortcuts (I, W/S, A/D), and an answer cannot take the reader to the spot on the page it talks about. This is a portfolio piece: it should read as a polished product, and "click a reference, the page moves there" is the moment that sells it.

## Goals

1. **Crisp answers.** 2 to 4 sentences, at most one short quote, plain wording; bullets only when the question asks for a list (at most 5 items). No preamble, no restating the question, no markdown characters on screen.
2. **Clickable references that move the page, on granted patents.** On a granted US patent (kind code B1, B2 and similar), a reference in an answer (a claim, a description paragraph, a page section) is a pill; clicking it scrolls Google Patents to that spot and highlights it briefly. On a published application (kind code A1, A2, A9 and similar) references stay plain text: the author's decision, because application pages have no paragraph numbers.
3. **No shortcut collisions.** Typing in the widget never triggers a Google Patents shortcut, and the page's own keys keep working outside the widget.
4. **Refined floating card** (direction A from the mockups): header with the patent number, pills inside the sentence, calmer spacing and type.
5. **Quieter conversation controls.** Quick-question chips only on the first turn; on later turns, one suggested follow-up chosen by code; feedback asked once per conversation, not per answer.

## Non-goals

- A docked side panel, a one-answer-at-a-time layout, saved chat history across page loads.
- Model-generated follow-ups (code picks them; see Constraints).
- Row-level links for CPC codes, cited patents, family members or legal events: those link to their section only.
- Clickable references on published applications (plain-text references only).
- Dark mode, mobile layout, localisation.

## Anti-goals (failure even if it "works")

- A link that goes nowhere or to the wrong place, presented as a working link.
- A reference the page does not contain shown as if it were real.
- The widget changing the patent page in any lasting way (only a scroll and a temporary highlight, fully undone).
- Any model output inserted as HTML.

## Constraints

- Shadow DOM stays closed; everything shown is built with `textContent` and DOM nodes, never `innerHTML`. Markdown handling is therefore a small DOM-building renderer, not a library that emits HTML.
- Every reference is validated by code before it becomes a pill, the same way claim numbers and law ids are today (`backend/core/assist/citations.py`). An unvalidated reference stays plain text and keeps the existing "could not be verified" note.
- The assistant's live prompt is a prompt version stored in the database (the artifact the improvement loop edits), not the file `backend/core/assist/system_prompt.md`. The "crisp" prompt therefore ships as a new, author-approved prompt version of the demo assistant (and as the new initial prompt in `system_prompt.md` for fresh setups). It must not bypass the version history.
- Groq's free tier allows about 8000 tokens a minute on the 120B model: no extra model call per answer. Follow-up suggestions come from code.
- Per-answer rating stays in the API (`/assist/ratings`, re-ask and citation signals unchanged). Only when the widget asks changes. The improvement loop still receives the whole conversation transcript with each flagged rating.
- Existing suites stay green (backend, extension vitest, frontend). The manual real-Chrome checklist (`extension/CHECKLIST.md`) gains rows for everything below that only real Chrome can show.

## Approaches considered (reference links)

1. **Claims and sections only.** Cheapest and already validated, but cannot point at a description paragraph, where many answers come from.
2. **Quote matching.** The widget searches the page for text the model quoted. Needs no capture changes, but a paraphrased quote gives a dead link and code cannot know in advance.
3. **Numbered anchors (chosen by the author).** The page capture tags claims, description paragraphs and sections with ids; the model cites ids; the backend validates them; the widget maps each id to a place on the page. Links cannot be invented. Most work: page capture, size caps, prompt, citation check, tests.

## Design

**Anchors.** The extractor already reads claim numbers; the saved page US12401720B1 shows claims with `id="CLM-00001" num="00001"`, description paragraphs with `num="0001"` and sections with ids such as `#claims`, `#description`, `#classifications`. The extractor will keep paragraph numbers with the description text it puts in the brief, so the model sees which paragraph each passage belongs to. Section anchors are fixed names (claims, description, classifications, citations, family, legal events, landscapes).

**Citing.** The prompt tells the model to cite claims as it does now and paragraphs and sections with a short, ASCII, bounded token validated like a law id. The exact token syntax is the builder's call. The backend is the only parser: it returns the answer plus the list of validated references, each with its start and end position in the returned answer string and its target (a claim number, a paragraph number or a section). The widget never parses the answer for references; it slices the returned string at those positions (before it undoes the `&lt;`/`&gt;`/`&amp;` escaping) and turns each validated occurrence into a pill labelled for people ("claim 3", "¶0042", "Description"). A range or list such as "claims 1-3" is one pill that jumps to the first claim named. Law ids such as `[usc-103]` are not page targets: they are shown as a plain, non-clickable tag. This adds a field to the chat response, so the extension, backend and console tests that rely on the response shape change with it.

**Clicking a pill.** The widget finds the target on the live page (claim by number, paragraph by number, section by id), scrolls it into view (no animation when the user prefers reduced motion) and highlights it for about two seconds, then removes the highlight. Pills exist only when the patent number's kind code marks a granted patent; on a published application the answer's references are shown as plain text, validated and flagged exactly as today, and nothing is clickable. If a pill's target is not on a granted patent's page (the page differs from the capture), the pill is disabled with the tooltip "Couldn't find this on the page" and nothing else happens.

**Answer rendering.** A renderer for a tiny markdown subset (paragraphs, `**bold**`, `*italic*`, `-`/`*` bullets, numbered lists) builds DOM nodes; anything else is shown as text. The prompt also asks for plain text, so the renderer is a safety net, not the main fix. The existing display unescaping of `&lt;`, `&gt;` and `&amp;` stays.

**Crisp prompt.** New rules replace "back each point with a short quotation": answer first; at most four sentences or about 80 words; at most one short quote and only when it carries the point; no markdown; citations inline as references, not as quoted passages; keep the legal-opinion rules and the fixed sentence unchanged. Delivered as described in Constraints. A lower reply token cap is a possible safety net; the builder must handle a cut-off reply (the existing finish-reason signal) without showing half a sentence as complete.

**Keyboard.** Key events originating in the widget are stopped before the page's handlers see them. The builder must test against the real Google Patents page: if the page listens early (capture phase), the widget must register its own earlier listener. Acceptance: typing `i w s a d` and the arrow keys into the box does nothing to the page, and the same keys outside the box still do what the page does.

**Conversation controls.**
- Chips ("Explain claim 1", "What do these CPC codes mean?", "Is this still in force?") show only while the conversation is empty.
- After each answer, one suggested follow-up button, picked by code from the answer's validated references and its question type (for example after a claim-1 answer, "What does claim 2 add?" when claim 2 exists; after a classification answer, a section the user has not asked about yet). It never repeats a question already asked and never names a claim or section the page lacks. Clicking it sends it as the next question. The mapping table is the builder's call.
- Feedback: after the 3rd question of a conversation, once, the widget shows "How is this going?" with Helpful / Not helpful (optional reason) / Didn't answer my question. The rating is saved against the latest answer through the existing rating call. Once rated or dismissed it is never shown again on that page view. The number 3 is one named setting.

**Look.** Refined floating card: patent-number chip in the header, consistent spacing scale, type and colour tokens in one place, visible focus rings, 44 px touch targets for the buttons, reduced-motion respected, all existing roles and labels kept. Colour and spacing details are the builder's call within these rules.

## Testing and honesty

- Unit tests: markdown renderer (including hostile input), reference validation (valid, invented, out of range, malformed), pill-to-target lookup, follow-up picker (no repeats, no invented targets), feedback timing (once, after the 3rd question, never again), key-event stopping.
- Backend tests: paragraph and section citation validation; the new prompt version is created through the version mechanism; answers keep the fixed legal sentence.
- Manual checklist rows, real Chrome only: pills scroll and highlight for a claim, a paragraph and a section; shortcut keys do nothing in the box; chips vanish after the first question; one follow-up appears; feedback appears once.
- Known risk, not yet verified: paragraph numbers exist on the saved granted-patent page US12401720B1 and not on the saved application page US20250119367A1 (which matches the author's observation), but no live granted-patent page was checked. A pill whose target is missing is disabled, as described above.
- No claim that answers are "better" without reading them and, where possible, the replay table; "shorter" can be measured, "better" is a judgement.

## Left to the builder

Reference token syntax; the highlight mechanism; the follow-up mapping table; exact wording of fixed messages; colours, spacing and type; the setting's name and location; how the new prompt version is seeded.

## Calls I made (open to change)

- Feedback after the 3rd question, once per conversation (the author asked for once per conversation; 3 is my choice).
- Follow-ups from code, not the model, because of the token limit and so that nothing invented can appear.
- Row-level links only for claims and description paragraphs; other sections link to the section. All links exist on granted patents only (the author's decision, 2026-11-24).
- The crisp prompt ships as an author-approved new prompt version rather than an automatic flywheel promotion. It is created as the **active** version of the demo assistant (the author asked for crisp answers; a merely proposed version would leave the demo answering at the old length), with a note that it was set by the author and not by the loop, and `system_prompt.md` is updated in the same step so fresh setups start from it. The widget still has to render old-style answers correctly, because versions can be restored.
- References are parsed once, by the backend, and the widget uses its positions (see Design, Citing); a range is one pill; law ids are plain tags, not links.
