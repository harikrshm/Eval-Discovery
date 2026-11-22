import importlib
import os
from unittest.mock import MagicMock, patch

import pytest


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


# --- Phase 4: dotenv switch, bounded request retries, token pacing ---


class _FakeCompletions:
    def __init__(self, owner, tag, tokens=10):
        self.owner, self.tag, self.tokens = owner, tag, tokens

    def create(self, **kwargs):
        self.owner.calls.append((self.tag, kwargs))
        resp = MagicMock()
        resp.choices[0].message.content = f"reply-{self.tag}"
        resp.usage.total_tokens = self.tokens
        return resp


class _FakeClient:
    """Records calls; with_options returns a distinct client tagged with the max_retries used."""

    def __init__(self, owner=None, tag="default"):
        self.owner = owner or self
        if owner is None:
            self.calls = []
            self.options_calls = []
        self.chat = MagicMock()
        self.chat.completions = _FakeCompletions(self.owner, tag)

    def with_options(self, **kwargs):
        self.owner.options_calls.append(kwargs)
        return _FakeClient(owner=self.owner, tag=f"retries{kwargs.get('max_retries')}")


class _FakeClientNoOptions:
    def __init__(self):
        self.calls = []
        self.chat = MagicMock()
        self.chat.completions = _FakeCompletions(self, "plain")


@pytest.fixture
def _pacing_off():
    from backend import groq_client

    groq_client.configure_pacing(None)
    yield
    groq_client.configure_pacing(None)


def test_request_retries_sets_and_restores_the_context_var():
    from backend import groq_client

    assert groq_client.request_retry_limit.get() is None
    with groq_client.request_retries(2):
        assert groq_client.request_retry_limit.get() == 2
    assert groq_client.request_retry_limit.get() is None


def test_request_retries_restores_on_exception():
    from backend import groq_client

    with pytest.raises(RuntimeError):
        with groq_client.request_retries(2):
            raise RuntimeError("boom")
    assert groq_client.request_retry_limit.get() is None


def test_request_retries_nests_and_restores_the_outer_value():
    from backend import groq_client

    with groq_client.request_retries(3):
        with groq_client.request_retries(1):
            assert groq_client.request_retry_limit.get() == 1
        assert groq_client.request_retry_limit.get() == 3


def test_groq_chat_uses_with_options_when_the_retry_limit_is_set(_pacing_off):
    from backend import groq_client

    fake = _FakeClient()
    with patch.object(groq_client, "client", fake):
        with groq_client.request_retries(2):
            out = groq_client.groq_chat("s", [{"role": "user", "content": "hi"}])
    assert fake.options_calls == [{"max_retries": 2}]
    assert out == "reply-retries2"
    assert [tag for tag, _ in fake.calls] == ["retries2"]


def test_groq_chat_uses_the_default_client_without_a_retry_limit(_pacing_off):
    from backend import groq_client

    fake = _FakeClient()
    with patch.object(groq_client, "client", fake):
        out = groq_client.groq_chat("s", [{"role": "user", "content": "hi"}])
    assert fake.options_calls == []
    assert out == "reply-default"


def test_groq_chat_tolerates_a_client_without_with_options(_pacing_off):
    from backend import groq_client

    fake = _FakeClientNoOptions()
    with patch.object(groq_client, "client", fake):
        with groq_client.request_retries(2):
            out = groq_client.groq_chat("s", [{"role": "user", "content": "hi"}])
    assert out == "reply-plain"


def test_skip_dotenv_switch_leaves_dotenv_only_variable_unset(tmp_path, monkeypatch):
    import dotenv
    from backend import groq_client

    env_file = tmp_path / ".env"
    env_file.write_text("EVAL_DISCOVERY_DOTENV_PROBE=from-dotenv\n", encoding="utf-8")
    real_load = dotenv.load_dotenv
    # Point the loader at the temp .env so the repo-root .env is never touched.
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: real_load(dotenv_path=env_file))
    monkeypatch.delenv("EVAL_DISCOVERY_DOTENV_PROBE", raising=False)
    try:
        # Control: without the switch the temp .env is loaded, so the check below is meaningful.
        monkeypatch.delenv("GROQ_SKIP_DOTENV", raising=False)
        importlib.reload(groq_client)
        assert os.environ.get("EVAL_DISCOVERY_DOTENV_PROBE") == "from-dotenv"

        monkeypatch.delenv("EVAL_DISCOVERY_DOTENV_PROBE")
        monkeypatch.setenv("GROQ_SKIP_DOTENV", "1")
        importlib.reload(groq_client)
        assert "EVAL_DISCOVERY_DOTENV_PROBE" not in os.environ
    finally:
        monkeypatch.undo()
        os.environ.pop("EVAL_DISCOVERY_DOTENV_PROBE", None)
        importlib.reload(groq_client)


class _Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _pacer(limit=100, **kw):
    from backend.groq_client import TokenPacer

    c = _Clock()
    return TokenPacer(limit, clock=c, sleep=c.sleep, **kw), c


def test_pacer_does_not_sleep_under_the_limit():
    p, c = _pacer(100)
    p.acquire("m", 40)
    p.record("m", 40)
    p.acquire("m", 50)
    assert c.sleeps == []


def test_pacer_sleeps_until_the_oldest_record_leaves_the_window():
    p, c = _pacer(100)
    p.record("m", 90)
    c.now += 50  # record at t=1000 leaves the window at t=1060
    p.acquire("m", 30)
    assert c.sleeps == [pytest.approx(10.0)]


def test_pacer_caps_the_wait_at_twenty_seconds_then_proceeds():
    p, c = _pacer(100)
    p.record("m", 100)
    p.acquire("m", 50)  # would need 60 s
    assert c.sleeps == [pytest.approx(20.0)]


def test_pacer_windows_are_per_model():
    p, c = _pacer(100)
    p.record("a", 100)
    p.acquire("b", 90)
    assert c.sleeps == []


def test_pacer_records_expire_after_the_window():
    p, c = _pacer(100)
    p.record("m", 100)
    c.now += 61
    p.acquire("m", 100)
    assert c.sleeps == []


def test_pacer_does_not_sleep_for_an_oversized_call_on_an_empty_window():
    p, c = _pacer(100)
    p.acquire("m", 500)
    assert c.sleeps == []


def test_groq_chat_records_usage_total_tokens(_pacing_off):
    from backend import groq_client

    c = _Clock()
    groq_client.configure_pacing(100, clock=c, sleep=c.sleep)
    fake = _FakeClient()
    fake.chat.completions.tokens = 95
    with patch.object(groq_client, "client", fake):
        groq_client.groq_chat("s", [{"role": "user", "content": "hi"}], max_tokens=10)
        assert c.sleeps == []
        groq_client.groq_chat("s", [{"role": "user", "content": "hi"}], max_tokens=10)
    assert len(c.sleeps) == 1  # the second call waits because 95 tokens are on record


def test_groq_chat_falls_back_to_the_estimate_when_usage_is_not_an_int(_pacing_off):
    from backend import groq_client

    c = _Clock()
    groq_client.configure_pacing(200, clock=c, sleep=c.sleep)
    fake = _FakeClient()

    def create(**kwargs):
        resp = MagicMock()  # usage.total_tokens is a MagicMock, not an int
        resp.choices[0].message.content = "ok"
        return resp

    fake.chat.completions.create = create
    msgs = [{"role": "user", "content": "x" * 400}]
    # estimate = (3 + 400) // 4 + 10 = 110; the first call records 110, so 110 + 110 > 200 on the second
    with patch.object(groq_client, "client", fake):
        groq_client.groq_chat("sys", msgs, max_tokens=10)
        assert c.sleeps == []
        groq_client.groq_chat("sys", msgs, max_tokens=10)
    assert len(c.sleeps) == 1


def test_estimate_uses_chars_over_four_plus_max_tokens_or_1000():
    from backend.groq_client import _estimate_tokens

    assert _estimate_tokens("a" * 40, [{"role": "user", "content": "b" * 40}], 50) == 20 + 50
    assert _estimate_tokens("a" * 40, [{"role": "user", "content": "b" * 40}], None) == 20 + 1000


def test_configure_pacing_none_turns_pacing_off():
    from backend import groq_client

    c = _Clock()
    groq_client.configure_pacing(10, clock=c, sleep=c.sleep)
    groq_client.configure_pacing(None)
    fake = _FakeClient()
    with patch.object(groq_client, "client", fake):
        for _ in range(3):
            groq_client.groq_chat("s", [{"role": "user", "content": "hi"}], max_tokens=5000)
    assert c.sleeps == []


@pytest.mark.parametrize("value", [None, "", "abc", "0", "-5", "1.5"])
def test_invalid_or_unset_tpm_limit_means_pacing_off(monkeypatch, value):
    from backend import groq_client

    if value is None:
        monkeypatch.delenv("GROQ_TPM_LIMIT", raising=False)
    else:
        monkeypatch.setenv("GROQ_TPM_LIMIT", value)
    assert groq_client._pacer_from_env() is None


def test_valid_tpm_limit_builds_a_pacer(monkeypatch):
    from backend import groq_client

    monkeypatch.setenv("GROQ_TPM_LIMIT", "8000")
    assert groq_client._pacer_from_env().limit == 8000
