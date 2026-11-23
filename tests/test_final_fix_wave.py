"""Final fix wave: tests for the whole-branch review findings (I1 backend half, I2-I5, M2-M5, M11, M14)."""

import pytest

from backend.core.assist.assemble import assemble
from backend.core.assist.brief import Brief

SYSTEM = "You are Patent Assist."
EXTRACTOR_KEYS = (
    "bibliographic", "abstract", "claims", "description", "classifications",
    "landscapes", "citations", "family", "legal_events",
)


# ---- I1: a false completeness flag shows up as "incomplete" for every extractor section ----------


@pytest.mark.parametrize("key", EXTRACTOR_KEYS)
def test_i1_every_extractor_section_flag_false_is_named_incomplete(key):
    completeness = {k: True for k in EXTRACTOR_KEYS}
    completeness[key] = False
    brief = Brief(patent_number="US10000000B2", completeness=completeness)
    _, messages = assemble(brief, [], "Who cites this patent?", law_excerpts=[], system_prompt=SYSTEM)
    text = messages[-1]["content"]
    first = text[text.index("<page_brief>") + len("<page_brief>"):].strip().splitlines()[0]
    assert "missing or incomplete" in first
    assert key in first
    assert all(other not in first for other in EXTRACTOR_KEYS if other != key and other not in key and key not in other)
