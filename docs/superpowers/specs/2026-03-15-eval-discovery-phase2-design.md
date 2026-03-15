# Eval Discovery — Phase 2 Design Spec
**Date:** 2026-03-15
**Status:** Approved
**Supersedes:** `2026-03-15-eval-discovery-design.md` (Phase 1)

---

## Overview

Three targeted changes based on Phase 1 testing findings:

1. **Thinking text stripping** — Qwen3-32b leaks `<think>` blocks to the UI. Apply a two-layer defense: `reasoning_format="hidden"` (scoped to Qwen only) + `_strip_thinking()` safety net inside `groq_chat()` for all responses.
2. **Smarter eval trigger** — Replace the hard count threshold (5 runs) with a hybrid: content-aware heuristic pre-filter → LLM pattern check → "Discover Evals" button appears only after pattern confirmed.
3. **Dynamic, conversation-grounded eval criteria** — Replace the fixed-enum intent detection + single rubric pipeline with a two-call discovery pipeline: pattern check (produces `pattern_summary`) → rubric generation (consumes `pattern_summary`). Eval criteria are grounded in what the user actually tested, not selected from a predefined category list. Output gains a `why_discovered` field.

---

## Model Assignments

| Endpoint | Model | Rationale |
|---|---|---|
| `POST /chat` | `llama-3.1-8b-instant` | Conversational, low-stakes, speed matters |
| `POST /check-pattern` | `openai/gpt-oss-120b` | High-stakes deep transcript analysis; 120B MoE, matches o4-mini on benchmarks |
| `POST /generate-rubric` | `qwen/qwen3-32b` | Structured output from pre-digested context; `reasoning_format="hidden"` scoped here only |

---

## Change 1: Thinking Text Stripping

### Problem
`qwen/qwen3-32b` with `reasoning_format="hidden"` still occasionally leaks `<think>...</think>` content into the response body, reaching the chat UI as visible text.

### Solution — `groq_client.py`

Replace the single `MODEL` constant with three named constants. Add a `model` parameter to `groq_chat()`. Apply `_strip_thinking()` to every response. Pass `reasoning_format="hidden"` only when model is `MODEL_RUBRIC`.

```python
MODEL_CHAT    = "llama-3.1-8b-instant"
MODEL_PATTERN = "openai/gpt-oss-120b"
MODEL_RUBRIC  = "qwen/qwen3-32b"

# The old MODULE-level MODEL constant is removed entirely.

def _strip_thinking(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

def groq_chat(system: str, messages: list[dict], model: str = MODEL_CHAT) -> str:
    full_messages = [{"role": "system", "content": system}] + messages
    kwargs = {"model": model, "messages": full_messages}
    if model == MODEL_RUBRIC:
        kwargs["reasoning_format"] = "hidden"
    content = client.chat.completions.create(**kwargs).choices[0].message.content
    return _strip_thinking(content)
```

- `agent.py` calls `groq_chat()` with no `model` argument — default `MODEL_CHAT` applies automatically. No change to `agent.py` required.
- `reasoning_format` is **not** passed for `MODEL_CHAT` or `MODEL_PATTERN`.
- `_strip_thinking()` runs on all three model outputs as a universal safety net.

### `extract_json()` — moved to `backend/utils.py`

`extract_json()` currently lives in `analyzer.py`. Since `analyzer.py` is being replaced (see Change 3), `extract_json()` is moved to a new `backend/utils.py` module. All callers and tests update their imports accordingly. `utils.py` retains both responsibilities: strip `<think>` blocks and strip markdown fences.

```python
# backend/utils.py
def extract_json(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"```(?:json)?\s*([\s\S]*?)\s*```", r"\1", text)
    return text.strip()
```

Existing tests `test_extract_json_strips_think_blocks` and `test_extract_json_strips_markdown_fences` are updated to import from `backend.utils`.

### `test_groq_client.py` update

The existing test `test_chat_passes_model` imports `MODEL` from `backend.groq_client`. After this change, `MODEL` no longer exists. The test is updated to import `MODEL_CHAT`, `MODEL_PATTERN`, and `MODEL_RUBRIC` instead, and assert the correct model constant is used per call.

---

## Change 2: Hybrid Eval Trigger

### Problem
The fixed threshold of 5 runs is arbitrary and domain-agnostic. A user could run 5 unrelated conversations with no real pattern, or have a clear pattern after 2 runs.

### Solution: Three-Stage Pipeline

```
[Run saved, runCount >= 2]
        ↓
Frontend heuristic pre-filter
(keyword overlap across ≥2 runs' user messages)
        ↓ (overlap found AND patternState is idle or pattern_not_found)
POST /check-pattern { transcripts }
        ↓ (found: true)
Store pattern_summary → patternState = "pattern_found" → show "Discover Evals" button
        ↓ (user clicks)
POST /generate-rubric { pattern_summary }
        ↓
patternState = "complete" → rubric panel shown
```

### Heuristic Pre-filter (frontend, zero cost)

After each run is saved, **if and only if `patternState` is `idle` or `pattern_not_found`** and `runCount >= 2`, scan user messages across all saved transcripts for keyword overlap. Overlap condition: at least one keyword appears in user messages from ≥2 different runs.

Domain keyword list (hardcoded in frontend):
```js
const PATTERN_KEYWORDS = [
  "price", "pricing", "cost", "budget", "demo", "meeting",
  "voicemail", "not interested", "competitor", "qualify"
];
```

If overlap found → fire `POST /check-pattern`. If not → stay in current state, retry on next `saveRun`.

**The heuristic does NOT fire when `patternState` is `checking`, `pattern_found`, `generating`, or `complete`.** This prevents redundant or concurrent calls.

### LLM Pattern Check — `POST /check-pattern`

- Model: `openai/gpt-oss-120b`
- Input: `{ transcripts: [[...]] }`
- Backend reads `ALEX_SYSTEM_PROMPT` internally — no system prompt in request
- Returns: `{ "found": bool, "pattern_summary": str }`

When `found: true`, `pattern_summary` must be non-empty and contain: the behavior name, what agent behavior it probes, and 1-2 quoted transcript exchanges as evidence. The Pydantic model enforces non-empty `pattern_summary` when `found=True` via a field validator.

When `found: false`, `pattern_summary` is an empty string `""`. Frontend sets `patternState = "pattern_not_found"` which immediately allows the next `saveRun` to re-trigger the heuristic (i.e., `pattern_not_found` is not a terminal state — it is functionally equivalent to `idle` for triggering purposes and transitions back to `idle` on the next `saveRun`).

### Concurrency Guard

`checkPattern` is **skipped entirely** if `patternState === "checking"` when `saveRun` fires. The in-flight call completes and sets state normally. There is no re-queue or retry of skipped calls.

### Error Handling

If `/check-pattern` returns a network error or 5xx, `patternState` transitions to `error`. An inline error message is shown near the "Save Run" button (same location as the existing `analyzeError` display). The user can save another run to retry — `saveRun` resets `patternState` from `error` back to `idle` and re-evaluates the heuristic.

### State Machine (`useSession.js`)

| State | Description | Transitions to |
|---|---|---|
| `idle` | Default start state; `runCount < 2` or heuristic not triggered | → `checking` (heuristic passes on saveRun) |
| `checking` | LLM pattern check in flight | → `pattern_found`, `pattern_not_found`, `error` |
| `pattern_found` | `found: true` — "Discover Evals" button rendered | → `generating` (user clicks) |
| `pattern_not_found` | `found: false` — on next `saveRun`, heuristic re-runs immediately | → `checking` (heuristic passes on saveRun) |
| `generating` | User clicked button, `/generate-rubric` call in flight | → `complete`, `error` |
| `complete` | Rubric ready, panel shown | terminal |
| `error` | Network/API failure | → `checking` (heuristic passes on saveRun) |

Note: `pattern_not_found` and `error` do not park in `idle` — they allow the next `saveRun` to immediately re-evaluate the heuristic and transition directly to `checking` if conditions are met.

"Discover Evals" button is **only rendered** when `patternState === "pattern_found"`.

### New `sessionStorage` Keys

| Key | Type | Description |
|---|---|---|
| `pattern_state` | string | One of the states above |
| `pattern_summary` | JSON string | Pattern description from LLM (`null` if not found) |

### Keys Removed from `useSession.js`

- `eval_intent` / `EVAL_INTENT` sessionStorage key — removed entirely. The intent detection step is gone; `why_discovered` in the rubric provides equivalent context.
- `EVAL_TRIGGER_THRESHOLD = 5` constant
- `shouldTriggerAnalysis` derived boolean
- `threshold` exported value

---

## Change 3: Two-Call Eval Discovery Pipeline

### Problem
The previous pipeline used a fixed enum of 6 intent categories. This produces generic eval criteria not grounded in the actual conversation behavior.

### Solution

Two focused calls where the first call's output is the second call's sole input. No transcripts are re-sent in the second call.

### Call 1: `POST /check-pattern` (described in Change 2)

`pattern_summary` example when `found: true`:
```
"Across 3 runs, the prospect repeatedly asked for pricing before agreeing to a demo.
Alex gave the placeholder price on the second ask in Runs 2 and 3.
Pattern: pricing guardrail compliance under repeated pressure.
Evidence — Run 2: Prospect: 'What does it cost?' Alex: '$20/month'.
Run 3: same exchange repeated."
```

### Call 2: `POST /generate-rubric { pattern_summary }`

- Model: `qwen/qwen3-32b` with `reasoning_format="hidden"` + `_strip_thinking()` safety net
- Input: `{ "pattern_summary": str }` — non-empty string enforced by Pydantic validator
- Output: `Rubric` model (see models.py below)
- No transcripts, no system prompt — `pattern_summary` is the complete context

Example output:
```json
{
  "name": "Pricing Redirect Under Repeated Pressure",
  "definition": "Evaluates whether Alex deflects repeated pricing questions toward demo booking without revealing pricing prematurely on a second ask.",
  "good_example": "Prospect: 'Can you just tell me the price?' Alex: 'Totally fair question — the demo is actually where that all comes to life. Can we grab 20 minutes?'",
  "bad_example": "Prospect: 'What does it cost?' Alex: '$20/month' — second ask, should have redirected to demo instead.",
  "why_discovered": "User ran 3 conversations where the prospect pushed for pricing. Alex's compliance with its own guardrail under repeated pressure was the consistent test pattern across runs."
}
```

### What Is Removed
- `INTENT_DETECTION_SYSTEM`, `INTENT_DETECTION_USER_TEMPLATE` from `prompts.py`
- `RUBRIC_GENERATION_SYSTEM`, `RUBRIC_GENERATION_USER_TEMPLATE` from `prompts.py` (replaced by new prompts)
- Fixed intent enum entirely
- `/analyze` endpoint and its route handler

### New Prompts (in `prompts.py`)
- `PATTERN_CHECK_SYSTEM` + `PATTERN_CHECK_USER_TEMPLATE`
- `RUBRIC_GEN_SYSTEM` + `RUBRIC_GEN_USER_TEMPLATE` (no enum, takes `pattern_summary` as input)

---

## API Contract Changes

```python
# REMOVED
POST /analyze
Request:  { "transcripts": [...], "run_count": int }
Response: { "intent": str, "rubric": { name, definition, good_example, bad_example } }

# NEW
POST /check-pattern
Request:  { "transcripts": [[{"role": str, "content": str}], ...] }
Response: { "found": bool, "pattern_summary": str }

POST /generate-rubric
Request:  { "pattern_summary": str }
Response: {
  "name": str,
  "definition": str,
  "good_example": str,
  "bad_example": str,
  "why_discovered": str
}
```

---

## `models.py` Changes

```python
# REMOVED
class AnalyzeRequest
class AnalyzeResponse

# REMOVED
# eval_intent is no longer stored or used anywhere

# MODIFIED — Rubric gains why_discovered
class Rubric(BaseModel):
    name: str
    definition: str
    good_example: str
    bad_example: str
    why_discovered: str  # NEW

# NEW
class PatternCheckRequest(BaseModel):
    transcripts: list[list[Message]]

class PatternCheckResponse(BaseModel):
    found: bool
    pattern_summary: str

    @field_validator("pattern_summary")
    @classmethod
    def non_empty_when_found(cls, v, info):
        if info.data.get("found") and not v.strip():
            raise ValueError("pattern_summary must be non-empty when found=True")
        return v

class RubricRequest(BaseModel):
    pattern_summary: str

    @field_validator("pattern_summary")
    @classmethod
    def non_empty(cls, v):
        if not v.strip():
            raise ValueError("pattern_summary must not be empty")
        return v

# /generate-rubric uses Rubric directly as its response_model.
# No separate RubricResponse class is needed.
```

---

## `analyzer.py` → replaced by `checker.py` + `rubric.py`

`analyzer.py` is replaced by two focused route files:

| File | Endpoint | Model used |
|---|---|---|
| `backend/checker.py` | `POST /check-pattern` | `MODEL_PATTERN` |
| `backend/rubric.py` | `POST /generate-rubric` | `MODEL_RUBRIC` |

Both import `extract_json` from `backend.utils`. `MIN_TURNS_PER_TRANSCRIPT = 3` guard is preserved in `checker.py` — each transcript in the request must have ≥3 turns, same error contract as the old `/analyze`.

`main.py` registers both new routers.

---

## Frontend Component Changes

### `useSession.js`
- Remove: `EVAL_TRIGGER_THRESHOLD`, `shouldTriggerAnalysis`, `threshold`, `evalIntent`, `saveIntent`, `EVAL_INTENT` key
- Add: `patternState` state (default `idle`), `patternSummary` state (default `null`)
- Add: `checkPattern(transcripts)` action — runs heuristic, fires `/check-pattern` if applicable, updates `patternState` + `patternSummary`
- Add: `generateRubric(patternSummary)` action — fires `/generate-rubric`, updates `patternState` to `complete` and saves rubric
- `saveRun`: after incrementing count, calls `checkPattern` if `patternState` is `idle`, `pattern_not_found`, or `error`; skips if `checking`, `pattern_found`, `generating`, or `complete`
- `patternState` stored in `sessionStorage` as `pattern_state`
- `patternSummary` stored in `sessionStorage` as `pattern_summary`

### `App.jsx`
- Remove: `analyzeTranscripts` call, `shouldTriggerAnalysis` effect, `evalIntent` usage
- "Discover Evals" button triggers `generateRubric(patternSummary)` — rendered only when `patternState === "pattern_found"`
- Error display updated to cover both `checking` and `generating` failure states

### `RunControls.jsx`
- Remove: `threshold` prop, "X / 5 to trigger eval" counter display
- Add: "Discover Evals" button — visible only when `patternState === "pattern_found"`, disabled during `generating`

### `EvalSuggestionPanel.jsx`
- Remove: `intent` prop and "We noticed you're testing X" header text
- Add: `why_discovered` displayed as a read-only, visually distinct field (e.g. light blue background) above the editable rubric fields
- Panel header becomes the rubric `name` field
- Downloaded JSON from `handleSaveRubric` includes `why_discovered` — this is intentional; it enriches the exported artifact

### `api.js`
- Remove: `analyzeTranscripts()`
- Add: `checkPattern(transcripts)` → `POST /check-pattern`
- Add: `generateRubric(patternSummary)` → `POST /generate-rubric`

---

## File-level Change Summary

| File | Change type |
|---|---|
| `backend/groq_client.py` | Replace `MODEL` with 3 constants, add `model` param, add `_strip_thinking()` |
| `backend/utils.py` | **New file** — `extract_json()` moved here |
| `backend/models.py` | Remove Analyze models, add Pattern/Rubric models, add `why_discovered`, remove `eval_intent` references |
| `backend/prompts.py` | Remove old prompts, add `PATTERN_CHECK_*` and `RUBRIC_GEN_*` prompts |
| `backend/analyzer.py` | **Deleted** — replaced by `checker.py` and `rubric.py` |
| `backend/checker.py` | **New file** — `POST /check-pattern` route |
| `backend/rubric.py` | **New file** — `POST /generate-rubric` route |
| `backend/main.py` | Register `checker` and `rubric` routers, remove `analyzer` router |
| `frontend/src/hooks/useSession.js` | Replace threshold logic with pattern state machine |
| `frontend/src/App.jsx` | Replace analyze effect with pattern/rubric flow, add Discover Evals button logic |
| `frontend/src/components/RunControls.jsx` | Remove threshold display, add Discover Evals button |
| `frontend/src/components/EvalSuggestionPanel.jsx` | Remove `intent` prop/header, add `why_discovered` field |
| `frontend/src/api.js` | Replace `analyzeTranscripts` with `checkPattern` + `generateRubric` |
| `tests/test_analyzer.py` | Update imports to `backend.utils` for `extract_json` tests |
| `tests/test_groq_client.py` | Update `MODEL` import to `MODEL_CHAT`, `MODEL_PATTERN`, `MODEL_RUBRIC` |

---

## Testing

- **Backend:** pytest — mock Groq for `/check-pattern` (found + not-found + empty pattern_summary cases), `/generate-rubric` (valid pattern_summary → full rubric shape including `why_discovered`), and `_strip_thinking()` unit test
- **Frontend:** Manual — heuristic trigger logic, state machine transitions (including concurrent saveRun guard and error recovery), button visibility gating, panel `why_discovered` rendering, downloaded JSON includes `why_discovered`
