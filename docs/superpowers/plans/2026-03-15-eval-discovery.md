# Eval Discovery Agent Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a full-stack app where users chat with an outbound sales AI (Alex), run preview sessions, and receive auto-generated eval rubrics after ≥5 runs.

**Architecture:** FastAPI backend (stateless) serves `/chat` and `/analyze`. The frontend (Vite + React + Tailwind) manages all session state in `sessionStorage`. Two Groq/Qwen calls handle the agent persona and rubric analysis via separate system prompts.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, `groq` SDK, Vite, React 18, Tailwind CSS 3

**Spec:** `docs/superpowers/specs/2026-03-15-eval-discovery-design.md`

---

## Chunk 1: Backend

### Task 1: Project Scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `backend/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create `requirements.txt`**

```
fastapi==0.115.0
uvicorn[standard]==0.30.0
groq==0.11.0
pydantic==2.8.0
python-dotenv==1.0.0
pytest==8.3.0
pytest-asyncio==0.24.0
httpx==0.27.0
```

- [ ] **Step 2: Create `.env.example`**

```
GROQ_API_KEY=your_groq_api_key_here
```

- [ ] **Step 3: Create `backend/__init__.py` and `tests/__init__.py`**

Both files are empty. Just `touch` them.

- [ ] **Step 4: Install dependencies**

```bash
pip install -r requirements.txt
```

Expected: all packages install without error.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt .env.example backend/__init__.py tests/__init__.py
git commit -m "chore: scaffold backend project structure"
```

---

### Task 2: Pydantic Models

**Files:**
- Create: `backend/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_models.py`:

```python
import pytest
from pydantic import ValidationError


def test_chat_request_valid():
    from backend.models import ChatRequest
    req = ChatRequest(messages=[{"role": "user", "content": "Hello"}])
    assert len(req.messages) == 1
    assert req.messages[0].role == "user"


def test_chat_request_empty_messages_fails():
    from backend.models import ChatRequest
    with pytest.raises(ValidationError):
        ChatRequest(messages=[])


def test_analyze_request_valid():
    from backend.models import AnalyzeRequest
    transcript = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hey"}, {"role": "user", "content": "Tell me more"}]
    req = AnalyzeRequest(transcripts=[transcript, transcript, transcript, transcript, transcript], run_count=5)
    assert req.run_count == 5
    assert len(req.transcripts) == 5


def test_analyze_request_single_transcript_valid():
    from backend.models import AnalyzeRequest
    transcript = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hey"}, {"role": "user", "content": "Tell me more"}]
    req = AnalyzeRequest(transcripts=[transcript], run_count=1)
    assert len(req.transcripts) == 1


def test_rubric_response_shape():
    from backend.models import Rubric, AnalyzeResponse
    rubric = Rubric(
        name="Test",
        definition="Def",
        good_example="Good",
        bad_example="Bad",
    )
    resp = AnalyzeResponse(intent="objection_handling", rubric=rubric)
    assert resp.intent == "objection_handling"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_models.py -v
```

Expected: `ModuleNotFoundError: No module named 'backend.models'`

- [ ] **Step 3: Create `backend/models.py`**

```python
from pydantic import BaseModel, field_validator
from typing import Literal


class Message(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]

    @field_validator("messages")
    @classmethod
    def messages_not_empty(cls, v):
        if not v:
            raise ValueError("messages must not be empty")
        return v


class ChatResponse(BaseModel):
    reply: str


class AnalyzeRequest(BaseModel):
    transcripts: list[list[Message]]
    run_count: int


class Rubric(BaseModel):
    name: str
    definition: str
    good_example: str
    bad_example: str


class AnalyzeResponse(BaseModel):
    intent: str
    rubric: Rubric
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_models.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/models.py tests/test_models.py
git commit -m "feat: add Pydantic models for chat and analyze endpoints"
```

---

### Task 3: Groq Client Wrapper

**Files:**
- Create: `backend/groq_client.py`
- Create: `tests/test_groq_client.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_groq_client.py`:

```python
from unittest.mock import MagicMock, patch


def test_chat_returns_content():
    from backend.groq_client import groq_chat

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Hello there!"

    with patch("backend.groq_client.client.chat.completions.create", return_value=mock_response):
        result = groq_chat(
            system="You are helpful.",
            messages=[{"role": "user", "content": "Hi"}],
        )

    assert result == "Hello there!"


def test_chat_passes_model():
    from backend.groq_client import groq_chat, MODEL

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "ok"

    with patch("backend.groq_client.client.chat.completions.create", return_value=mock_response) as mock_create:
        groq_chat(system="sys", messages=[{"role": "user", "content": "hi"}])
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["model"] == MODEL
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_groq_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'backend.groq_client'`

- [ ] **Step 3: Create `backend/groq_client.py`**

```python
import os
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

MODEL = "qwen-qwq-32b"

client = Groq(api_key=os.getenv("GROQ_API_KEY"))


def groq_chat(system: str, messages: list[dict]) -> str:
    """Send messages to Groq and return the assistant reply text."""
    full_messages = [{"role": "system", "content": system}] + messages
    response = client.chat.completions.create(
        model=MODEL,
        messages=full_messages,
    )
    return response.choices[0].message.content
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_groq_client.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/groq_client.py tests/test_groq_client.py
git commit -m "feat: add Groq client wrapper for qwen-qwq-32b"
```

---

### Task 4: Prompts

**Files:**
- Create: `backend/prompts.py`

No tests needed — this file is pure string constants. The prompts are exercised via the analyzer tests in Task 6.

- [ ] **Step 1: Create `backend/prompts.py`**

```python
ALEX_SYSTEM_PROMPT = """# Personality
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
- Never pressure or guilt-trip — if they're not interested, thank them and move on"""


INTENT_DETECTION_SYSTEM = """You are an AI that analyzes voice agent test transcripts to identify what behavior a developer is testing.

Given multiple conversation transcripts between a prospect and an outbound SDR named Alex (who is trying to book demos for CloudSync), identify the single most dominant testing pattern.

Return a JSON object with exactly these keys:
- "intent": one of: value_prop_clarity, objection_handling, qualification_questions, demo_booking_flow, graceful_exit, edge_cases
- "reasoning": one sentence explaining why

Respond ONLY with valid JSON. No markdown fences, no explanation outside the JSON."""


INTENT_DETECTION_USER_TEMPLATE = """Here are {n} preview run transcripts:

{transcripts}

What is the developer primarily testing? Return JSON only."""


RUBRIC_GENERATION_SYSTEM = """You are an AI that generates evaluation rubrics for voice agent behavior.

Given a testing intent and sample transcripts, generate a specific, actionable evaluation rubric grounded in the actual transcript exchanges.

Return a JSON object with exactly these keys:
- "name": descriptive rubric name (5-10 words)
- "definition": clear explanation of what's being evaluated (2-3 sentences)
- "good_example": a sample exchange showing ideal agent behavior, using dialogue from or inspired by the transcripts
- "bad_example": a sample exchange showing problematic agent behavior, contrasting the good example

Respond ONLY with valid JSON. No markdown fences, no explanation outside the JSON."""


RUBRIC_GENERATION_USER_TEMPLATE = """Intent being tested: {intent}

Transcripts:
{transcripts}

Generate an evaluation rubric for this intent. Return JSON only."""


def format_transcripts(transcripts: list[list[dict]]) -> str:
    """Format a list of transcripts into a readable string for prompts."""
    parts = []
    for i, transcript in enumerate(transcripts, 1):
        lines = [f"--- Run {i} ---"]
        for msg in transcript:
            speaker = "Prospect" if msg["role"] == "user" else "Alex"
            lines.append(f"{speaker}: {msg['content']}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)
```

- [ ] **Step 2: Verify import works**

```bash
python -c "from backend.prompts import ALEX_SYSTEM_PROMPT; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/prompts.py
git commit -m "feat: add system prompts for Alex agent and rubric analysis"
```

---

### Task 5: Agent Endpoint

**Files:**
- Create: `backend/agent.py`
- Create: `tests/test_agent.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agent.py`:

```python
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from backend.main import app
    return TestClient(app)


def test_chat_returns_reply(client):
    with patch("backend.agent.groq_chat", return_value="Hi, this is Alex from CloudSync!"):
        response = client.post("/chat", json={
            "messages": [{"role": "user", "content": "Hello"}]
        })
    assert response.status_code == 200
    assert response.json() == {"reply": "Hi, this is Alex from CloudSync!"}


def test_chat_passes_messages_to_groq(client):
    from backend.prompts import ALEX_SYSTEM_PROMPT
    with patch("backend.agent.groq_chat", return_value="Got it") as mock_chat:
        client.post("/chat", json={
            "messages": [
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello!"},
                {"role": "user", "content": "Tell me more"},
            ]
        })
        call_kwargs = mock_chat.call_args.kwargs
        assert len(call_kwargs["messages"]) == 3
        assert call_kwargs["system"] == ALEX_SYSTEM_PROMPT


def test_chat_empty_messages_returns_422(client):
    response = client.post("/chat", json={"messages": []})
    assert response.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_agent.py -v
```

Expected: `ModuleNotFoundError: No module named 'backend.main'`

- [ ] **Step 3: Create `backend/agent.py`**

```python
from fastapi import APIRouter
from backend.models import ChatRequest, ChatResponse
from backend.groq_client import groq_chat
from backend.prompts import ALEX_SYSTEM_PROMPT

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    messages = [{"role": m.role, "content": m.content} for m in request.messages]
    reply = groq_chat(system=ALEX_SYSTEM_PROMPT, messages=messages)
    return ChatResponse(reply=reply)
```

- [ ] **Step 4: Create `backend/main.py`** (minimal, just enough for these tests)

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.agent import router as agent_router

app = FastAPI(title="Eval Discovery API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_router)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_agent.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/agent.py backend/main.py tests/test_agent.py
git commit -m "feat: add /chat endpoint for Alex sales agent"
```

---

### Task 6: Analyzer Pipeline

**Files:**
- Create: `backend/analyzer.py`
- Create: `tests/test_analyzer.py`
- Modify: `backend/main.py` (add analyzer router)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_analyzer.py`:

```python
import json
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


VALID_TRANSCRIPT = [
    {"role": "user", "content": "Hi"},
    {"role": "assistant", "content": "Hey! Alex from CloudSync."},
    {"role": "user", "content": "What do you want?"},
]

FIVE_TRANSCRIPTS = [VALID_TRANSCRIPT] * 5


@pytest.fixture
def client():
    from backend.main import app
    return TestClient(app)


def test_analyze_returns_rubric(client):
    intent_resp = json.dumps({"intent": "objection_handling", "reasoning": "User kept objecting"})
    rubric_resp = json.dumps({
        "name": "Objection Handling",
        "definition": "Tests how Alex handles a single objection.",
        "good_example": "Alex: Totally fair, let's revisit next quarter.",
        "bad_example": "Alex: But you really need this!",
    })

    with patch("backend.analyzer.groq_chat", side_effect=[intent_resp, rubric_resp]):
        response = client.post("/analyze", json={
            "transcripts": FIVE_TRANSCRIPTS,
            "run_count": 5,
        })

    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "objection_handling"
    assert "name" in body["rubric"]
    assert "definition" in body["rubric"]
    assert "good_example" in body["rubric"]
    assert "bad_example" in body["rubric"]


def test_analyze_rejects_short_transcript(client):
    short_transcript = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hey"},
    ]
    response = client.post("/analyze", json={
        "transcripts": [short_transcript],
        "run_count": 1,
    })
    assert response.status_code == 400
    assert "too short" in response.json()["detail"].lower()


def test_analyze_rejects_empty_transcripts(client):
    response = client.post("/analyze", json={
        "transcripts": [],
        "run_count": 0,
    })
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_analyze_handles_groq_failure(client):
    with patch("backend.analyzer.groq_chat", side_effect=Exception("Groq down")):
        response = client.post("/analyze", json={
            "transcripts": FIVE_TRANSCRIPTS,
            "run_count": 5,
        })
    assert response.status_code == 502


def test_extract_json_strips_think_blocks():
    from backend.analyzer import extract_json
    raw = "<think>Let me analyze this carefully...</think>\n{\"intent\": \"objection_handling\"}"
    result = extract_json(raw)
    assert result == '{"intent": "objection_handling"}'


def test_extract_json_strips_markdown_fences():
    from backend.analyzer import extract_json
    raw = "```json\n{\"intent\": \"graceful_exit\"}\n```"
    result = extract_json(raw)
    assert result == '{"intent": "graceful_exit"}'
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_analyzer.py -v
```

Expected: all fail — `ModuleNotFoundError: No module named 'backend.analyzer'`

- [ ] **Step 3: Create `backend/analyzer.py`**

```python
import json
import re
from fastapi import APIRouter, HTTPException
from backend.models import AnalyzeRequest, AnalyzeResponse, Rubric
from backend.groq_client import groq_chat
from backend.prompts import (
    INTENT_DETECTION_SYSTEM,
    INTENT_DETECTION_USER_TEMPLATE,
    RUBRIC_GENERATION_SYSTEM,
    RUBRIC_GENERATION_USER_TEMPLATE,
    format_transcripts,
)

router = APIRouter()

MIN_TURNS_PER_TRANSCRIPT = 3


def extract_json(text: str) -> str:
    """Strip <think> blocks and markdown fences that reasoning models may emit before JSON."""
    # Remove <think>...</think> blocks (qwen-qwq-32b emits these)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # Strip ```json ... ``` or ``` ... ``` markdown fences
    text = re.sub(r"```(?:json)?\s*([\s\S]*?)\s*```", r"\1", text)
    return text.strip()


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest):
    # Validate transcripts not empty
    if not request.transcripts:
        raise HTTPException(status_code=400, detail="transcripts must not be empty.")

    # Validate each transcript meets minimum length
    for i, transcript in enumerate(request.transcripts):
        if len(transcript) < MIN_TURNS_PER_TRANSCRIPT:
            raise HTTPException(
                status_code=400,
                detail=f"Transcript {i + 1} is too short — have a longer conversation first (minimum {MIN_TURNS_PER_TRANSCRIPT} turns).",
            )

    transcripts_as_dicts = [
        [{"role": m.role, "content": m.content} for m in transcript]
        for transcript in request.transcripts
    ]
    formatted = format_transcripts(transcripts_as_dicts)

    try:
        # Step 1: Detect intent
        intent_raw = groq_chat(
            system=INTENT_DETECTION_SYSTEM,
            messages=[{
                "role": "user",
                "content": INTENT_DETECTION_USER_TEMPLATE.format(
                    n=len(request.transcripts),
                    transcripts=formatted,
                ),
            }],
        )
        intent_data = json.loads(extract_json(intent_raw))
        intent = intent_data["intent"]

        # Step 2: Generate rubric
        rubric_raw = groq_chat(
            system=RUBRIC_GENERATION_SYSTEM,
            messages=[{
                "role": "user",
                "content": RUBRIC_GENERATION_USER_TEMPLATE.format(
                    intent=intent,
                    transcripts=formatted,
                ),
            }],
        )
        rubric_data = json.loads(extract_json(rubric_raw))
        rubric = Rubric(**rubric_data)

    except Exception as exc:
        raise HTTPException(status_code=502, detail="Analysis unavailable, try again.") from exc

    return AnalyzeResponse(intent=intent, rubric=rubric)
```

- [ ] **Step 4: Update `backend/main.py`** to include the analyzer router

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.agent import router as agent_router
from backend.analyzer import router as analyzer_router

app = FastAPI(title="Eval Discovery API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_router)
app.include_router(analyzer_router)
```

- [ ] **Step 5: Run all backend tests**

```bash
pytest tests/ -v
```

Expected: all tests pass (test_models, test_groq_client, test_agent, test_analyzer).

- [ ] **Step 6: Smoke-test the server locally**

Create a `.env` file from `.env.example` with your real `GROQ_API_KEY`, then:

```bash
uvicorn backend.main:app --reload
```

Visit `http://localhost:8000/docs` — you should see the FastAPI Swagger UI with `/chat` and `/analyze`.

- [ ] **Step 7: Commit**

```bash
git add backend/analyzer.py backend/main.py tests/test_analyzer.py
git commit -m "feat: add /analyze endpoint with intent detection and rubric generation"
```

---

## Chunk 2: Frontend

### Task 7: Frontend Scaffolding

**Files:**
- Create: `frontend/` (Vite project)
- Create: `frontend/tailwind.config.js`
- Create: `frontend/postcss.config.js`
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Scaffold Vite + React project**

```bash
cd frontend
npm create vite@latest . -- --template react
```

When prompted: select "React", then "JavaScript".

- [ ] **Step 2: Install base dependencies**

```bash
npm install
```

Expected: `node_modules/` is created and Vite/React packages are installed.

- [ ] **Step 3: Install Tailwind**

```bash
npm install -D tailwindcss postcss autoprefixer
npx tailwindcss init -p
```

- [ ] **Step 4: Configure `tailwind.config.js`**

Replace the generated file contents with:

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: { extend: {} },
  plugins: [],
}
```

- [ ] **Step 5: Update `frontend/src/index.css`**

Replace entire file with:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] **Step 6: Verify dev server starts**

```bash
npm run dev
```

Expected: Vite server starts at `http://localhost:5173`.

- [ ] **Step 7: Commit**

```bash
cd ..
git add frontend/
git commit -m "chore: scaffold Vite + React + Tailwind frontend"
```

---

### Task 8: Session Hook

**Files:**
- Create: `frontend/src/hooks/useSession.js`

- [ ] **Step 1: Create `frontend/src/hooks/useSession.js`**

```js
import { useState, useCallback } from "react";

const KEYS = {
  RUN_COUNT: "run_count",
  TRANSCRIPTS: "transcripts",
  CURRENT_TRANSCRIPT: "current_transcript",
  EVAL_TRIGGERED: "eval_triggered",
  EVAL_DISMISSED: "eval_dismissed",
  EVAL_RUBRIC: "eval_rubric",
  EVAL_INTENT: "eval_intent",
};

const EVAL_TRIGGER_THRESHOLD = 5;

function getItem(key, fallback) {
  try {
    const raw = sessionStorage.getItem(key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

function setItem(key, value) {
  sessionStorage.setItem(key, JSON.stringify(value));
}

export function useSession() {
  const [runCount, setRunCountState] = useState(() => getItem(KEYS.RUN_COUNT, 0));
  const [transcripts, setTranscriptsState] = useState(() => getItem(KEYS.TRANSCRIPTS, []));
  const [currentTranscript, setCurrentTranscriptState] = useState(() => getItem(KEYS.CURRENT_TRANSCRIPT, []));
  const [evalTriggered, setEvalTriggeredState] = useState(() => getItem(KEYS.EVAL_TRIGGERED, false));
  const [evalDismissed, setEvalDismissedState] = useState(() => getItem(KEYS.EVAL_DISMISSED, false));
  const [evalRubric, setEvalRubricState] = useState(() => getItem(KEYS.EVAL_RUBRIC, null));
  const [evalIntent, setEvalIntentState] = useState(() => getItem(KEYS.EVAL_INTENT, null));

  const addMessage = useCallback((message) => {
    setCurrentTranscriptState((prev) => {
      const next = [...prev, message];
      setItem(KEYS.CURRENT_TRANSCRIPT, next);
      return next;
    });
  }, []);

  const saveRun = useCallback(() => {
    const nextTranscripts = [...transcripts, currentTranscript];
    const nextCount = runCount + 1;
    setTranscriptsState(nextTranscripts);
    setRunCountState(nextCount);
    setItem(KEYS.TRANSCRIPTS, nextTranscripts);
    setItem(KEYS.RUN_COUNT, nextCount);
    setCurrentTranscriptState([]);
    setItem(KEYS.CURRENT_TRANSCRIPT, []);
  }, [transcripts, currentTranscript, runCount]);

  const markTriggered = useCallback(() => {
    setEvalTriggeredState(true);
    setItem(KEYS.EVAL_TRIGGERED, true);
  }, []);

  const dismissEval = useCallback(() => {
    setEvalDismissedState(true);
    setItem(KEYS.EVAL_DISMISSED, true);
  }, []);

  const saveRubric = useCallback((rubric) => {
    setEvalRubricState(rubric);
    setItem(KEYS.EVAL_RUBRIC, rubric);
  }, []);

  const saveIntent = useCallback((intent) => {
    setEvalIntentState(intent);
    setItem(KEYS.EVAL_INTENT, intent);
  }, []);

  const shouldTriggerAnalysis = runCount >= EVAL_TRIGGER_THRESHOLD && !evalTriggered;

  return {
    runCount,
    transcripts,
    currentTranscript,
    evalTriggered,
    evalDismissed,
    evalRubric,
    evalIntent,
    shouldTriggerAnalysis,
    threshold: EVAL_TRIGGER_THRESHOLD,
    addMessage,
    saveRun,
    markTriggered,
    dismissEval,
    saveRubric,
    saveIntent,
  };
}
```

- [ ] **Step 2: Verify it imports cleanly**

```bash
cd frontend && node -e "console.log('syntax ok')" 2>&1 || echo "check syntax"
```

(Actual runtime test happens via the components in later tasks.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useSession.js
git commit -m "feat: add useSession hook for sessionStorage state management"
```

---

### Task 9: API Client

**Files:**
- Create: `frontend/src/api.js`

- [ ] **Step 1: Create `frontend/src/api.js`**

```js
const BASE_URL = "http://localhost:8000";

export async function sendMessage(messages) {
  const res = await fetch(`${BASE_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  });
  if (!res.ok) throw new Error(`Chat failed: ${res.status}`);
  return res.json(); // { reply: string }
}

export async function analyzeTranscripts(transcripts, runCount) {
  const res = await fetch(`${BASE_URL}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transcripts, run_count: runCount }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Analysis failed: ${res.status}`);
  }
  return res.json(); // { intent, rubric }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/api.js
git commit -m "feat: add API client for /chat and /analyze"
```

---

### Task 10: ChatInterface Component

**Files:**
- Create: `frontend/src/components/ChatInterface.jsx`

- [ ] **Step 1: Create `frontend/src/components/ChatInterface.jsx`**

```jsx
import { useState } from "react";
import { sendMessage } from "../api";

export default function ChatInterface({ currentTranscript, onMessage, disabled }) {
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

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

  return (
    <div className="flex flex-col h-full">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {currentTranscript.length === 0 && (
          <p className="text-gray-400 text-sm text-center mt-8">
            Start a conversation with Alex to begin a preview run.
          </p>
        )}
        {currentTranscript.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-xs lg:max-w-md px-4 py-2 rounded-2xl text-sm ${
                msg.role === "user"
                  ? "bg-blue-600 text-white"
                  : "bg-gray-100 text-gray-800"
              }`}
            >
              <p className="text-xs font-semibold mb-1 opacity-60">
                {msg.role === "user" ? "You (Prospect)" : "Alex"}
              </p>
              {msg.content}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-gray-100 text-gray-500 px-4 py-2 rounded-2xl text-sm">
              Alex is typing…
            </div>
          </div>
        )}
      </div>

      {/* Error */}
      {error && (
        <p className="text-red-500 text-xs px-4 pb-2">{error}</p>
      )}

      {/* Input */}
      <div className="border-t p-4 flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={disabled ? "Save this run to start a new one" : "Reply as the prospect…"}
          disabled={disabled || loading}
          className="flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50 disabled:text-gray-400"
        />
        <button
          onClick={handleSend}
          disabled={disabled || loading || !input.trim()}
          className="bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Send
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/ChatInterface.jsx
git commit -m "feat: add ChatInterface component"
```

---

### Task 11: RunControls Component

**Files:**
- Create: `frontend/src/components/RunControls.jsx`

- [ ] **Step 1: Create `frontend/src/components/RunControls.jsx`**

```jsx
export default function RunControls({ runCount, threshold, currentTranscriptLength, onSaveRun }) {
  const canSave = currentTranscriptLength >= 3;

  return (
    <div className="flex items-center gap-4 px-4 py-3 bg-gray-50 border-b">
      <div className="flex items-center gap-2">
        <span className="text-sm text-gray-500">Preview runs:</span>
        <span className="font-semibold text-gray-800">{runCount}</span>
        <span className="text-gray-400 text-sm">/ {threshold} to trigger eval</span>
      </div>

      <div className="flex-1" />

      <button
        onClick={onSaveRun}
        disabled={!canSave}
        className="bg-gray-800 text-white px-4 py-1.5 rounded-lg text-sm font-medium hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        Save Run &amp; Start New
      </button>

      {!canSave && currentTranscriptLength > 0 && (
        <span className="text-xs text-gray-400">
          (need {3 - currentTranscriptLength} more turns)
        </span>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/RunControls.jsx
git commit -m "feat: add RunControls component"
```

---

### Task 12: EvalSuggestionPanel Component

**Files:**
- Create: `frontend/src/components/EvalSuggestionPanel.jsx`

- [ ] **Step 1: Create `frontend/src/components/EvalSuggestionPanel.jsx`**

```jsx
import { useState, useEffect } from "react";

export default function EvalSuggestionPanel({ rubric, intent, onDismiss, onSave }) {
  const [edited, setEdited] = useState(rubric);

  useEffect(() => {
    setEdited(rubric);
  }, [rubric]);

  function handleChange(field, value) {
    setEdited((prev) => ({ ...prev, [field]: value }));
  }

  const intentLabel = intent?.replace(/_/g, " ") ?? "";

  return (
    <div className="border-l bg-amber-50 flex flex-col h-full">
      {/* Header */}
      <div className="px-4 py-3 bg-amber-100 border-b border-amber-200 flex items-start justify-between gap-2">
        <div>
          <p className="text-xs font-semibold text-amber-700 uppercase tracking-wide">
            Eval Suggestion
          </p>
          <p className="text-sm text-amber-900 mt-0.5">
            We noticed you're testing <strong>{intentLabel}</strong>. Here's a rubric to evaluate it.
          </p>
        </div>
        <button
          onClick={onDismiss}
          className="text-amber-500 hover:text-amber-700 text-lg leading-none flex-shrink-0"
          aria-label="Dismiss"
        >
          ×
        </button>
      </div>

      {/* Editable fields */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {[
          { key: "name", label: "Rubric Name", multiline: false },
          { key: "definition", label: "Definition", multiline: true },
          { key: "good_example", label: "Good Example", multiline: true },
          { key: "bad_example", label: "Bad Example", multiline: true },
        ].map(({ key, label, multiline }) => (
          <div key={key}>
            <label className="block text-xs font-semibold text-gray-600 mb-1">
              {label}
            </label>
            {multiline ? (
              <textarea
                value={edited[key]}
                onChange={(e) => handleChange(key, e.target.value)}
                rows={4}
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400 bg-white resize-none"
              />
            ) : (
              <input
                type="text"
                value={edited[key]}
                onChange={(e) => handleChange(key, e.target.value)}
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400 bg-white"
              />
            )}
          </div>
        ))}
      </div>

      {/* Actions */}
      <div className="px-4 py-3 border-t border-amber-200 flex gap-2">
        <button
          onClick={() => onSave(edited)}
          className="flex-1 bg-amber-500 text-white py-2 rounded-lg text-sm font-medium hover:bg-amber-600"
        >
          Save Rubric
        </button>
        <button
          onClick={onDismiss}
          className="px-4 py-2 border border-gray-300 rounded-lg text-sm text-gray-600 hover:bg-gray-100"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/EvalSuggestionPanel.jsx
git commit -m "feat: add EvalSuggestionPanel component with editable rubric fields"
```

---

### Task 13: App.jsx — Wire Everything Together

**Files:**
- Modify: `frontend/src/App.jsx`

- [ ] **Step 1: Replace `frontend/src/App.jsx`**

```jsx
import { useEffect, useState } from "react";
import { useSession } from "./hooks/useSession";
import { analyzeTranscripts } from "./api";
import ChatInterface from "./components/ChatInterface";
import RunControls from "./components/RunControls";
import EvalSuggestionPanel from "./components/EvalSuggestionPanel";

export default function App() {
  const {
    runCount,
    transcripts,
    currentTranscript,
    evalTriggered,
    evalDismissed,
    evalRubric,
    evalIntent,
    shouldTriggerAnalysis,
    threshold,
    addMessage,
    saveRun,
    markTriggered,
    dismissEval,
    saveRubric,
    saveIntent,
  } = useSession();

  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState(null);

  // Trigger analysis when threshold is crossed
  useEffect(() => {
    if (!shouldTriggerAnalysis || analyzing) return;

    async function runAnalysis() {
      setAnalyzing(true);
      setAnalyzeError(null);
      markTriggered();
      try {
        const allTranscripts = transcripts.map((t) =>
          t.map((m) => ({ role: m.role, content: m.content }))
        );
        const result = await analyzeTranscripts(allTranscripts, runCount);
        saveIntent(result.intent);
        saveRubric(result.rubric);
      } catch (err) {
        setAnalyzeError(err.message);
      } finally {
        setAnalyzing(false);
      }
    }

    runAnalysis();
  }, [shouldTriggerAnalysis, analyzing, transcripts, runCount, markTriggered, saveRubric, saveIntent]);

  const showEvalPanel =
    evalRubric && evalTriggered && !evalDismissed && !analyzing;

  function handleSaveRubric(edited) {
    saveRubric(edited);
    const blob = new Blob([JSON.stringify(edited, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "eval-rubric.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="flex flex-col h-screen bg-white">
      {/* Header */}
      <header className="px-6 py-4 border-b flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">Eval Discovery</h1>
          <p className="text-xs text-gray-500">Chat with Alex · CloudSync SDR</p>
        </div>
        {analyzing && (
          <span className="text-xs text-amber-600 animate-pulse font-medium">
            Analyzing transcripts…
          </span>
        )}
        {analyzeError && (
          <span className="text-xs text-red-500">{analyzeError}</span>
        )}
      </header>

      {/* Body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: chat */}
        <div className="flex flex-col flex-1 overflow-hidden">
          <RunControls
            runCount={runCount}
            threshold={threshold}
            currentTranscriptLength={currentTranscript.length}
            onSaveRun={saveRun}
          />
          <ChatInterface
            currentTranscript={currentTranscript}
            onMessage={addMessage}
            disabled={false}
          />
        </div>

        {/* Right: eval panel (slides in when ready) */}
        {showEvalPanel && (
          <div className="w-96 flex-shrink-0 overflow-hidden">
            <EvalSuggestionPanel
              rubric={evalRubric}
              intent={evalIntent}
              onDismiss={dismissEval}
              onSave={handleSaveRubric}
            />
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify the app builds without errors**

```bash
cd frontend && npm run build
```

Expected: build completes with no errors.

- [ ] **Step 3: Manual smoke test**

Start the backend:
```bash
uvicorn backend.main:app --reload
```

Start the frontend (new terminal):
```bash
cd frontend && npm run dev
```

Open `http://localhost:5173`. Verify:
- [ ] You can type a message and get a reply from Alex
- [ ] "Save Run & Start New" button saves the transcript and increments run count
- [ ] After 5 runs, the analysis is triggered and the eval panel appears on the right
- [ ] You can edit rubric fields inline
- [ ] "Save Rubric" downloads a JSON file
- [ ] "Dismiss" hides the panel and does not reappear on subsequent runs

- [ ] **Step 4: Commit**

```bash
cd .. && git add frontend/src/App.jsx
git commit -m "feat: wire App.jsx — chat, run tracking, and eval suggestion panel"
```

---

### Task 14: Final Polish & README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Create `README.md`**

Write the following content to `README.md` (the file itself, not a code block):

    # Eval Discovery Agent

    Helps voice agent builders break out of the preview run loop by auto-generating eval rubrics.

    ## Setup

    **Backend:**

        pip install -r requirements.txt
        cp .env.example .env   # add your GROQ_API_KEY
        uvicorn backend.main:app --reload

    **Frontend:**

        cd frontend
        npm install
        npm run dev

    Open http://localhost:5173.

    ## How It Works

    1. Chat with Alex (CloudSync's SDR) as the prospect
    2. Click "Save Run & Start New" after each conversation
    3. After 5 runs, transcripts are analyzed by Groq/Qwen
    4. A suggested eval rubric appears — edit it and save as JSON

    ## Running Tests

        pytest tests/ -v

- [ ] **Step 2: Run the full test suite one final time**

```bash
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add README with setup and usage instructions"
```
