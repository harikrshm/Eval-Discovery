# Demo script: Patent Assist

A three-minute talk track for Patent Assist, the assistant that lives inside a product's own page (Google Patents) and improves through the Eval Discovery loop behind a human gate. Offer it as **"here is the loop, and here is the evidence the console shows"**. Do not offer it as "watch the answer get better": the code cannot guarantee that, and the one real run with a real Groq key (2026-11-23) did not show the flywheel improving the assistant (see below).

Three minutes is the **target**, reached only by staging the slow parts beforehand (see [Staging](#staging-the-slow-parts)). The live version has never been timed, and the Evolution steps start background jobs that each take real model calls.

## Read this first

Say these things about the state of the work, before or during the demo, whichever fits. They are all true at the time of writing.

- **The law pack is a draft.** The 12 provisions (`backend/core/assist/lawpack/*.md`, each `status: draft`) are hand-written summaries, not statute text, and have had no legal review. They are a draft until the author has reviewed them. Review them before this demo. Until then, do not present a law-citing answer as legal information, and do not choose a demo question that depends on a provision being exactly right.
- **The extension checklist has not been run.** `extension/CHECKLIST.md` is for the author to run in real Chrome, and it is marked NOT RUN. Everything the extension does on live Google Patents pages is verified only against four saved, trimmed pages and in jsdom. If you have not run the checklist yourself, you are the first person to see this in real Chrome, so rehearse.
- **The imported data is a demo dataset.** The page views and questions in the Adoption tab come from `examples/assist/page_views.example.jsonl` (12 page views, 17 questions, 5 flagged conversations), which the author wrote by hand. The console labels it "demo dataset". It is not usage, and the version comparison deliberately ignores it.
- **Nothing is pushed or deployed.** This runs locally, on the branch `feat/patent-assist`.
- **One real run was made, on 2026-11-23, and the fixes after it were not re-run.** 14 chat calls with the real Groq models on 3 trimmed fixture pages (not live Google Patents). My judgement of the first 12 answers, before the fixes: 5 correct, 1 mostly correct, 3 partly correct, 3 wrong (list and table answers: cited-by, prior art, CPC codes). The extractor matched its committed expected output. The flywheel did not improve the assistant: the discovery path found a pattern about legal-opinion refusals in imported demo runs whose tests already passed, so it suggested no fix, and the manual-criterion path produced a v2 that failed verification for a judge-blind-spot reason that was then fixed but not re-verified. The insights brief failed its own grounding check. The defects were fixed afterwards and are covered by offline tests only. **A second real run after the fixes has not been done.** Do not claim the flywheel improves Patent Assist answers. The Phase 4 simulation did not show the loop reducing preview runs, and that story is unchanged.
- **Widget v2 has not been run in real Chrome either.** The reference pills, the follow-up button, the feedback card, keystroke isolation and the shorter prompt are tested in jsdom and with faked model calls only; `extension/CHECKLIST.md` section K is the author's to run. The new prompt asks for shorter answers (at most four short sentences); that is an instruction, it is unverified until the checklist is run, and it is not a claim that answers are better.
- **Not exercised in the real run:** a 429 from the provider and adversarial input (injection).
- **Frontend focus behaviour and the cross-tab behaviour** of the console (a second tab following connect and disconnect) are checked only in jsdom, not in a real browser. Keep to one tab.

## Setup checklist

Do this before the call, not during it. The longer procedure, with checks, is `extension/CHECKLIST.md`; the short form is in the README's Patent Assist section.

- [ ] **Backend running** from the repository root: `alembic upgrade head` (applies `0004` and `0005`), `python scripts/setup_assist.py` once (it prints the **demo workspace token once**; keep it somewhere private, and do not show it on screen), then `.env` with `ASSIST_AGENT_ID`, `ASSIST_KEY` and `GROQ_API_KEY`, then `uvicorn backend.main:app`. Keep the `.env` file closed on screen.
- [ ] **Crisp prompt live.** The live prompt is a database prompt version, not the file. Run `python scripts/set_assist_prompt.py` (same database as the backend) once; it prints "Created prompt version N and made it current." or "Prompt already current (version N).". The console header then shows that version.
- [ ] **Extension built and loaded.** In `extension/`: set `VITE_ASSIST_KEY` (the same value as `ASSIST_KEY`) and, if your backend is not on `http://127.0.0.1:8000`, `VITE_BACKEND_ORIGIN`, then `npm run build`. In Chrome: `chrome://extensions`, Developer mode, **Load unpacked**, `extension/dist`. After any rebuild reload the extension and the patent tab.
- [ ] **Example data imported:** `python scripts/import_page_views.py examples/assist/page_views.example.jsonl`. It is idempotent, so importing again is safe.
- [ ] **Frontend running:** `cd frontend`, `npm run dev`, http://localhost:5173. Connect once before the call to check the token works, then disconnect.
- [ ] **Two patent tabs ready:** `https://patents.google.com/patent/US12401720B1/en` and `https://patents.google.com/patent/US6756997B1/en` (the pages the checklist uses). Open each once beforehand so the page has loaded fully, and confirm the **Patent Assist** button shows at the bottom right.
- [ ] **Groq pacing.** On Groq's free tier the 120B model is limited to about 8000 tokens per minute. Set `GROQ_TPM_LIMIT=7000` in `.env` and restart the backend, and leave about a minute between live questions.
- [ ] **Stage the Evolution steps** (next section), and **rehearse the whole thing once** with your real key.

### Staging the slow parts

The Adoption tab is instant. The Evolution tab is not: checking for a pattern, discovering a criterion, building tests, suggesting a fix and verifying it are each model calls in the queue, and they can hit the rate limit. Do them beforehand, in the same demo workspace, and say plainly that you staged them. Do not pretend a staged state was produced live. A sensible staging: ask three questions live on a patent page, then click **Not helpful** on the "How is this going?" card (it appears only after the 3rd answered question), so there is a live flagged conversation; open **Evolution**, **Check flagged conversations for a pattern**, then **Discover evals**; wait for the tests; **Suggest fix** on a failing fix test; and stop with the suggested fix **waiting for review** (or, if you want to show a finished table, approve it and let it verify). If the pattern check finds nothing, or the suggested fix fails verification, that is a valid outcome, not a failed rehearsal: see [If the model misbehaves](#if-the-model-misbehaves).

## The talk track

Opening line: "Products with rich pages are hard to learn. This is an assistant inside Google Patents that answers questions about the patent you are reading. It also tells the product builder where people get stuck, and it can propose fixes to itself, but a human approves every change."

### 1. Ask on a real patent (0:00 to 0:45)

- **Click:** on the US12401720B1 tab, the **Patent Assist** button, then the chip **Explain claim 1**. Wait for the answer. The chips show on the first turn only; after the answer one follow-up button appears. Then type `Can I sell a product like this without getting sued?` and send.
- **Say:** "It reads the page I am on and answers from it; I would expect it to point at the claim text, but I am not promising a quote. The legal question gets no verdict. It explains what the claims say and ends with a fixed sentence: this is not legal advice. That is a keyword rule plus the system prompt, so a phrasing it misses depends on the model following its prompt."
- **Expect, by the code and tests, not confirmed in real Chrome:** an answer that cites claim numbers that exist on the page; for the legal question, an answer without a yes or no that ends with "This is not legal advice, and I can't judge validity or infringement." If the answer cites a claim or law id it was not given, the extension shows "Some citations in this answer could not be verified against the page." The check covers claim numbers, law ids, `[pNNNN]` paragraph markers and `[section:NAME]` tokens. Claim, paragraph and section references in the answer are returned by the backend as `references` and shown as clickable pills on granted patents only (plain text on applications); a pill scrolls the page to that claim, paragraph or section and highlights it for about 2 seconds. US12401720B1 has paragraph numbers, so it can show paragraph pills; the other saved pages do not. Say that this check is deterministic code. If you chose a question that cites a law excerpt, say the law pack is a draft.

### 2. Rate (0:45 to 1:05)

- **Click:** there are no per-answer rating buttons. After the **third** answered question one card, "How is this going?", appears under that answer, once per conversation. Ask a third question (use the follow-up button if you like), then click **Not helpful** on the card, type a reason (for example `missed the point`) and **Send feedback**. (**Dismiss** hides the card for good.)
- **Say:** "A thumbs-down, a 'didn't answer', a re-ask or a failed citation check saves the conversation, with a snapshot of the page it saw, as a flagged conversation. The assistant's own prompt is not touched by this."
- **Expect:** "Thanks for the feedback." The rating is stored against the newest answer together with the whole conversation. The conversation appears in the console's flagged list after **Refresh data**.

### 3. The console shows the pattern (1:05 to 1:50)

- **Click:** in the app at http://localhost:5173, **Assist console**, paste the demo workspace token, **Connect**. On **Adoption**, point at the amber note first, then at the help-demand table, the re-ask rates by task, a hotspot button, and the flagged conversations it filters.
- **Say, about the label, before any number:** "This panel is a hand-written demo dataset (12 imported page views; any live rows from my own questions are counted with them, and the source counts show the split). The UI says so, in this amber note and in the source counts. It is not measured usage, and the version comparison below it counts only live questions, so it will say that versions cannot be compared yet. I would not show you a made-up trend as a result."
- **Say, about the data:** "These are signals about Google Patents, not about my widget: which sections people actually look at, where they ask for help, which kinds of questions get re-asked. Click a hotspot and you see the conversations behind it." Optionally **Generate insights**: a model writes a short summary from the aggregates only, and the console shows the figures it used; if a number cannot be matched it says "Check these figures". Do not read the insights out as findings.
- **Expect:** counts and rates, never question or answer text, in the metrics and the flagged list.

### 4. A fix, through the gate (1:50 to 2:35)

- **Click:** **Review in Evolution** on a flagged conversation (or the **Evolution** tab). Show the criterion that was discovered from the flagged conversations, its tests, then the staged **suggested fix**: the line diff, the failing turn quoted by code, and "why this change". Click **Approve and verify** (or open the staged, already verified version with its before and after table).
- **Say, the human gate:** "Nothing changes the assistant until a person reads the diff and approves. After approval it goes live only if code, not a model, finds that the fix test passes, a held-out test passes, and nothing that passed before now fails. A suggestion can fail that, and then chat never leaves the old prompt. The suggester never sees the held-out test, and it sees an evidence summary of the conversation, not the page text. It may flag a possible gap in the law pack, which only I can change."
- **Say, the evidence:** "The measured evidence is this replay table: the same tests, asked through the same code path the extension uses, under the old and the new prompt version. It is a signal, not proof: a model judges it and the sample is a handful of tests."
- **Expect:** a before and after table and a status; either outcome (promoted or failed verification) is a normal result.

### 5. The next answer, and the close (2:35 to 3:00)

- **Click:** back on the patent tab, ask the same question again, then in the console note the assistant's current version in the header. Warning: asking the same kind of question again within 90 seconds of the previous answer counts as a **re-ask** and flags the conversation, so wait longer than that (or ask a different kind of question) unless a flag is what you want to show.
- **Say:** "If the fix was promoted, the next answer is produced under the new prompt version. Whether it is better is something I judge by reading it and by the replay table, not something I will promise. I have not yet run this loop with a real model on Patent Assist; that run is separate and I will report it as it is. What exists today is the pipeline, tested with faked model calls, and the console that shows its evidence."
- **Close, one honest sentence about limits:** "The law pack is a draft until I review it, the extension has not been through its real-Chrome checklist, and the example data is labelled as demo data."

## If something goes wrong

### Google Patents has changed its layout

The extractor was written against pages saved in 2026 and has not been re-checked against the live site. Symptoms: no **Patent Assist** button, a button but an answer that says part of the page is missing, or wrong claims.

- Try the other tab (US6756997B1), and reload the page. The extension waits until the page's number, title and claims match the URL, so give it a few seconds.
- If the answer says a section is missing, say so: "The extension reports which sections it could not read, and the assistant is told, so it says so rather than guessing. That is the completeness check working." Do not call it a bug fix.
- If the button never appears, say: "Google changes this page, and I only checked my selectors against pages I saved on one date. This is exactly what the manual checklist in `extension/CHECKLIST.md` is for." Then move to step 3: the console and the Evolution tab do not depend on the extension, because they run on the imported demo data (still labelled as such). Keep the saved fixtures in mind as the proof of what the extractor does: `cd extension && npx vitest run`.

### Groq is rate limited

On the free tier the 120B model allows about 8000 tokens per minute (the extension's answers use the 20B chat model, whose limit this repository does not state, and the judge, suggester and insights use the 120B model).

- In the widget you will see "Too many requests. Please wait a moment and try again." or "The assistant could not answer right now. Please try again." with a **Retry** button. Wait about a minute and click **Retry**. Say: "Free-tier limit; the backend retries twice and then says so."
- Ask one or two live questions and no more, and use `GROQ_TPM_LIMIT=7000`.
- In the console, a job that cannot run says "The judge was unavailable. Try again." Press **Retry** on the row after a pause. Insights can answer "The insights could not be generated right now. Please try again."; skip that button if it keeps failing, it is optional.
- Fall back to the staged state for step 4: show the finished suggested fix or the before and after table you staged, and say it was staged.

### The backend is down

- The widget shows "The assistant could not answer right now. Please try again." with **Retry**; the page is unaffected. The console shows "Couldn't load the console: ...". Start the backend (`uvicorn backend.main:app`), click **Retry** in the widget and **Refresh data** in the console.
- If it will not start, you can still show what runs offline: `python scripts/import_page_views.py examples/assist/page_views.example.jsonl --dry-run` (no database needed; it prints "12 page views, 17 questions, 5 flagged conversations"), `npx vitest run` in `extension/` (468 tests), and the architecture section [docs/ARCHITECTURE.md, section 10](ARCHITECTURE.md#10-patent-assist). Say plainly that the live demo was not available.

### Smaller problems

- **The console will not connect.** The token is printed once and is not stored. If you lost it, `python scripts/setup_assist.py --force` creates another assistant agent and workspace (and a new token); update `ASSIST_AGENT_ID`, restart the backend and import the example file again.
- **"Assist is not configured."** The extension was built without `VITE_ASSIST_KEY`. Rebuild with it and reload.
- **Every question fails after a rebuild.** The key in the extension must equal `ASSIST_KEY` on the backend, with no surrounding whitespace.

## If the model misbehaves

Models vary from run to run. Do not hide it.

- **An answer is longer than four sentences, or shows raw markers.** The prompt asks for at most four short sentences, but that is an instruction the model may not follow, and the legal-opinion rule (list the claim's elements, then the fixed sentence) can pull against it. Say so; do not claim the new prompt makes answers better, only shorter by instruction.
- **A pill does nothing or is greyed out.** A pill whose target is not on the live page is disabled ("Couldn't find this on the page"); paragraph pills need the page to carry paragraph numbers. Say it is unverified in real Chrome.
- **A list answer is wrong or partial** (cited-by, prior art, CPC codes). This was the weakest area in the real run. The slice fitting was fixed afterwards and not re-run with the real model, so say "a list can be cut to fit and the assistant is told, but I have not re-verified it with the real model" and open the page's own section.
- **Two near-identical questions get different answers.** Seen in the real run (a wrong and a right answer on one page). Known and not fixed; say so.
- **The insights brief says its figures could not be matched, or that it was cut off.** The grounding check is best effort and the brief is flagged when the model stops early; say that, and read the figures against the counts shown.
- **The assistant gives a verdict on a legal question, or leaves out the fixed sentence.** The sentence is appended by code for the intents the keyword rules catch, but a phrasing the rules miss depends on the model. Say so, and say it is a known limitation of keyword rules.
- **A cited claim is wrong or unsupported.** The check catches claim numbers, law ids, `[pNNNN]` paragraph markers and `[section:NAME]` tokens that do not exist in what the model was given. It does not check whether a correct citation supports the point.
- **No pattern is found, or the discovered criterion is not the one you expected.** The criterion is a model's reading of the flagged conversations, and the panel lets you edit it. Say: "Discovery proposes, I decide."
- **A suggested fix fails verification.** This is a designed outcome. Say: "The held-out test or a previously passing test caught it, and the assistant never left the old prompt."
- **The replay table does not show an improvement.** Say what it shows. Do not explain it away.

## What to claim and what not to

| You can say | You cannot say |
|-------------|----------------|
| The pipeline is built and tested with faked model calls, and was run once with real models (2026-11-23), which found defects that were then fixed and tested offline only | The flywheel improves Patent Assist answers (the one real run did not show it, and the fixes were not re-run with a real model) |
| The console shows counts, rates and a replay table, and labels demo data as such | The Adoption numbers are real usage, or that imported data measures an improvement |
| A human approves every change, and promotion is a code-written rule | The assistant evolves on its own |
| The law pack is a draft the author must review | The law pack is correct or reviewed |
| The extension is verified against saved pages and in jsdom, with a manual checklist for real Chrome | The extension works on live Google Patents (until the checklist has been run and recorded) |
| The new prompt asks for shorter answers, and the references and pills are tested with faked calls and in jsdom | The answers are better, or the pills work on live Google Patents (until checklist section K has been run and the answers read) |
| Strategy-document figures are illustrative | Any measured adoption or revenue figure; none exists here |
