# Phase 3 — UI Redesign (ElevenLabs Style) Design Spec

## Goal

Replace the current Tailwind-only frontend with shadcn/ui components, adopting an ElevenLabs-inspired visual language: white background, near-black text, black primary action buttons, minimal borders, and generous whitespace.

## Reference

ElevenLabs conversation preview UI — white/near-white background with subtle borders, black circular/pill action buttons, thin top bar, clean two-panel split (chat + right eval panel), bottom controls bar.

---

## Layout & Structure

### Top Bar (44px, white, `border-b border-border`)

- **Left:** `↺ Runs (N)` — display-only counter (no click action; decorative label showing completed run count)
- **Center:** App title "Eval Discovery" in semibold + agent subtitle "· Alex / CloudSync SDR" in muted text
- **Right:** Pattern status `Badge` (visible when not `idle`/`pattern_not_found`) + placeholder `···` icon button (no action, reserved for future use)
- No "Back" or session-reset link — session reset is out of scope for this phase

### Chat Panel (fills remaining width, white background)

- `ScrollArea` for messages — auto-scrolls to bottom only when the user is already at the bottom (standard chat UX: scrolling up to read history is not interrupted)
- User messages: right-aligned, `bg-[#f5f5f5]` bubble, `rounded-xl rounded-br-sm`
- Assistant messages: left-aligned, white bubble with `border border-[#ebebeb]`, `rounded-xl rounded-bl-sm`
- "Alex is typing…" shown as assistant bubble with three animated dots while awaiting reply
- **Input bar** at bottom of panel: pill-shaped wrapper (`rounded-full`) containing `Input` + turn count label + black circular send `Button`
  - Send button disabled when: input is empty, assistant is typing, or `isGenerating` is true
  - Turn count shows `currentTranscript.length` (raw message count, e.g. "4 messages")

### Eval Panel (384px fixed right, `bg-[#fafafa]`, `border-l border-border`)

Rendered as a plain `div` (not a `Sheet`) with a CSS slide-in transition (`transform translate-x-full → translate-x-0`) when `patternState === "complete"` and `evalRubric` is set. Using a plain div preserves the two-panel flex layout and keeps chat interactive while the eval panel is open.

Contains:
1. **Header** — "EVAL SUGGESTION" all-caps muted label + rubric `name` in semibold + dismiss `×` `Button variant="ghost"` (`size="icon"`)
2. **Why Discovered** — `Separator` + read-only section with muted `Label` + paragraph text (not editable)
3. **Editable fields** (inside `ScrollArea`) — `Label` + `Textarea` for: Definition, Good Example, Bad Example; `Label` + `Input` for: Rubric Name
4. **Footer** — `Separator` + `Button variant="default"` (black) "Save Rubric ↓" + `Button variant="outline"` "Dismiss"

### Bottom Run Controls Bar (44px, white, `border-t border-border`)

- **Left:** "Preview runs:" muted text + bold run count
- **Right (flex end):**
  - "Discover Evals" `Button variant="outline"` — visible when `patternState === "pattern_found"` OR `patternState === "error"` (retry path); disabled when `patternState === "generating"`; hidden at `complete` (eval panel is already open)
  - "Save Run & Start New" `Button variant="default"` (black) — disabled when `currentTranscriptLength < 3` or `saving === true`
  - "(need N more turns)" muted hint when `currentTranscriptLength > 0 && currentTranscriptLength < 3`

### Discover Evals → Eval Panel Flow (explicit)

1. `patternState === "pattern_found"` → "Discover Evals" button appears in bottom bar
2. User clicks → `patternState` becomes `"generating"` → top bar badge changes to amber "Generating eval…", button disabled
3. On success → `patternState` becomes `"complete"`, `evalRubric` set → eval panel slides in from right
4. On failure → `patternState` becomes `"error"` → top bar badge changes to red with error message from `rubricError`

---

## Error Display

`rubricError` (from `App.jsx` state) is displayed inside the top bar's right section when `patternState === "error"`:
- A `Badge` with error styling showing: "Eval generation failed — retry" (dismisses when user clicks "Discover Evals" again)
- Replaces the pattern-found badge in the same slot

---

## Color & Typography

| Token | Value | Usage |
|---|---|---|
| `--background` | `#ffffff` | Page background |
| `--foreground` | `#111111` | Primary text, default button fill |
| `--muted` | `#fafafa` | Panel backgrounds, surfaces |
| `--muted-foreground` | `#999999` | Labels, secondary text, placeholders |
| `--border` | `#e8e8e8` | All dividers and input borders |
| Primary button | `bg-foreground text-background` | Save Run, Send |
| Outline button | `border-border bg-white text-foreground` | Discover Evals, Dismiss |

**Status badge styles (Tailwind arbitrary values):**

| State | Badge text | Style |
|---|---|---|
| `checking` | "Checking for patterns…" | `bg-gray-100 text-gray-500` (no border) |
| `pattern_found` | "Pattern found" | `bg-green-50 text-green-700 border border-green-200` |
| `generating` | "Generating eval…" | `bg-amber-50 text-amber-700 border border-amber-200` |
| `complete` | "Eval ready" | `bg-blue-50 text-blue-700 border border-blue-200` |
| `error` | "Eval generation failed — retry" | `bg-red-50 text-red-600 border border-red-200` |
| `idle` / `pattern_not_found` | — | Badge hidden |

**Typography:** System font stack (`-apple-system, BlinkMacSystemFont, "Inter", sans-serif`). No custom font install needed.

---

## shadcn/ui Installation Approach

**Do not use `npx shadcn@latest init`** — as of early 2026, the shadcn CLI defaults to Tailwind v4 config syntax and will conflict with the existing Tailwind v3 setup.

Instead, install manually:

1. Install npm packages:
   ```bash
   npm install class-variance-authority clsx tailwind-merge lucide-react
   npm install @radix-ui/react-slot @radix-ui/react-scroll-area @radix-ui/react-label @radix-ui/react-separator
   ```

2. Add `frontend/src/lib/utils.js`:
   ```js
   import { clsx } from "clsx";
   import { twMerge } from "tailwind-merge";
   export function cn(...inputs) { return twMerge(clsx(inputs)); }
   ```

3. Add CSS variables to `frontend/src/index.css` `:root` block (see File Changes section)

4. Update `frontend/tailwind.config.js` to extend theme with CSS variable tokens (no `darkMode: ["class"]` — light only)

5. Copy component source files from shadcn docs into `frontend/src/components/ui/`

---

## shadcn/ui Component Mapping

| UI element | shadcn component | Notes |
|---|---|---|
| All buttons | `Button` | variants: `default` (black fill), `outline`, `ghost` |
| Chat message input | `Input` | inside pill wrapper `div` |
| Rubric text fields | `Textarea` | `resize-none` |
| Rubric name field | `Input` | single line |
| Eval panel container | Plain `div` with CSS transition | NOT `Sheet` — preserves flex layout, keeps chat interactive |
| Status indicator | `Badge` | `variant="outline"` + custom color classes per state |
| Chat scroll container | `ScrollArea` | smart auto-scroll (only when at bottom) |
| Field labels | `Label` | all-caps via `className`, `text-xs tracking-wide text-muted-foreground` |
| Dividers | `Separator` | between eval panel sections |

---

## File-Level Change Summary

| File | Change |
|---|---|
| `frontend/package.json` | Add: `class-variance-authority`, `clsx`, `tailwind-merge`, `lucide-react`, `@radix-ui/react-slot`, `@radix-ui/react-scroll-area`, `@radix-ui/react-label`, `@radix-ui/react-separator` (no `@radix-ui/react-dialog` — Sheet not used) |
| `frontend/tailwind.config.js` | Extend theme with CSS variable tokens: `background`, `foreground`, `muted`, `muted-foreground`, `border`, `card`; NO `darkMode` key |
| `frontend/src/index.css` | Add `:root` CSS variable block for shadcn tokens; keep existing Tailwind directives |
| `frontend/src/lib/utils.js` | New — `cn()` utility |
| `frontend/src/components/ui/button.jsx` | New — shadcn Button |
| `frontend/src/components/ui/input.jsx` | New — shadcn Input |
| `frontend/src/components/ui/textarea.jsx` | New — shadcn Textarea |
| `frontend/src/components/ui/badge.jsx` | New — shadcn Badge |
| `frontend/src/components/ui/scroll-area.jsx` | New — shadcn ScrollArea |
| `frontend/src/components/ui/label.jsx` | New — shadcn Label |
| `frontend/src/components/ui/separator.jsx` | New — shadcn Separator |
| `frontend/src/App.jsx` | New layout: top bar + two-panel flex + bottom controls; eval panel as conditional div with slide transition |
| `frontend/src/components/ChatInterface.jsx` | Redesigned: `ScrollArea`, new bubble styles, pill input bar, black send button |
| `frontend/src/components/RunControls.jsx` | Redesigned: `Button` components, clean layout |
| `frontend/src/components/EvalSuggestionPanel.jsx` | Redesigned: `Label`, `Textarea`, `Input`, `Separator`, `Button`; remove Sheet |

---

## Out of Scope

- No session reset / "Back" navigation (requires hook changes)
- No sidebar navigation
- No dark mode / theme toggle (light only)
- No animation library beyond Tailwind's built-in `transition` utilities
- No changes to backend, `useSession.js` hook, or `api.js`
