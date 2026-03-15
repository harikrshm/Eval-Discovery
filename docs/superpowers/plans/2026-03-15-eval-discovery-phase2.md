# Eval Discovery Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed-count eval trigger and hardcoded intent categories with a content-aware heuristic → LLM pattern check → user-triggered rubric generation pipeline, while stripping thinking text from all Groq outputs.

**Architecture:** Backend splits into two new focused route files (`checker.py`, `rubric.py`) replacing `analyzer.py`. Shared JSON parsing moves to `utils.py`. Frontend replaces a simple threshold counter with a 7-state machine in `useSession.js` that gates the "Discover Evals" button behind confirmed LLM pattern detection.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, Groq SDK, pytest — React 18, Vite, Tailwind CSS, sessionStorage

---

## Chunk 1: Backend Foundation

### Task 1: Extract `extract_json()` to `backend/utils.py`

**Files:**
- Create: `backend/utils.py`
- Modify: `tests/test_analyzer.py` (update imports for two existing tests)

- [ ] **Step 1: Write the failing test**

Create `tests/test_utils.py`:

```python
def test_extract_json_strips_think_blocks():
    from backend.utils import extract_json
    raw = "<think>Let me analyze this carefully...</think>\n{\"intent\": \"objection_handling\"}"
    result = extract_json(raw)
    assert result == '{"intent": "objection_handling"}'


def test_extract_json_strips_markdown_fences():
    from backend.utils import extract_json
    raw = "```json\n{\"intent\": \"graceful_exit\"}\n```"
    result = extract_json(raw)
    assert result == '{"intent": "graceful_exit"}'


def test_extract_json_strips_both():
    from backend.utils import extract_json
    raw = "<think>hmm</think>\n```json\n{\"found\": true}\n```"
    result = extract_json(raw)
    assert result == '{"found": true}'
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd C:\Users\91978\eval-discovery && python -m pytest tests/test_utils.py -v
```
Expected: `ImportError` — `backend.utils` does not exist yet.

- [ ] **Step 3: Create `backend/utils.py`**

```python
import re


def extract_json(text: str) -> str:
    """Strip <think> blocks and markdown fences that reasoning models may emit before JSON."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"```(?:json)?\s*([\s\S]*?)\s*```", r"\1", text)
    return text.strip()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_utils.py -v
```
Expected: 3 PASSED.

- [ ] **Step 5: Update imports in `tests/test_analyzer.py`**

Change these two test functions so they import from `backend.utils` instead of `backend.analyzer`:

```python
def test_extract_json_strips_think_blocks():
    from backend.utils import extract_json          # was backend.analyzer
    raw = "<think>Let me analyze this carefully...</think>\n{\"intent\": \"objection_handling\"}"
    result = extract_json(raw)
    assert result == '{"intent": "objection_handling"}'


def test_extract_json_strips_markdown_fences():
    from backend.utils import extract_json          # was backend.analyzer
    raw = "```json\n{\"intent\": \"graceful_exit\"}\n```"
    result = extract_json(raw)
    assert result == '{"intent": "graceful_exit"}'
```

- [ ] **Step 6: Run full test suite to confirm nothing broke**

```bash
python -m pytest tests/ -v
```
Expected: all existing tests pass (test_analyzer.py's two extract_json tests now import from utils — both should still pass since `backend.analyzer` still defines `extract_json` for now).

- [ ] **Step 7: Commit**

```bash
git add backend/utils.py tests/test_utils.py tests/test_analyzer.py
git commit -m "refactor: move extract_json to backend/utils.py, add test_utils.py"
```

---

### Task 2: Update `backend/groq_client.py`

**Files:**
- Modify: `backend/groq_client.py`
- Modify: `tests/test_groq_client.py`

- [ ] **Step 1: Write the failing tests**

Replace the entire content of `tests/test_groq_client.py`:

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


def test_default_model_is_chat_model():
    from backend.groq_client import groq_chat, MODEL_CHAT

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "ok"

    with patch("backend.groq_client.client.chat.completions.create", return_value=mock_response) as mock_create:
        groq_chat(system="sys", messages=[{"role": "user", "content": "hi"}])
        assert mock_create.call_args.kwargs["model"] == MODEL_CHAT


def test_pattern_model_does_not_receive_reasoning_format():
    from backend.groq_client import groq_chat, MODEL_PATTERN

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "ok"

    with patch("backend.groq_client.client.chat.completions.create", return_value=mock_response) as mock_create:
        groq_chat(system="sys", messages=[{"role": "user", "content": "hi"}], model=MODEL_PATTERN)
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["model"] == MODEL_PATTERN
        assert "reasoning_format" not in call_kwargs


def test_rubric_model_receives_reasoning_format_hidden():
    from backend.groq_client import groq_chat, MODEL_RUBRIC

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "ok"

    with patch("backend.groq_client.client.chat.completions.create", return_value=mock_response) as mock_create:
        groq_chat(system="sys", messages=[{"role": "user", "content": "hi"}], model=MODEL_RUBRIC)
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["reasoning_format"] == "hidden"


def test_strip_thinking_removes_think_blocks_from_output():
    from backend.groq_client import groq_chat

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "<think>internal reasoning</think>Final answer here."

    with patch("backend.groq_client.client.chat.completions.create", return_value=mock_response):
        result = groq_chat(system="sys", messages=[{"role": "user", "content": "hi"}])

    assert result == "Final answer here."
    assert "<think>" not in result


def test_strip_thinking_leaves_clean_output_unchanged():
    from backend.groq_client import groq_chat

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Clean response with no thinking."

    with patch("backend.groq_client.client.chat.completions.create", return_value=mock_response):
        result = groq_chat(system="sys", messages=[{"role": "user", "content": "hi"}])

    assert result == "Clean response with no thinking."
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_groq_client.py -v
```
Expected: `test_default_model_is_chat_model` fails (MODEL_CHAT doesn't exist), `test_pattern_model_does_not_receive_reasoning_format` fails, etc.

- [ ] **Step 3: Update `backend/groq_client.py`**

> **Note:** This is a full replacement. The existing unconditional `reasoning_format="hidden"` kwarg on the `create()` call is **removed** and replaced by the conditional block below. `agent.py` calls `groq_chat()` with no `model` argument and requires **no changes** — it inherits `MODEL_CHAT` as the default.

```python
import os
import re
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

MODEL_CHAT    = "llama-3.1-8b-instant"
MODEL_PATTERN = "openai/gpt-oss-120b"
MODEL_RUBRIC  = "qwen/qwen3-32b"

_api_key = os.getenv("GROQ_API_KEY") or "sk-test-placeholder"
client = Groq(api_key=_api_key)


def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks that reasoning models may leak into responses."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def groq_chat(system: str, messages: list[dict], model: str = MODEL_CHAT) -> str:
    """Send messages to Groq and return the assistant reply text, stripped of thinking blocks."""
    full_messages = [{"role": "system", "content": system}] + messages
    kwargs = {"model": model, "messages": full_messages}
    if model == MODEL_RUBRIC:
        kwargs["reasoning_format"] = "hidden"
    response = client.chat.completions.create(**kwargs)
    return _strip_thinking(response.choices[0].message.content)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_groq_client.py -v
```
Expected: 6 PASSED.

- [ ] **Step 5: Run full suite to check nothing else broke**

```bash
python -m pytest tests/ -v
```
Expected: all pass. (test_agent.py patches `backend.agent.groq_chat` — not affected by signature change since `model` has a default.)

- [ ] **Step 6: Commit**

```bash
git add backend/groq_client.py tests/test_groq_client.py
git commit -m "feat: add model-per-call routing and thinking text stripping to groq_client"
```

---

### Task 3: Update `backend/models.py`

**Files:**
- Modify: `backend/models.py`
- Modify: `tests/test_models.py`

- [ ] **Step 1: Write the failing tests**

Replace the entire content of `tests/test_models.py`:

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


def test_rubric_requires_why_discovered():
    from backend.models import Rubric
    with pytest.raises(ValidationError):
        Rubric(name="Test", definition="Def", good_example="Good", bad_example="Bad")


def test_rubric_valid_with_why_discovered():
    from backend.models import Rubric
    rubric = Rubric(
        name="Pricing Redirect",
        definition="Evaluates whether Alex deflects pricing questions.",
        good_example="Alex: Let's cover that in the demo.",
        bad_example="Alex: It's $20/month.",
        why_discovered="User pushed for pricing in 3 consecutive runs.",
    )
    assert rubric.why_discovered == "User pushed for pricing in 3 consecutive runs."


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


def test_rubric_request_rejects_empty_summary():
    from backend.models import RubricRequest
    with pytest.raises(ValidationError):
        RubricRequest(pattern_summary="")


def test_rubric_request_rejects_whitespace_only_summary():
    from backend.models import RubricRequest
    with pytest.raises(ValidationError):
        RubricRequest(pattern_summary="   ")


def test_rubric_request_valid():
    from backend.models import RubricRequest
    req = RubricRequest(pattern_summary="User repeatedly pushed for pricing before booking demo.")
    assert req.pattern_summary == "User repeatedly pushed for pricing before booking demo."
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_models.py -v
```
Expected: multiple failures — `Rubric` missing `why_discovered`, `PatternCheckRequest` doesn't exist, etc.

- [ ] **Step 3: Update `backend/models.py`**

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


class Rubric(BaseModel):
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


class RubricRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pattern_summary: str

    @field_validator("pattern_summary")
    @classmethod
    def non_empty(cls, v):
        if not v.strip():
            raise ValueError("pattern_summary must not be empty")
        return v
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_models.py -v
```
Expected: 12 PASSED.

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -v
```
Expected: all pass except tests in `test_analyzer.py` that reference `AnalyzeRequest`/`AnalyzeResponse` — those will fail now. That's acceptable; `test_analyzer.py` will be fully replaced in Task 7.

- [ ] **Step 6: Commit**

```bash
git add backend/models.py tests/test_models.py
git commit -m "feat: update models — add PatternCheck/Rubric models, add why_discovered to Rubric"
```

---

### Task 4: Update `backend/prompts.py`

**Files:**
- Modify: `backend/prompts.py`

No new unit tests needed — prompts are strings consumed by integration-level tests in Tasks 5 and 6.

- [ ] **Step 1: Replace `backend/prompts.py`**

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


PATTERN_CHECK_SYSTEM = """You are an expert at analyzing AI agent test transcripts to detect repeated testing patterns.

Given multiple conversation transcripts and the AI agent's system prompt, determine whether the person testing the agent is consistently probing the same behavior across multiple runs.

A pattern exists when:
- The prospect raises the same type of challenge (e.g. pricing, objection, scheduling) in multiple runs
- The agent faces the same behavioral test repeatedly
- There is clear evidence of deliberate, repeated probing of a specific capability

Return a JSON object with exactly these keys:
- "found": true if a clear repeated pattern is detected across at least 2 transcripts; false otherwise
- "pattern_summary": if found=true, a rich description including (1) the behavior name, (2) what agent capability is being tested, (3) 1-2 direct quoted exchanges from the transcripts as evidence. Use exact quotes. If found=false, return an empty string "".

Respond ONLY with valid JSON. No markdown fences, no explanation outside the JSON."""


PATTERN_CHECK_USER_TEMPLATE = """Agent system prompt:
{system_prompt}

Conversation transcripts:
{transcripts}

Is there a repeated testing pattern across these transcripts? Return JSON only."""


RUBRIC_GEN_SYSTEM = """You are an expert at creating evaluation rubrics for AI agents.

Given a description of a testing pattern discovered from real conversation transcripts (including behavioral context and quoted evidence), generate one specific, actionable evaluation criterion grounded in those exact exchanges.

Do NOT use generic AI evaluation language. The criterion must be specific to the exact behavior and context described.

Return a JSON object with exactly these keys:
- "name": a concise, specific rubric name (5-10 words, not generic — name the exact behavior)
- "definition": what this criterion evaluates and why it matters for this specific agent (2-3 sentences)
- "good_example": a short dialogue exchange showing ideal agent behavior, grounded in the described context
- "bad_example": a short dialogue exchange showing the failure mode the pattern revealed, using context from the evidence
- "why_discovered": one sentence explaining what the tester's repeated behavior revealed about which agent capability needed evaluation

Respond ONLY with valid JSON. No markdown fences, no explanation outside the JSON."""


RUBRIC_GEN_USER_TEMPLATE = """Testing pattern discovered from transcript analysis:
{pattern_summary}

Generate one evaluation rubric for this specific pattern. Return JSON only."""


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

- [ ] **Step 2: Verify `test_agent.py` still passes** (it imports `ALEX_SYSTEM_PROMPT`)

```bash
python -m pytest tests/test_agent.py -v
```
Expected: 3 PASSED.

- [ ] **Step 3: Commit**

```bash
git add backend/prompts.py
git commit -m "feat: replace intent/rubric prompts with pattern-check and rubric-gen prompts"
```

---

## Chunk 2: Backend Endpoints

### Task 5: Create `backend/checker.py` (`POST /check-pattern`)

**Files:**
- Create: `backend/checker.py`
- Create: `tests/test_checker.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_checker.py`:

```python
import json
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


VALID_TRANSCRIPT = [
    {"role": "user", "content": "What does it cost?"},
    {"role": "assistant", "content": "Let's cover pricing in the demo."},
    {"role": "user", "content": "Just give me a number."},
]

TWO_TRANSCRIPTS = [VALID_TRANSCRIPT, VALID_TRANSCRIPT]
THREE_TRANSCRIPTS = [VALID_TRANSCRIPT] * 3


@pytest.fixture
def client():
    from backend.main import app
    return TestClient(app)


def test_check_pattern_found(client):
    mock_resp = json.dumps({
        "found": True,
        "pattern_summary": "User repeatedly pushed for pricing. Evidence — Run 1: Prospect: 'What does it cost?' Alex: 'Let's cover that in the demo.'"
    })
    with patch("backend.checker.groq_chat", return_value=mock_resp):
        response = client.post("/check-pattern", json={"transcripts": TWO_TRANSCRIPTS})
    assert response.status_code == 200
    body = response.json()
    assert body["found"] is True
    assert len(body["pattern_summary"]) > 0


def test_check_pattern_not_found(client):
    mock_resp = json.dumps({"found": False, "pattern_summary": ""})
    with patch("backend.checker.groq_chat", return_value=mock_resp):
        response = client.post("/check-pattern", json={"transcripts": TWO_TRANSCRIPTS})
    assert response.status_code == 200
    assert response.json()["found"] is False


def test_check_pattern_rejects_short_transcript(client):
    short = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hey"},
    ]
    response = client.post("/check-pattern", json={"transcripts": [short]})
    assert response.status_code == 400
    assert "too short" in response.json()["detail"].lower()


def test_check_pattern_rejects_empty_transcripts(client):
    response = client.post("/check-pattern", json={"transcripts": []})
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_check_pattern_handles_groq_failure(client):
    with patch("backend.checker.groq_chat", side_effect=Exception("Groq down")):
        response = client.post("/check-pattern", json={"transcripts": TWO_TRANSCRIPTS})
    assert response.status_code == 502
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_checker.py -v
```
Expected: all fail — `/check-pattern` route does not exist.

- [ ] **Step 3: Create `backend/checker.py`**

```python
import json
from fastapi import APIRouter, HTTPException
from backend.models import PatternCheckRequest, PatternCheckResponse
from backend.groq_client import groq_chat, MODEL_PATTERN
from backend.prompts import ALEX_SYSTEM_PROMPT, PATTERN_CHECK_SYSTEM, PATTERN_CHECK_USER_TEMPLATE, format_transcripts
from backend.utils import extract_json

router = APIRouter()

MIN_TURNS_PER_TRANSCRIPT = 3


@router.post("/check-pattern", response_model=PatternCheckResponse)
def check_pattern(request: PatternCheckRequest):
    if not request.transcripts:
        raise HTTPException(status_code=400, detail="transcripts must not be empty.")

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
        raw = groq_chat(
            system=PATTERN_CHECK_SYSTEM,
            messages=[{
                "role": "user",
                "content": PATTERN_CHECK_USER_TEMPLATE.format(
                    system_prompt=ALEX_SYSTEM_PROMPT,
                    transcripts=formatted,
                ),
            }],
            model=MODEL_PATTERN,
        )
        data = json.loads(extract_json(raw))
        return PatternCheckResponse(**data)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Pattern check unavailable, try again.") from exc
```

- [ ] **Step 4: Register the router in `backend/main.py`** (add alongside existing routers — do NOT remove `analyzer` router yet):

```python
from backend.checker import router as checker_router
app.include_router(checker_router)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_checker.py -v
```
Expected: 5 PASSED.

- [ ] **Step 6: Commit**

```bash
git add backend/checker.py tests/test_checker.py backend/main.py
git commit -m "feat: add POST /check-pattern endpoint with LLM pattern detection"
```

---

### Task 6: Create `backend/rubric.py` (`POST /generate-rubric`)

**Files:**
- Create: `backend/rubric.py`
- Create: `tests/test_rubric.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rubric.py`:

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


def test_generate_rubric_returns_full_rubric(client):
    mock_resp = json.dumps({
        "name": "Pricing Redirect Under Repeated Pressure",
        "definition": "Evaluates whether Alex deflects repeated pricing questions toward demo booking.",
        "good_example": "Prospect: 'Just tell me the price.' Alex: 'The demo is where that all comes to life — 20 minutes?'",
        "bad_example": "Prospect: 'What does it cost?' Alex: '$20/month' — second ask, should have redirected.",
        "why_discovered": "User ran 3 conversations where the prospect pushed for pricing; Alex's guardrail compliance was the consistent test pattern.",
    })
    with patch("backend.rubric.groq_chat", return_value=mock_resp):
        response = client.post("/generate-rubric", json={"pattern_summary": VALID_PATTERN_SUMMARY})
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Pricing Redirect Under Repeated Pressure"
    assert "why_discovered" in body
    assert len(body["why_discovered"]) > 0


def test_generate_rubric_rejects_empty_summary(client):
    response = client.post("/generate-rubric", json={"pattern_summary": ""})
    assert response.status_code == 422


def test_generate_rubric_rejects_whitespace_summary(client):
    response = client.post("/generate-rubric", json={"pattern_summary": "   "})
    assert response.status_code == 422


def test_generate_rubric_handles_groq_failure(client):
    with patch("backend.rubric.groq_chat", side_effect=Exception("Groq down")):
        response = client.post("/generate-rubric", json={"pattern_summary": VALID_PATTERN_SUMMARY})
    assert response.status_code == 502
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_rubric.py -v
```
Expected: all fail — `/generate-rubric` route does not exist.

- [ ] **Step 3: Create `backend/rubric.py`**

```python
import json
from fastapi import APIRouter, HTTPException
from backend.models import RubricRequest, Rubric
from backend.groq_client import groq_chat, MODEL_RUBRIC
from backend.prompts import RUBRIC_GEN_SYSTEM, RUBRIC_GEN_USER_TEMPLATE
from backend.utils import extract_json

router = APIRouter()


@router.post("/generate-rubric", response_model=Rubric)
def generate_rubric(request: RubricRequest):
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
        return Rubric(**data)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Rubric generation unavailable, try again.") from exc
```

- [ ] **Step 4: Register the router in `backend/main.py`**

```python
from backend.rubric import router as rubric_router
app.include_router(rubric_router)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_rubric.py -v
```
Expected: 4 PASSED.

- [ ] **Step 6: Commit**

```bash
git add backend/rubric.py tests/test_rubric.py backend/main.py
git commit -m "feat: add POST /generate-rubric endpoint"
```

---

### Task 7: Remove `backend/analyzer.py`, clean up `main.py` and `test_analyzer.py`

**Files:**
- Delete: `backend/analyzer.py`
- Modify: `backend/main.py`
- Replace: `tests/test_analyzer.py`

- [ ] **Step 1: Replace `tests/test_analyzer.py`**

The old endpoint tests (`test_analyze_returns_rubric`, `test_analyze_rejects_*`, etc.) are now covered by `test_checker.py` and `test_rubric.py`. The two `extract_json` tests already moved to `test_utils.py`. Replace the file with a redirect comment so no tests are silently lost:

```python
# All extract_json tests are in tests/test_utils.py
# All /check-pattern endpoint tests are in tests/test_checker.py
# All /generate-rubric endpoint tests are in tests/test_rubric.py
# The /analyze endpoint and analyzer.py have been removed (Phase 2 refactor).
```

- [ ] **Step 2: Update `backend/main.py` — remove analyzer, finalize routers**

Replace the full content of `backend/main.py`:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.agent import router as agent_router
from backend.checker import router as checker_router
from backend.rubric import router as rubric_router

app = FastAPI(title="Eval Discovery API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_router)
app.include_router(checker_router)
app.include_router(rubric_router)
```

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest tests/ -v
```
Expected: all tests pass. No reference to `backend.analyzer` should remain.

- [ ] **Step 4: Commit**

```bash
git add backend/main.py tests/test_analyzer.py
git rm backend/analyzer.py
git commit -m "refactor: remove analyzer.py, register checker and rubric routers in main"
```

---

## Chunk 3: Frontend

### Task 8: Update `frontend/src/api.js`

**Files:**
- Modify: `frontend/src/api.js`

- [ ] **Step 1: Replace `frontend/src/api.js`**

```javascript
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

export async function generateRubric(patternSummary) {
  const res = await fetch(`${BASE_URL}/generate-rubric`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pattern_summary: patternSummary }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Rubric generation failed: ${res.status}`);
  }
  return res.json(); // { name, definition, good_example, bad_example, why_discovered }
}
```

- [ ] **Step 2: Verify the dev server still starts without errors**

```bash
cd C:\Users\91978\eval-discovery\frontend && npm run dev
```
Expected: Vite starts without import errors. (Callers of `analyzeTranscripts` will fail at runtime — that's expected and will be fixed in Tasks 9 and 10.)

- [ ] **Step 3: Commit**

```bash
cd C:\Users\91978\eval-discovery
git add frontend/src/api.js
git commit -m "feat: replace analyzeTranscripts with checkPattern + generateRubric in api.js"
```

---

### Task 9: Update `frontend/src/hooks/useSession.js`

**Files:**
- Modify: `frontend/src/hooks/useSession.js`

This is the core state machine change. Read this task completely before starting.

- [ ] **Step 1: Replace `frontend/src/hooks/useSession.js`**

```javascript
import { useState, useCallback } from "react";
import { checkPattern as apiCheckPattern } from "../api";

const KEYS = {
  RUN_COUNT: "run_count",
  TRANSCRIPTS: "transcripts",
  CURRENT_TRANSCRIPT: "current_transcript",
  EVAL_TRIGGERED: "eval_triggered",
  EVAL_DISMISSED: "eval_dismissed",
  EVAL_RUBRIC: "eval_rubric",
  PATTERN_STATE: "pattern_state",
  PATTERN_SUMMARY: "pattern_summary",
};

// Keywords that signal the user is probing a specific Alex behavior.
// If any keyword appears in user messages across ≥2 runs, fire the LLM pattern check.
const PATTERN_KEYWORDS = [
  "price", "pricing", "cost", "budget", "demo", "meeting",
  "voicemail", "not interested", "competitor", "qualify",
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
  const [evalTriggered, setEvalTriggeredState] = useState(() => getItem(KEYS.EVAL_TRIGGERED, false));
  const [evalDismissed, setEvalDismissedState] = useState(() => getItem(KEYS.EVAL_DISMISSED, false));
  const [evalRubric, setEvalRubricState] = useState(() => getItem(KEYS.EVAL_RUBRIC, null));
  const [patternState, setPatternStateRaw] = useState(() => getItem(KEYS.PATTERN_STATE, "idle"));
  const [patternSummary, setPatternSummaryRaw] = useState(() => getItem(KEYS.PATTERN_SUMMARY, null));

  function setPatternState(val) {
    setPatternStateRaw(val);
    setItem(KEYS.PATTERN_STATE, val);
  }

  function setPatternSummary(val) {
    setPatternSummaryRaw(val);
    setItem(KEYS.PATTERN_SUMMARY, val);
  }

  const addMessage = useCallback((message) => {
    setCurrentTranscriptState((prev) => {
      const next = [...prev, message];
      setItem(KEYS.CURRENT_TRANSCRIPT, next);
      return next;
    });
  }, []);

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

  /**
   * Saves the current transcript as a completed run, then immediately evaluates
   * whether to fire the LLM pattern check.
   *
   * Pattern check fires if:
   *   1. runCount after save >= 2
   *   2. patternState is "idle", "pattern_not_found", or "error" (not checking/found/generating/complete)
   *   3. The heuristic detects keyword overlap across ≥2 transcripts
   */
  const saveRun = useCallback(async (currentState) => {
    // currentState is passed from App to avoid stale closure issues
    const { currentPatternState, currentTranscripts, currentRunCount, currentCurrentTranscript } = currentState;

    const nextTranscripts = [...currentTranscripts, currentCurrentTranscript];
    const nextCount = currentRunCount + 1;

    setTranscriptsState(nextTranscripts);
    setRunCountState(nextCount);
    setItem(KEYS.TRANSCRIPTS, nextTranscripts);
    setItem(KEYS.RUN_COUNT, nextCount);
    setCurrentTranscriptState([]);
    setItem(KEYS.CURRENT_TRANSCRIPT, []);

    const retriggerable = ["idle", "pattern_not_found", "error"];
    if (
      nextCount >= 2 &&
      retriggerable.includes(currentPatternState) &&
      hasKeywordOverlap(nextTranscripts)
    ) {
      setPatternState("checking");
      try {
        const apiTranscripts = nextTranscripts.map((t) =>
          t.map((m) => ({ role: m.role, content: m.content }))
        );
        const result = await apiCheckPattern(apiTranscripts);
        if (result.found) {
          setPatternSummary(result.pattern_summary);
          setPatternState("pattern_found");
        } else {
          setPatternState("pattern_not_found");
        }
      } catch {
        setPatternState("error");
      }
    }
  }, []);

  return {
    runCount,
    transcripts,
    currentTranscript,
    evalTriggered,
    evalDismissed,
    evalRubric,
    patternState,
    patternSummary,
    addMessage,
    saveRun,
    markTriggered,
    dismissEval,
    saveRubric,
    setPatternState,
  };
}
```

- [ ] **Step 2: Verify the dev server starts without import errors**

```bash
cd C:\Users\91978\eval-discovery\frontend && npm run dev
```
Expected: starts. App.jsx will now have type errors on the old `saveRun` call signature — fix those in Task 10.

- [ ] **Step 3: Commit**

```bash
cd C:\Users\91978\eval-discovery
git add frontend/src/hooks/useSession.js
git commit -m "feat: replace threshold trigger with content-aware pattern state machine in useSession"
```

---

### Task 10: Update `frontend/src/App.jsx`

**Files:**
- Modify: `frontend/src/App.jsx`

- [ ] **Step 1: Replace `frontend/src/App.jsx`**

```jsx
import { useState } from "react";
import { useSession } from "./hooks/useSession";
import { generateRubric } from "./api";
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
    patternState,
    patternSummary,
    addMessage,
    saveRun,
    markTriggered,
    dismissEval,
    saveRubric,
    setPatternState,
  } = useSession();

  const [rubricError, setRubricError] = useState(null);

  async function handleSaveRun() {
    await saveRun({
      currentPatternState: patternState,
      currentTranscripts: transcripts,
      currentRunCount: runCount,
      currentCurrentTranscript: currentTranscript,
    });
  }

  async function handleDiscoverEvals() {
    if (!patternSummary) return;
    setPatternState("generating");
    setRubricError(null);
    markTriggered();
    try {
      const rubric = await generateRubric(patternSummary);
      saveRubric(rubric);
      setPatternState("complete");
    } catch (err) {
      setRubricError(err.message);
      setPatternState("error");
    }
  }

  function handleSaveRubric(edited) {
    saveRubric(edited);
    const blob = new Blob([JSON.stringify(edited, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "eval-rubric.json";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  const showEvalPanel = evalRubric && evalTriggered && !evalDismissed && patternState === "complete";
  const isChecking = patternState === "checking";
  const isGenerating = patternState === "generating";

  return (
    <div className="flex flex-col h-screen bg-white">
      {/* Header */}
      <header className="px-6 py-4 border-b flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">Eval Discovery</h1>
          <p className="text-xs text-gray-500">Chat with Alex · CloudSync SDR</p>
        </div>
        <div className="flex items-center gap-3">
          {isChecking && (
            <span className="text-xs text-blue-600 animate-pulse font-medium">
              Checking for patterns…
            </span>
          )}
          {isGenerating && (
            <span className="text-xs text-amber-600 animate-pulse font-medium">
              Generating eval…
            </span>
          )}
          {patternState === "error" && !isGenerating && (
            <span className="text-xs text-red-500">
              Pattern check failed — save another run to retry.
            </span>
          )}
          {rubricError && (
            <span className="text-xs text-red-500">{rubricError}</span>
          )}
        </div>
      </header>

      {/* Body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: chat */}
        <div className="flex flex-col flex-1 overflow-hidden">
          <RunControls
            runCount={runCount}
            currentTranscriptLength={currentTranscript.length}
            patternState={patternState}
            onSaveRun={handleSaveRun}
            onDiscoverEvals={handleDiscoverEvals}
          />
          <ChatInterface
            currentTranscript={currentTranscript}
            onMessage={addMessage}
            disabled={isGenerating}
          />
        </div>

        {/* Right: eval panel */}
        {showEvalPanel && (
          <div className="w-96 flex-shrink-0 overflow-hidden">
            <EvalSuggestionPanel
              rubric={evalRubric}
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

- [ ] **Step 2: Commit**

```bash
git add frontend/src/App.jsx
git commit -m "feat: update App.jsx — use pattern state machine, add handleDiscoverEvals"
```

---

### Task 11: Update `frontend/src/components/RunControls.jsx`

**Files:**
- Modify: `frontend/src/components/RunControls.jsx`

- [ ] **Step 1: Replace `frontend/src/components/RunControls.jsx`**

```jsx
export default function RunControls({
  runCount,
  currentTranscriptLength,
  patternState,
  onSaveRun,
  onDiscoverEvals,
}) {
  const canSave = currentTranscriptLength >= 3;
  const showDiscoverButton = patternState === "pattern_found";
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
git commit -m "feat: update RunControls — remove threshold display, add Discover Evals button"
```

---

### Task 12: Update `frontend/src/components/EvalSuggestionPanel.jsx`

**Files:**
- Modify: `frontend/src/components/EvalSuggestionPanel.jsx`

- [ ] **Step 1: Replace `frontend/src/components/EvalSuggestionPanel.jsx`**

```jsx
import { useState, useEffect } from "react";

export default function EvalSuggestionPanel({ rubric, onDismiss, onSave }) {
  const [edited, setEdited] = useState(rubric);

  useEffect(() => {
    setEdited(rubric);
  }, [rubric]);

  function handleChange(field, value) {
    setEdited((prev) => ({ ...prev, [field]: value }));
  }

  return (
    <div className="border-l bg-amber-50 flex flex-col h-full">
      {/* Header */}
      <div className="px-4 py-3 bg-amber-100 border-b border-amber-200 flex items-start justify-between gap-2">
        <div>
          <p className="text-xs font-semibold text-amber-700 uppercase tracking-wide">
            Eval Suggestion
          </p>
          <p className="text-sm font-medium text-amber-900 mt-0.5">
            {edited.name}
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

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Why discovered — read-only */}
        <div className="bg-blue-50 border border-blue-200 rounded-lg px-3 py-2">
          <p className="text-xs font-semibold text-blue-700 mb-1">Why this was discovered</p>
          <p className="text-xs text-blue-800">{edited.why_discovered}</p>
        </div>

        {/* Editable rubric fields */}
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

- [ ] **Step 2: Run the full backend test suite one final time**

```bash
cd C:\Users\91978\eval-discovery && python -m pytest tests/ -v
```
Expected: all tests pass.

- [ ] **Step 3: Manual smoke test**

Start both servers:
```bash
# Terminal 1
cd C:\Users\91978\eval-discovery && uvicorn backend.main:app --reload

# Terminal 2
cd C:\Users\91978\eval-discovery\frontend && npm run dev
```

Manual checklist:
- [ ] Chat with Alex — no `<think>` blocks visible in replies
- [ ] Complete 2 runs that mention "price" or "budget" — "Discover Evals" button does NOT appear yet (LLM check in flight, then either appears or doesn't based on result)
- [ ] When "Discover Evals" button appears, click it — rubric panel slides in with `why_discovered` field at top
- [ ] `why_discovered` is read-only (blue box), other fields are editable
- [ ] "Save Rubric" downloads a JSON file that includes `why_discovered`
- [ ] Run 2 conversations on unrelated topics (no keywords) — button does NOT appear

- [ ] **Step 4: Final commit**

```bash
git add frontend/src/components/EvalSuggestionPanel.jsx
git commit -m "feat: update EvalSuggestionPanel — remove intent header, add why_discovered field"
```
