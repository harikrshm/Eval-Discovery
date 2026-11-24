"""Extract validated claim, paragraph and section references from an answer.

The widget turns each reference into a clickable pill. A reference is returned
only if it points at something that exists in the brief: every claim number in
a claim reference, a `[pNNNN]` marker present in the description, or a section
name from SECTION_NAMES. Offsets are UTF-16 code units (JavaScript string
indexes), not Python code points.

Pure module: no I/O, no DB or web imports.
"""

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from backend.core.assist.brief import Brief, iter_claim_ref_spans
from backend.core.assist.intent import SECTION_NAMES

MAX_REFERENCES = 50

PARAGRAPH_MARKER_RE = re.compile(r"\[p([0-9]{1,6})\]")
SECTION_TOKEN_RE = re.compile(r"\[section:([a-z_]{1,20})\]")

SECTION_LABELS: dict[str, str] = {
    "claims": "Claims",
    "description": "Description",
    "classifications": "Classifications",
    "citations": "Citations",
    "family": "Family",
    "legal_events": "Legal events",
    "landscapes": "Landscapes",
}


@dataclass(frozen=True)
class Reference:
    start: int  # UTF-16 offset, inclusive
    end: int  # UTF-16 offset, exclusive
    kind: str  # "claim" | "paragraph" | "section"
    target: str
    label: str


def paragraph_numbers(brief: Brief) -> set[str]:
    """Digit strings of every `[pNNNN]` marker in the description sections."""
    return {m.group(1) for section in brief.description for m in PARAGRAPH_MARKER_RE.finditer(section.text)}


def utf16_offset(text: str, index: int) -> int:
    """UTF-16 code units in `text[:index]`."""
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text[:index])


def _all_exist(numbers: list[int], items: list[tuple[int, int]]) -> bool:
    """True when every integer in every (lo, hi) item is in sorted `numbers` (no range expansion)."""
    if not items:
        return False
    for lo, hi in items:
        if lo < 1 or hi < lo:
            return False
        if bisect_right(numbers, hi) - bisect_left(numbers, lo) != hi - lo + 1:
            return False
    return True


def extract_references(answer: str, brief: Brief) -> list[Reference]:
    """Validated references in `answer`, sorted by start, non-overlapping, at most MAX_REFERENCES."""
    # (start, end, kind, target, label) with str indexes, converted to UTF-16 at the end.
    found: list[tuple[int, int, str, str, str]] = []

    claim_numbers = sorted({c.number for c in brief.claims})
    for start, end, items in iter_claim_ref_spans(answer):
        if _all_exist(claim_numbers, items):
            found.append((start, end, "claim", str(items[0][0]), answer[start:end]))

    paragraphs = paragraph_numbers(brief)
    for m in PARAGRAPH_MARKER_RE.finditer(answer):
        if m.group(1) in paragraphs:
            found.append((m.start(), m.end(), "paragraph", m.group(1), "¶" + m.group(1)))

    for m in SECTION_TOKEN_RE.finditer(answer):
        name = m.group(1)
        if name in SECTION_NAMES:
            found.append((m.start(), m.end(), "section", name, SECTION_LABELS[name]))

    found.sort(key=lambda f: (f[0], f[1]))
    kept: list[tuple[int, int, str, str, str]] = []
    last_end = 0
    for f in found:
        if f[0] < last_end:
            continue
        kept.append(f)
        last_end = f[1]
        if len(kept) == MAX_REFERENCES:
            break

    return [
        Reference(utf16_offset(answer, s), utf16_offset(answer, e), kind, target, label)
        for s, e, kind, target, label in kept
    ]
