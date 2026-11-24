Status: NOT RUN. The build agents could not run a real browser; the author runs this checklist.

# Patent Assist: manual verification checklist

How to record: in each table, write PASS, FAIL or SKIP (with the date) in the Result column and what you saw in Notes. Items marked NOT VERIFIED have never run in real Chrome; they are the likeliest to fail (see section J). Section K (Widget v2) is new and has never run either; its keystroke and highlight rows are the likeliest to fail.

Backend origin used here: `http://127.0.0.1:8010` (no path). Port 8000 may already be your dev server.

## Setup

Run from the repo root in PowerShell. Use the same shell for S2 to S6 so `DATABASE_URL` is the same for all of them.

Working directory: S1 to S6, the `.env` edits and the database check run from the repo root. S7 starts with `cd extension` and the window stays there, so the build, G2, H2 and H3 run from `extension`. Paths in the tables are written from the repo root (`extension\dist\...`) unless they say `dist\...`, which is from `extension`. After S7, use `cd ..` to return to the repo root.

- **S1.** Install backend dependencies (once): `pip install -r requirements.txt`
- **S2.** Choose a database and set `DATABASE_URL` in the shell. Do not rely on a `.env` line for it.
  - Option A, SQLite (default): leave `DATABASE_URL` unset. The app uses `sqlite:///./dev.db` in the folder it starts from.
  - Option B, Postgres: `docker compose up -d postgres`, then `$env:DATABASE_URL="postgresql://postgres:postgres@localhost:5432/eval_discovery"`. A `postgres://` URL is also accepted.
- **S3.** Migrate: `alembic upgrade head`
- **S4.** Create the demo workspace and agent: `python scripts/setup_assist.py`. It prints `Patent Assist agent created.`, then `Workspace token: ...`, `Agent id: ...` and an `ASSIST_AGENT_ID=` line. **The token is printed once and never stored. Keep it private; do not paste it into a chat or a commit.** The extension does not need it. Run the script once: a second run refuses unless you pass `--force`, which creates another agent.
- **S5.** Edit the backend `.env` at the repo root (create it from `.env.example` with `Copy-Item .env.example .env` only if you have none). Set:
  - `GROQ_API_KEY=` your Groq key (answers need it)
  - `ASSIST_AGENT_ID=` the id from S4
  - `ASSIST_KEY=` generated as below. Delete any existing `ASSIST_KEY=` line first, so there is exactly one.

  **Warning about the key.** Anything you type on the command line is saved to PowerShell's history file. So never type the key: generate it into a variable and write it straight to `.env`, as shown. Nothing is printed.

  ```powershell
  $chars = [char[]]'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
  $bytes = New-Object byte[] 48
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $env:VITE_ASSIST_KEY = -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
  Add-Content -Path .env -Value ("`nASSIST_KEY=" + $env:VITE_ASSIST_KEY) -Encoding ascii
  ```

  The leading `` `n `` starts a new line, in case the existing `.env` has no trailing newline; a blank line in `.env` is harmless.

  Use the same PowerShell window for S5 and S7, so the value is still in `$env:VITE_ASSIST_KEY`. In another window, from the repo root, recover it into the variable without printing it:

  ```powershell
  $line = Get-Content .env | Where-Object { $_ -like 'ASSIST_KEY=*' } | Select-Object -Last 1
  $env:VITE_ASSIST_KEY = $line.Substring(11)
  ```

  Clear your history afterwards. `Clear-History` clears only the current session. To stop saving history in this session, run `Set-PSReadLineOption -HistorySaveStyle SaveNothing`. To remove lines already saved, run `notepad (Get-PSReadLineOption).HistorySavePath` and delete any line containing the key. Do not paste the key into a chat.

  `.env` is git-ignored. Never commit it.
- **S6.** Start the backend on a free port: `uvicorn backend.main:app --port 8010`. Leave it running and look for `Uvicorn running on http://127.0.0.1:8010`. Start it after S5, because the app reads `.env` at startup.
- **S7.** Build the extension, in the S5 window (or after recovering `VITE_ASSIST_KEY` as in S5):

  ```powershell
  cd extension
  npm install
  $env:VITE_BACKEND_ORIGIN = "http://127.0.0.1:8010"
  npm run build
  ```

  `npm install` is needed on a fresh clone because `extension/node_modules` is git-ignored. `VITE_BACKEND_ORIGIN` must be an origin (scheme, host, port); a path makes the build fail. `VITE_ASSIST_KEY` is compiled into `dist/background.js` and must equal the backend's `ASSIST_KEY`.
- **S8.** Load in Chrome: open `chrome://extensions`, turn on Developer mode, click Load unpacked and choose the `extension\dist` folder in the repo. After every rebuild, reload the Patent Assist card and reload each patent tab.
- **S9.** Rate limit: the project's docs give 8000 tokens per minute as the Groq free-tier limit for `gpt-oss-120b` (`docs/ARCHITECTURE.md`, "Groq's free tier"). The assist chat uses `openai/gpt-oss-20b` (`MODEL_CHAT` in `backend/groq_client.py`), whose limit is not stated there. Neither figure was checked against Groq's current page, so check your account's limits. Wait about a minute between questions. On "Too many requests. Please wait a moment and try again." wait and retry. Optionally add `GROQ_TPM_LIMIT=7000` to `.env` (example value) so the backend paces its model calls, then restart S6.

**Database check (read only; used in C, D and F).** Run it in the S2 shell, from the repo root. It only SELECTs and never prints the key or the token.

```powershell
@'
import os
from sqlalchemy import text
from backend.db.session import make_engine, DEFAULT_DATABASE_URL

QUERIES = [
    ("assist_question", "SELECT id, intent, rating, rating_reason, reasked, patent_number, session_id FROM assist_question ORDER BY created_at DESC LIMIT 10"),
    ("assist_page_view", "SELECT session_id, patent_number, dwell_ms, bounce, sections FROM assist_page_view ORDER BY updated_at DESC LIMIT 10"),
    ("run (source = assist)", "SELECT id, session_id, position, source FROM run WHERE source = 'assist' ORDER BY created_at DESC LIMIT 10"),
]
engine = make_engine(os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL)
with engine.connect() as conn:
    for label, sql in QUERIES:
        print("==", label)
        for row in conn.execute(text(sql)):
            print(tuple(row))
engine.dispose()
'@ | python -
```

Test pages: US12401720B1 `https://patents.google.com/patent/US12401720B1/en` and US6756997B1 `https://patents.google.com/patent/US6756997B1/en`.

## A. Installation and permissions

| ID | Steps | Expected result | Result | Notes |
|---|---|---|---|---|
| A1 | Run S7 | `npm run build` ends with no error. `extension\dist` holds `background.js`, `content.js` and `manifest.json`. | | |
| A2 | Load unpacked (S8) | The Patent Assist 0.1.0 card appears with no error shown on it. | | |
| A3 | Open the card's Details and read the site access / permissions part | Only patents.google.com and 127.0.0.1:8010 are listed (wording varies by Chrome version). No "tabs" or "storage" permission. | | |
| A4 | Open `extension\dist\manifest.json` | `host_permissions` is exactly `https://patents.google.com/*` and `http://127.0.0.1:8010/*`. | | |
| A5 | NOT VERIFIED: host permission with a port. Repeat A2 and A3 | The card loads with no manifest error and port 8010 is listed. If Chrome refuses it, record the error text (see J7). | | |

## B. US patent page

| ID | Steps | Expected result | Result | Notes |
|---|---|---|---|---|
| B1 | Open US12401720B1 and wait until the claims are on screen | A "Patent Assist" button sits at the bottom-right corner, 16 px from the edges. The panel is closed. | | |
| B2 | Click the Patent Assist button | Panel "Patent Assist" opens with the patent number (`US12401720B1`) in the header next to the title, close button "×", the text box (placeholder "Ask about this patent", accessible name "Your question"), chips "Explain claim 1", "What do these CPC codes mean?", "Is this still in force?" (first turn only: they disappear once any question is sent), and the footer. The launcher hides while the panel is open. Focus moves to the text box. | | |
| B3 | Type `What does claim 1 cover?` and press Enter | "Thinking…" shows, then a short answer that cites claim numbers (for example "claim 1") and uses the page's wording. No error text. The chips are gone and one follow-up button (a suggested next question) appears under the conversation. | | |
| B4 | Chips show on the first turn only, so for each of the three chips in turn: reload the tab, open the panel, click that chip and wait for the answer | Each chip sends exactly its label as your message and gets an answer. After the answer the chips are gone and one follow-up button is shown. The status answer uses the page's status and dates and does not say whether the patent is valid. | | |
| B5 | Type a line, press Shift+Enter, type more, press Enter | Shift+Enter adds a new line without sending. Enter sends. | | |
| B6 | Select a sentence in the Description, click the text box, type `explain simply`, press Enter | A row reads `Selected on the page: "<sentence>"` with a Clear button. The sent message is `Explain this passage: "<sentence>" explain simply`. The row disappears after sending. | | |
| B7 | Select text, click the text box, click Clear | The row hides. The next plain question is not quoted. | | |
| B8 | Press Escape while focus is in the text box | The panel closes and the Patent Assist button is shown and focused. | | |
| B9 | Reopen the panel (with at least one answer in it) and Tab through it | Tab order is as in I1: close, answer pills, the follow-up button, then the text box and Send. Each control shows a focus ring. | | |
| B10 | Look at the bottom of the panel | The footer reads "Not legal advice. Answers are based on the text of this page." | | |
| B11 | Ask `Can I sell a product like this without getting sued?` | No yes/no verdict about selling, infringing or validity. The answer discusses the claims in general terms and contains the sentence "This is not legal advice, and I can't judge validity or infringement." A citation warning may follow it. | | |
| B12 | Repeat B1 to B3 on US6756997B1 | Same behaviour. The answer refers to the claims of 6756997B1, not 12401720B1. | | |

## C. Ratings and re-asks

There are no per-answer rating buttons. One card, "How is this going?", appears once per conversation under the 3rd answered question (see K8). A rating is stored against the newest answered question (the 3rd) together with the whole conversation. Each of C1 to C3 and C7 therefore needs a fresh conversation: reload the patent tab (so the card can appear again), ask three questions (typed, or with the follow-up button), wait for the 3rd answer, then use the card. Wait about a minute between questions (S9).

| ID | Steps | Expected result | Result | Notes |
|---|---|---|---|---|
| C1 | Fresh conversation; after the 3rd answer, click "Helpful" on the "How is this going?" card | The card's buttons (Helpful, Not helpful, Didn't answer my question, Dismiss) become disabled and "Thanks for the feedback." appears. The reason box stays hidden. In the DB check, the 3rd question has `rating` `up`. | | |
| C2 | Fresh conversation; after the 3rd answer, click "Not helpful", type `missed the point` in "What was wrong? (optional)", click "Send feedback" | "Thanks for the feedback." appears and the buttons lock. In the DB check, the 3rd question has `rating` `down` and `rating_reason` holds the text. | | |
| C3 | Fresh conversation; after the 3rd answer, click "Didn't answer my question" | "Thanks for the feedback." appears. In the DB check, the 3rd question has `rating` `no_answer`. | | |
| C4 | Run the database check | The C2 and C3 questions each have a `run` row with `source` `assist` and their `session_id`. `up` is stored and does not by itself write a run row. | | |
| C5 | Reload the tab and open the panel (chips show on the first turn only). Click the "Explain claim 1" chip and wait for the answer. Make sure no text is selected on the page (a selection adds the "Explain this passage" prefix). Then type exactly `Explain claim 1` into the text box (not the follow-up button) and send, within 90 s of that answer | Both are answered. Identical text gives the same intent, so a re-ask applies. In the DB check, the first question (the chip) has `reasked` true. | | |
| C6 | Type a question (for example `What does claim 1 cover?`), wait more than 90 s, then type a same-intent question | `reasked` stays false on the first question. | | |
| C7 | Fresh conversation; after the 3rd answer, click "Dismiss" on the card, then ask a 4th and a 5th question | The card disappears and does not come back. In the DB check, no rating is stored for that conversation. | | |

## D. Single-page navigation (NOT VERIFIED in real Chrome)

Google Patents is a single-page app. Search results open patents without a page reload.

| ID | Steps | Expected result | Result | Notes |
|---|---|---|---|---|
| D1 | Open `https://patents.google.com/?q=patent`. Check the page's Network tab and the service worker's Network tab | No Patent Assist button. No requests to 127.0.0.1:8010 from the page or the worker. | | |
| D2 | Click a result to open a patent, with no page reload | The button appears only after the patent's number, title and claims are on the page. It never appears while the search results still show. If it appears early, mark FAIL. | | |
| D3 | On patent A, follow a link to patent B inside the page (for example a "Cited by" or "Similar documents" entry) | The page shows B. The chat is empty (no messages from A). Ask `What does claim 1 cover?`. In the DB check, that question's `patent_number` is B, not A. If the browser reloads fully instead, record "full reload, D3 not tested" and rely on D2. | | |
| D4 | Press browser Back to patent A and ask a question | The chat is empty again. The DB check shows the question with A's `patent_number`. | | |

## E. Non-US patent

| ID | Steps | Expected result | Result | Notes |
|---|---|---|---|---|
| E1 | Open `https://patents.google.com/patent/EP4517554A1/en` and open the panel | The notice "Patent Assist works on US utility patents and applications only." shows. The text box and chips are disabled. The service worker's Network tab shows no `/assist/` requests. | | |

## F. Page-view tracker (NOT VERIFIED in real Chrome)

Heartbeats are sent by the service worker, so they appear in the worker's DevTools, not in the page's Network tab. Open `chrome://extensions`, click "Inspect views: service worker" on the Patent Assist card, open its Network tab and filter by `page-views`.

| ID | Steps | Expected result | Result | Notes |
|---|---|---|---|---|
| F1 | Stay on US12401720B1 with the tab visible for about 45 s | About every 15 s a POST to `/assist/page-views`, the first at about 15 s, with `dwell_ms` growing. | | |
| F2 | Open one request's Payload | Exactly five keys: `session_id`, `patent_number`, `dwell_ms`, `sections`, `bounce`. `sections` has seven keys: claims, description, classifications, citations, family, legal_events, landscapes. All numbers are whole numbers. | | |
| F3 | Note the DB `dwell_ms`. Switch to another tab for 30 s, then return | At most one heartbeat, sent when you switch away (only if the totals changed). None while the tab is hidden. After the switch, the DB `dwell_ms` equals the value at the moment you left and does not grow while hidden. | | |
| F4 | Close the patent tab | Possibly a final heartbeat (best effort). The DB `dwell_ms` roughly matches the time on the page. Not guaranteed; record what you see. | | |
| F5 | Open `https://patents.google.com/?q=patent` and wait 30 s | No page-views requests. | | |
| F6 | Run the database check | One `assist_page_view` row per session. `patent_number` matches the page (for example `US12401720B1`). Sections you looked at are above 0. Each section value is at most `dwell_ms`. | | |
| F7 | NOT VERIFIED: on US12401720B1, scroll slowly through the Description, which is taller than the window, for about 20 s | The `description` value grows while its text scrolls through the middle of the window. If it stays 0, mark FAIL. | | |

## G. API key safety

| ID | Steps | Expected result | Result | Notes |
|---|---|---|---|---|
| G1 | On a patent page, open DevTools, go to Sources, search across files, and paste the key value into the search box (never into a chat) | No hits. The console shows no hits either. | | |
| G2 | In the same PowerShell window where you ran the build step (S7, so the folder is `extension`), run `Select-String -Path dist\*.js -SimpleMatch -List -Pattern $env:VITE_ASSIST_KEY \| ForEach-Object { $_.Path }` | Only the path `dist\background.js` is printed. `content.js` must NOT appear. The pipe to `$_.Path` prints file paths only; never drop it, because `-List` alone also prints the matching line on Windows PowerShell 5.1, which would show the key. | | |
| G3 | In the service worker's DevTools, search Sources for the key | The key is found in `background.js` only. This is expected: the worker is where the key lives. | | |

## H. Failure handling

| ID | Steps | Expected result | Result | Notes |
|---|---|---|---|---|
| H1 | Stop the backend (Ctrl+C in its window). Ask `What does claim 1 cover?` | "The assistant could not answer right now. Please try again." with a Retry button. The page is unchanged and still scrolls. Restart S6 and click Retry. | | |
| H2 | Rebuild with `$env:VITE_ASSIST_KEY = "wrong-key-for-testing"` and `npm run build`. Reload the card and the patent tab. Ask a question. Then rebuild with the correct key (S7) and reload | The same fixed text and Retry as H1. The backend's own message ("Invalid assist key.") and the key never appear. | | |
| H3 | Run `Remove-Item Env:VITE_ASSIST_KEY`, rebuild, reload, ask a question. Then rebuild with the correct key | "Assist is not configured." with no Retry button. The backend log shows no request. | | |

## I. Accessibility spot checks

| ID | Steps | Expected result | Result | Notes |
|---|---|---|---|---|
| I1 | With the panel open, press Tab through the widget. Then press Shift+Tab from the text box | Focus starts in the text box. Tab order inside the panel: close "×"; then, in the log, the reference pills of each answer (granted patents only; a disabled pill is skipped), the feedback card's buttons once it has appeared (Helpful, Not helpful, Didn't answer my question, Dismiss), with the reason field and "Send feedback" when open, and Retry after an error; then the chips (first turn only, before any question is sent); then the follow-up button (after an answer); then Clear (only while a selection row is shown); then the text box; then Send. Shift+Tab from the text box goes back through Clear, the follow-up button, the chips, the log controls, and then close. There are no per-answer rating buttons. Each control shows a focus ring. Disabled controls during "Thinking…" are skipped. | | |
| I2 | In DevTools > Elements, select the Patent Assist button and read the Accessibility pane. If DevTools cannot reach the closed shadow root, mark SKIP | Names: launcher "Open Patent Assist", close "Close Patent Assist", dialog "Patent Assist", log "Conversation", chip group "Quick questions", text box "Your question", "Clear selected text". On the feedback card (after the 3rd answer): "Thumbs up: this answer helped", "Thumbs down: this answer did not help", and by their text "Didn't answer my question" and "Dismiss"; after "Not helpful", the field "Reason (optional)" and the button "Send feedback". The follow-up button and the reference pills are named by their text (for example "What does claim 2 add?", "claim 1", "¶0042", "Description"). There are no per-answer rating buttons. | | |
| I3 | Turn on Narrator (Ctrl+Win+Enter) and ask a question | Narrator announces the answer. The conversation is a polite live region (`role=log`). | | |

## J. Not verified: known risks

| ID | What was not verified | Risk if it fails | Where to look |
|---|---|---|---|
| J1 | Real Chrome messaging between the content script and the service worker (`chrome.runtime.sendMessage`, `lastError`) | Every question shows the generic error | B3, H1 |
| J2 | Closed shadow DOM focus handling | Focus may not enter the panel, or Escape may not reach it | B2, B8 |
| J3 | The `navigation` event (`currententrychange`) is available | Route detection falls back to popstate and DOM changes | D3 |
| J4 | The `?raw` CSS import in the content bundle | The widget may render unstyled | B1 |
| J5 | IntersectionObserver thresholds and the band `rootMargin` on a real viewport | Section times may be off | F6, F7 |
| J6 | `pagehide` delivers the final heartbeat | The last seconds may be lost | F4 |
| J7 | Host permission with a port (127.0.0.1:8010) | The extension may not load, or the worker may be blocked | A5, B3 |
| J8 | Google's live DOM (the fixtures are from build 2026-11-06) | The widget may never become ready, or the brief may be incomplete | B1, D2 |
| J9 | The real backend accepts heartbeats (integers only; the schema was read, not called) | Heartbeats rejected with 422 | F1, F6 |
| J10 | The 429 path and answer quality from the model | Not exercised | S9, B4 |
| J11 | The database check command (the here-string piped to `python -` in PowerShell 5.1, under the Setup section) has never been run | The check fails or prints nothing; C4, D3 and F6 cannot be confirmed | C4, D3, F6 |
| J12 | The service worker accepts any `patents.google.com` sender, so the final heartbeat of a patent view must still arrive when leaving for the search page, and `sender.url` after an SPA pushState must be the last committed URL | The quickest visits (bounces) are not recorded, or the worker answers 403 | F4, F6 |
| J13 | Widget v2: keystrokes typed in the widget are stopped in the bubble phase only. Whether Google Patents handles keys in the capture phase (which this cannot stop) is unknown | Typing `i w s a d` or arrow keys in the box still moves or changes the page | K6 |
| J14 | Widget v2: pills scroll to and highlight a claim, a description paragraph or a section on the live page. The selectors were written from saved pages, and paragraph pills need the live page to carry paragraph numbers (the saved granted patent US12401720B1 does; the saved applications and the older granted US6756997B1 do not, so they get no paragraph pills) | A pill does nothing, is disabled, or leaves its highlight on | K2, K3, K4 |
| J15 | Widget v2: the shorter answers come from the new prompt (at most four short sentences, plain text, at most one short quote). They have not been judged against real model output, and the legal-opinion rule ("list their elements" plus a fixed sentence) can sit awkwardly with "at most four short sentences", so the model may resolve it either way | Answers are longer than asked, show raw markers, or the legal-opinion answer is cut or oversized | K1, B11 |
| J16 | Widget v2: the feedback card appears after the 3rd answered question (the setting `FEEDBACK_AFTER_QUESTIONS` in `extension/src/widget/followups.js`). Whether 3 is a good moment has not been tested with anyone | The card appears too early or too late for a real reader | K8 |

## K. Widget v2 (NOT RUN; NOT VERIFIED in real Chrome)

New in this version: reference pills, shorter answers by instruction, keystroke isolation, first-turn chips, one follow-up and one feedback request. None of it has run in real Chrome, and the answers have not been judged against real model output. Before K1 to K8, run `python scripts/set_assist_prompt.py` against the same database as the backend (see K9), then rebuild and reload the extension (S7, S8). Use `US12401720B1` unless a row says otherwise: it is a granted patent whose page carries paragraph numbers.

| ID | Steps | Expected result | Result | Notes |
|---|---|---|---|---|
| K1 | On `US12401720B1`, ask `What does claim 1 cover?` | The answer is 2 to 4 sentences (that is the instruction; the model may overshoot, so record how long it was). No `*`, `#` or `[p…]` text is visible. | | |
| K2 | Click a claim pill (for example "claim 1") in an answer | Google Patents scrolls to that claim and highlights it for about 2 seconds. Then the highlight is gone and the page looks as before (no leftover outline or background). A pill whose target is not on the page is greyed out with the tooltip "Couldn't find this on the page". | | |
| K3 | On `US12401720B1` (not `US6756997B1` or an application: their pages carry no paragraph numbers, so they get no paragraph pills), ask something about the description (for example `What problem does this patent solve?`) until an answer cites a paragraph pill (`¶NNNN`), then click it | Same as K2 for that description paragraph: scroll, highlight for about 2 seconds, then clean. If no answer cites a paragraph after a few tries, mark SKIP and record that. | | |
| K4 | Get an answer that names a section pill (for example "Classifications"; `What do these CPC codes mean?` may do it) and click it | Same as K2 for that section of the page. If no answer names a section, mark SKIP and record that. | | |
| K5 | On the application `US20250119367A1`, ask the same kind of question as K1 | No pills anywhere (on applications references are plain text). No raw `[section:…]` text is visible. | | |
| K6 | NOT VERIFIED: click in the text box, type `i w s a d` and press the arrow keys. Then click on the page and press the same keys outside the box | In the box the keys do nothing to Google Patents (no image viewer, no scrolling, no shortcuts). The same keys outside the box still behave as before. If the page still reacts to keys typed in the box, record exactly what it does and stop: this fix only stops keys in the bubble phase and cannot stop a page that listens in the capture phase (see J13). | | |
| K7 | Open a fresh panel and look at the chips. Ask one question, then click the follow-up button. Repeat for a few answers | Chips show on the first turn only and never again in that conversation. After each answer exactly one follow-up button is shown. It never repeats a question already asked, never names a claim the patent does not have, and no claim follow-up appears when the answer cited no claim. Clicking it asks exactly its text. | | |
| K8 | Ask 3 questions in a fresh conversation, then a 4th and a 5th | The "How is this going?" card (Helpful, Not helpful, Didn't answer my question, Dismiss) appears once, under the 3rd answer, and not under later answers, nor after a rating or Dismiss. The DB side is in C1 to C3 and C7. | | |
| K9 | Run `python scripts/set_assist_prompt.py` (first run: "Created prompt version N and made it current."; a second run: "Prompt already current (version N)."), then open the Assist console | The console header reads like "<agent name> · vN · ..." with the new version number N. | | |
