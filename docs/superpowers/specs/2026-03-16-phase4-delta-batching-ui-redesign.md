# Phase 4: Delta Batching Fix & UI Redesign

**Date:** 2026-03-16
**Status:** Spec Approved — Pending Implementation
**Issues addressed:** Issue 1 (pattern detection repeat), Issue 2a–2e (UI improvements)

---

## Issue 1 — Delta Batching for Pattern Detection

### Problem

In `useSession.js`, `saveRun()` passes **all** accumulated transcripts to both `hasKeywordOverlap()` and `apiCheckPattern()` on every check. Since `lastCheckedAtRun` advances after each successful check, old transcripts (runs 1–2) continue to dominate pattern detection in runs 3–4, causing the same eval criteria to be generated even when the tester has moved on to probing a different behavior.

### Root Cause

```js
// Line 172 — sends ALL transcripts, not just new ones
const apiTranscripts = nextTranscripts.map((t) => ...)
```

`lastCheckedAtRun` was designed to track the checkpoint but was never wired to slice the transcript array.

### Fix — Approach A: Pure Delta

Compute a delta slice before the existing guard and use it in both places:

```js
const deltaTranscripts = nextTranscripts.slice(currentLastCheckedAtRun);

// Replace both usages inside the if-block:
hasKeywordOverlap(deltaTranscripts)           // was: nextTranscripts
const apiTranscripts = deltaTranscripts.map(...)  // was: nextTranscripts.map(...)
```

**Files changed:** `frontend/src/hooks/useSession.js` (2 lines inside `saveRun`)
**No backend changes required.**

### Invariants preserved

- `lastCheckedAtRun` is always set to `nextCount` on a successful check (line 176). Because transcripts are appended one per run, `lastCheckedAtRun` equals the number of previously-checked transcripts, making it a valid array slice index into `nextTranscripts`.
- The guard `nextCount - currentLastCheckedAtRun >= 2` ensures `deltaTranscripts.length >= 2` before the check fires, satisfying the backend's minimum-transcript requirement and `hasKeywordOverlap`'s internal `< 2` guard.
- On the very first qualifying window (`lastCheckedAtRun === 0`), `nextTranscripts.slice(0)` returns all transcripts — this is intentional and identical to the current behavior.
- `lastCheckedAtRun` is only updated on success; on API error it is left unchanged so the same delta is retried next run.

---

## Issue 2 — UI Redesign

### 2a — Typography & Spacing

**Goal:** More readable, less cramped — closer to ElevenLabs aesthetic (clean, generous whitespace, larger type).

| Element | Before | After | File |
|---|---|---|---|
| Chat message font | `text-sm` (14px) | `text-base` (16px) | `ChatInterface.jsx` |
| Chat bubble padding | `px-3 py-2` | `px-4 py-3` | `ChatInterface.jsx` |
| Chat bubble max-width | `max-w-[65%]` | `max-w-[72%]` | `ChatInterface.jsx` |
| Message gap | `space-y-3` | `space-y-4` | `ChatInterface.jsx` |
| App header title | `text-sm font-semibold` | `text-sm font-semibold tracking-wide` | `App.jsx` |

---

### 2b — History Panel (left slide-in drawer)

**Goal:** Replace the "Runs (N)" counter in the top-left header with a "History" label that opens a left-side panel showing all completed run transcripts for the current session.

**Data source:** `transcripts` from `useSession` — type `Array<Array<{role: "user"|"assistant", content: string}>>`. No backend changes.

**Display name mapping:** `role === "user"` → `"Prospect"`, `role === "assistant"` → `"Alex"` (matching `format_transcripts` in `prompts.py`).

**New component:** `frontend/src/components/HistoryPanel.jsx`

**Props:**
```js
HistoryPanel({ transcripts, onDismiss })
// transcripts: Array<Array<{role, content}>> — completed runs
// onDismiss: () => void — closes the panel
```

**HistoryPanel content:**
- Header: "History" label + close (×) button, same style as `EvalCriteriaPanel` header (h-11, border-b)
- List of completed runs: "Run 1", "Run 2", etc., each collapsible (ChevronDown/Up toggle)
- Each expanded run shows transcript lines: `Prospect: ...` / `Alex: ...`
- Empty state: "No completed runs yet. End a run to see it here."
- Scrollable body via `ScrollArea`

**Mounting strategy:** Always mounted (never conditionally rendered), same as `EvalCriteriaPanel`. Width is CSS-animated via `transition-[width] duration-200`. Panel width is `384` when `historyVisible && transcripts.length > 0`, else `0`. This preserves the slide-in animation on first open. Unlike `EvalCriteriaPanel` (which uses an inner conditional guard before rendering its content), `HistoryPanel` renders its full content unconditionally inside the outer div — including the empty state message — so no inner conditional is needed.

**"History" label behavior:**
- The label is **always rendered** in the header (never conditionally removed from the DOM).
- When `transcripts.length === 0`: rendered as non-interactive muted text (`text-muted-foreground/50`, no cursor-pointer, no click handler).
- When `transcripts.length > 0`: rendered as a clickable label (`cursor-pointer`, onClick sets `historyVisible(true)`).

**App.jsx changes:**
- Add `historyVisible` state (default `false`)
- Replace `↺ Runs (N)` in top-left header with the always-present `History` label described above (interactive or muted depending on `transcripts.length`)
- Left panel div added before the chat column, always mounted, width controlled by `historyVisible && transcripts.length > 0`
- Pass `transcripts` and `onDismiss={() => setHistoryVisible(false)}` to `HistoryPanel`

**Files:** `App.jsx` (state + layout + label), new `HistoryPanel.jsx`

---

### 2c — Button Layout & Styling

#### Discover Evals — move to top-right header

- Remove from `RunControls`: delete `showDiscoverButton` and `onDiscoverEvals` props from the component signature and remove the conditional `<Button>` block
- Remove from `App.jsx` `<RunControls>` call site: remove `showDiscoverButton` and `onDiscoverEvals` props
- Add directly to `App.jsx` header right section. Visual order (left→right): `PatternBadge` | `Discover Evals button` | `Eval Criteria (N) button` | `···` menu placeholder
- `PatternBadge` is still shown alongside Discover Evals — it conveys "Checking…" / "Generating…" states that are distinct from the button's "pattern found, click to generate" state. Both can appear simultaneously without conflict.
- Visibility: reuse existing `showDiscoverButton` derived value (lines 152–154 of `App.jsx`) — no logic change, just moved to a different render location
- Style: solid black — `className="bg-black text-white hover:bg-black/90 text-xs font-semibold px-3 py-1 rounded-md"`

#### Eval Criteria (N) button — black/white

- Keep as raw `<button>` (not shadcn Button) to maintain the existing pill shape with custom rounding
- Change from blue pill (`bg-blue-50 text-blue-700 border-blue-200`) to: `bg-black text-white hover:bg-black/90 rounded-full px-2.5 py-0.5 text-xs font-semibold`

#### Add Criterion button — match Export All

- In `EvalCriteriaPanel` footer, change `variant="outline"` to the default variant (solid black, white font) — same as Export All button
- Both Export All and Add Criterion use `<Button className="flex-1">` default variant

#### Textarea fields — no resize slider

- In `CriterionCard`, replace `rows={3}` with `className="min-h-[100px] resize-none"`
- Provides ample visible space for typical content without a visible resize handle or scroll bar

**Files:** `RunControls.jsx` (remove props + button), `App.jsx` (remove props from call site, add button to header, restyle Eval Criteria button), `EvalCriteriaPanel.jsx` (Add Criterion variant, textarea class)

---

### 2d — Remove "Eval Ready" Badge

- In `App.jsx` `PatternBadge` component, delete the `patternState === "complete"` block (lines 35–41)
- The `"complete"` state still exists in the state machine and is still set internally; it simply has no visible badge
- The Eval Criteria (N) button in the header already communicates that criteria exist

**Files:** `App.jsx`

---

### 2e — Rename & Remove Counters

| Element | Before | After |
|---|---|---|
| Save button label | "Save Run & Start New" | "END" |
| Bottom-left run counter | `<span>Preview runs: N</span>` | Removed entirely |
| "need N more turns" hint | Shown in bottom bar | Removed entirely |
| Message count in input bar | `N messages` text in pill | Removed entirely |

**"END" rationale:** The button ends the current role-play call/run and starts a fresh one. "END" is concise and maps to the SDR call framing ("end the call") used throughout the product.

**RunControls prop cleanup:** After removing the run counter and the Discover Evals button, two props become unused — remove them from the component signature and the call site:
- `runCount` — no longer rendered
- `showDiscoverButton` and `onDiscoverEvals` — moved to header (see 2c)

**Files:** `RunControls.jsx` (rename button, remove counter span, remove turn hint span, remove dead props), `ChatInterface.jsx` (remove message count span from input pill), `App.jsx` (remove dead props from `<RunControls>` call)

---

## File Change Summary

| File | Changes |
|---|---|
| `frontend/src/hooks/useSession.js` | Delta slice for pattern check (Issue 1) |
| `frontend/src/App.jsx` | History state + layout, History label in header, Discover Evals moved to header, Eval Criteria button restyled to black, "Eval Ready" badge removed, RunControls call site cleaned of dead props |
| `frontend/src/components/HistoryPanel.jsx` | **New file** — left side history drawer (always mounted) |
| `frontend/src/components/RunControls.jsx` | Remove run counter, remove turn hint, remove Discover Evals button + props, remove runCount prop, rename button to "END" |
| `frontend/src/components/ChatInterface.jsx` | Larger fonts/bubble padding/spacing, remove message counter from input pill |
| `frontend/src/components/EvalCriteriaPanel.jsx` | Add Criterion button → solid default variant, textarea → min-h-[100px] resize-none |
