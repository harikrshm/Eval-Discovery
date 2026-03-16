# Phase 4: Delta Batching Fix & UI Redesign — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix pattern detection repeating across runs by sending only new transcripts to the pattern checker, and redesign the UI with better typography, a History panel, cleaner buttons, and removed clutter.

**Architecture:** Issue 1 is a 2-line fix in `useSession.js` — slice transcripts to the delta window before sending to pattern detection. Issues 2a–2e are pure frontend changes across 5 existing files plus one new `HistoryPanel.jsx` component.

**Tech Stack:** React 18 (JSX), Tailwind CSS v3, shadcn/ui components, Vite dev server, FastAPI backend (Python), pytest for backend regression.

**Spec:** `docs/superpowers/specs/2026-03-16-phase4-delta-batching-ui-redesign.md`

---

## Chunk 1: Logic Fix + Quick UI Cleanups

### Task 1: Delta Batching Fix (`useSession.js`)

**Files:**
- Modify: `frontend/src/hooks/useSession.js` (lines 168–175)

**Context:** `saveRun()` currently sends ALL accumulated transcripts to pattern detection on every check. We need to send only the transcripts since `lastCheckedAtRun`. The variable `currentLastCheckedAtRun` (passed in from App.jsx) equals the number of already-checked transcripts, making it a valid array slice index.

- [ ] **Step 1: Locate the pattern check block in `saveRun`**

Open `frontend/src/hooks/useSession.js`. Find lines ~165–175:

```js
if (
  nextCount - currentLastCheckedAtRun >= 2 &&
  !["checking", "generating"].includes(currentPatternState) &&
  hasKeywordOverlap(nextTranscripts)   // ← sends ALL transcripts
) {
  setPatternState("checking");
  try {
    const apiTranscripts = nextTranscripts.map((t) =>  // ← sends ALL transcripts
      t.map((m) => ({ role: m.role, content: m.content }))
    );
```

- [ ] **Step 2: Apply the delta slice fix**

Add `const deltaTranscripts = nextTranscripts.slice(currentLastCheckedAtRun);` immediately before the `if` block, then replace the two `nextTranscripts` usages inside it:

```js
    const deltaTranscripts = nextTranscripts.slice(currentLastCheckedAtRun);

    if (
      nextCount - currentLastCheckedAtRun >= 2 &&
      !["checking", "generating"].includes(currentPatternState) &&
      hasKeywordOverlap(deltaTranscripts)   // ← only new transcripts
    ) {
      setPatternState("checking");
      try {
        const apiTranscripts = deltaTranscripts.map((t) =>  // ← only new transcripts
          t.map((m) => ({ role: m.role, content: m.content }))
        );
```

No other lines change. `deltaTranscripts` is not used outside this block.

- [ ] **Step 3: Run backend tests to verify no regressions**

```bash
cd C:\Users\91978\eval-discovery
python -m pytest tests/ -v
```

Expected: all tests pass (no backend code changed — this verifies the backend API contract is intact).

- [ ] **Step 4: Manual smoke test**

Start both servers:
```bash
# Terminal 1
cd C:\Users\91978\eval-discovery
uvicorn backend.main:app --reload

# Terminal 2
cd C:\Users\91978\eval-discovery\frontend
npm run dev
```

Open `http://localhost:5173`. Do 2 runs testing pricing (`"how much does it cost"`). Observe pattern found. Do 2 more runs testing a competitor (`"I'm already using Snowflake"`). The second pattern check should detect competitor handling — NOT pricing again.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useSession.js
git commit -m "fix: delta-slice transcripts for pattern detection — only send new runs since last check"
```

---

### Task 2: Remove Counters + Rename "END" (`RunControls.jsx`, `ChatInterface.jsx`)

**Files:**
- Modify: `frontend/src/components/RunControls.jsx`
- Modify: `frontend/src/components/ChatInterface.jsx`

**Context:** Remove visual clutter — the run counter in the bottom bar, the "need N more turns" hint, and the message count in the chat input pill. Rename the save button from "Save Run & Start New" to "END". Also remove dead props from `RunControls` since the run counter and (in Task 4) the Discover Evals button are being removed.

- [ ] **Step 1: Rewrite `RunControls.jsx`**

Replace the entire file with:

```jsx
import { Button } from "./ui/button";

export default function RunControls({
  currentTranscriptLength,
  onSaveRun,
  saving,
}) {
  const canSave = currentTranscriptLength >= 3;

  return (
    <div className="h-11 flex items-center gap-3 px-6 border-t border-border bg-white flex-shrink-0">
      <div className="flex-1" />

      <Button
        size="sm"
        onClick={onSaveRun}
        disabled={!canSave || saving}
      >
        END
      </Button>
    </div>
  );
}
```

Removed: `runCount` prop, `showDiscoverButton` prop, `onDiscoverEvals` prop, `discoverDisabled` variable, run counter span, turn hint span, Discover Evals button block.

- [ ] **Step 2: Remove message count from `ChatInterface.jsx` input pill**

In `frontend/src/components/ChatInterface.jsx`, find and delete the message count span (lines ~117–120):

```jsx
// DELETE this entire span:
<span className="text-xs text-muted-foreground whitespace-nowrap flex-shrink-0">
  {currentTranscript.length}{" "}
  {currentTranscript.length === 1 ? "message" : "messages"}
</span>
```

- [ ] **Step 3: Update `App.jsx` — remove dead props from `<RunControls>` call**

In `frontend/src/App.jsx`, find the `<RunControls>` JSX block (lines ~230–238) and remove the three dead props:

```jsx
// BEFORE:
<RunControls
  runCount={runCount}
  currentTranscriptLength={currentTranscript.length}
  patternState={patternState}
  showDiscoverButton={showDiscoverButton}
  onSaveRun={handleSaveRun}
  onDiscoverEvals={handleDiscoverEvals}
  saving={saving}
/>

// AFTER:
<RunControls
  currentTranscriptLength={currentTranscript.length}
  onSaveRun={handleSaveRun}
  saving={saving}
/>
```

Note: `patternState` is removed too since `RunControls` no longer uses it.

- [ ] **Step 4: Verify in browser**

Open `http://localhost:5173`. Confirm:
- Bottom bar shows only the "END" button (right-aligned), no counters or hints
- Chat input pill has no message count text
- "END" button is disabled until 3 turns are exchanged
- Clicking "END" clears the chat and increments the session (same behavior as before)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/RunControls.jsx frontend/src/components/ChatInterface.jsx frontend/src/App.jsx
git commit -m "feat: rename save button to END, remove run counter and message count clutter"
```

---

### Task 3: Remove "Eval Ready" Badge (`App.jsx`)

**Files:**
- Modify: `frontend/src/App.jsx`

**Context:** The `patternState === "complete"` case in `PatternBadge` renders a "Eval ready" badge. The Eval Criteria (N) button in the header already communicates criteria exist. Remove the badge; keep the state machine value intact.

- [ ] **Step 1: Delete the "complete" block from `PatternBadge` in `App.jsx`**

Find and delete (lines ~35–41):

```jsx
// DELETE this block entirely:
  if (patternState === "complete") {
    return (
      <Badge variant="outline" className="bg-blue-50 text-blue-700 border border-blue-200">
        Eval ready
      </Badge>
    );
  }
```

`handleDiscoverEvals` still calls `setPatternState("complete")` — leave that unchanged.

- [ ] **Step 2: Verify in browser**

Start a conversation, complete 2 runs with a repeated pattern, click Discover Evals. Confirm:
- No "Eval ready" badge appears after criteria are generated
- The Eval Criteria (N) button appears in the header as expected
- All other badges (Checking…, Pattern found, Generating eval…, error states) still appear correctly

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.jsx
git commit -m "feat: remove Eval Ready badge — Eval Criteria button in header is sufficient signal"
```

---

## Chunk 2: Button Restyling + Typography

### Task 4: Move & Restyle Buttons (`App.jsx`, `EvalCriteriaPanel.jsx`)

**Files:**
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/components/EvalCriteriaPanel.jsx`

**Context:** Move "Discover Evals" from the bottom bar (already removed in Task 2) to the top-right header. Restyle Eval Criteria button from blue pill to black. Restyle Add Criterion to match Export All. Fix textareas to use fixed height with no resize handle.

- [ ] **Step 1: Add Discover Evals to the header in `App.jsx`**

Find the header right section in `App.jsx` (lines ~174–194). Current content is: `PatternBadge | Eval Criteria button | ··· button`.

Replace with this order: `PatternBadge | Discover Evals (conditional) | Eval Criteria button | ··· button`:

```jsx
{/* Right: badges + buttons */}
<div className="flex items-center gap-2 w-40 justify-end">
  <PatternBadge patternState={patternState} criteriaError={criteriaError} evalCriteriaCount={evalCriteria.length} />

  {showDiscoverButton && (
    <button
      onClick={handleDiscoverEvals}
      disabled={patternState === "generating"}
      className="bg-black text-white hover:bg-black/90 text-xs font-semibold px-3 py-1 rounded-md disabled:opacity-50"
    >
      Discover Evals
    </button>
  )}

  {evalCriteria.length > 0 && (
    <button
      onClick={() => setPanelVisible(true)}
      className="inline-flex items-center bg-black text-white hover:bg-black/90 rounded-full px-2.5 py-0.5 text-xs font-semibold"
    >
      Eval Criteria ({evalCriteria.length})
    </button>
  )}

  <button
    className="h-7 w-7 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted"
    aria-label="More options (reserved)"
    tabIndex="-1"
    disabled
  >
    ···
  </button>
</div>
```

Note: `showDiscoverButton` derived value (lines 152–154) remains unchanged — just moved to where the button now renders.

- [ ] **Step 2: Restyle Add Criterion button in `EvalCriteriaPanel.jsx`**

Find the footer in `EvalCriteriaPanel.jsx` (lines ~134–141):

```jsx
// BEFORE:
<Button variant="outline" onClick={onAdd}>
  + Add Criterion
</Button>

// AFTER:
<Button className="flex-1" onClick={onAdd}>
  + Add Criterion
</Button>
```

Removing `variant="outline"` makes it use the default solid variant (same as Export All).

- [ ] **Step 3: Fix textarea resize in `EvalCriteriaPanel.jsx`**

In `CriterionCard`, find the `Textarea` element (line ~58):

```jsx
// BEFORE:
<Textarea
  defaultValue={criterion[key]}
  onBlur={(e) => handleBlur(key, e.target.value)}
  rows={3}
/>

// AFTER:
<Textarea
  defaultValue={criterion[key]}
  onBlur={(e) => handleBlur(key, e.target.value)}
  className="min-h-[100px] resize-none"
/>
```

- [ ] **Step 4: Verify in browser**

Open `http://localhost:5173`. Confirm:
- "Discover Evals" button appears in the top-right header (not the bottom bar) when a pattern is found
- "Discover Evals" is black with white text
- "Eval Criteria (N)" button is black with white text (was blue pill)
- Opening the Eval Criteria panel: "Add Criterion" button matches "Export All" (both solid black)
- Definition / Good Example / Bad Example textareas are taller with no resize handle

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.jsx frontend/src/components/EvalCriteriaPanel.jsx
git commit -m "feat: move Discover Evals to header, restyle buttons to black/white, fix textarea sizing"
```

---

### Task 5: Typography & Spacing (`ChatInterface.jsx`, `App.jsx`)

**Files:**
- Modify: `frontend/src/components/ChatInterface.jsx`
- Modify: `frontend/src/App.jsx`

**Context:** Increase font size and spacing in chat for better readability. Add tracking to the app header title.

- [ ] **Step 1: Update chat bubble styles in `ChatInterface.jsx`**

Find the messages scroll area div (line ~63). Change `space-y-3` → `space-y-4`:

```jsx
// BEFORE:
className="flex-1 overflow-y-auto px-6 py-4 space-y-3"

// AFTER:
className="flex-1 overflow-y-auto px-6 py-4 space-y-4"
```

Find the bubble inner div (line ~77). Update max-width, padding, font size:

```jsx
// BEFORE:
className={`max-w-[65%] px-3 py-2 text-sm leading-relaxed ${
  msg.role === "user"
    ? "bg-[#f5f5f5] text-foreground rounded-xl rounded-br-sm"
    : "bg-white border border-[#ebebeb] text-foreground rounded-xl rounded-bl-sm"
}`}

// AFTER:
className={`max-w-[72%] px-4 py-3 text-base leading-relaxed ${
  msg.role === "user"
    ? "bg-[#f5f5f5] text-foreground rounded-xl rounded-br-sm"
    : "bg-white border border-[#ebebeb] text-foreground rounded-xl rounded-bl-sm"
}`}
```

- [ ] **Step 2: Add tracking-wide to app header title in `App.jsx`**

Find the center title span (line ~169):

```jsx
// BEFORE:
<span className="text-sm font-semibold text-foreground">Eval Discovery</span>

// AFTER:
<span className="text-sm font-semibold tracking-wide text-foreground">Eval Discovery</span>
```

- [ ] **Step 3: Verify in browser**

Open `http://localhost:5173` and start a conversation. Confirm:
- Chat messages are noticeably larger (16px vs 14px)
- Bubbles have more padding (feel roomier)
- Spacing between messages is slightly larger
- "Eval Discovery" title in the header has wider letter spacing

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ChatInterface.jsx frontend/src/App.jsx
git commit -m "feat: increase chat font/spacing for readability, add header title tracking"
```

---

## Chunk 3: History Panel

### Task 6: Create `HistoryPanel.jsx`

**Files:**
- Create: `frontend/src/components/HistoryPanel.jsx`

**Context:** New component matching the style of `EvalCriteriaPanel`. Always renders its content (no inner conditional guard). Shows completed run transcripts from client-side state. Each run is collapsible. Role mapping: `"user"` → `"Prospect"`, `"assistant"` → `"Alex"`.

- [ ] **Step 1: Create `HistoryPanel.jsx`**

Create `frontend/src/components/HistoryPanel.jsx` with this content:

```jsx
import { useState } from "react";
import { Button } from "./ui/button";
import { ScrollArea } from "./ui/scroll-area";
import { ChevronDown, ChevronUp } from "lucide-react";

function RunCard({ transcript, index }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-white">
      <button
        onClick={() => setExpanded((prev) => !prev)}
        className="w-full flex items-center justify-between px-4 py-2.5 text-left hover:bg-muted/30"
      >
        <span className="text-sm font-medium text-foreground">Run {index + 1}</span>
        {expanded ? (
          <ChevronUp className="h-4 w-4 text-muted-foreground flex-shrink-0" />
        ) : (
          <ChevronDown className="h-4 w-4 text-muted-foreground flex-shrink-0" />
        )}
      </button>

      {expanded && (
        <div className="px-4 pb-4 pt-2 space-y-2 border-t border-border">
          {transcript.map((msg, i) => (
            <div key={i} className="text-sm leading-relaxed">
              <span className="font-medium text-foreground">
                {msg.role === "user" ? "Prospect" : "Alex"}:
              </span>{" "}
              <span className="text-muted-foreground">{msg.content}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function HistoryPanel({ transcripts, onDismiss }) {
  return (
    <div className="flex flex-col h-full bg-[#fafafa]">
      {/* Header */}
      <div className="h-11 flex items-center justify-between px-4 border-b border-border flex-shrink-0">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          History
        </span>
        <Button
          variant="ghost"
          size="icon"
          onClick={onDismiss}
          className="h-7 w-7 text-muted-foreground"
          aria-label="Close history panel"
        >
          ×
        </Button>
      </div>

      {/* Body */}
      {transcripts.length === 0 ? (
        <p className="text-sm text-muted-foreground text-center mt-12 px-4">
          No completed runs yet. End a run to see it here.
        </p>
      ) : (
        <ScrollArea className="flex-1">
          <div className="p-4 space-y-3">
            {transcripts.map((transcript, index) => (
              <RunCard key={index} transcript={transcript} index={index} />
            ))}
          </div>
        </ScrollArea>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify the component renders without errors**

Import it temporarily in `App.jsx` to check for import errors:

```jsx
import HistoryPanel from "./components/HistoryPanel";
```

Then check the browser console at `http://localhost:5173` — no errors expected. Revert the temporary import if you haven't done Task 7 yet.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/HistoryPanel.jsx
git commit -m "feat: add HistoryPanel component — left slide-in drawer showing completed run transcripts"
```

---

### Task 7: Wire History Panel into `App.jsx`

**Files:**
- Modify: `frontend/src/App.jsx`

**Context:** Add `historyVisible` state, replace the `↺ Runs (N)` top-left label with a "History" label (always rendered, interactive only when transcripts exist), add the always-mounted left panel div before the chat column, and pass the correct props to `HistoryPanel`.

- [ ] **Step 1: Add `historyVisible` state and `HistoryPanel` import to `App.jsx`**

At the top of `App.jsx`, add the import:

```jsx
import HistoryPanel from "./components/HistoryPanel";
```

Inside the `App` function body, add state after the existing `panelVisible` state:

```jsx
const [historyVisible, setHistoryVisible] = useState(false);
```

- [ ] **Step 2: Replace the top-left header label**

Find the header left div (lines ~162–165):

```jsx
// BEFORE:
<div className="flex items-center gap-1 text-sm text-muted-foreground w-40">
  <span>↺</span>
  <span>Runs ({runCount})</span>
</div>

// AFTER:
<div className="flex items-center gap-1 text-sm w-40">
  {transcripts.length > 0 ? (
    <button
      onClick={() => setHistoryVisible(true)}
      className="text-sm font-medium text-foreground hover:text-foreground/70 cursor-pointer"
    >
      History
    </button>
  ) : (
    <span className="text-sm font-medium text-muted-foreground/50">History</span>
  )}
</div>
```

- [ ] **Step 3: Add the always-mounted left panel div**

Find the body section where the chat column starts (lines ~198–207):

```jsx
// BEFORE:
<div className="flex flex-1 overflow-hidden">

  {/* Chat column */}
  <div className="flex flex-col flex-1 overflow-hidden">
    <ChatInterface .../>
  </div>
```

Add the history panel div BEFORE the chat column (same pattern as the right eval panel):

```jsx
<div className="flex flex-1 overflow-hidden">

  {/* History panel — always mounted, width-animated */}
  <div
    className="flex-shrink-0 overflow-hidden border-r border-border bg-[#fafafa] transition-[width] duration-200"
    style={{ width: historyVisible && transcripts.length > 0 ? 384 : 0 }}
  >
    <div className="w-96 h-full">
      <HistoryPanel
        transcripts={transcripts}
        onDismiss={() => setHistoryVisible(false)}
      />
    </div>
  </div>

  {/* Chat column */}
  <div className="flex flex-col flex-1 overflow-hidden">
    <ChatInterface .../>
  </div>
```

- [ ] **Step 4: Keep `runCount` in the destructure — it is still used**

`runCount` is still passed to `saveRun()` inside `handleSaveRun` as `currentRunCount: runCount`. Do NOT remove it from the `useSession()` destructure. No change needed here.

- [ ] **Step 5: Verify in browser**

Open `http://localhost:5173`. Confirm:
- "History" label appears in the top-left (muted/non-clickable when no runs)
- After ending a run ("END" button), "History" becomes clickable
- Clicking "History" slides in the left panel showing "Run 1" collapsible card
- Clicking "Run 1" expands it showing "Prospect: ..." / "Alex: ..." lines
- Clicking × closes the panel
- The chat area shrinks/grows smoothly during the animation
- The eval panel on the right is unaffected

- [ ] **Step 6: Run backend tests one final time**

```bash
cd C:\Users\91978\eval-discovery
python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.jsx
git commit -m "feat: wire HistoryPanel into App — left slide-in history drawer with transcript review"
```

---

## Verification Checklist (run after all tasks)

Open `http://localhost:5173` with both servers running and verify the following end-to-end:

**Layout:**
- [ ] Top bar: `History` (left) | `Eval Discovery · Alex / CloudSync SDR` (center) | badges + buttons (right)
- [ ] Bottom bar: only the `END` button, right-aligned, no counters or hints
- [ ] No message count in the chat input pill

**Issue 1 — Delta batching:**
- [ ] Do 2 pricing runs → pattern found for pricing → click Discover Evals → criteria generated
- [ ] Do 2 competitor runs → new pattern detected for competitor (NOT pricing again)

**Issue 2b — History:**
- [ ] "History" is muted before any runs are completed
- [ ] After first END, "History" becomes clickable, panel slides in smoothly (animated) with Run 1
- [ ] Expanding Run 1 shows "Prospect: ..." and "Alex: ..." lines (not "user"/"assistant")
- [ ] The × close button inside the panel dismisses it (panel slides back closed)
- [ ] After two ENDs, two separate collapsible cards appear: "Run 1" and "Run 2"

**Issue 2c — Buttons:**
- [ ] Discover Evals appears in header (black/white), not in bottom bar
- [ ] Eval Criteria (N) button is black/white pill
- [ ] Add Criterion and Export All buttons match (both solid black)
- [ ] Textareas in criteria cards are tall, no resize handle

**Issue 2d:** No "Eval Ready" badge after criteria are generated

**Issue 2e:** Button says "END", no run counter, no turn hint, no message count
