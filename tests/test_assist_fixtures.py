"""The extension's exported briefs (from real saved Google Patents pages) satisfy the backend model.

extension/fixtures/*.expected.json are written by `node scripts/export-expected.js` (the JavaScript
extractor run over the trimmed fixtures) and reviewed against the pages by hand; this keeps the
extractor's output and the backend `Brief` caps from drifting apart.
"""

import json
from pathlib import Path

import pytest

from backend.core.assist.brief import Brief, build_claim_tree, is_us_patent

FIXTURES = Path(__file__).resolve().parent.parent / "extension" / "fixtures"

# fixture name -> (patent number, is a US number)
PAGES = {
    "ep4517554a1": ("EP4517554A1", False),
    "us12401720b1": ("US12401720B1", True),
    "us6756997b1": ("US6756997B1", True),
    "us20250119367a1": ("US20250119367A1", True),
}

COMPLETENESS_KEYS = {
    "bibliographic", "abstract", "claims", "description", "classifications",
    "landscapes", "citations", "family", "legal_events",
}


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.expected.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name,expected", PAGES.items())
def test_exported_brief_validates(name, expected):
    number, is_us = expected
    data = _load(name)
    brief = Brief.model_validate(data)
    assert brief.patent_number == number
    assert is_us_patent(brief.patent_number) is is_us
    assert set(brief.completeness) == COMPLETENESS_KEYS
    assert all(brief.completeness.values())
    assert brief.claims and brief.description and brief.classifications


@pytest.mark.parametrize("name", PAGES)
def test_claim_tree_matches_backend_rules(name):
    """The JavaScript depends_on/independent equal the Python build_claim_tree on the same text."""
    brief = Brief.model_validate(_load(name))
    rebuilt = build_claim_tree([(c.number, c.text) for c in brief.claims])
    assert [(c.number, c.depends_on, c.independent) for c in rebuilt] == [
        (c.number, c.depends_on, c.independent) for c in brief.claims
    ]


def test_granted_patents_carry_the_grant_event():
    for name in ("us12401720b1", "us6756997b1"):
        brief = Brief.model_validate(_load(name))
        assert "grant" in brief.dates
        assert any(e.text == "Application granted" for e in brief.legal_events)
    assert "grant" not in _load("us20250119367a1")["dates"]
