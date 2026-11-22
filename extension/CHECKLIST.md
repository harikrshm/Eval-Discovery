Status: NOT RUN. The build agents could not run a real browser; the author runs this checklist.

# Patent Assist: manual verification checklist

How to record: in each table, write PASS, FAIL or SKIP (with the date) in the Result column and what you saw in Notes. Items marked NOT VERIFIED have never run in real Chrome; they are the likeliest to fail (see section J).

Backend origin used here: `http://127.0.0.1:8010` (no path). Port 8000 may already be your dev server.

## Setup

Run from the repo root in PowerShell. Use the same shell for S2 to S6 so `DATABASE_URL` is the same for all of them.

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
  Add-Content -Path .env -Value ("ASSIST_KEY=" + $env:VITE_ASSIST_KEY) -Encoding ascii
  ```

  Use the same PowerShell window for S5 and S7, so the value is still in `$env:VITE_ASSIST_KEY`. In another window, recover it into the variable without printing it:

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
- **S8.** Load in Chrome: open `chrome://extensions`, turn on Developer mode, click Load unpacked and choose `extension\dist`. After every rebuild, reload the Patent Assist card and reload each patent tab.
- **S9.** Rate limit: the project has observed roughly 8000 tokens a minute on Groq's free tier; check Groq's current limits for your account. Wait about a minute between questions. On "Too many requests. Please wait a moment and try again." wait and retry. Optionally add `GROQ_TPM_LIMIT=7000` to `.env` (example value) so the backend paces its model calls, then restart S6.

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
| B2 | Click the Patent Assist button | Panel "Patent Assist" opens with close button "×", the text box (placeholder "Ask about this patent", accessible name "Your question"), chips "Explain claim 1", "What do these CPC codes mean?", "Is this still in force?", and the footer. The launcher hides while the panel is open. Focus moves to the text box. | | |
| B3 | Type `What does claim 1 cover?` and press Enter | "Thinking…" shows, then an answer that cites claim numbers (for example "claim 1") and uses the page's wording. No error text. | | |
| B4 | Click each chip, waiting for each answer | Each chip sends exactly its label as your message and gets an answer. The status answer uses the page's status and dates and does not say whether the patent is valid. | | |
| B5 | Type a line, press Shift+Enter, type more, press Enter | Shift+Enter adds a new line without sending. Enter sends. | | |
| B6 | Select a sentence in the Description, click the text box, type `explain simply`, press Enter | A row reads `Selected on the page: "<sentence>"` with a Clear button. The sent message is `Explain this passage: "<sentence>" explain simply`. The row disappears after sending. | | |
| B7 | Select text, click the text box, click Clear | The row hides. The next plain question is not quoted. | | |
| B8 | Press Escape while focus is in the text box | The panel closes and the Patent Assist button is shown and focused. | | |
| B9 | Reopen the panel and Tab through it | Tab order is as in I1. Each control shows a focus ring. | | |
| B10 | Look at the bottom of the panel | The footer reads "Not legal advice. Answers are based on the text of this page." | | |
| B11 | Ask `Can I sell a product like this without getting sued?` | No yes/no verdict about selling, infringing or validity. The answer discusses the claims in general terms and contains the sentence "This is not legal advice, and I can't judge validity or infringement." A citation warning may follow it. | | |
| B12 | Repeat B1 to B3 on US6756997B1 | Same behaviour. The answer refers to the claims of 6756997B1, not 12401720B1. | | |

## C. Ratings and re-asks

| ID | Steps | Expected result | Result | Notes |
|---|---|---|---|---|
| C1 | On one answer, click "Helpful" | All three rating buttons (Helpful, Not helpful, Didn't answer my question) become disabled and "Thanks for the feedback." appears. The reason box stays hidden. | | |
| C2 | On a second answer, click "Not helpful", type `missed the point` in "What was wrong? (optional)", click "Send feedback" | "Thanks for the feedback." appears and the buttons lock. In the DB check, that question has `rating` `down` and `rating_reason` holds the text. | | |
| C3 | On a third answer, click "Didn't answer my question" | "Thanks for the feedback." appears. In the DB check, `rating` is `no_answer`. | | |
| C4 | Run the database check | The C2 and C3 questions each have a `run` row with `source` `assist` and their `session_id`. `up` is stored and does not by itself write a run row. | | |
| C5 | Click the "Explain claim 1" chip and wait for the answer. Then type exactly `Explain claim 1` and send, within 90 s of that answer | Both are answered. Identical text gives the same intent, so a re-ask applies. In the DB check, the first question (the chip) has `reasked` true. | | |
| C6 | Ask a question, wait more than 90 s, then ask a same-intent question | `reasked` stays false on the first question. | | |

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
| E1 | Open `https://patents.google.com/patent/EP4517554A1/en` and open the panel | The notice "Patent Assist works on US patents only." shows. The text box and chips are disabled. The service worker's Network tab shows no `/assist/` requests. | | |

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
| G2 | In the same PowerShell window where you ran the build step (S7), run `Select-String -Path extension\dist\* -SimpleMatch -List -Pattern $env:VITE_ASSIST_KEY` | Only `extension\dist\background.js` is listed. `-List` prints file names, not the matching text. | | |
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
| I1 | With the panel open, press Tab through the widget. Then press Shift+Tab from the text box | Focus starts in the text box. Tab order inside the panel: close "×"; then, in the log, the answer buttons (Helpful, Not helpful, Didn't answer my question) of each answer, with the reason field and "Send feedback" when open and Retry after an error; then the chips; then Clear (only while a selection row is shown); then the text box; then Send. Shift+Tab from the text box goes back through Clear, the chips, the answer buttons, and then close. Each control shows a focus ring. Disabled controls during "Thinking…" are skipped. | | |
| I2 | In DevTools > Elements, select the Patent Assist button and read the Accessibility pane. If DevTools cannot reach the closed shadow root, mark SKIP | Names: launcher "Open Patent Assist", close "Close Patent Assist", dialog "Patent Assist", log "Conversation", chip group "Quick questions", text box "Your question", "Thumbs up: this answer helped", "Thumbs down: this answer did not help", "Clear selected text". | | |
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
