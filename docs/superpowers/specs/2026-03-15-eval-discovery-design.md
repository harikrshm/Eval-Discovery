# Eval Discovery Agent — Design Spec
**Date:** 2026-03-15
**Status:** Approved

---

## Overview

An intelligent debugging assistant that helps first-time voice agent builders on ElevenLabs reduce reliance on trial-and-error preview runs. The system monitors how many times a user runs a preview of their agent, analyzes the accumulated transcripts to detect what they are testing, and automatically generates evaluation rubrics to accelerate time-to-deployment.

**Problem:** First-time agent builders repeat preview runs to debug behavior, consuming ~50% of credits during development and delaying deployment (>7 days correlates with 70% higher churn). Users are unaware that eval criteria is a faster alternative.

**Solution:** Break the "preview loop" by surfacing auto-generated eval rubrics after detecting a debugging pattern in consecutive preview runs.

---

## Architecture

**Stack:**
- Backend: Python + FastAPI
- Frontend: React (Vite + Tailwind CSS)
- LLM: Groq API (OpenAI-compatible), model: `qwen-qwq-32b`
- Session state: Browser `sessionStorage` (no database)

**Diagram:**

```
┌─────────────────────────────────────────────┐
│              React Frontend                 │
│   (Vite + Tailwind, sessionStorage)         │
│                                             │
│  ┌──────────┐  ┌───────────────────────┐   │
│  │ Chat UI  │  │  Eval Suggestion UI   │   │
│  │ (Agent   │  │  (rubric + edit +     │   │
│  │  Builder)│  │   dismiss)            │   │
│  └────┬─────┘  └──────────┬────────────┘   │
└───────┼────────────────────┼───────────────┘
        │ REST                │ REST
┌───────▼────────────────────▼───────────────┐
│              FastAPI Backend                │
│                                             │
│  ┌──────────────┐   ┌─────────────────────┐│
│  │  POST /chat  │   │  POST /analyze      ││
│  │  Alex sales  │   │  ┌───────────────┐  ││
│  │  agent       │   │  │ 1. Run Monitor│  ││
│  └──────┬───────┘   │  ├───────────────┤  ││
│         │           │  │ 2. Transcript │  ││
│         │           │  │    Analyzer   │  ││
│         │           │  ├───────────────┤  ││
│         │           │  │ 3. Intent     │  ││
│         │           │  │    Detector   │  ││
│         │           │  ├───────────────┤  ││
│         │           │  │ 4. Rubric     │  ││
│         │           │  │    Generator  │  ││
│         │           │  └───────────────┘  ││
│         │           └──────────┬──────────┘│
│  ┌──────▼──────────────────────▼─────────┐ │
│  │     Groq API (qwen-qwq-32b model)     │ │
│  └───────────────────────────────────────┘ │
└────────────────────────────────────────────┘
```

---

## Components

### Backend

| File | Responsibility |
|------|---------------|
| `main.py` | FastAPI app instantiation, CORS, route registration |
| `agent.py` | `/chat` endpoint — runs Alex system prompt against user messages via Groq |
| `analyzer.py` | `/analyze` endpoint — orchestrates the 4-step pipeline |
| `groq_client.py` | Thin wrapper around Groq API (OpenAI-compatible client) |
| `prompts.py` | System prompts for intent detection and rubric generation |
| `models.py` | Pydantic models for all request/response shapes |

### Frontend

| Component | Responsibility |
|-----------|---------------|
| `ChatInterface.jsx` | Renders conversation turns, sends messages to `/chat`, appends replies |
| `RunControls.jsx` | "New Preview Run" button, run counter display, saves transcript to sessionStorage |
| `EvalSuggestionPanel.jsx` | Appears after threshold; shows editable rubric fields, Save and Dismiss actions |
| `useSession.js` | Hook for reading/writing run count and transcripts in `sessionStorage` |
| `api.js` | `fetch` wrappers for `/chat` and `/analyze` |

---

## Data Flow

### Chat (per turn)
```
User types message
→ POST /chat { messages: [{role, content}] }
→ Groq (Alex system prompt)
→ { reply: string }
→ Appended to current transcript in state
```

### Preview Run Cycle
```
User clicks "New Preview Run"
→ Current transcript saved to sessionStorage transcripts[]
→ run_count++ in sessionStorage
→ If run_count >= threshold (default: 5):
    → POST /analyze { transcripts: [[...], ...], run_count: int }
    → Pipeline: monitor → parse → intent detect → rubric gen
    → { intent: string, rubric: { name, definition, good_example, bad_example } }
    → EvalSuggestionPanel renders with editable fields
```

### API Contracts

```python
# POST /chat
Request:  { "messages": [{"role": "user"|"assistant", "content": str}] }
Response: { "reply": str }

# POST /analyze
Request:  { "transcripts": [[{"role": str, "content": str}], ...], "run_count": int }
Response: {
  "intent": str,
  "rubric": {
    "name": str,
    "definition": str,
    "good_example": str,
    "bad_example": str
  }
}
```

---

## Sales Agent — Alex

Alex is an outbound SDR for CloudSync (cloud data platform). Goal: book a demo meeting.

**System prompt (used verbatim in `agent.py`):**

```
# Personality
You are Alex, a sharp and likable sales development rep for CloudSync, a cloud data platform.
You're confident without being cocky — think friendly neighbor who happens to know a lot about
data infrastructure. You respect people's time and never push.

# Environment
You're making outbound follow-up calls to leads who've shown some interest (downloaded a
whitepaper, visited the pricing page, etc.). Your only job is to book a demo with an account
executive. You are NOT the closer.

# Tone
- Upbeat, conversational, never salesy
- Quick and respectful of their time
- Confident in your one-liner but not pushy
- Graceful when rejected — "Totally get it, appreciate your time"
- Natural cadence — avoid sounding like you're reading a script

# Goal
Book a demo meeting. That's it. Deliver a one-line value prop, ask one qualifying question,
and if there's interest, lock in a time. If they say no, exit gracefully.

# Guardrails
- Keep calls under 2 minutes
- Handle ONE objection max, then let it go
- Never badmouth competitors
- If you hit voicemail, leave a 15-second message and end
- Don't discuss pricing — that's for the demo. If the prospect asks a second time and won't drop it, say "$20/month" as a placeholder to unblock the conversation, then immediately redirect to booking the demo
- Never pressure or guilt-trip — if they're not interested, thank them and move on
```

---

## Intent Detection

The intent detector classifies what the agent builder is testing across all preview run transcripts. One category is returned per analysis.

| Intent Category | What the builder is probing |
|----------------|----------------------------|
| `value_prop_clarity` | Does Alex's one-liner land clearly? |
| `objection_handling` | How does Alex handle cost/timing/"not interested"/"already have a tool"? |
| `qualification_questions` | Does Alex ask the right discovery question? |
| `demo_booking_flow` | Does Alex successfully lock in a time when the prospect shows interest? |
| `graceful_exit` | Does Alex handle rejection without pushing? |
| `edge_cases` | Voicemail, off-topic, rude prospects, silence |

The detector uses a Groq/Qwen call with a structured prompt that receives all transcripts and returns one category + a short reasoning string.

---

## Eval Rubric Generator

Takes the detected intent + raw transcripts and generates a structured rubric. Good/bad examples are grounded in actual transcript excerpts.

**Output shape:**
```json
{
  "name": "Objection Handling — Budget Concern",
  "definition": "Evaluates whether Alex acknowledges the budget objection empathetically, pivots once with a value reframe, and exits gracefully if the objection persists.",
  "good_example": "Prospect: 'We don't really have budget right now.' Alex: 'Totally fair — a lot of teams are in the same boat. The demo's just 20 minutes and might help you build the business case if timing improves. Worth a quick look?'",
  "bad_example": "Prospect: 'We don't really have budget right now.' Alex: 'Are you sure? Even a small investment now could save you a lot later. What if I told you we have flexible payment options?'"
}
```

---

## Preview Run Monitoring

- **Threshold:** `run_count >= 5` (configurable via frontend constant `EVAL_TRIGGER_THRESHOLD`)
- **Storage:** `sessionStorage` — resets on page refresh, no backend state needed
- **Threshold enforcement:** Frontend-only. The backend does not validate `run_count`; it analyzes whatever transcripts it receives.
- **Trigger:** Frontend calls `/analyze` once after threshold is first crossed. A `eval_triggered` flag (boolean) in `sessionStorage` prevents re-triggering on subsequent runs.
- **Dismiss behavior:** Clicking Dismiss sets `eval_dismissed: true` in `sessionStorage` and hides the panel. It does NOT reset `run_count` or `eval_triggered` — the suggestion will not reappear in the same session.

### sessionStorage Keys

| Key | Type | Description |
|-----|------|-------------|
| `run_count` | number | Total completed preview runs this session |
| `transcripts` | JSON string (array of arrays) | All saved run transcripts |
| `current_transcript` | JSON string (array) | In-progress conversation turns |
| `eval_triggered` | boolean string (`"true"`) | Whether `/analyze` has been called this session |
| `eval_dismissed` | boolean string (`"true"`) | Whether the user dismissed the suggestion panel |
| `eval_rubric` | JSON string | Last rubric returned by `/analyze` (persists for editing) |

---

## Error Handling

| Scenario | Backend | Frontend |
|----------|---------|----------|
| Groq API failure | Return HTTP 502 | Show "Analysis unavailable, try again" toast |
| Any individual transcript has < 3 turns | Return HTTP 400 | Show "Have a longer conversation first" message |
| Empty transcripts array | Return HTTP 400 | Blocked at frontend before calling `/analyze` |

---

## Project Structure

```
eval-discovery/
├── backend/
│   ├── main.py
│   ├── agent.py
│   ├── analyzer.py
│   ├── groq_client.py
│   ├── prompts.py
│   └── models.py
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── components/
│   │   │   ├── ChatInterface.jsx
│   │   │   ├── RunControls.jsx
│   │   │   └── EvalSuggestionPanel.jsx
│   │   ├── hooks/
│   │   │   └── useSession.js
│   │   └── api.js
│   ├── index.html
│   ├── vite.config.js
│   └── package.json
├── .env
└── requirements.txt
```

---

## Testing

- **Backend:** pytest with mocked Groq responses — test intent categorization logic and rubric JSON shape validation
- **Frontend:** Manual testing of session threshold trigger, panel rendering, edit flow, and dismiss behavior
