import pytest

from backend.core.config import MAX_PROMPT_CHARS, PRESETS, get_preset

# Copied verbatim from frontend/src/hooks/useSession.js PATTERN_KEYWORDS (lines 16-37).
# 40 terms; the README's "38" is stale.
LEGACY_KEYWORDS = {
    "price", "pricing", "cost", "costs", "budget", "expensive", "cheap",
    "afford", "pay", "payment", "fee", "how much", "monthly", "annual",
    "demo", "meeting", "schedule", "calendar", "book", "appointment",
    "20 minutes", "30 minutes", "time slot",
    "not interested", "busy", "no thanks", "bad time", "pass",
    "competitor", "alternative", "already using", "other solution",
    "snowflake", "databricks",
    "voicemail", "leave a message",
    "qualify", "decision maker", "data team", "pipeline",
}


def test_legacy_keywords_literal_has_40_terms():
    assert len(LEGACY_KEYWORDS) == 40


def test_alex_preset_keeps_every_legacy_keyword():
    terms = {t for b in PRESETS["alex-sales"].analysis.behaviors for t in b.trigger_terms}
    assert terms == LEGACY_KEYWORDS


def test_alex_preset_uses_legacy_system_prompt():
    from backend.prompts import ALEX_SYSTEM_PROMPT

    assert PRESETS["alex-sales"].config.system_prompt == ALEX_SYSTEM_PROMPT


def test_preset_keys_and_role_labels():
    assert set(PRESETS) == {"alex-sales", "support", "booking"}
    assert PRESETS["alex-sales"].analysis.user_role_label == "Prospect"
    assert PRESETS["support"].analysis.user_role_label == "Customer"
    assert PRESETS["booking"].analysis.user_role_label == "Caller"
    for key, p in PRESETS.items():
        assert p.id == key


def test_every_preset_is_well_formed():
    for p in PRESETS.values():
        assert 0 < len(p.config.system_prompt) <= MAX_PROMPT_CHARS
        assert len(p.analysis.behaviors) >= 4
        assert all(
            len(b.trigger_terms) >= 2 and all(t == t.lower() for t in b.trigger_terms)
            for b in p.analysis.behaviors
        )
        assert len(p.analysis.starter_messages) == 3
        assert p.config.user_role_label == p.analysis.user_role_label


def test_fresh_prompts_are_short():
    assert len(PRESETS["support"].config.system_prompt) < 1500
    assert len(PRESETS["booking"].config.system_prompt) < 1500


def test_get_preset_returns_preset_and_unknown_raises():
    assert get_preset("support") is PRESETS["support"]
    with pytest.raises(KeyError):
        get_preset("nope")
