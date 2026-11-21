import json
from unittest.mock import patch

import pytest

from backend.core.config import AgentConfig, Behavior
from backend.core.pattern import (
    MIN_TURNS_PER_TRANSCRIPT,
    PatternCheckFailed,
    PatternResult,
    TranscriptTooShort,
    detect_pattern,
    keyword_prefilter,
)
from backend.groq_client import MODEL_PATTERN

CONFIG = AgentConfig(name="Sam", system_prompt="Be helpful.", user_role_label="Customer")
BEHAVIORS = (
    Behavior(name="Refund", description="d", trigger_terms=("refund", "money back")),
    Behavior(name="Login", description="d", trigger_terms=("password",)),
)


def _run(*user_texts):
    """Build a transcript with the given user messages, each followed by an assistant reply."""
    out = []
    for text in user_texts:
        out.append({"role": "user", "content": text})
        out.append({"role": "assistant", "content": "ok"})
    return out


def _long_run(text="hello"):
    return _run(text, "more", "even more")


def test_prefilter_true_when_term_in_two_runs():
    assert keyword_prefilter(BEHAVIORS, [_run("I want a refund"), _run("Refund me now")]) is True


def test_prefilter_false_when_term_in_one_run_only():
    assert keyword_prefilter(BEHAVIORS, [_run("I want a refund"), _run("hello there")]) is False


def test_prefilter_ignores_assistant_messages():
    runs = [
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "refund policy"}],
        [{"role": "user", "content": "hey"}, {"role": "assistant", "content": "refund policy"}],
    ]
    assert keyword_prefilter(BEHAVIORS, runs) is False


def test_prefilter_counts_runs_not_repeats():
    assert keyword_prefilter(BEHAVIORS, [_run("refund", "refund again"), _run("hello")]) is False


def test_prefilter_is_case_insensitive():
    assert keyword_prefilter(BEHAVIORS, [_run("REFUND please"), _run("Money Back now")]) is False
    assert keyword_prefilter(BEHAVIORS, [_run("REFUND please"), _run("Refund Now")]) is True


def test_prefilter_needs_two_transcripts():
    assert keyword_prefilter(BEHAVIORS, [_run("refund refund")]) is False
    assert keyword_prefilter(BEHAVIORS, []) is False


def test_detect_pattern_found():
    raw = json.dumps({"found": True, "pattern_summary": "Probes refunds."})
    with patch("backend.core.pattern.groq_chat", return_value=raw) as m:
        result = detect_pattern(CONFIG, [_long_run(), _long_run()])
    assert result == PatternResult(True, "Probes refunds.")
    assert m.call_args.kwargs["model"] == MODEL_PATTERN


def test_detect_pattern_not_found():
    raw = json.dumps({"found": False, "pattern_summary": ""})
    with patch("backend.core.pattern.groq_chat", return_value=raw):
        result = detect_pattern(CONFIG, [_long_run(), _long_run()])
    assert result == PatternResult(False, "")


def test_detect_pattern_rejects_short_transcript():
    with patch("backend.core.pattern.groq_chat") as m:
        with pytest.raises(TranscriptTooShort) as exc:
            detect_pattern(CONFIG, [_long_run(), _run("one")[:MIN_TURNS_PER_TRANSCRIPT - 1]])
    assert "too short" in str(exc.value)
    assert "2" in str(exc.value)
    m.assert_not_called()


def test_detect_pattern_rejects_empty():
    with pytest.raises(ValueError, match="transcripts must not be empty."):
        detect_pattern(CONFIG, [])


def test_detect_pattern_wraps_groq_failure():
    with patch("backend.core.pattern.groq_chat", side_effect=RuntimeError("boom")):
        with pytest.raises(PatternCheckFailed):
            detect_pattern(CONFIG, [_long_run(), _long_run()])


def test_detect_pattern_found_with_empty_summary_fails():
    raw = json.dumps({"found": True, "pattern_summary": "  "})
    with patch("backend.core.pattern.groq_chat", return_value=raw):
        with pytest.raises(PatternCheckFailed):
            detect_pattern(CONFIG, [_long_run(), _long_run()])


def test_detect_pattern_unparsable_output_fails():
    with patch("backend.core.pattern.groq_chat", return_value="not json at all"):
        with pytest.raises(PatternCheckFailed):
            detect_pattern(CONFIG, [_long_run(), _long_run()])


def test_detect_pattern_missing_found_fails():
    raw = json.dumps({"pattern_summary": "x"})
    with patch("backend.core.pattern.groq_chat", return_value=raw):
        with pytest.raises(PatternCheckFailed):
            detect_pattern(CONFIG, [_long_run(), _long_run()])
