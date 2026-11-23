# Patent Assist: page-view import format

The demo console is populated by hand-written data, not by a simulation. You write one JSON object per line
(JSON Lines, UTF-8) and `scripts/import_page_views.py` validates and imports it. The importer never calls a
model and never reads any key.

**Labelling rule.** Everything this importer writes carries `source: "imported"`. Imported data shows as
**demo dataset** in the console and never counts as measured effect: before/after signals are computed only
from rows whose source is not `imported`, and the replay before/after table stays the measured headline. The
example file's session ids start with `demo-` so it cannot be mistaken for real visitors; the importer itself does not enforce a prefix. Imported answers are recorded against the agent's **current** prompt version, not against a record's `assist_version`, so a replay of an imported flagged conversation is not tied to the version that produced the imported answer. Timestamps are not sanity-checked (a date far in the past or future is accepted); the console labels the dataset as demo data.

**File limit.** The importer reads the file line by line and refuses a file larger than 20 MiB (20 * 1024 * 1024 bytes) with the message `<file> is larger than the 20971520-byte limit; nothing was imported`. A live heartbeat or question for a session id that is stored as imported is refused (409), so a session id that was imported stays imported.

Example file: [`examples/assist/page_views.example.jsonl`](../examples/assist/page_views.example.jsonl)
(12 page views, 17 questions, 5 flagged conversations over three US patents).

## One record

One line is one page view: one visitor session on one patent, and the questions asked in it.

| Field | Required | Type and rule |
| --- | --- | --- |
| `session_id` | yes | 8-64 characters, ASCII letters, digits and hyphens. Unique within the file. |
| `patent_number` | yes | A US patent or application number in compact form (`US12401720B1`, `US20250119367A1`). Stripped and upper-cased. No other country. |
| `started_at` | yes | ISO 8601 string. With a time zone offset or `Z` it is converted to UTC; without one it is read as UTC. |
| `dwell_ms` | yes | Integer, 0 to 86,400,000 (one day): time on the page. |
| `sections` | no | Object `{section: visible_ms}`. Names: `claims`, `description`, `classifications`, `citations`, `family`, `legal_events`, `landscapes`. Each value an integer 0 to 86,400,000. |
| `brief` | when a question is flagged | The page brief, validated with the same model as the live API (`backend/core/assist/brief.py`, same caps). Its `patent_number` must equal the record's. Keep it small by hand: a few claims, the abstract, a few classifications. |
| `questions` | no | List of 0 to 40 questions (below). |

Each question:

| Field | Required | Type and rule |
| --- | --- | --- |
| `text` | yes | The visitor's question, 1-2000 characters, not blank (stripped, like the live API). |
| `answer` | yes | The assistant's answer, 1-16000 characters. |
| `assist_version` | yes | Integer >= 1: the prompt version number that produced the answer. |
| `intent` | no | One of `claim_explain`, `claim_scope`, `status_family`, `citations_prior_art`, `classification`, `law_concept`, `navigation_help`, `legal_opinion_request`, `other`. Computed with `classify_intent(text)` when absent. |
| `section_focus` | no | One of the seven section names, or `null`. Computed with `primary_section(intent)` when the key is absent; an explicit `null` keeps "no section". |
| `rating` | no | `up`, `down` or `no_answer`. |
| `rating_reason` | no | Up to 500 characters; needs a `rating`. |
| `reasked` | no | `true`/`false` (default `false`): the visitor asked the same thing again. |
| `citation_ok` | no | `true`/`false` (default `true`): the answer's citations passed the check. |
| `asked_at` | no | ISO 8601 as `started_at`. Default: `started_at` plus the question's position in seconds (the first question is +1 s). |

Unknown keys are rejected at every level (record, question, brief and its parts). Booleans and integers must be
real JSON booleans and integers (`"5000"` and `true` for a duration are errors). Blank lines are skipped. A
duplicate key in one object, or a session id used on two lines, is an error.

### What is derived

- `bounce` is never written: it is computed with the live rule, `dwell_ms < 10000` and no section visible
  3000 ms or more (`backend/core/assist/bounce.py`, the same function the tracker uses).
- The page view's `assist_version` is that of the session's first question (none when it has no questions).
- A question is **flagged** when `rating` is `down` or `no_answer`, `reasked` is `true`, or `citation_ok` is
  `false`. A flagged question requires the record's `brief`, because the whole conversation is saved as a
  flagged run: every turn in order (the question as `user`, the answer as `assistant`) plus the brief. One run
  per session; its `created_at` is the session's `started_at`. A record with a flagged question must fit the
  live conversation caps: at most 21 questions (42 turns) and 120,000 characters of questions and answers.
- The brief is stored in the run as the validated, re-serialised model, not as typed.

## Worked example

One line (shown wrapped here; in the file it is a single line):

```json
{"session_id": "demo-a-0003", "patent_number": "US12401720B1", "started_at": "2026-11-03T14:05:12Z",
 "dwell_ms": 262000, "sections": {"claims": 140000, "classifications": 22000, "citations": 8000},
 "brief": {"patent_number": "US12401720B1", "title": "System and method for server based control",
           "claims": [{"number": 1, "text": "A device comprising: multiple microphones ...", "independent": true}]},
 "questions": [
   {"text": "Is claim 1 valid?", "answer": "I cannot give a legal opinion on validity. ...",
    "rating": "down", "rating_reason": "Refused instead of pointing me to the prior art citations",
    "assist_version": 2, "asked_at": "2026-11-03T14:07:40Z"},
   {"text": "What do the CPC codes mean?", "answer": "H04L67/12 covers protocols for ...",
    "rating": "up", "assist_version": 2, "asked_at": "2026-11-03T14:09:02Z"}]}
```

It becomes one page view (not a bounce: 262 s), two questions (intents `legal_opinion_request` and
`classification`, computed; section focus `null` and `classifications`), and one flagged conversation
(because the first question was rated `down`) holding four turns and the brief.

## Running it

```
python scripts/import_page_views.py FILE [--dry-run] [--database-url URL]
```

- The database is `--database-url`, else `$DATABASE_URL`, else the local SQLite file. The schema must be
  migrated (`alembic upgrade head`), and the assist agent must exist (`python scripts/setup_assist.py`) with
  `ASSIST_AGENT_ID` set to its id: the rows go into that agent's workspace.
- **All-or-nothing.** Every line is validated first and every problem is printed as `line N: field: message`.
  If any line is invalid, nothing is imported. Otherwise everything is written in one transaction.
- **`--dry-run`** validates and prints the counts (page views, questions, flagged conversations) and writes
  nothing. It does not connect to a database, so it cannot detect conflicts or the run cap against existing data.
- **Idempotent.** Importing the same session ids again updates them: the page view is merged with the live
  rules (each duration becomes the larger of the stored and the file's value; `started_at` and the first
  `assist_version` are kept), that session's imported questions are **replaced** by the file's questions, and
  its flagged run is updated in place (a run is never deleted: removing a flag from a record does not remove
  the run it created earlier).
- **Live data is never touched.** A session id that already exists as `live` data (a page view, questions or a
  run) is a conflict: the import aborts and nothing is written. So does a session id that exists for another patent.
- **Run cap.** Flagged conversations become runs. The live API's cap of 1000 flagged runs per workspace
  applies to the import (the live cap of 50 manual runs does not): a file that would exceed it is rejected.
- The output never contains the database password (the URL is printed masked), the workspace token or a key,
  and database driver errors are never echoed.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Imported (or the dry run passed). |
| 2 | Invalid input: bad lines, a conflict with live data, the run cap, an empty, missing or non-UTF-8 file. Nothing was imported. |
| 3 | Database or configuration error: no assist agent (run `scripts/setup_assist.py`, set `ASSIST_AGENT_ID`), schema out of date, connection failure, or a failure during the transaction (rolled back). |
