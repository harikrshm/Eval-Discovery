# Phase 3 Issue 1 — UI Redesign (ElevenLabs Style) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current Tailwind-only frontend with shadcn/ui components, adopting an ElevenLabs-inspired white/black minimal design: white background, near-black text, black primary buttons, clean two-panel layout with a 44px top bar and 44px bottom controls bar.

**Architecture:** Manual shadcn/ui installation (no CLI — avoids Tailwind v4 conflict). New `frontend/src/lib/utils.js` and `frontend/src/components/ui/` components. Full rewrites of `App.jsx`, `ChatInterface.jsx`, `RunControls.jsx`, and `EvalCriteriaPanel.jsx`. The data model and hook (`useSession.js`, `api.js`) are unchanged — this plan is pure UI.

**PREREQUISITE:** Plan A (`2026-03-15-phase3-issue2-pattern-detection.md`) must be fully implemented first. This plan builds on `EvalCriteriaPanel.jsx` and the new hook shape from Plan A.

**Tech Stack:** React 19 / Vite / Tailwind CSS v3 / `@radix-ui/react-slot`, `@radix-ui/react-scroll-area`, `@radix-ui/react-label`, `@radix-ui/react-separator`, `class-variance-authority`, `clsx`, `tailwind-merge`, `lucide-react`.

**Run frontend:** `cd C:\Users\91978\eval-discovery\frontend && npm run dev` → visit `http://localhost:5173`

---

## Chunk 1: Setup

### Task 1: Install npm packages

**Files:**
- Modify: `frontend/package.json` (via npm install)

- [ ] **Step 1: Install all required packages**

```bash
cd C:\Users\91978\eval-discovery\frontend
npm install class-variance-authority clsx tailwind-merge lucide-react
npm install @radix-ui/react-slot @radix-ui/react-scroll-area @radix-ui/react-label @radix-ui/react-separator
```

- [ ] **Step 2: Verify packages appear in `frontend/package.json` dependencies**

```bash
cd C:\Users\91978\eval-discovery\frontend && node -e "const p = require('./package.json'); ['class-variance-authority','clsx','tailwind-merge','lucide-react','@radix-ui/react-slot','@radix-ui/react-scroll-area','@radix-ui/react-label','@radix-ui/react-separator'].forEach(k => { if (!p.dependencies[k]) throw new Error('Missing: ' + k); }); console.log('All packages present');"
```

Expected: `All packages present`

---

### Task 2: CSS variables, Tailwind config, utils

**Files:**
- Modify: `frontend/src/index.css`
- Modify: `frontend/tailwind.config.js`
- Create: `frontend/src/lib/utils.js`

- [ ] **Step 1: Replace `frontend/src/index.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  --background: #ffffff;
  --foreground: #111111;
  --muted: #fafafa;
  --muted-foreground: #999999;
  --border: #e8e8e8;
  --card: #ffffff;
  --ring: #111111;
}
```

- [ ] **Step 2: Replace `frontend/tailwind.config.js`**

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        background: "var(--background)",
        foreground: "var(--foreground)",
        muted: {
          DEFAULT: "var(--muted)",
          foreground: "var(--muted-foreground)",
        },
        border: "var(--border)",
        card: "var(--card)",
      },
      ringColor: {
        DEFAULT: "var(--ring)",
      },
    },
  },
  plugins: [],
}
```

- [ ] **Step 3: Create `frontend/src/lib/utils.js`**

```js
import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}
```

---

### Task 3: Create shadcn/ui component files

**Files:**
- Create: `frontend/src/components/ui/button.jsx`
- Create: `frontend/src/components/ui/input.jsx`
- Create: `frontend/src/components/ui/textarea.jsx`
- Create: `frontend/src/components/ui/badge.jsx`
- Create: `frontend/src/components/ui/label.jsx`
- Create: `frontend/src/components/ui/separator.jsx`
- Create: `frontend/src/components/ui/scroll-area.jsx`

- [ ] **Step 1: Create `frontend/src/components/ui/button.jsx`**

```jsx
import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva } from "class-variance-authority";
import { cn } from "../../lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--ring)] disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "bg-foreground text-background hover:bg-foreground/90",
        outline:
          "border border-border bg-background text-foreground hover:bg-muted",
        ghost: "hover:bg-muted hover:text-foreground",
      },
      size: {
        default: "h-9 px-4 py-2",
        sm: "h-8 rounded-md px-3 text-xs",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);

const Button = React.forwardRef(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

export { Button, buttonVariants };
```

- [ ] **Step 2: Create `frontend/src/components/ui/input.jsx`**

```jsx
import * as React from "react";
import { cn } from "../../lib/utils";

const Input = React.forwardRef(({ className, type, ...props }, ref) => {
  return (
    <input
      type={type}
      className={cn(
        "flex h-9 w-full rounded-md border border-border bg-transparent px-3 py-1 text-sm text-foreground shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--ring)] disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      ref={ref}
      {...props}
    />
  );
});
Input.displayName = "Input";

export { Input };
```

- [ ] **Step 3: Create `frontend/src/components/ui/textarea.jsx`**

```jsx
import * as React from "react";
import { cn } from "../../lib/utils";

const Textarea = React.forwardRef(({ className, ...props }, ref) => {
  return (
    <textarea
      className={cn(
        "flex min-h-[60px] w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm text-foreground shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--ring)] disabled:cursor-not-allowed disabled:opacity-50 resize-none",
        className
      )}
      ref={ref}
      {...props}
    />
  );
});
Textarea.displayName = "Textarea";

export { Textarea };
```

- [ ] **Step 4: Create `frontend/src/components/ui/badge.jsx`**

```jsx
import * as React from "react";
import { cva } from "class-variance-authority";
import { cn } from "../../lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors",
  {
    variants: {
      variant: {
        default: "border-transparent bg-foreground text-background",
        outline: "text-foreground border-border",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
);

function Badge({ className, variant, ...props }) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  );
}

export { Badge, badgeVariants };
```

- [ ] **Step 5: Create `frontend/src/components/ui/label.jsx`**

```jsx
import * as React from "react";
import * as LabelPrimitive from "@radix-ui/react-label";
import { cn } from "../../lib/utils";

const Label = React.forwardRef(({ className, ...props }, ref) => (
  <LabelPrimitive.Root
    ref={ref}
    className={cn(
      "text-xs font-semibold uppercase tracking-wide text-muted-foreground",
      className
    )}
    {...props}
  />
));
Label.displayName = LabelPrimitive.Root.displayName;

export { Label };
```

- [ ] **Step 6: Create `frontend/src/components/ui/separator.jsx`**

```jsx
import * as React from "react";
import * as SeparatorPrimitive from "@radix-ui/react-separator";
import { cn } from "../../lib/utils";

const Separator = React.forwardRef(
  (
    { className, orientation = "horizontal", decorative = true, ...props },
    ref
  ) => (
    <SeparatorPrimitive.Root
      ref={ref}
      decorative={decorative}
      orientation={orientation}
      className={cn(
        "shrink-0 bg-border",
        orientation === "horizontal" ? "h-[1px] w-full" : "h-full w-[1px]",
        className
      )}
      {...props}
    />
  )
);
Separator.displayName = SeparatorPrimitive.Root.displayName;

export { Separator };
```

- [ ] **Step 7: Create `frontend/src/components/ui/scroll-area.jsx`**

```jsx
import * as React from "react";
import * as ScrollAreaPrimitive from "@radix-ui/react-scroll-area";
import { cn } from "../../lib/utils";

const ScrollArea = React.forwardRef(({ className, children, ...props }, ref) => (
  <ScrollAreaPrimitive.Root
    ref={ref}
    className={cn("relative overflow-hidden", className)}
    {...props}
  >
    <ScrollAreaPrimitive.Viewport className="h-full w-full rounded-[inherit]">
      {children}
    </ScrollAreaPrimitive.Viewport>
    <ScrollBar />
    <ScrollAreaPrimitive.Corner />
  </ScrollAreaPrimitive.Root>
));
ScrollArea.displayName = ScrollAreaPrimitive.Root.displayName;

const ScrollBar = React.forwardRef(
  ({ className, orientation = "vertical", ...props }, ref) => (
    <ScrollAreaPrimitive.ScrollAreaScrollbar
      ref={ref}
      orientation={orientation}
      className={cn(
        "flex touch-none select-none transition-colors",
        orientation === "vertical" &&
          "h-full w-2.5 border-l border-l-transparent p-[1px]",
        orientation === "horizontal" &&
          "h-2.5 flex-col border-t border-t-transparent p-[1px]",
        className
      )}
      {...props}
    >
      <ScrollAreaPrimitive.ScrollAreaThumb className="relative flex-1 rounded-full bg-border" />
    </ScrollAreaPrimitive.ScrollAreaScrollbar>
  )
);
ScrollBar.displayName = ScrollAreaPrimitive.ScrollAreaScrollbar.displayName;

export { ScrollArea, ScrollBar };
```

- [ ] **Step 8: Verify the dev server starts without import errors**

```bash
cd C:\Users\91978\eval-discovery\frontend && npm run dev
```

Expected: Server starts on `http://localhost:5173`, no console errors about missing modules. Stop the dev server (Ctrl+C) before proceeding.

- [ ] **Step 9: Commit**

```bash
cd C:\Users\91978\eval-discovery
git add frontend/package.json frontend/package-lock.json
git add frontend/src/index.css frontend/tailwind.config.js frontend/src/lib/utils.js
git add frontend/src/components/ui/
git commit -m "feat: install shadcn/ui dependencies and base components (manual, Tailwind v3)"
```

---

## Chunk 2: Component Redesigns

### Task 4: Rewrite ChatInterface.jsx

**Files:**
- Modify: `frontend/src/components/ChatInterface.jsx`

The new design: `bg-white`, right-aligned user bubbles (`bg-[#f5f5f5]`), left-aligned assistant bubbles (`bg-white border border-[#ebebeb]`), smart auto-scroll (only when already at bottom), pill-shaped input bar at bottom.

- [ ] **Step 1: Fully replace `frontend/src/components/ChatInterface.jsx`**

```jsx
import { useState, useRef, useEffect, useCallback } from "react";
import { sendMessage } from "../api";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

export default function ChatInterface({ currentTranscript, onMessage, disabled }) {
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const scrollRef = useRef(null);
  const isAtBottomRef = useRef(true);

  // Track whether user is at the bottom of the scroll container
  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;
    isAtBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
  }

  // Auto-scroll to bottom only when already at bottom
  useEffect(() => {
    if (isAtBottomRef.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [currentTranscript.length, loading]);

  async function handleSend() {
    const text = input.trim();
    if (!text || loading) return;

    const userMsg = { role: "user", content: text };
    onMessage(userMsg);
    setInput("");
    setLoading(true);
    setError(null);

    try {
      const allMessages = [...currentTranscript, userMsg];
      const { reply } = await sendMessage(allMessages);
      onMessage({ role: "assistant", content: reply });
    } catch {
      setError("Failed to reach Alex. Check that the backend is running.");
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  const canSend = !disabled && !loading && input.trim().length > 0;

  return (
    <div className="flex flex-col flex-1 overflow-hidden bg-white">
      {/* Messages scroll area */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto px-6 py-4 space-y-3"
      >
        {currentTranscript.length === 0 && (
          <p className="text-muted-foreground text-sm text-center mt-12">
            Start a conversation with Alex to begin a preview run.
          </p>
        )}

        {currentTranscript.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[65%] px-3 py-2 text-sm leading-relaxed ${
                msg.role === "user"
                  ? "bg-[#f5f5f5] text-foreground rounded-xl rounded-br-sm"
                  : "bg-white border border-[#ebebeb] text-foreground rounded-xl rounded-bl-sm"
              }`}
            >
              {msg.content}
            </div>
          </div>
        ))}

        {/* "Alex is typing…" indicator */}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-white border border-[#ebebeb] text-muted-foreground px-3 py-2 rounded-xl rounded-bl-sm text-sm">
              <span className="inline-flex gap-1">
                <span className="animate-bounce" style={{ animationDelay: "0ms" }}>·</span>
                <span className="animate-bounce" style={{ animationDelay: "150ms" }}>·</span>
                <span className="animate-bounce" style={{ animationDelay: "300ms" }}>·</span>
              </span>
            </div>
          </div>
        )}
      </div>

      {error && (
        <p className="text-red-500 text-xs px-6 pb-2">{error}</p>
      )}

      {/* Pill-shaped input bar */}
      <div className="px-4 pb-4 flex-shrink-0">
        <div className="flex items-center gap-2 bg-[#fafafa] border border-[#e8e8e8] rounded-full px-4 py-1.5">
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={disabled ? "Generating eval — please wait…" : "Message Alex…"}
            disabled={disabled || loading}
            className="flex-1 border-0 bg-transparent shadow-none focus-visible:ring-0 text-sm h-8 px-0"
          />
          <span className="text-xs text-muted-foreground whitespace-nowrap flex-shrink-0">
            {currentTranscript.length}{" "}
            {currentTranscript.length === 1 ? "message" : "messages"}
          </span>
          <Button
            onClick={handleSend}
            disabled={!canSend}
            size="icon"
            className="rounded-full h-8 w-8 flex-shrink-0"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="h-4 w-4"
            >
              <line x1="12" y1="19" x2="12" y2="5" />
              <polyline points="5 12 12 5 19 12" />
            </svg>
          </Button>
        </div>
      </div>
    </div>
  );
}
```

---

### Task 5: Rewrite RunControls.jsx (bottom bar)

**Files:**
- Modify: `frontend/src/components/RunControls.jsx`

The bottom controls bar is now 44px high, white background, `border-t border-border`. Left: run count. Right: "Discover Evals" (outline) + "Save Run & Start New" (default/black).

- [ ] **Step 1: Fully replace `frontend/src/components/RunControls.jsx`**

```jsx
import { Button } from "./ui/button";

export default function RunControls({
  runCount,
  currentTranscriptLength,
  patternState,
  showDiscoverButton,
  onSaveRun,
  onDiscoverEvals,
  saving,
}) {
  const canSave = currentTranscriptLength >= 3;
  const discoverDisabled = patternState === "generating";

  return (
    <div className="h-11 flex items-center gap-3 px-6 border-t border-border bg-white flex-shrink-0">
      {/* Left: run count */}
      <span className="text-sm text-muted-foreground">
        Preview runs:{" "}
        <strong className="text-foreground font-semibold">{runCount}</strong>
      </span>

      <div className="flex-1" />

      {/* Right: action buttons */}
      {!canSave && currentTranscriptLength > 0 && (
        <span className="text-xs text-muted-foreground">
          (need {3 - currentTranscriptLength} more turns)
        </span>
      )}

      {showDiscoverButton && (
        <Button
          variant="outline"
          size="sm"
          onClick={onDiscoverEvals}
          disabled={discoverDisabled}
        >
          Discover Evals
        </Button>
      )}

      <Button
        size="sm"
        onClick={onSaveRun}
        disabled={!canSave || saving}
      >
        Save Run &amp; Start New
      </Button>
    </div>
  );
}
```

---

### Task 6: Rewrite App.jsx (new top bar layout + panel slide)

**Files:**
- Modify: `frontend/src/App.jsx`

New layout: fixed 44px top bar → flex body (chat column + eval panel) → fixed 44px bottom controls bar. The top bar shows `↺ Runs (N)` left, "Eval Discovery · Alex / CloudSync SDR" center, and status Badge + Eval Criteria count badge right.

- [ ] **Step 1: Fully replace `frontend/src/App.jsx`**

```jsx
import { useState, useEffect } from "react";
import { useSession } from "./hooks/useSession";
import { generateCriteria } from "./api";
import ChatInterface from "./components/ChatInterface";
import RunControls from "./components/RunControls";
import EvalCriteriaPanel from "./components/EvalCriteriaPanel";
import { Badge } from "./components/ui/badge";

// Status badge config keyed by patternState + evalCriteria.length
// "New pattern found" when criteria already exist (evalCriteriaCount > 0), "Pattern found" on first discovery
function PatternBadge({ patternState, criteriaError, evalCriteriaCount }) {
  if (patternState === "idle" || patternState === "pattern_not_found") return null;

  if (patternState === "checking") {
    return (
      <Badge variant="outline" className="bg-gray-100 text-gray-500 border-0 animate-pulse">
        Checking for patterns…
      </Badge>
    );
  }
  if (patternState === "pattern_found") {
    return (
      <Badge variant="outline" className="bg-green-50 text-green-700 border border-green-200">
        {evalCriteriaCount === 0 ? "Pattern found" : "New pattern found"}
      </Badge>
    );
  }
  if (patternState === "generating") {
    return (
      <Badge variant="outline" className="bg-amber-50 text-amber-700 border border-amber-200 animate-pulse">
        Generating eval…
      </Badge>
    );
  }
  if (patternState === "complete") {
    return (
      <Badge variant="outline" className="bg-blue-50 text-blue-700 border border-blue-200">
        Eval ready
      </Badge>
    );
  }
  if (patternState === "error") {
    const msg =
      criteriaError !== null
        ? "Eval generation failed — retry"
        : "Pattern check failed — retrying automatically";
    return (
      <Badge variant="outline" className="bg-red-50 text-red-600 border border-red-200">
        {msg}
      </Badge>
    );
  }
  return null;
}

export default function App() {
  const {
    runCount,
    transcripts,
    currentTranscript,
    evalCriteria,
    patternState,
    patternSummary,
    lastCheckedAtRun,
    addMessage,
    saveRun,
    addCriteria,
    updateCriteria,
    setPatternState,
  } = useSession();

  const [criteriaError, setCriteriaError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [panelVisible, setPanelVisible] = useState(false);

  // Reset criteriaError whenever a new pattern check starts
  useEffect(() => {
    if (patternState === "checking") {
      setCriteriaError(null);
    }
  }, [patternState]);

  async function handleSaveRun() {
    if (saving) return;
    setSaving(true);
    try {
      await saveRun({
        currentPatternState: patternState,
        currentTranscripts: transcripts,
        currentRunCount: runCount,
        currentCurrentTranscript: currentTranscript,
        currentLastCheckedAtRun: lastCheckedAtRun,
      });
    } finally {
      setSaving(false);
    }
  }

  async function handleDiscoverEvals() {
    if (!patternSummary) return;
    setCriteriaError(null);
    setPatternState("generating");
    try {
      const result = await generateCriteria(patternSummary);
      addCriteria({ id: crypto.randomUUID(), ...result });
      setPanelVisible(true);
      setPatternState("complete");
    } catch (err) {
      setCriteriaError(err.message);
      setPatternState("error");
    }
  }

  function handleExportAll() {
    const blob = new Blob([JSON.stringify(evalCriteria, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "eval-criteria.json";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function handleAddCriteria() {
    addCriteria({
      id: crypto.randomUUID(),
      name: "New Criterion",
      definition: "",
      good_example: "",
      bad_example: "",
      why_discovered: "(manually added)",
    });
    setPanelVisible(true);
  }

  const isGenerating = patternState === "generating";
  const showDiscoverButton =
    patternState === "pattern_found" ||
    (patternState === "error" && criteriaError !== null);

  return (
    <div className="flex flex-col h-screen bg-background font-['-apple-system,BlinkMacSystemFont,Inter,sans-serif']">

      {/* ── Top Bar (44px) ── */}
      <header className="h-11 flex items-center px-5 border-b border-border flex-shrink-0 bg-white">
        {/* Left: run counter */}
        <div className="flex items-center gap-1 text-sm text-muted-foreground w-40">
          <span>↺</span>
          <span>Runs ({runCount})</span>
        </div>

        {/* Center: title */}
        <div className="flex-1 text-center">
          <span className="text-sm font-semibold text-foreground">Eval Discovery</span>
          <span className="text-xs text-muted-foreground ml-2">· Alex / CloudSync SDR</span>
        </div>

        {/* Right: badges + menu placeholder */}
        <div className="flex items-center gap-2 w-40 justify-end">
          <PatternBadge patternState={patternState} criteriaError={criteriaError} evalCriteriaCount={evalCriteria.length} />

          {evalCriteria.length > 0 && (
            <button
              onClick={() => setPanelVisible(true)}
              className="inline-flex items-center rounded-full border border-blue-200 bg-blue-50 px-2.5 py-0.5 text-xs font-semibold text-blue-700 hover:bg-blue-100 transition-colors"
            >
              Eval Criteria ({evalCriteria.length})
            </button>
          )}

          <button
            className="h-7 w-7 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted"
            aria-label="More options (reserved)"
            disabled
          >
            ···
          </button>
        </div>
      </header>

      {/* ── Body ── */}
      <div className="flex flex-1 overflow-hidden">

        {/* Chat column */}
        <div className="flex flex-col flex-1 overflow-hidden">
          <ChatInterface
            currentTranscript={currentTranscript}
            onMessage={addMessage}
            disabled={isGenerating}
          />
        </div>

        {/* Eval criteria panel — kept mounted to preserve state, hidden via width collapse */}
        {evalCriteria.length > 0 && (
          <div
            className="flex-shrink-0 overflow-hidden border-l border-border bg-[#fafafa] transition-[width] duration-200"
            style={{ width: panelVisible ? 384 : 0 }}
          >
            <div className="w-96 h-full">
              <EvalCriteriaPanel
                evalCriteria={evalCriteria}
                patternState={patternState}
                onDismiss={() => setPanelVisible(false)}
                onUpdate={updateCriteria}
                onAdd={handleAddCriteria}
                onExportAll={handleExportAll}
              />
            </div>
          </div>
        )}
      </div>

      {/* ── Bottom Controls Bar (44px) ── */}
      <RunControls
        runCount={runCount}
        currentTranscriptLength={currentTranscript.length}
        patternState={patternState}
        showDiscoverButton={showDiscoverButton}
        onSaveRun={handleSaveRun}
        onDiscoverEvals={handleDiscoverEvals}
        saving={saving}
      />
    </div>
  );
}
```

---

### Task 7: Rewrite EvalCriteriaPanel.jsx with shadcn components

**Files:**
- Modify: `frontend/src/components/EvalCriteriaPanel.jsx`

Replace the plain Tailwind version from Plan A with the full shadcn-styled version.

- [ ] **Step 1: Fully replace `frontend/src/components/EvalCriteriaPanel.jsx`**

```jsx
import { useState, useEffect } from "react";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Textarea } from "./ui/textarea";
import { Label } from "./ui/label";
import { Separator } from "./ui/separator";
import { ScrollArea } from "./ui/scroll-area";
import { ChevronDown, ChevronUp } from "lucide-react";

function CriterionCard({ criterion, index, isExpanded, onToggle, onUpdate }) {
  function handleBlur(field, value) {
    onUpdate(criterion.id, { [field]: value });
  }

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-white">
      {/* Card header — criterion name Input inline + collapse toggle */}
      {/* Input click is stopped so it doesn't trigger the collapse toggle */}
      <div className="flex items-center gap-2 px-4 py-2.5 hover:bg-muted transition-colors">
        <span className="text-sm font-medium text-foreground flex-shrink-0">
          #{index + 1}
        </span>
        <Input
          defaultValue={criterion.name}
          onBlur={(e) => handleBlur("name", e.target.value)}
          onClick={(e) => e.stopPropagation()}
          className="flex-1 h-7 text-sm"
        />
        <button onClick={onToggle} className="flex-shrink-0 p-1 rounded hover:bg-muted-foreground/10">
          {isExpanded ? (
            <ChevronUp className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          )}
        </button>
      </div>

      {isExpanded && (
        <div className="px-4 pb-4 pt-2 space-y-3 border-t border-border">
          {/* Why discovered — read-only */}
          <div>
            <Label className="mb-1 block">Why discovered</Label>
            <p className="text-xs text-muted-foreground leading-relaxed">
              {criterion.why_discovered}
            </p>
          </div>

          <Separator />

          {/* Definition, Good Example, Bad Example */}
          {[
            { key: "definition", label: "Definition" },
            { key: "good_example", label: "Good Example" },
            { key: "bad_example", label: "Bad Example" },
          ].map(({ key, label }) => (
            <div key={key}>
              <Label className="mb-1 block">{label}</Label>
              <Textarea
                defaultValue={criterion[key]}
                onBlur={(e) => handleBlur(key, e.target.value)}
                rows={3}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function EvalCriteriaPanel({
  evalCriteria,
  patternState,
  onDismiss,
  onUpdate,
  onAdd,
  onExportAll,
}) {
  // expandedIndex: only the last card is expanded by default
  const [expandedIndex, setExpandedIndex] = useState(evalCriteria.length - 1);

  // When the list grows (new criterion appended), expand the new last card
  useEffect(() => {
    setExpandedIndex(evalCriteria.length - 1);
  }, [evalCriteria.length]);

  function handleToggle(index) {
    setExpandedIndex((prev) => (prev === index ? null : index));
  }

  return (
    <div className="flex flex-col h-full bg-[#fafafa]">
      {/* Header */}
      <div className="h-11 flex items-center justify-between px-4 border-b border-border flex-shrink-0">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Eval Criteria
        </span>
        <Button
          variant="ghost"
          size="icon"
          onClick={onDismiss}
          className="h-7 w-7 text-muted-foreground"
          aria-label="Close panel"
        >
          ×
        </Button>
      </div>

      {/* "New pattern detected" notification banner */}
      {patternState === "pattern_found" && evalCriteria.length > 0 && (
        <div className="px-4 py-2 bg-green-50 border-b border-green-200 text-xs text-green-700 flex-shrink-0">
          ⚡ New pattern detected — click Discover Evals to add another criterion
        </div>
      )}

      {/* Scrollable criteria cards */}
      <ScrollArea className="flex-1">
        <div className="p-4 space-y-3">
          {evalCriteria.map((criterion, index) => (
            <CriterionCard
              key={criterion.id}
              criterion={criterion}
              index={index}
              isExpanded={expandedIndex === index}
              onToggle={() => handleToggle(index)}
              onUpdate={onUpdate}
            />
          ))}
        </div>
      </ScrollArea>

      {/* Fixed footer */}
      <Separator />
      <div className="px-4 py-3 flex gap-2 flex-shrink-0">
        <Button className="flex-1" onClick={onExportAll}>
          Export All
        </Button>
        <Button variant="outline" onClick={onAdd}>
          + Add Criteria
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Start both backend and frontend, do a full smoke test**

Start backend: `cd C:\Users\91978\eval-discovery && uvicorn backend.main:app --reload`

Start frontend: `cd C:\Users\91978\eval-discovery\frontend && npm run dev`

Visit `http://localhost:5173` and verify:
- White background, black text, black "Save Run & Start New" button
- 44px top bar with run counter left, title center, badges right
- Chat has white/gray bubble styles, no blue bubbles
- "Alex is typing…" shows as bouncing dots
- Pill-shaped input bar at bottom of chat
- 44px bottom controls bar with "Save Run" and "Discover Evals" buttons using shadcn styles
- After pattern found + "Discover Evals": eval panel slides in from right (384px)
- Eval panel has shadcn Card, Textarea, Label, Separator styling
- "Export All" downloads JSON, "+ Add Criteria" adds blank card
- "Eval Criteria (N)" badge in top bar re-opens panel
- `×` closes panel; state (criteria cards) preserved on reopen

- [ ] **Step 3: Commit**

```bash
cd C:\Users\91978\eval-discovery
git add frontend/src/App.jsx frontend/src/components/ChatInterface.jsx
git add frontend/src/components/RunControls.jsx frontend/src/components/EvalCriteriaPanel.jsx
git commit -m "feat: UI redesign — ElevenLabs-style layout with shadcn/ui components"
```

---

## Out of Scope (do not implement)

- No session reset / "Back" navigation
- No sidebar navigation
- No dark mode
- No animation library beyond Tailwind's built-in `transition` utilities
- No changes to backend, `useSession.js`, or `api.js`
