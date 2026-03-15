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
