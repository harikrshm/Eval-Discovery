"""Cross-language cap parity (final fix wave M3): the extension's widget, tracker and extractor caps must
equal the backend's. The JS constants are read as text, so no JS runtime is needed; a drift on either
side fails here."""

import re
from pathlib import Path

import pytest

from backend.core.assist import brief as brief_mod
from backend.core.config import (
    ASSIST_MAX_CONVERSATION_TURNS,
    ASSIST_MAX_HISTORY_TURNS,
    ASSIST_MAX_REASON_CHARS,
    ASSIST_MAX_TURN_CHARS,
    MAX_ASSISTANT_MESSAGE_CHARS,
    MAX_DURATION_MS,
    MAX_REQUEST_CHARS,
)

SRC = Path(__file__).resolve().parent.parent / "extension" / "src"


def js_object(text: str, name: str) -> dict[str, int]:
    """The integer members of ``export const NAME = { key: 123, ... }`` (underscores allowed in numbers)."""
    match = re.search(rf"export const {name} = \{{(.*?)\n\}}", text, re.DOTALL)
    assert match, f"{name} object not found"
    out = {}
    for key, value in re.findall(r"^\s*(\w+):\s*([\d_]+)\s*,?", match.group(1), re.MULTILINE):
        out[key] = int(value.replace("_", ""))
    return out


def js_number(text: str, name: str) -> int:
    match = re.search(rf"export const {name} = ([\d_]+)", text)
    assert match, f"{name} not found"
    return int(match.group(1).replace("_", ""))


def test_the_parser_reads_what_it_should():
    sample = "export const X = {\n  a: 1_000, // c\n  b: 20,\n}\nexport const N = 86_400_000 // d\n"
    assert js_object(sample, "X") == {"a": 1000, "b": 20}
    assert js_number(sample, "N") == 86_400_000


def test_widget_limits_match_the_backend():
    limits = js_object((SRC / "widget" / "Widget.js").read_text(encoding="utf-8"), "LIMITS")
    assert limits["question"] == ASSIST_MAX_TURN_CHARS
    assert limits["userTurn"] == ASSIST_MAX_TURN_CHARS
    assert limits["reason"] == ASSIST_MAX_REASON_CHARS
    assert limits["historyTurns"] == ASSIST_MAX_HISTORY_TURNS
    assert limits["conversationTurns"] == ASSIST_MAX_CONVERSATION_TURNS
    assert limits["assistantTurn"] == MAX_ASSISTANT_MESSAGE_CHARS
    assert limits["requestChars"] == MAX_REQUEST_CHARS


def test_tracker_duration_cap_matches_the_backend():
    assert js_number((SRC / "signals.js").read_text(encoding="utf-8"), "MAX_DURATION_MS") == MAX_DURATION_MS


def test_extractor_caps_match_the_brief_model():
    caps = js_object((SRC / "extractor.js").read_text(encoding="utf-8"), "CAPS")
    assert caps["claims"] == brief_mod.MAX_CLAIMS
    assert caps["claimChars"] == brief_mod.MAX_CLAIM_CHARS
    assert caps["descriptionSections"] == brief_mod.MAX_DESCRIPTION_SECTIONS
    assert caps["sectionChars"] == brief_mod.MAX_SECTION_CHARS
    assert caps["listItems"] == brief_mod.MAX_LIST_ITEMS
    assert caps["short"] == brief_mod.MAX_SHORT_CHARS
    assert caps["abstract"] == brief_mod.MAX_ABSTRACT_CHARS
    # the extractor's own budget leaves a safety margin under the backend's total
    assert caps["total"] <= brief_mod.MAX_BRIEF_JSON_CHARS - 60_000


@pytest.mark.parametrize("name", ["LIMITS", "CAPS"])
def test_a_drifted_value_would_be_caught(name):
    text = "export const LIMITS = {\n  question: 1999,\n}\n"
    if name == "LIMITS":
        assert js_object(text, "LIMITS")["question"] != ASSIST_MAX_TURN_CHARS
    else:
        assert js_object("export const CAPS = {\n  claims: 201,\n}\n", "CAPS")["claims"] != brief_mod.MAX_CLAIMS
