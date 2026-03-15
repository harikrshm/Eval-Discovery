# Phase 3 Issue 2 — Pattern Detection + Eval Criteria History Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace single-rubric eval with an accumulating criteria history, make pattern checking fire every 2 runs continuously, and rename all "rubric" references to "criteria/eval criteria" across frontend and backend.

**Architecture:** Backend renames `Rubric`→`EvalCriteria`, `RubricRequest`→`CriteriaRequest`, `/generate-rubric`→`/generate-criteria`, and `rubric.py`→`criteria.py`. Frontend replaces single `evalRubric` state with `evalCriteria[]` array, adds `lastCheckedAtRun` counter for periodic re-checking every 2 runs, gains `panelVisible` + `criteriaError` local state in `App.jsx`, and gets a new `EvalCriteriaPanel` with scrollable editable criterion cards.

**Tech Stack:** Python 3.11+ / FastAPI / Pydantic v2 / pytest (backend); React 19 / Vite / Tailwind CSS v3 / sessionStorage (frontend). No new dependencies.

**Run backend tests:** `cd C:\Users\91978\eval-discovery && python -m pytest tests/ -v`

---

## Chunk 1: Backend

### Task 1: Rename Rubric → EvalCriteria in models.py + test_models.py

**Files:**
- Modify: `backend/models.py`
- Modify: `tests/test_models.py`

- [ ] **Step 1: In `backend/models.py`, replace the `Rubric` class with `EvalCriteria` and `RubricRequest` with `CriteriaRequest`**

Replace everything from line 28 to end of file with:

```python
class EvalCriteria(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str
    definition: str
    good_example: str
    bad_example: str
    why_discovered: str


class PatternCheckRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    transcripts: list[list[Message]]


class PatternCheckResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    found: bool           # MUST be declared first — validator below reads it via info.data
    pattern_summary: str  # Pydantic v2 validates fields in declaration order; info.data only contains prior fields

    @field_validator("pattern_summary")
    @classmethod
    def non_empty_when_found(cls, v, info):
        if info.data.get("found") and not v.strip():
            raise ValueError("pattern_summary must be non-empty when found=True")
        return v


class CriteriaRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pattern_summary: str

    @field_validator("pattern_summary")
    @classmethod
    def non_empty(cls, v):
        if not v.strip():
            raise ValueError("pattern_summary must not be empty")
        return v
```

The full `backend/models.py` after edit:

```python
from pydantic import BaseModel, ConfigDict, field_validator
from typing import Literal


class Message(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    messages: list[Message]

    @field_validator("messages")
    @classmethod
    def messages_not_empty(cls, v):
        if not v:
            raise ValueError("messages must not be empty")
        return v


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reply: str


class EvalCriteria(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str
    definition: str
    good_example: str
    bad_example: str
    why_discovered: str


class PatternCheckRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    transcripts: list[list[Message]]


class PatternCheckResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    found: bool
    pattern_summary: str

    @field_validator("pattern_summary")
    @classmethod
    def non_empty_when_found(cls, v, info):
        if info.data.get("found") and not v.strip():
            raise ValueError("pattern_summary must be non-empty when found=True")
        return v


class CriteriaRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pattern_summary: str

    @field_validator("pattern_summary")
    @classmethod
    def non_empty(cls, v):
        if not v.strip():
            raise ValueError("pattern_summary must not be empty")
        return v
```

- [ ] **Step 2: Update `tests/test_models.py` — rename `Rubric`→`EvalCriteria` and `RubricRequest`→`CriteriaRequest`**

Replace the 5 test functions that import `Rubric` or `RubricRequest`:

```python
def test_eval_criteria_requires_why_discovered():
    from backend.models import EvalCriteria
    with pytest.raises(ValidationError):
        EvalCriteria(name="Test", definition="Def", good_example="Good", bad_example="Bad")


def test_eval_criteria_valid_with_why_discovered():
    from backend.models import EvalCriteria
    criterion = EvalCriteria(
        name="Pricing Redirect",
        definition="Evaluates whether Alex deflects pricing questions.",
        good_example="Alex: Let's cover that in the demo.",
        bad_example="Alex: It's $20/month.",
        why_discovered="User pushed for pricing in 3 consecutive runs.",
    )
    assert criterion.why_discovered == "User pushed for pricing in 3 consecutive runs."


def test_criteria_request_rejects_empty_summary():
    from backend.models import CriteriaRequest
    with pytest.raises(ValidationError):
        CriteriaRequest(pattern_summary="")


def test_criteria_request_rejects_whitespace_only_summary():
    from backend.models import CriteriaRequest
    with pytest.raises(ValidationError):
        CriteriaRequest(pattern_summary="   ")


def test_criteria_request_valid():
    from backend.models import CriteriaRequest
    req = CriteriaRequest(pattern_summary="User repeatedly pushed for pricing before booking demo.")
    assert req.pattern_summary == "User repeatedly pushed for pricing before booking demo."
```

The full `tests/test_models.py` after replacing those 5 functions (keep the other tests unchanged):

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


def test_eval_criteria_requires_why_discovered():
    from backend.models import EvalCriteria
    with pytest.raises(ValidationError):
        EvalCriteria(name="Test", definition="Def", good_example="Good", bad_example="Bad")


def test_eval_criteria_valid_with_why_discovered():
    from backend.models import EvalCriteria
    criterion = EvalCriteria(
        name="Pricing Redirect",
        definition="Evaluates whether Alex deflects pricing questions.",
        good_example="Alex: Let's cover that in the demo.",
        bad_example="Alex: It's $20/month.",
        why_discovered="User pushed for pricing in 3 consecutive runs.",
    )
    assert criterion.why_discovered == "User pushed for pricing in 3 consecutive runs."


def test_pattern_check_request_valid():
    from backend.models import PatternCheckRequest
    transcript = [
        {"role": "user", "content": "What does it cost?"},
        {"role": "assistant", "content": "Let's cover that in the demo."},
        {"role": "user", "content": "Just tell me the price."},
    ]
    req = PatternCheckRequest(transcripts=[transcript, transcript])
    assert len(req.transcripts) == 2


def test_pattern_check_response_found_requires_non_empty_summary():
    from backend.models import PatternCheckResponse
    with pytest.raises(ValidationError):
        PatternCheckResponse(found=True, pattern_summary="")


def test_pattern_check_response_found_with_summary():
    from backend.models import PatternCheckResponse
    resp = PatternCheckResponse(found=True, pattern_summary="User repeatedly asked for pricing.")
    assert resp.found is True
    assert len(resp.pattern_summary) > 0


def test_pattern_check_response_not_found_allows_empty_summary():
    from backend.models import PatternCheckResponse
    resp = PatternCheckResponse(found=False, pattern_summary="")
    assert resp.found is False


def test_criteria_request_rejects_empty_summary():
    from backend.models import CriteriaRequest
    with pytest.raises(ValidationError):
        CriteriaRequest(pattern_summary="")


def test_criteria_request_rejects_whitespace_only_summary():
    from backend.models import CriteriaRequest
    with pytest.raises(ValidationError):
        CriteriaRequest(pattern_summary="   ")


def test_criteria_request_valid():
    from backend.models import CriteriaRequest
    req = CriteriaRequest(pattern_summary="User repeatedly pushed for pricing before booking demo.")
    assert req.pattern_summary == "User repeatedly pushed for pricing before booking demo."
```

- [ ] **Step 3: Run model tests to confirm all pass**

```bash
cd C:\Users\91978\eval-discovery && python -m pytest tests/test_models.py -v
```

Expected: 11 tests pass, no `ImportError` for `Rubric` or `RubricRequest`.

---

### Task 2: Create criteria.py, update main.py

**Files:**
- Create: `backend/criteria.py`
- Modify: `backend/main.py`
- Delete: `backend/rubric.py` (after criteria.py is working)

**Note on `backend/prompts.py`:** The spec lists prompts.py as needing "docstring references" updated. There are no Python docstrings in this file — the word "rubric" appears only in variable names (`RUBRIC_GEN_SYSTEM`, `RUBRIC_GEN_USER_TEMPLATE`) and inside prompt text strings (which the spec says to leave unchanged). Renaming those variables would require updating the import in `criteria.py` with no functional benefit. **Leave `backend/prompts.py` unchanged.**

**Key renames in criteria.py vs rubric.py:**
- Route: `POST /generate-rubric` → `POST /generate-criteria`
- Function: `generate_rubric()` → `generate_criteria()`
- Router: `router` stays `router` (imported as `criteria_router` in main.py)
- Models: `RubricRequest` → `CriteriaRequest`, `Rubric` → `EvalCriteria`
- Mock target in tests: `backend.rubric.groq_chat` → `backend.criteria.groq_chat`

- [ ] **Step 1: Create `backend/criteria.py`**

```python
import json
import logging
from fastapi import APIRouter, HTTPException
from backend.models import CriteriaRequest, EvalCriteria
from backend.groq_client import groq_chat, MODEL_RUBRIC
from backend.prompts import RUBRIC_GEN_SYSTEM, RUBRIC_GEN_USER_TEMPLATE
from backend.utils import extract_json

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/generate-criteria", response_model=EvalCriteria)
def generate_criteria(request: CriteriaRequest):
    try:
        raw = groq_chat(
            system=RUBRIC_GEN_SYSTEM,
            messages=[{
                "role": "user",
                "content": RUBRIC_GEN_USER_TEMPLATE.format(
                    pattern_summary=request.pattern_summary,
                ),
            }],
            model=MODEL_RUBRIC,
        )
        data = json.loads(extract_json(raw))
        # Normalize dialogue fields: model sometimes returns a list of speaker objects
        for field in ("good_example", "bad_example"):
            if isinstance(data.get(field), list):
                data[field] = " ".join(
                    f"{turn.get('speaker', '')}: '{turn.get('text', turn.get('content', ''))}'"
                    for turn in data[field]
                )
        return EvalCriteria(**data)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Criteria generation failed")
        raise HTTPException(status_code=502, detail="Criteria generation unavailable, try again.") from exc
```

- [ ] **Step 2: Update `backend/main.py`**

Replace the rubric import/include with criteria:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.agent import router as agent_router
from backend.checker import router as checker_router
from backend.criteria import router as criteria_router

app = FastAPI(title="Eval Discovery API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_router)
app.include_router(checker_router)
app.include_router(criteria_router)
```

- [ ] **Step 3: Delete `backend/rubric.py`**

```bash
rm C:/Users/91978/eval-discovery/backend/rubric.py
```

- [ ] **Step 4: Verify the server starts and `/generate-criteria` route is registered**

```bash
cd C:\Users\91978\eval-discovery && python -c "from backend.main import app; routes = [r.path for r in app.routes]; assert '/generate-criteria' in routes, routes; print('OK — /generate-criteria registered')"
```

Expected output: `OK — /generate-criteria registered`

---

### Task 3: Rename test_rubric.py → test_criteria.py

**Files:**
- Create: `tests/test_criteria.py`
- Delete: `tests/test_rubric.py`

- [ ] **Step 1: Create `tests/test_criteria.py`**

```python
import json
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


VALID_PATTERN_SUMMARY = (
    "User repeatedly pushed for pricing before agreeing to a demo. "
    "Alex gave the placeholder price on the second ask in Runs 2 and 3. "
    "Evidence — Run 2: Prospect: 'What does it cost?' Alex: '$20/month'."
)


@pytest.fixture
def client():
    from backend.main import app
    return TestClient(app)


def test_generate_criteria_returns_full_criteria(client):
    mock_resp = json.dumps({
        "name": "Pricing Redirect Under Repeated Pressure",
        "definition": "Evaluates whether Alex deflects repeated pricing questions toward demo booking.",
        "good_example": "Prospect: 'Just tell me the price.' Alex: 'The demo is where that all comes to life — 20 minutes?'",
        "bad_example": "Prospect: 'What does it cost?' Alex: '$20/month' — second ask, should have redirected.",
        "why_discovered": "User ran 3 conversations where the prospect pushed for pricing; Alex's guardrail compliance was the consistent test pattern.",
    })
    with patch("backend.criteria.groq_chat", return_value=mock_resp):
        response = client.post("/generate-criteria", json={"pattern_summary": VALID_PATTERN_SUMMARY})
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Pricing Redirect Under Repeated Pressure"
    assert "why_discovered" in body
    assert len(body["why_discovered"]) > 0


def test_generate_criteria_rejects_empty_summary(client):
    response = client.post("/generate-criteria", json={"pattern_summary": ""})
    assert response.status_code == 422


def test_generate_criteria_rejects_whitespace_summary(client):
    response = client.post("/generate-criteria", json={"pattern_summary": "   "})
    assert response.status_code == 422


def test_generate_criteria_normalizes_list_examples(client):
    """Model sometimes returns good_example/bad_example as lists of speaker objects."""
    mock_resp = json.dumps({
        "name": "Pricing Redirect Under Repeated Pressure",
        "definition": "Evaluates whether Alex deflects repeated pricing questions.",
        "good_example": [
            {"speaker": "Prospect", "text": "What does it cost?"},
            {"speaker": "Alex", "text": "Let's cover that in the demo."},
        ],
        "bad_example": [
            {"speaker": "Prospect", "text": "Just give me a number."},
            {"speaker": "Alex", "text": "$20/month."},
        ],
        "why_discovered": "User repeatedly asked for pricing.",
    })
    with patch("backend.criteria.groq_chat", return_value=mock_resp):
        response = client.post("/generate-criteria", json={"pattern_summary": VALID_PATTERN_SUMMARY})
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["good_example"], str)
    assert isinstance(body["bad_example"], str)
    assert "Prospect" in body["good_example"]


def test_generate_criteria_handles_groq_failure(client):
    with patch("backend.criteria.groq_chat", side_effect=Exception("Groq down")):
        response = client.post("/generate-criteria", json={"pattern_summary": VALID_PATTERN_SUMMARY})
    assert response.status_code == 502
```

- [ ] **Step 2: Delete `tests/test_rubric.py`**

```bash
rm C:/Users/91978/eval-discovery/tests/test_rubric.py
```

- [ ] **Step 3: Run all backend tests**

```bash
cd C:\Users\91978\eval-discovery && python -m pytest tests/ -v
```

Expected: all tests pass, no `test_rubric` references, `test_criteria` passes with 5 tests.

- [ ] **Step 4: Commit**

```bash
cd C:\Users\91978\eval-discovery
git add backend/models.py backend/criteria.py backend/main.py tests/test_criteria.py
git rm backend/rubric.py tests/test_rubric.py
git commit -m "feat: rename rubric → eval-criteria in backend (models, router, tests)"
```

---

## Chunk 2: Frontend

### Task 4: Update api.js

**Files:**
- Modify: `frontend/src/api.js`

- [ ] **Step 1: In `frontend/src/api.js`, rename `generateRubric` → `generateCriteria` and update the endpoint**

Full replacement:

```js
const BASE_URL = "http://localhost:8000";

export async function sendMessage(messages) {
  const res = await fetch(`${BASE_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  });
  if (!res.ok) throw new Error(`Chat failed: ${res.status}`);
  return res.json();
}

export async function checkPattern(transcripts) {
  const res = await fetch(`${BASE_URL}/check-pattern`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transcripts }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Pattern check failed: ${res.status}`);
  }
  return res.json(); // { found: bool, pattern_summary: string }
}

export async function generateCriteria(patternSummary) {
  const res = await fetch(`${BASE_URL}/generate-criteria`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pattern_summary: patternSummary }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Criteria generation failed: ${res.status}`);
  }
  return res.json(); // { name, definition, good_example, bad_example, why_discovered }
}
```

---

### Task 5: Rewrite useSession.js

**Files:**
- Modify: `frontend/src/hooks/useSession.js`

- [ ] **Step 1: Fully replace `frontend/src/hooks/useSession.js`**

```js
import { useState, useCallback } from "react";
import { checkPattern as apiCheckPattern } from "../api";

const KEYS = {
  RUN_COUNT: "run_count",
  TRANSCRIPTS: "transcripts",
  CURRENT_TRANSCRIPT: "current_transcript",
  EVAL_CRITERIA: "eval_criteria",
  PATTERN_STATE: "pattern_state",
  PATTERN_SUMMARY: "pattern_summary",
  LAST_CHECKED_AT_RUN: "last_checked_at_run",
};

// Keywords covering all Alex/CloudSync behaviors from the system prompt.
// If any keyword appears in user messages across ≥2 runs, fire the LLM pattern check.
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

/**
 * Returns true if at least one PATTERN_KEYWORD appears in user messages
 * across ≥2 different transcripts.
 */
function hasKeywordOverlap(transcripts) {
  if (transcripts.length < 2) return false;
  const runKeywords = transcripts.map((transcript) =>
    new Set(
      transcript
        .filter((m) => m.role === "user")
        .flatMap((m) =>
          PATTERN_KEYWORDS.filter((kw) =>
            m.content.toLowerCase().includes(kw)
          )
        )
    )
  );
  return PATTERN_KEYWORDS.some(
    (kw) => runKeywords.filter((s) => s.has(kw)).length >= 2
  );
}

export function useSession() {
  const [runCount, setRunCountState] = useState(() => getItem(KEYS.RUN_COUNT, 0));
  const [transcripts, setTranscriptsState] = useState(() => getItem(KEYS.TRANSCRIPTS, []));
  const [currentTranscript, setCurrentTranscriptState] = useState(() => getItem(KEYS.CURRENT_TRANSCRIPT, []));
  const [evalCriteria, setEvalCriteriaState] = useState(() => getItem(KEYS.EVAL_CRITERIA, []));
  const [patternState, setPatternStateRaw] = useState(() => getItem(KEYS.PATTERN_STATE, "idle"));
  const [patternSummary, setPatternSummaryRaw] = useState(() => getItem(KEYS.PATTERN_SUMMARY, null));
  const [lastCheckedAtRun, setLastCheckedAtRunState] = useState(() => getItem(KEYS.LAST_CHECKED_AT_RUN, 0));

  const setPatternState = useCallback((val) => {
    setPatternStateRaw(val);
    setItem(KEYS.PATTERN_STATE, val);
  }, []);

  const setPatternSummary = useCallback((val) => {
    setPatternSummaryRaw(val);
    setItem(KEYS.PATTERN_SUMMARY, val);
  }, []);

  const setLastCheckedAtRun = useCallback((val) => {
    setLastCheckedAtRunState(val);
    setItem(KEYS.LAST_CHECKED_AT_RUN, val);
  }, []);

  const addMessage = useCallback((message) => {
    setCurrentTranscriptState((prev) => {
      const next = [...prev, message];
      setItem(KEYS.CURRENT_TRANSCRIPT, next);
      return next;
    });
  }, []);

  /**
   * Appends a new criterion to the evalCriteria array and persists to sessionStorage.
   * Called from App.jsx handleDiscoverEvals after a successful /generate-criteria response.
   */
  const addCriteria = useCallback((criterion) => {
    setEvalCriteriaState((prev) => {
      const next = [...prev, criterion];
      setItem(KEYS.EVAL_CRITERIA, next);
      return next;
    });
  }, []);

  /**
   * Updates a specific criterion by id. Called on blur from EvalCriteriaPanel field edits.
   */
  const updateCriteria = useCallback((id, fields) => {
    setEvalCriteriaState((prev) => {
      const next = prev.map((c) => (c.id === id ? { ...c, ...fields } : c));
      setItem(KEYS.EVAL_CRITERIA, next);
      return next;
    });
  }, []);

  /**
   * Saves the current transcript as a completed run, then evaluates whether to fire
   * the LLM pattern check.
   *
   * Pattern check fires if:
   *   1. nextCount - lastCheckedAtRun >= 2  (checks every 2 runs)
   *   2. patternState NOT IN ["checking", "generating"]
   *   3. hasKeywordOverlap detects keyword match across ≥2 transcripts
   *
   * On success (found=true): updates lastCheckedAtRun, sets patternSummary, → "pattern_found"
   * On success (found=false): updates lastCheckedAtRun → "pattern_not_found"
   * On error: does NOT update lastCheckedAtRun → "error" (allows retry on next run)
   */
  const saveRun = useCallback(async (currentState) => {
    const {
      currentPatternState,
      currentTranscripts,
      currentRunCount,
      currentCurrentTranscript,
      currentLastCheckedAtRun,
    } = currentState;

    const nextTranscripts = [...currentTranscripts, currentCurrentTranscript];
    const nextCount = currentRunCount + 1;

    setTranscriptsState(nextTranscripts);
    setRunCountState(nextCount);
    setItem(KEYS.TRANSCRIPTS, nextTranscripts);
    setItem(KEYS.RUN_COUNT, nextCount);
    setCurrentTranscriptState([]);
    setItem(KEYS.CURRENT_TRANSCRIPT, []);

    if (
      nextCount - currentLastCheckedAtRun >= 2 &&
      !["checking", "generating"].includes(currentPatternState) &&
      hasKeywordOverlap(nextTranscripts)
    ) {
      setPatternState("checking");
      try {
        const apiTranscripts = nextTranscripts.map((t) =>
          t.map((m) => ({ role: m.role, content: m.content }))
        );
        const result = await apiCheckPattern(apiTranscripts);
        setLastCheckedAtRun(nextCount);
        if (result.found) {
          setPatternSummary(result.pattern_summary); // overwrites any previous summary
          setPatternState("pattern_found");
        } else {
          setPatternState("pattern_not_found");
        }
      } catch {
        setPatternState("error");
        // lastCheckedAtRun NOT updated — allows retry on next qualifying run
      }
    }
  }, [setPatternState, setPatternSummary, setLastCheckedAtRun]);

  return {
    runCount,
    transcripts,
    currentTranscript,
    evalCriteria,
    patternState,
    patternSummary,
    lastCheckedAtRun,   // REQUIRED: App.jsx passes this as currentLastCheckedAtRun to saveRun
    addMessage,
    saveRun,
    addCriteria,
    updateCriteria,
    setPatternState,
  };
}
```

---

### Task 6: Rewrite App.jsx + update RunControls.jsx

**Files:**
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/components/RunControls.jsx`

- [ ] **Step 1: Fully replace `frontend/src/App.jsx`**

```jsx
import { useState, useEffect } from "react";
import { useSession } from "./hooks/useSession";
import { generateCriteria } from "./api";
import ChatInterface from "./components/ChatInterface";
import RunControls from "./components/RunControls";
import EvalCriteriaPanel from "./components/EvalCriteriaPanel";

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
  // "Discover Evals" shows for pattern_found, and for error-from-generation (criteriaError set)
  const showDiscoverButton =
    patternState === "pattern_found" ||
    (patternState === "error" && criteriaError !== null);

  return (
    <div className="flex flex-col h-screen bg-white">
      {/* Header */}
      <header className="px-6 py-4 border-b flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">Eval Discovery</h1>
          <p className="text-xs text-gray-500">Chat with Alex · CloudSync SDR</p>
        </div>
        <div className="flex items-center gap-3">
          {patternState === "checking" && (
            <span className="text-xs text-blue-600 animate-pulse font-medium">
              Checking for patterns…
            </span>
          )}
          {patternState === "generating" && (
            <span className="text-xs text-amber-600 animate-pulse font-medium">
              Generating eval…
            </span>
          )}
          {patternState === "error" && criteriaError !== null && (
            <span className="text-xs text-red-500">Eval generation failed — retry</span>
          )}
          {patternState === "error" && criteriaError === null && (
            <span className="text-xs text-red-500">
              Pattern check failed — retrying automatically
            </span>
          )}
          {patternState === "pattern_found" && evalCriteria.length === 0 && (
            <span className="text-xs text-green-600 font-medium">Pattern found</span>
          )}
          {patternState === "pattern_found" && evalCriteria.length > 0 && (
            <span className="text-xs text-green-600 font-medium">New pattern found</span>
          )}
          {patternState === "complete" && (
            <span className="text-xs text-blue-600 font-medium">Eval ready</span>
          )}
          {evalCriteria.length > 0 && (
            <button
              onClick={() => setPanelVisible(true)}
              className="text-xs text-blue-700 bg-blue-50 border border-blue-200 rounded-full px-3 py-1 hover:bg-blue-100"
            >
              Eval Criteria ({evalCriteria.length})
            </button>
          )}
        </div>
      </header>

      {/* Body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: chat + controls */}
        <div className="flex flex-col flex-1 overflow-hidden">
          <RunControls
            runCount={runCount}
            currentTranscriptLength={currentTranscript.length}
            patternState={patternState}
            showDiscoverButton={showDiscoverButton}
            onSaveRun={handleSaveRun}
            onDiscoverEvals={handleDiscoverEvals}
            saving={saving}
          />
          <ChatInterface
            currentTranscript={currentTranscript}
            onMessage={addMessage}
            disabled={isGenerating}
          />
        </div>

        {/* Right: eval criteria panel — kept mounted to preserve expandedIndex state,
             hidden via width collapse (spec requires state not be cleared on dismiss) */}
        {evalCriteria.length > 0 && (
          <div
            className="flex-shrink-0 overflow-hidden border-l transition-[width] duration-200"
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
    </div>
  );
}
```

- [ ] **Step 2: Update `frontend/src/components/RunControls.jsx`**

```jsx
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
    <div className="flex items-center gap-4 px-4 py-3 bg-gray-50 border-b">
      <div className="flex items-center gap-2">
        <span className="text-sm text-gray-500">Preview runs:</span>
        <span className="font-semibold text-gray-800">{runCount}</span>
      </div>

      <div className="flex-1" />

      {showDiscoverButton && (
        <button
          onClick={onDiscoverEvals}
          disabled={discoverDisabled}
          className="bg-amber-500 text-white px-4 py-1.5 rounded-lg text-sm font-medium hover:bg-amber-600 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Discover Evals
        </button>
      )}

      <button
        onClick={onSaveRun}
        disabled={!canSave || saving}
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

---

### Task 7: Create EvalCriteriaPanel.jsx, delete EvalSuggestionPanel.jsx

**Files:**
- Create: `frontend/src/components/EvalCriteriaPanel.jsx`
- Delete: `frontend/src/components/EvalSuggestionPanel.jsx`

- [ ] **Step 1: Create `frontend/src/components/EvalCriteriaPanel.jsx`**

```jsx
import { useState, useEffect } from "react";

function CriterionCard({ criterion, index, isExpanded, onToggle, onUpdate }) {
  function handleBlur(field, value) {
    onUpdate(criterion.id, { [field]: value });
  }

  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-4 py-3 bg-gray-50 text-left hover:bg-gray-100"
        onClick={onToggle}
      >
        <span className="text-sm font-medium text-gray-800">
          #{index + 1} {criterion.name}
        </span>
        <span className="text-gray-400 text-xs ml-2 flex-shrink-0">
          {isExpanded ? "▲" : "▼"}
        </span>
      </button>

      {isExpanded && (
        <div className="p-4 space-y-3 border-t border-gray-200">
          {/* Why discovered — read-only */}
          <div>
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">
              Why discovered
            </p>
            <p className="text-xs text-gray-600 leading-relaxed">
              {criterion.why_discovered}
            </p>
          </div>

          {/* Name (editable) */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
              Name
            </label>
            <input
              type="text"
              defaultValue={criterion.name}
              onBlur={(e) => handleBlur("name", e.target.value)}
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white"
            />
          </div>

          {/* Definition, Good example, Bad example (editable textareas) */}
          {[
            { key: "definition", label: "Definition" },
            { key: "good_example", label: "Good Example" },
            { key: "bad_example", label: "Bad Example" },
          ].map(({ key, label }) => (
            <div key={key}>
              <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
                {label}
              </label>
              <textarea
                defaultValue={criterion[key]}
                onBlur={(e) => handleBlur(key, e.target.value)}
                rows={3}
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white resize-none"
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
  // Track which card is expanded: last card expanded by default
  const [expandedIndex, setExpandedIndex] = useState(evalCriteria.length - 1);

  // When list grows (new criterion added), expand the new last card
  useEffect(() => {
    setExpandedIndex(evalCriteria.length - 1);
  }, [evalCriteria.length]);

  function handleToggle(index) {
    setExpandedIndex((prev) => (prev === index ? null : index));
  }

  return (
    <div className="flex flex-col h-full bg-gray-50">
      {/* Header */}
      <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between flex-shrink-0">
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
          Eval Criteria
        </span>
        <button
          onClick={onDismiss}
          className="text-gray-400 hover:text-gray-600 text-lg leading-none"
          aria-label="Close panel"
        >
          ×
        </button>
      </div>

      {/* New pattern notification banner */}
      {patternState === "pattern_found" && evalCriteria.length > 0 && (
        <div className="px-4 py-2 bg-green-50 border-b border-green-200 text-xs text-green-700 flex-shrink-0">
          ⚡ New pattern detected — click Discover Evals to add another criterion
        </div>
      )}

      {/* Scrollable criteria cards */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
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

      {/* Fixed footer */}
      <div className="px-4 py-3 border-t border-gray-200 flex gap-2 flex-shrink-0">
        <button
          onClick={onExportAll}
          className="flex-1 bg-gray-800 text-white py-2 rounded-lg text-sm font-medium hover:bg-gray-700"
        >
          Export All
        </button>
        <button
          onClick={onAdd}
          className="px-4 py-2 border border-gray-300 rounded-lg text-sm text-gray-600 hover:bg-gray-100"
        >
          + Add Criteria
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Delete `frontend/src/components/EvalSuggestionPanel.jsx`**

```bash
rm C:/Users/91978/eval-discovery/frontend/src/components/EvalSuggestionPanel.jsx
```

- [ ] **Step 3: Start the dev server and do a smoke test**

Start backend: `cd C:\Users\91978\eval-discovery && uvicorn backend.main:app --reload`

Start frontend: `cd C:\Users\91978\eval-discovery\frontend && npm run dev`

Visit `http://localhost:5173`. Verify:
- App loads without errors
- Can send messages to Alex
- "Save Run & Start New" button works after 3+ turns
- After 2 runs with pricing/demo keywords, "Checking for patterns…" appears
- If pattern found, "Discover Evals" button appears
- Clicking "Discover Evals" → criteria panel slides in on right
- Panel shows criterion card expanded
- Fields are editable (blur to save)
- "+ Add Criteria" adds a blank card
- "Export All" downloads `eval-criteria.json`
- "Eval Criteria (N)" badge in header re-opens dismissed panel
- Run 4+ checks fire again when keywords match

- [ ] **Step 4: Commit**

```bash
cd C:\Users\91978\eval-discovery
git add frontend/src/api.js frontend/src/hooks/useSession.js frontend/src/App.jsx
git add frontend/src/components/RunControls.jsx frontend/src/components/EvalCriteriaPanel.jsx
git rm frontend/src/components/EvalSuggestionPanel.jsx
git commit -m "feat: consecutive pattern checking + eval criteria history + rename rubric → criteria"
```

---

## Out of Scope (do not implement)

- No pagination of criteria list
- No per-criterion delete
- No server-side persistence (sessionStorage only)
- No changes to `POST /check-pattern` endpoint
