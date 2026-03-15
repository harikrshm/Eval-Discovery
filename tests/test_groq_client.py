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
