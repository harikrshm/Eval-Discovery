# Phase 3 — Consecutive Pattern Detection + Eval Criteria History Design Spec

## Goal

Make pattern detection fire continuously every 2 runs throughout a session (not just once), accumulate all discovered eval criteria in an editable history list, and rename "rubric" to "eval criteria" everywhere across frontend and backend.

---

## Issue 2a: Consecutive Pattern Checking

### Current Behavior (superseded)

`saveRun` fires a pattern check only when `patternState` is in `["idle", "pattern_not_found", "error"]`. Once `patternState === "complete"`, the check never re-triggers.

**This guard is removed.** The new trigger rule replaces it entirely — `complete` is re-triggerable.

### New Trigger Rule

Add `lastCheckedAtRun: number` (default `0`) to session state, persisted to sessionStorage under key `"last_checked_at_run"`.

On every `saveRun`, after incrementing `nextCount`:

```
if (
  nextCount - lastCheckedAtRun >= 2
  AND patternState NOT IN ["checking", "generating"]
  AND hasKeywordOverlap(nextTranscripts)
)
→ setPatternState("checking")
→ fire apiCheckPattern(nextTranscripts)
→ on success (found=true):  update lastCheckedAtRun = nextCount
                             setPatternSummary(result.pattern_summary)  // overwrites any previous summary
                             setPatternState("pattern_found")
→ on success (found=false): update lastCheckedAtRun = nextCount
                             setPatternState("pattern_not_found")
→ on error: setPatternState("error")
            (lastCheckedAtRun is NOT updated on error — allows retry as soon as the
             threshold condition is re-met, typically the next run since the gap has
             already accumulated)
```

`patternSummary` is a string stored in session state (sessionStorage key `"pattern_summary"`), populated from `result.pattern_summary` when the check API returns `found: true`. Each `found: true` response **overwrites** the previous `patternSummary` — the most recent summary is always used. It is passed unchanged to `generateCriteria(patternSummary)` when the user clicks "Discover Evals". This field already exists in the current codebase — no schema change is needed.

This replaces the current `retriggerable` list check. Effective trigger schedule:
- Run 2: check (if `nextCount - 0 >= 2` and keywords match)
- Run 4: check (if `nextCount - 2 >= 2`)
- Run 6: check
- etc.

`complete`, `pattern_found`, `pattern_not_found`, `idle`, and `error` are all re-triggerable. Only `checking` and `generating` block a new check. If the user ignores the "Discover Evals" button for 2+ runs while in `pattern_found`, the checker re-fires and overwrites `patternSummary` with the newest result — the "Discover Evals" button remains visible (now backed by the updated summary).

### State Machine (updated transitions)

| From state | Event | To state |
|---|---|---|
| any except `checking`/`generating` | `saveRun` + threshold reached + keywords match | `checking` |
| `checking` | API returns `found: true` | `pattern_found` |
| `checking` | API returns `found: false` | `pattern_not_found` |
| `checking` | API error | `error` (`criteriaError` stays `null`) |
| `pattern_found` | user clicks "Discover Evals" | `generating` |
| `error` (from `generateCriteria`) | user clicks "Discover Evals" (retry) | `generating` |
| `generating` | API call succeeds (non-streaming, returns single criterion object) | `complete` |
| `generating` | API error | `error` (`criteriaError` = error message) |
| `complete` | next `saveRun` threshold reached | `checking` (re-trigger) |
| `pattern_found` | next `saveRun` threshold reached | `checking` (re-trigger, overwrites `patternSummary`) |
| `error` (from `apiCheckPattern`) | next `saveRun` threshold reached | `checking` (re-trigger) |

**`lastCheckedAtRun` on `complete`:** `lastCheckedAtRun` is NOT updated when `setPatternState("complete")` is called in `handleDiscoverEvals`. The re-trigger from `complete` is governed entirely by the standard trigger rule — the next `saveRun` that satisfies `nextCount - lastCheckedAtRun >= 2` will fire a check. No special-casing at generation time is needed or performed.

**`error` state disambiguation:** `error` is a single state. The `criteriaError` value (in `App.jsx`) distinguishes the error source: `criteriaError !== null` means the error came from `generateCriteria` (generation failed); `criteriaError === null` means it came from `apiCheckPattern` (pattern check failed). The `apiCheckPattern` error branch **does NOT set** `criteriaError`. This drives "Discover Evals" button visibility — see Top bar badge section.

### Top bar badge behavior

The top bar right section shows two independent elements: a **pattern status badge** (transient) and an **"Eval Criteria (N)" count badge** (persistent once criteria exist). They coexist side-by-side.

**Pattern status badge** (visible only in non-idle transient states):

| `patternState` | `evalCriteria.length` | Badge text | Style |
|---|---|---|---|
| `checking` | any | "Checking for patterns…" | gray |
| `pattern_found` | `=== 0` (first) | "Pattern found" | green |
| `pattern_found` | `> 0` (re-discovery) | "New pattern found" | green |
| `generating` | any | "Generating eval…" | amber |
| `complete` | any | "Eval ready" | blue |
| `error` AND `criteriaError !== null` | any | "Eval generation failed — retry" | red |
| `error` AND `criteriaError === null` | any | "Pattern check failed — retrying automatically" | red |
| `idle` / `pattern_not_found` | any | *(hidden)* | — |

**"Eval Criteria (N)" count badge** (persistent):
- Visible whenever `evalCriteria.length > 0` (regardless of `patternState`)
- Shows: "Eval Criteria (N)" where N = `evalCriteria.length`
- Style: `bg-blue-50 text-blue-700 border border-blue-200`
- Clicking it sets `panelVisible = true` (re-opens dismissed panel)

The "Discover Evals" button appears in the run controls bar when:
- `patternState === "pattern_found"`, OR
- `patternState === "error"` AND `criteriaError !== null` (i.e., error came from `generateCriteria`, not from `apiCheckPattern`)

This distinguishes "retry generation" (valid `patternSummary` available) from "pattern check failed" (no valid `patternSummary` to generate from). A pattern check error shows the error badge but no "Discover Evals" button — the user waits for the next automatic re-check.

### "New Pattern Found" notification when already complete

When `patternState` transitions to `pattern_found` and `evalCriteria.length > 0` (not the first discovery):
- Top bar badge: "New pattern found" (green) — per table above
- Eval criteria panel (if open) shows a notification banner at top: "New pattern detected — click Discover Evals to add another criterion"
- "Discover Evals" button re-appears in run controls bar

---

## Issue 2b: Multiple Eval Criteria (History)

### State Changes in `useSession.js`

Replace `evalRubric: object | null` with `evalCriteria: array` (default `[]`).

Each entry has shape:
```js
{
  id: string,          // crypto.randomUUID() at generation time
  name: string,
  definition: string,
  good_example: string,
  bad_example: string,
  why_discovered: string,
}
```

Replace `saveRubric(rubric)` with `addCriteria(criterion)` — appends to array, persists to sessionStorage.
Add `updateCriteria(id, fields)` — updates a specific criterion by id (for in-panel editing).
Remove `evalTriggered` and `evalDismissed` state — no longer needed; panel is always visible when `evalCriteria.length > 0`.

SessionStorage key: `"eval_criteria"` (replaces `"eval_rubric"`, `"eval_triggered"`, `"eval_dismissed"`).

### `handleDiscoverEvals` in `App.jsx`

```
setCriteriaError(null)         // reset error from any prior failed attempt
setPatternState("generating")
→ call generateCriteria(patternSummary)
→ on success: addCriteria({ id: crypto.randomUUID(), ...result })
             setPanelVisible(true)
             setPatternState("complete")
→ on error:  setCriteriaError(err.message)
             setPatternState("error")
```

### Panel Visibility

Add the following local state to `App.jsx` (none persisted to sessionStorage):
- `panelVisible: boolean` (default `false`)
- `criteriaError: string | null` (default `null`) — tracks generation error message; NOT set by pattern-check errors

**`criteriaError` reset on re-check:** Add a `useEffect` in `App.jsx` watching `patternState`. When `patternState` transitions to `"checking"`, call `setCriteriaError(null)`. This ensures a stale generation error is cleared whenever the pattern check re-fires from `useSession.js`, preventing the "Eval generation failed" badge from persisting into the `checking` state.

- Panel is rendered (and slides in) when `evalCriteria.length > 0 AND panelVisible === true`
- `panelVisible` is set to `true` explicitly in `handleDiscoverEvals` in `App.jsx`, immediately after `addCriteria()` succeeds
- `panelVisible` is set to `false` when the user clicks `×` in the panel header
- `panelVisible` is set to `true` when the user clicks the "Eval Criteria (N)" badge in the top bar

No `evalTriggered`/`evalDismissed` flags needed. Panel cannot be permanently dismissed (replaced by collapse behavior if needed in a future phase).

**Reload behavior (intentional):** On page reload, `evalCriteria` is re-hydrated from sessionStorage but `panelVisible` defaults to `false`. The panel stays closed after reload even when criteria exist. The user re-opens it via the "Eval Criteria (N)" badge in the top bar.

---

## Issue 2c: EvalCriteriaPanel (replaces EvalSuggestionPanel)

File renamed: `EvalSuggestionPanel.jsx` → `EvalCriteriaPanel.jsx`

### Layout

```
┌─────────────────────────────┐
│ EVAL CRITERIA          [×]  │  ← header; × hides panel visually (see Panel Dismissal)
├─────────────────────────────┤
│ ⚡ New pattern detected      │  ← notification banner (only when patternState=pattern_found
│    Click Discover Evals     │    AND evalCriteria.length > 0)
├─────────────────────────────┤
│ ScrollArea (criteria cards) │
│ ┌─────────────────────────┐ │
│ │ #1 Pricing Redirect     │ │  ← criterion card (expanded by default)
│ │ Why discovered: …       │ │
│ │ [Definition field]      │ │
│ │ [Good example field]    │ │
│ │ [Bad example field]     │ │
│ └─────────────────────────┘ │
│ ┌─────────────────────────┐ │
│ │ #2 Objection Handling   │ │  ← second card (collapsed by default if >1 criteria)
│ └─────────────────────────┘ │
├─────────────────────────────┤
│ [Export All] [+ Add Criteria]│  ← fixed footer
└─────────────────────────────┘
```

### Criterion Card

- **Header row:** index (#1, #2…) + criterion name (`Input` for editing) + collapse toggle chevron
- **Why discovered:** read-only muted text (not editable)
- **Editable fields:** `Textarea` for Definition, Good Example, Bad Example
- Changes update `evalCriteria` in session state via `updateCriteria(id, fields)` on blur (no explicit per-card save button)

**Display order:** Criteria are displayed oldest-first (the order they were added). Card #1 = first criterion ever generated; the last card = most recently added.

**Collapse state implementation:** Track a single `expandedIndex: number | null` in `EvalCriteriaPanel` local `useState`, initialized to `evalCriteria.length - 1` (last card). Render `isExpanded={index === expandedIndex}` per card. Card header click toggle logic:
- If clicking a **collapsed** card (`index !== expandedIndex`): set `expandedIndex = index` (expand it, collapsing the previously expanded one)
- If clicking the **already-expanded** card (`index === expandedIndex`): set `expandedIndex = null` (collapse all)

Use a `useEffect` keyed on `evalCriteria.length` to reset `expandedIndex` to `evalCriteria.length - 1` whenever the list grows. This applies equally to `addCriteria()` (auto-generated) and `+ Add Criteria` (manual). State is not persisted — resets on reload.

### Panel Footer (fixed, always visible)

- **"Export All"** `Button variant="default"` — downloads `eval-criteria.json` containing the full `evalCriteria` array
- **"+ Add Criteria"** `Button variant="outline"` — appends a blank criterion to the list:
  ```js
  { id: crypto.randomUUID(), name: "New Criterion", definition: "", good_example: "", bad_example: "", why_discovered: "(manually added)" }
  ```

### Panel Dismissal

The `×` button in the header hides the panel visually (CSS `translateX`) but does NOT clear `evalCriteria` from state. Re-opening is triggered by clicking the "Eval Criteria (N)" count badge in the top bar.

---

## Issue 2d: Rename "Rubric" → "Eval Criteria" Everywhere

### Backend

| Old | New |
|---|---|
| `class Rubric` (models.py) | `class EvalCriteria` |
| `class RubricRequest` | `class CriteriaRequest` |
| `backend/rubric.py` | `backend/criteria.py` |
| `POST /generate-rubric` | `POST /generate-criteria` |
| `router as rubric_router` | `router as criteria_router` |
| `tests/test_rubric.py` | `tests/test_criteria.py` |
| `generate_rubric()` function | `generate_criteria()` |
| `backend.rubric.groq_chat` (mock target) | `backend.criteria.groq_chat` |

Response JSON field names: no change — `name`, `definition`, `good_example`, `bad_example`, `why_discovered` remain (these are already non-"rubric" names).

### Frontend

| Old | New |
|---|---|
| `generateRubric()` (api.js) | `generateCriteria()` |
| `evalRubric` (state) | `evalCriteria` (array) |
| `saveRubric()` (hook) | `addCriteria()` |
| — (new) | `updateCriteria(id, fields)` added to `useSession.js` |
| `evalTriggered`, `evalDismissed` (state) | removed |
| `KEYS.EVAL_RUBRIC` | `KEYS.EVAL_CRITERIA` |
| `KEYS.EVAL_TRIGGERED`, `KEYS.EVAL_DISMISSED` | removed |
| `EvalSuggestionPanel.jsx` | `EvalCriteriaPanel.jsx` |
| "Save Rubric" button text | "Export All" |
| "Discover Evals" button text | "Discover Evals" (unchanged) |
| `rubricError` state (in `App.jsx`) | `criteriaError` state (stays in `App.jsx`) |
| — (new) | `panelVisible` boolean state added to `App.jsx` |

---

## Issue 2e: Expanded Keyword Heuristic

Replace the 10-word `PATTERN_KEYWORDS` list in `useSession.js` with a 40-entry list derived from the Alex/CloudSync system prompt, covering all guardrails and behaviors Alex is instructed to handle:

```js
const PATTERN_KEYWORDS = [
  // Pricing guardrail (don't discuss pricing)
  "price", "pricing", "cost", "costs", "budget", "expensive", "cheap",
  "afford", "pay", "payment", "fee", "how much", "monthly", "annual",

  // Demo booking (primary goal)
  "demo", "meeting", "schedule", "calendar", "book", "appointment",
  "20 minutes", "30 minutes", "time slot",

  // Objection handling (one max, then let go)
  "not interested", "busy", "no thanks", "bad time", "pass",

  // Competitors (never badmouth)
  "competitor", "alternative", "already using", "other solution",
  "snowflake", "databricks",

  // Voicemail
  "voicemail", "leave a message",

  // Qualification
  "qualify", "decision maker", "data team", "pipeline",
];
```

---

## File-Level Change Summary

### Backend

| File | Change |
|---|---|
| `backend/models.py` | Rename `Rubric` → `EvalCriteria`, `RubricRequest` → `CriteriaRequest` |
| `backend/rubric.py` | Rename file to `criteria.py`; rename function, router, imports |
| `backend/main.py` | Update import: `criteria_router` from `backend.criteria` |
| `tests/test_rubric.py` | Rename file to `test_criteria.py`; update all references |
| `backend/prompts.py` | Update docstring references: "rubric" → "eval criteria" (prompt text unchanged) |

### Frontend

| File | Change |
|---|---|
| `frontend/src/api.js` | Rename `generateRubric` → `generateCriteria`, update endpoint to `/generate-criteria` |
| `frontend/src/hooks/useSession.js` | Add `lastCheckedAtRun`, replace `evalRubric`+`evalTriggered`+`evalDismissed` with `evalCriteria[]`, add `addCriteria()`+`updateCriteria()`, update trigger logic, expand `PATTERN_KEYWORDS` |
| `frontend/src/App.jsx` | Update `handleDiscoverEvals`, `criteriaError` state, panel visibility logic, top bar "Eval Criteria (N)" badge |
| `frontend/src/components/EvalSuggestionPanel.jsx` | Rename to `EvalCriteriaPanel.jsx`; full rewrite for multi-criteria list |

---

## Out of Scope

- No pagination of criteria list (all shown in ScrollArea)
- No per-criterion delete (can be added in a future phase)
- No server-side persistence of criteria (sessionStorage only)
- No changes to `POST /check-pattern` endpoint or `PatternCheckRequest`/`PatternCheckResponse` models
