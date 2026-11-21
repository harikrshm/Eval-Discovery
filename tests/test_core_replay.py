from unittest.mock import patch

import pytest

from backend.core.config import AgentConfig
from backend.core.replay import (
    MAX_REPLAY_USER_MESSAGES,
    MAX_REPLY_CHARS,
    REPLAY_MAX_TOKENS,
    ReplayFailed,
    replay,
    user_messages,
)
from backend.groq_client import MODEL_CHAT

CONFIG = AgentConfig(
    name="Zorblax Support Bot",
    system_prompt="You are Zorblax. Never promise refunds.",
    user_role_label="Patron",
)


def test_constants():
    assert MAX_REPLAY_USER_MESSAGES == 20
    assert REPLAY_MAX_TOKENS == 3000
    assert MAX_REPLY_CHARS == 16000


def test_replay_sends_growing_history_at_temperature_zero():
    with patch("backend.core.replay.groq_chat", side_effect=["r1", "r2"]) as mock:
        out = replay(CONFIG, ["hello", "refund me"])

    assert out == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "r1"},
        {"role": "user", "content": "refund me"},
        {"role": "assistant", "content": "r2"},
    ]
    assert mock.call_count == 2
    first, second = mock.call_args_list
    assert first.args[0] == CONFIG.system_prompt
    assert first.args[1] == [{"role": "user", "content": "hello"}]
    assert second.args[0] == CONFIG.system_prompt
    assert second.args[1] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "r1"},
        {"role": "user", "content": "refund me"},
    ]
    for call in (first, second):
        assert call.kwargs["temperature"] == 0
        assert call.kwargs["max_tokens"] == REPLAY_MAX_TOKENS
        assert call.kwargs.get("model", MODEL_CHAT) == MODEL_CHAT


def test_replay_history_is_not_aliased_between_calls():
    with patch("backend.core.replay.groq_chat", side_effect=["r1", "r2"]) as mock:
        replay(CONFIG, ["a", "b"])
    # the first call's recorded argument must not have been mutated by later turns
    assert mock.call_args_list[0].args[1] == [{"role": "user", "content": "a"}]


def test_replay_truncates_long_replies():
    long = "x" * (MAX_REPLY_CHARS + 500)
    with patch("backend.core.replay.groq_chat", return_value=long):
        out = replay(CONFIG, ["hi"])
    assert len(out[1]["content"]) == MAX_REPLY_CHARS


def test_replay_rejects_empty_and_too_many():
    with patch("backend.core.replay.groq_chat") as mock:
        with pytest.raises(ValueError):
            replay(CONFIG, [])
        with pytest.raises(ValueError):
            replay(CONFIG, ["m"] * (MAX_REPLAY_USER_MESSAGES + 1))
    mock.assert_not_called()


def test_replay_accepts_exactly_the_cap():
    with patch("backend.core.replay.groq_chat", return_value="ok") as mock:
        out = replay(CONFIG, ["m"] * MAX_REPLAY_USER_MESSAGES)
    assert mock.call_count == MAX_REPLAY_USER_MESSAGES
    assert len(out) == 2 * MAX_REPLAY_USER_MESSAGES


def test_replay_wraps_groq_errors():
    with patch("backend.core.replay.groq_chat", side_effect=RuntimeError("boom")):
        with pytest.raises(ReplayFailed):
            replay(CONFIG, ["hi"])


def test_replay_failed_is_runtime_error():
    assert issubclass(ReplayFailed, RuntimeError)


def test_user_messages_keeps_only_user_turns():
    transcript = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "two"},
        {"role": "system", "content": "s"},
    ]
    assert user_messages(transcript) == ["one", "two"]


def test_user_messages_caps_at_twenty():
    transcript = [{"role": "user", "content": f"m{i}"} for i in range(30)]
    got = user_messages(transcript)
    assert len(got) == MAX_REPLAY_USER_MESSAGES
    assert got[0] == "m0" and got[-1] == "m19"
