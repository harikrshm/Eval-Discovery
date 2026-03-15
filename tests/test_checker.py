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
