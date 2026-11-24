# Patent Assist console v2: design

Status: draft for the author's approval. Project 2 of 2 (the widget is `2026-11-24-patent-assist-widget-v2-design.md`, built first). Builds on `2026-11-22-patent-assist-design.md`; where this document is silent, that one holds.

## Intent and why

The console exists to show that the author, as a forward-deployed engineer, would build and ship a production-ready monitoring and observability console that a platform team can use to track a product and decide what to build next. Today's console (Adoption and Evolution tabs) is plain text, tables and one hand-made bar. It reads as a prototype. The target is what a platform team recognises from production tools: health at a glance, trends over time, alerts, drill-down from a chart to the conversations behind it, and the improvement loop shown as a visible pipeline with versions on the timeline.

## Goals

1. **Five tabs:** Overview, Adoption, Quality, Evolution, Operations (layout C from the mockups).
2. **Real charts** (line, bar, grouped bar, KPI cards with sparklines and change since the previous period), with one visual system, accessible fallbacks and clear loading, empty and error states.
3. **Global controls:** time range (24 hours, 7 days, 30 days, all), data source (all, live only, demo dataset), prompt-version filter, refresh. The source is always visible as a badge.
4. **Service health (new measurement):** answer latency, error and rate-limit rates, request volume, token use by kind of call.
5. **Automatic conversation judge (new):** a model reads whole finished conversations and scores them; a low score flags the conversation into the existing improvement loop, as a thumbs-down would. This is the second step, built after the telemetry and before the tab work that displays it.
6. **Alerts:** a strip on Overview, computed by code from thresholds (for example error rate, p95 latency, re-ask rate rising, unreviewed flagged conversations).
7. **Drill-down:** from a chart point or bar to the flagged or judged conversations behind it (counts and labels, as today).
8. **Demo data that looks like a product:** a generated, deterministic, clearly labelled "demo dataset" covering about 30 days, so trends and version markers are visible.

## Non-goals

- Real-time streaming, notifications or paging, user accounts and roles, saved views, export, a dark theme.
- Integrating an external monitoring system. (Exposing a standard metrics endpoint is a possible later step.)
- Showing question or answer text in the console. The existing rule stays: counts, rates, ids and labels only. The judge's reasons are fixed labels, not free text.
- Judging quality of the judge against humans in this project.

## Anti-goals (failure even if it "works")

- Made-up numbers presented as real usage or as a measured improvement. Generated data is labelled "demo dataset" everywhere it appears; imported or generated data is never counted as evidence that a prompt version improved the assistant (the version comparison keeps using live data only, as the 2026-11-22 design requires).
- Charts that look good but cannot be read without colour or a mouse.
- A judge that quietly drifts: every judgement records the model, the rubric version and the prompt version it judged.
- A console that only works with the generated data.

## Constraints

- Builder-side auth is unchanged (`X-Workspace-Token`); the console keeps reading only its own workspace's assistant.
- Frontend stays React 19 with Tailwind and the existing components; a charting library is added (see Approaches). It must support React 19 and be testable in jsdom. Bundle growth is checked and reported.
- Groq's free tier (about 8000 tokens a minute on the 120B model) bounds the judge: judgements run through the existing job queue, are paced, and are capped per hour. One judgement per conversation per prompt version.
- Telemetry must not slow or break chat: a failure to record a call is logged and ignored.
- Colours, chart choice and number formatting follow the `dataviz` skill and the installed `data-visualization-guide` (chart type by data shape, one hue per series, sparing colour, a text equivalent for every chart). The guide's text is partly garbled; it is used as a checklist, not as the authority. Palette contrast is checked with the `dataviz` validator.
- Existing suites stay green; new code has tests; the real-browser steps are written into a manual checklist, as for the widget.

## Approaches considered

Scope: **A** two tabs rebuilt with charts; **B** four tabs with an Overview landing and a Quality tab; **C** B plus an Operations tab for service health. The author chose **C**: it is the only option that shows observability (latency, errors, rate limits, tokens), which is what a platform team uses to run a product. It costs new measurement in the chat and judge paths and a larger demo dataset.

Charts: **a library** (Recharts is my suggestion: widely used in React consoles) versus **hand-built SVG** (what the repository does today for one bar). Hand-built charts for lines with axes, tooltips, legends and keyboard access would be most of a charting library, so a library is chosen. The builder confirms React 19 support and may pick a different library if Recharts fails that or the jsdom tests.

## Design

**Call telemetry.** One new record per model call the service makes for the assistant: time, kind (chat, judge, insights), model, latency, outcome (ok, error, rate-limited, timeout), tokens used, prompt version, source (live or imported), workspace. The chat endpoint, the judge and the insights call write it. Token counts come from the provider response, which `backend/groq_client.py` already reads for pacing and does not yet expose to callers; exposing it follows the pattern of the finish-reason value already shared that way.

**Conversation judge.**
- A new job kind in the existing queue reads one session's stored questions and answers (no extra input from the extension) plus, when available, the page brief snapshot, and returns a strict, parsed verdict: was the question answered (yes, partly, no), was it concise, were citations sensible, was a legal-opinion refusal handled correctly where relevant, an overall grade, and one fixed-label reason. The rubric is a versioned text kept in the repository.
- Trigger: a conversation with no new question for a configurable idle time (default a few minutes, short enough for a demo), found by the worker; plus an "Evaluate now" action in the console for unjudged conversations.
- A verdict at or below a threshold flags the conversation into the improvement loop through the same path as a rating (reason "auto-judge"), so Evolution can find a pattern across human and automatic signals.
- A judge failure records a telemetry row and no verdict; it is retried by the queue's normal rules and never blocks chat.
- The console shows judge results as aggregates and labels, never as conversation text.

**Demo dataset.** A seeded, deterministic script (author runs it; it is never run silently) writes about 30 days of page views, questions, ratings, re-asks, telemetry, judgements and a few prompt-version changes, all tagged as imported and labelled "demo dataset". It is idempotent and never touches live rows. It must be believable: weekday and weekend rhythm, a latency bump, a rate-limit burst, a quality dip that a version change then addresses. That last storyline is demo data and is described as such; it is not evidence of improvement.

**Tabs.**
- *Overview:* alert strip; KPI cards (questions, auto-judged OK share, open flagged conversations, live version); quality-over-time line with prompt-version markers; the improvement-loop step tracker.
- *Adoption:* views and questions trend, feature coverage by section, help demand by section, task mix, re-ask and thumbs-down by intent; every hotspot opens the flagged list it explains.
- *Quality:* judge grade trend and verdict breakdown, rating distribution, flagged conversations with filters (thumbs-down, no answer, re-ask, citation failure, auto-judge), insights brief as today.
- *Evolution:* the loop as a pipeline (flagged, pattern, criterion, tests, fix, review, verify, promoted) with each step's state; version timeline; before/after replay results as a grouped chart; the existing actions and the human approval gate stay exactly as they are.
- *Operations:* latency p50 and p95 over time, error and rate-limit rate, requests per hour, tokens by kind of call, judge queue (waiting count, oldest age), recent failed jobs.

**Alerts.** Rules in code with thresholds in one place (error rate, p95 latency, re-ask rate up, flagged backlog, judge backlog). The strip shows only active alerts, each linking to the chart behind it.

**States and responsiveness.** Every panel has loading, empty ("Not enough data yet.") and error ("Couldn't load ..., Retry") states. Layout works from laptop width down to a narrow window; a table or text summary is available for each chart.

## Order of work

Backend first, then the face: (1) telemetry record and exposing token usage; (2) conversation judge and its job; (3) the seeded demo dataset script; (4) the new read endpoints for time series and health; (5) console shell, global controls and chart components; (6) the five tabs; (7) manual checklist and honest documentation. Each step is testable on its own.

## Testing and honesty

- Backend: telemetry written for ok, error and rate-limited calls and never breaks chat; judge job with the model faked (parsing, bad output, thresholds, one-per-version); idle-trigger logic; seed script determinism and idempotence; new endpoints return aggregates only, no text.
- Frontend: component tests per tab with generated data, empty and error states, filter and drill-down behaviour, chart text equivalents.
- Not verified until run: a real Groq judge run and its rubric against real conversations; the visual result in a real browser; behaviour with a large live dataset.
- Documentation states that the judge is an LLM's opinion, unvalidated against human raters, and that generated data is not usage.

## Settled after the builder check (my calls)

- **Demo data and prompt versions.** Demo data goes into the same demo workspace and assistant as live data, always tagged imported. It never creates `PromptVersion` rows. Demo version changes are version-marker events on imported rows only, so real restore, promote and the "live version" KPI are never touched. The Evolution before/after replay chart shows real replay results only; nothing in it is generated. The demo storyline (a dip, then a version marker) appears only on the Overview, Adoption, Quality and Operations charts, labelled demo.
- **"Flagged", "unreviewed" and the pipeline.** No review status is added. "Unreviewed flagged" means flagged conversations newer than the assistant's last pattern check (the existing "new runs since the last check" idea). The pipeline stepper shows the state of the assistant's current improvement cycle, derived from what the Evolution tab already reads (pattern, criterion, tests, suggested fix, verification, promotion).
- **Judge and the shared queue.** Judge jobs use the existing queue and worker. They yield: the idle scan enqueues a judge job only when no flywheel job is queued or running and no chat arrived in the last minute, one at a time, with its own hourly cap (a single named setting; the builder picks the value). Conversations still unjudged when the cap is hit wait for the next hour, oldest first, and the console shows how many are waiting. The builder decides whether judge jobs also count toward the existing started-jobs limit and must not let them starve chat or the flywheel.
- **Judge and imported data.** The judge never runs on imported conversations and spends no tokens on demo text; the demo script writes its own judgements directly. Demo question and answer text is canned (short templates in the script), never model-generated, and is never shown in the console.
- **Judge grain.** One verdict per conversation per prompt version. The judge also names the one question it judges worst; the flag into the improvement loop is created per conversation and takes its intent and section from that question (or the latest question when none is worse than the rest).
- **Default data source.** The default is "all", with the source badge always visible. KPIs, charts and alerts follow the selected source; any panel that includes demo rows says so, and an alert computed from demo rows is labelled "demo". Switching to "live only" shows exactly what real traffic produced, including an empty console.
- **Telemetry semantics.** Recorded latency is the total time the user waited, including retries, with the attempt count stored beside it. Telemetry older than 90 days is deleted at startup, as expired workspaces already are.

## Left to the builder

Table and column names; endpoint shapes; rubric wording and thresholds; alert thresholds; the idle time default; chart type per metric within the rules above; palette and layout details; the library, subject to the stated check.

## Calls I made (open to change)

- Five tabs named as above; time range, source and version controls are global.
- A charting library is added instead of more hand-built SVG.
- The author's "C" is taken to include generating the labelled demo dataset.
- The judge runs on idle conversations and on demand, one judgement per conversation per prompt version, through the existing job queue.
- Alerts are computed by code only; nothing is sent anywhere.
