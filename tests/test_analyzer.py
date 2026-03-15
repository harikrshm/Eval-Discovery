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
