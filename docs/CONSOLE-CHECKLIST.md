Status: NOT RUN. The build agents could not run a real browser and made no real Groq call for the console; the author runs this checklist.

# Patent Assist console v2: manual verification checklist

The console (Overview, Adoption, Quality, Evolution, Operations) is tested in jsdom and against a faked model. This checklist is for what that cannot show: how it looks and behaves in a real browser, and what the real judge model does with real conversations. Nothing below has been run.

How to record: in each table write PASS, FAIL or SKIP (with the date) in the Result column and what you saw in Notes. Rows with a Result of NOT RUN have never run. Where a row says "record", write the actual observation down: the point of rows E1 to E5 is to find out what the judge really does, not to confirm that it works.

Keep the claims honest while you do this. The judge is a language model's opinion and has not been compared with human raters. The demo dataset is generated, not usage. A demo version marker is a label on generated rows, not an improvement. See [docs/ARCHITECTURE.md, section 10.11](ARCHITECTURE.md#1011-console-v2).

## Setup

Run from the repository root. Use one `DATABASE_URL` (or none, for the default local SQLite file `dev.db`) for every command in this list.

- **S1.** Migrate: `alembic upgrade head`. This applies `0006` (the `assist_call` table) and `0007` (the `assist_judgement` table and `assist_question.judge_flagged`), on top of `0004` and `0005`.
- **S2.** If you have no demo workspace yet: `python scripts/setup_assist.py`. It prints the workspace token once (only its hash is stored); keep it private. Put `ASSIST_AGENT_ID` and `GROQ_API_KEY` in `.env` (and `ASSIST_KEY` if you also use the extension).
- **S3.** Start the backend: `uvicorn backend.main:app` (port 8000), or for example `uvicorn backend.main:app --port 8010`. Leave `WORKER_ENABLED` unset (the job worker runs unless it is `0`); the judge runs on that worker. On Groq's free tier the 120B model is limited to about 8000 tokens per minute, so set `GROQ_TPM_LIMIT=7000` in `.env` before starting if you will run E1 to E5.
- **S4.** Start the frontend. The default backend address is `http://localhost:8000`. If the backend runs elsewhere, set `VITE_API_URL` in the shell that runs the dev server, for example PowerShell `$env:VITE_API_URL="http://127.0.0.1:8010"; npm run dev` (or bash `VITE_API_URL=http://127.0.0.1:8010 npm run dev`), from `frontend/`. Open http://localhost:5173.
- **S5.** Seed the demo dataset: `python scripts/seed_demo_console.py --confirm --end <yesterday as YYYY-MM-DD>`. The default `--end` is today (UTC), and the seeded timestamps for today run to about 22:50 UTC, which can be later than now; those rows are left out of 24 hours, 7 days and 30 days but counted in All. So pass yesterday. Other flags: `--seed N` (default 2026), `--database-url URL`. Without `--confirm` it prints the planned counts and writes nothing (exit 2). Running it twice leaves the same rows. With `--end 2026-11-24` and the default seed it writes 428 page views, 477 questions, 719 model calls, 217 judgements and 25 flagged conversations (task 4 report), all tagged `imported` with session ids starting `demo30-`.
- **S6.** To undo: `python scripts/seed_demo_console.py --remove --confirm`. It deletes the `demo30-` rows and all imported call rows of this workspace. It does not touch live rows, the hand-written example import, or prompt versions, and it does **not** reset `last_checked_at_run` or the stored pattern, so a pattern check run after seeding keeps counting the seeded conversations as already seen. Both commands refuse (exit 2) if live flagged conversations were saved after the seed and would sit between seed rows.
- **S7.** Connect: click **Assist console** in the app header, paste the workspace token from S2, **Connect**. While connected the console replaces the chat app in that browser; **Disconnect** restores your own workspace. Use one browser tab.

Facts the rows rely on:

- The UI defaults are range 30 days, source All, version All. (The backend's own defaults for a call without parameters are range `all` and source `all`.)
- The source badge under the title reads "Data: Live + demo dataset", "Data: Live only" or "Data: Demo dataset". All times are UTC.
- Bounded ranges are 24 hourly buckets, 7 daily or 30 daily, counted back from the moment of the request, so they are not calendar aligned.
- The judge flags a conversation when its grade is 2 or lower (`FLAG_GRADE_AT_OR_BELOW`). Evaluate now queues one conversation per click.

## A. The five tabs with the demo dataset

Do S1 to S7 first. Filters at their defaults (30 days, All, All).

| ID | Check | Expected | Result | Notes |
|---|---|---|---|---|
| A1 | Open the console and look at the header and controls | The tab list reads Overview, Adoption, Quality, Evolution, Operations, with Overview selected. The source badge reads "Data: Live + demo dataset". An amber note says "Data source: demo dataset. Includes imported demo data, which is not measured production usage." | NOT RUN | |
| A2 | Overview: read it top to bottom | An alert strip (only if an alert is active), four KPI cards (Questions, Auto-judged OK, Open flagged conversations, Live version), a "Quality over time" line with dashed version markers, and an "Improvement loop" stepper with an "Open Evolution" button. No card shows "0%" where there is no data; it shows "n/a". | NOT RUN | |
| A3 | Adoption: read it | Four KPI cards (page views, questions, re-ask rate, thumbs-down rate; the thumbs-down card has a note that the rating protocol changed with widget v2), a "Page views and questions over time" line, four bar charts (feature coverage, help demand, task mix, re-ask and thumbs-down by task), the "Where people struggle" numbers, definitions, version comparison (it should say versions cannot be compared yet when only demo data exists), then the flagged list. | NOT RUN | |
| A4 | Quality: read it | The judge disclaimer (the opinion of a language model, not validated against human raters), the **Evaluate now** control, KPI cards (judged, average grade, concise share, citations share), the unjudged line, "This view includes demo dataset conversations..." note, "Average judge grade over time", Verdicts, Reasons and Ratings bar charts, the insights brief, and the flagged list with reason chips. | NOT RUN | |
| A5 | Evolution: read it | The line "Applies to the whole assistant, not the selected range.", the "Improvement loop" stepper, the "Version timeline", the "Before and after replay" chart (or "No replay results yet."), then the existing actions (Check flagged conversations for a pattern, Discover evals, and so on). Nothing in the timeline or replay chart is labelled demo or comes from the seeded rows. | NOT RUN | |
| A6 | Operations: read it | The line saying latency is the total time a user waited and percentiles use chat calls only; KPI cards (p95, error rate, rate-limited, tokens); "Latency over time" with a median and a slowest-5% line; "Calls by outcome"; "Calls over time"; "Tokens by kind of call"; the "Judge queue" panel; "Recent failed jobs" (likely "No failed jobs."). The demo-rows note is shown. | NOT RUN | |
| A7 | Look for the generated storyline in the charts (the generator writes a weekday and weekend rhythm, a latency bump on days 8 to 10, a rate-limit burst on day 15, a quality dip on days 14 to 20 and version markers on day 20 and day 27) | The shapes are visible and every one carries a demo cue (the amber notes, "(demo)" on the version markers). You can say, out loud, that these are generated. | NOT RUN | |
| A8 | Switch tabs back and forth several times, then press Refresh data | Each tab keeps its place. Charts redraw at the right size after switching back (no zero-width chart, no overlap). | NOT RUN | |

## B. Filters

| ID | Check | Expected | Result | Notes |
|---|---|---|---|---|
| B1 | Source: choose "Live only" on each of the five tabs, on a database that has no live conversations (a fresh one, or after you have not asked any question) | The badge reads "Data: Live only". The amber demo note disappears. Every tab is empty or shows "Not enough data yet." with no error banner, no "NaN", no "0%" for an empty rate (it reads "n/a"). The Overview Live version card still shows the agent's own version. | NOT RUN | |
| B2 | Source: choose "Demo dataset" | The badge reads "Data: Demo dataset". Charts show the generated data only. The version dropdown now lists only the demo versions (v1, v2, v3). | NOT RUN | |
| B3 | Source: choose "All" again | The badge reads "Data: Live + demo dataset". With live conversations present, the live and the demo numbers add up. | NOT RUN | |
| B4 | Range: click 24 hours, 7 days, 30 days, All in turn | The pressed button changes; the charts redraw. With the seed ending yesterday, 24 hours shows hourly points, the others daily points. With `--end` set to today, the rows later than now are missing from the three bounded ranges. | NOT RUN | |
| B5 | Version: with source All, pick a version | Charts that depend on the filter redraw and show an "Updating…" cue while they load (see M1). Picking a different source resets the version to All. | NOT RUN | |
| B6 | Version markers: open Overview and Quality with source All and 30 days | The demo markers read "v2 (demo)" and "v3 (demo)" and the live marker (the agent's real version, for example "v1") has no "demo". The marker labels do not hide the line or each other. | NOT RUN | |
| B7 | Read the version markers' meaning aloud | They are labels on generated rows. Nothing in the console says or implies that the demo quality "improved" after v2. Confirm that no sentence does. | NOT RUN | |
| B8 | Version comparison in Adoption | It uses live questions only and says so ("Real (non-imported) questions only."). With only demo data it says versions cannot be compared yet. | NOT RUN | |

## C. Alerts and drill-down

| ID | Check | Expected | Result | Notes |
|---|---|---|---|---|
| C1 | Look at the alert strip on Overview with the seeded data | Zero or more alerts. Each shows "Warning" or "Critical" in words (not only colour or icon), a title, a detail, and a "demo" tag when it was computed from rows that include demo data. Active rules and their thresholds are in `backend/core/assist/alerts.py` (`THRESHOLDS`: error rate 5%, p95 latency 8000 ms, re-ask rise 5 points, flagged backlog 5, judge backlog 10). If none is active, record that and go to C3. | NOT RUN | |
| C2 | Click an alert | The console switches to the alert's tab (error rate and p95 latency to Operations, re-ask rise to Adoption, flagged backlog to Evolution, judge backlog to Quality), and the page scrolls to the chart or section it points at (`ops-errors`, `ops-latency`, `adoption-reasks`, `evolution-loop`, `quality-judge`). The tab button keeps focus. | NOT RUN | |
| C3 | To force an alert if none is active, set Source to Demo dataset and range to 30 days, and look for any; otherwise skip | Record whether any alert ever fired on the seeded data. (No test established that the seeded data crosses a threshold.) | NOT RUN | |
| C4 | Adoption: click (or focus and press Enter) a bar in "Feature coverage by section", "Help demand by section", "Task mix" or "Re-ask and thumbs-down by task" | The flagged list below changes to "Filtered by section: ..., last 30 days (UTC), source: ..." (or the task) and shows only conversations behind that bar. Focus and scroll move to the list heading. Press "Clear" to return. | NOT RUN | |
| C5 | With C4 active, change the range, the source and the version | The list refetches with the new scope and keeps the picked section or task. The count line ("N flagged conversations") updates. | NOT RUN | |
| C6 | Read a flagged row | It shows ids, intent, section, a source tag and fixed reason labels (Thumbs-down, No answer, Re-ask, Citation failure, Auto-judge). It shows no question or answer text anywhere. | NOT RUN | |

## D. Narrow window and layout

| ID | Check | Expected | Result | Notes |
|---|---|---|---|---|
| D1 | Make the browser window (or the dev-tools device mode) 360 px wide. For each of the five tabs scroll to the bottom | There is no horizontal scroll of the page. The tab list scrolls inside itself. Controls wrap. KPI cards stack. Tables scroll inside their own box if needed. | NOT RUN | Record per tab. |
| D2 | At 360 px, read the category labels of every bar chart (feature coverage, help demand, task mix, re-ask by task, verdicts, reasons, ratings, tokens, replay) | Long names are not cut off or printed over the bars (the kit reserves a 120 px label column). If they collide, record which chart. | NOT RUN | |
| D3 | At 360 px, Operations with range 24 hours | The hourly x axis shows readable `HH:MM` labels (the first label of a new UTC day also shows the date); labels are skipped, not overlapped. Stacked "Calls by outcome" columns are readable. | NOT RUN | |
| D4 | At 360 px, Operations and Adoption with range 30 days | The daily x axis shows readable labels like "Nov 24"; labels are skipped, not overlapped. | NOT RUN | |
| D5 | At 360 px, the dashed version-marker labels on Overview and Quality | Marker labels do not collide with each other or with the line enough to be unreadable. If they do, record it (the chart also lists markers in its text legend). | NOT RUN | |
| D6 | At 360 px, the controls bar | The range group, the two selects and "Refresh data" are all reachable without horizontal scroll. (The range group was estimated at about 270 of 296 px usable width and never measured.) | NOT RUN | |
| D7 | At 360 px, Evolution | Single column. The stepper, version timeline, replay chart and the existing action buttons are all usable; the approval buttons do not move while the loop refreshes (see M3). | NOT RUN | |
| D8 | At a laptop width (about 1280 px), Evolution and Adoption | Evolution shows the version timeline and replay chart side by side from the `lg` breakpoint; Adoption shows its bar charts two to a row. | NOT RUN | |

## E. Evaluate now with a real Groq key

This is the only row group that makes real model calls for the judge. It needs live conversations. Ask them through the extension (see `extension/CHECKLIST.md`) or any client of `POST /assist/chat`; the judge never judges imported (demo) conversations and never spends tokens on them.

| ID | Check | Expected | Result | Notes |
|---|---|---|---|---|
| E1 | Prepare at least five live conversations of two to four questions each, in this mix: two answered well, one where the assistant should have refused a legal-opinion question, one asking about something the page does not show, one with an off-topic question. Ask a few seconds apart, wait about a minute between questions (S3) | The conversations are stored. Open Quality: the "still unjudged" line counts them (each conversation, not each question). | NOT RUN | |
| E2 | Quality: click **Evaluate now** once | "Evaluating…", then "Queued 1; N waiting for the next hour. Results appear after the judge runs; use Refresh data in a few minutes." One click queues one conversation (the oldest). Click again after each finishes to judge the next. (Without a click the idle scan queues one after 3 minutes of silence in a conversation and 1 minute of quiet in chat, one at a time, at most 20 judge jobs per hour per workspace.) | NOT RUN | |
| E3 | After the judge job finishes (watch Operations, "Judge queue": queued judge jobs, judged in the last hour "x of 20"), press **Refresh data** on Quality | The judged KPI goes up, the Verdicts, Reasons and grade charts show the new verdict, and the unjudged line goes down. Record the actual verdict of every judged conversation (answered, concise, citations, legal, grade, reason) next to your own reading of that conversation. | NOT RUN | Record all. |
| E4 | For each conversation you expected to be weak, record whether the judge's grade was 2 or lower | Record, per conversation, the grade and whether you agree. This is the first look at the judge on real conversations; do not generalise from five. Note any case where it was fooled, too lenient, too strict, or always gave the same grade. | NOT RUN | |
| E5 | A low grade (2 or lower): open Quality, chip "Auto-judge" | The conversation appears in the flagged list with the reason "Auto-judge". It is also counted under "Open flagged conversations" on Overview. | NOT RUN | |
| E6 | Open Evolution and run **Check flagged conversations for a pattern** | The judge-flagged conversation is among the evidence (it is a run with source `assist` and no stored page brief). Record whether a pattern is found. Do not expect replay tests built from it to work: the run carries an empty page brief, and replaying a test built from it fails with the fixed message "That test's saved page is not valid." (read from `backend/jobs/handlers.py`; not exercised by any test). | NOT RUN | |
| E7 | Record the real cost of a judge run: for each judge call, read the call's total tokens and latency. The numbers are in the `assist_call` table (`kind = 'judge'`: `latency_ms`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `attempts`, `outcome`) and in Operations ("Tokens by kind of call", latency) | Record: tokens per judge call (the model is told to output at most 600 tokens, part of which can go to hidden reasoning), latency, and whether any call hit a 429. Compare one judge call's total tokens with the 8000 tokens per minute limit of the 120B model and with the calls chat and insights make in the same minute. | NOT RUN | Record all. |
| E8 | A judge failure: stop the backend's access to Groq (for example an invalid key) and press Evaluate now | The job is retried by the queue and then fails; chat is unaffected; Operations shows the failed job with one of the fixed messages ("The judge was unavailable. Try again.", "Something went wrong. Try again." or "The judge could not read this conversation."); the judge's failed calls appear as `error`, `rate_limited` or `timeout` in "Calls by outcome"; no question or answer text appears anywhere. Record which message you got. Restore the key. | NOT RUN | |
| E9 | Run **Evaluate now** when there is nothing to judge | "Nothing to evaluate". | NOT RUN | |

## F. Evolution regression

The console v2 work touched the screens around the Evolution actions but not the actions or the approval gate. This is a regression pass over what existed before.

| ID | Check | Expected | Result | Notes |
|---|---|---|---|---|
| F1 | Evolution: **Check flagged conversations for a pattern**, then **Discover evals** (needs flagged evidence; the seeded runs count as evidence) | Same behaviour as before: a pattern summary, a criterion, then tests. After each step the stepper and the version timeline update by themselves, without an "Updating…" cue and without dimming the page or moving the buttons. | NOT RUN | |
| F2 | Suggest a fix on a failing fix test | The suggested fix shows its line diff and change report as before. The version timeline shows the candidate. | NOT RUN | |
| F3 | Click **Approve and verify** (or Reject) | A person must click it; nothing is approved by itself. After approval the verification runs; the outcome (promoted, failed verification, rejected) shows in the stepper and the timeline. | NOT RUN | |
| F4 | After a promotion, look at "Before and after replay" | One group per verified version with "Passed before" and "Passed after" and a "View as table" with the number of tests checked. The numbers come from stored replay results of real tests only. | NOT RUN | |
| F5 | The stepper against the page | The stepper's current step matches what the Evolution page shows (pattern, criterion, tests, candidate). After a criterion exists the "Pattern found" step stays done (a later step's evidence marks earlier steps done). | NOT RUN | |
| F6 | Restore an older version in the existing version history | Works as before; the timeline's "Current" badge follows. | NOT RUN | |
| F7 | The stepper's "Improvement loop" on Overview vs Evolution | Both show the same step states. | NOT RUN | |

## G. Keyboard only

Unplug the mouse or do not touch it.

| ID | Check | Expected | Result | Notes |
|---|---|---|---|---|
| G1 | Tab to the tab list, use Left and Right, Home and End | The selected tab moves and its panel changes. A tab button has a visible focus ring. | NOT RUN | |
| G2 | Tab through the controls: range buttons, source select, version select, "Refresh data" | Every control is reachable in order, shows a focus ring, and works with Enter, Space or the arrow keys. The pressed range is announced as pressed. | NOT RUN | |
| G3 | Quality: Tab to the reason chips and press Enter or Space | The flagged list changes; the chip shows it is pressed. | NOT RUN | |
| G4 | Adoption: Tab to a bar of "Task mix" | The bar takes focus and shows a visible ring (a 3 px stroke). Enter or Space picks it. The ring does not appear when you click with a mouse. | NOT RUN | |
| G5 | Tab to every "View as table" button and press Enter | The table opens and the button says "Hide table"; Tab continues into the table's content and out again; no focus trap. | NOT RUN | |
| G6 | Alert strip: Tab to an alert and press Enter | Same result as C2. | NOT RUN | |
| G7 | **Evaluate now** by keyboard | Works; the status line is read by a screen reader (G8). | NOT RUN | |

## H. Screen reader and chart text equivalents

Use NVDA (Windows) or VoiceOver.

| ID | Check | Expected | Result | Notes |
|---|---|---|---|---|
| H1 | On Overview, move over the "Quality over time" chart | The chart is announced as an image with a summary ("Quality over time: N rows from ... to .... Latest ...: ..."). | NOT RUN | |
| H2 | Press "View as table" on three different charts (a line, a plain bar chart, a clickable bar chart) | A real table is read with a caption, column headers and row headers (the time or category). Empty cells read "n/a", never 0. | NOT RUN | |
| H3 | Clickable bars (Adoption) | The group is not hidden behind an image role; each bar is announced as a button with its category, series and value. | NOT RUN | |
| H4 | KPI cards | Each is read as label, value, and change in words ("up 3 pts", "down 2.5 pts", "no change", with "(better)" or "(worse)"), never only an arrow or colour. | NOT RUN | |
| H5 | Alerts and stepper steps | Severity is read as "Warning" or "Critical"; the stepper reads "Done", "Current", "To do" and marks the current step. | NOT RUN | |
| H6 | Loading and updating | "Loading…" and "Updating…" are announced once, not repeatedly. If a screen reader does not announce "Updating…" when it appears, record it. | NOT RUN | |
| H7 | Look at the page in a high-contrast or greyscale setting, or with a colour-blindness simulator | Series are told apart by the text legend, bar labels and tables, not by colour alone. Record where you cannot tell two series apart (the stacked outcomes on Operations rely on the legend, the totals line and the table). | NOT RUN | |

## M. Loading, failure and stale-data behaviour

| ID | Check | Expected | Result | Notes |
|---|---|---|---|---|
| M1 | Change the range or source while a tab is open, on a slow connection (dev tools throttling "Slow 3G") | The old content stays, dims, and "Updating…" shows above it; the badge already shows the new source. When the data arrives the dim goes away. No tab shows old numbers under a new badge without the cue. | NOT RUN | |
| M2 | Console chunk failure. Open dev tools, Network, block the request for the `AssistConsole-*.js` file (right-click, Block request URL), reload and connect | The app stays up. A message "Couldn't load the console." with **Retry** and **Disconnect**. Unblock and click Retry: the console loads. If Retry keeps failing because the browser cached the failed import, reload the page by hand (a "Reload page" button does not exist; this is the recorded fallback). **Disconnect** returns to the chat app. | NOT RUN | |
| M3 | In Evolution, trigger a loop move (for example a pattern check that finds a pattern) | The stepper and timeline update. The tab does not dim, no "Updating…" shows, and the approval buttons do not move. Pressing **Refresh data** does show "Updating…". | NOT RUN | |
| M4 | Stop the backend and press Refresh data | Each panel shows "Couldn't load ...: <message>" with **Retry** (the console as a whole shows "Couldn't load the console: ..." only when status fails). Nothing crashes. Start the backend and press Retry on a panel: it recovers. | NOT RUN | |
| M5 | A slow first load of each tab | "Loading…" shows, then the content; no layout jump larger than the content itself. | NOT RUN | |

## N. Not covered here

Large live datasets (the endpoints load a workspace's rows and compute aggregates in Python per request; no timing was taken), a deployment (nothing is deployed), a second browser or a phone, and printing. If one of these matters to you, test it.
