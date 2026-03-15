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
