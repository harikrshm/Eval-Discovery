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


def test_criteria_model_receives_reasoning_format_hidden():
    from backend.groq_client import groq_chat, MODEL_CRITERIA

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "ok"

    with patch("backend.groq_client.client.chat.completions.create", return_value=mock_response) as mock_create:
        groq_chat(system="sys", messages=[{"role": "user", "content": "hi"}], model=MODEL_CRITERIA)
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


def test_max_tokens_is_forwarded_only_when_given():
    from backend.groq_client import groq_chat

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "ok"
    with patch("backend.groq_client.client.chat.completions.create", return_value=mock_response) as create:
        groq_chat(system="s", messages=[{"role": "user", "content": "hi"}], max_tokens=123)
        assert create.call_args.kwargs["max_tokens"] == 123
        groq_chat(system="s", messages=[{"role": "user", "content": "hi"}])
        assert "max_tokens" not in create.call_args.kwargs
