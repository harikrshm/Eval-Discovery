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


def test_temperature_is_forwarded_only_when_given():
    from backend.groq_client import groq_chat

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "ok"
    with patch("backend.groq_client.client.chat.completions.create", return_value=mock_response) as create:
        groq_chat(system="s", messages=[{"role": "user", "content": "hi"}], temperature=0)
        assert create.call_args.kwargs["temperature"] == 0
        groq_chat(system="s", messages=[{"role": "user", "content": "hi"}])
        assert "temperature" not in create.call_args.kwargs


def test_judge_model_is_gpt_oss_120b():
    from backend.groq_client import MODEL_JUDGE

    assert MODEL_JUDGE == "openai/gpt-oss-120b"


def test_max_tokens_is_forwarded_only_when_given():
    from backend.groq_client import groq_chat

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "ok"
    with patch("backend.groq_client.client.chat.completions.create", return_value=mock_response) as create:
        groq_chat(system="s", messages=[{"role": "user", "content": "hi"}], max_tokens=123)
        assert create.call_args.kwargs["max_tokens"] == 123
        groq_chat(system="s", messages=[{"role": "user", "content": "hi"}])
        assert "max_tokens" not in create.call_args.kwargs


def test_client_bounds_each_call_with_a_timeout_and_five_retries():
    from backend import groq_client

    assert groq_client.GROQ_TIMEOUT_SECONDS == 45.0
    assert groq_client.GROQ_MAX_RETRIES == 5
    assert groq_client.client.timeout == 45.0
    assert groq_client.client.max_retries == 5


def test_client_is_built_with_the_timeout_and_retry_arguments():
    from backend import groq_client

    with patch("backend.groq_client.Groq") as groq_cls:
        groq_client._make_client("sk-test")
    groq_cls.assert_called_once_with(api_key="sk-test", timeout=45.0, max_retries=5)
