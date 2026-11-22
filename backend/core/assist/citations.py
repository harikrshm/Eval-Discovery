"""Deterministic citation check for Patent Assist answers. No model call, no I/O.

The model is told to cite claims as "claim N" and law as "[id]". This module
checks those citations against what the page and the law excerpts actually
contained, so a made-up claim number or law id can be flagged to the user.

Claim references use exactly the grammar of `brief.parse_claim_refs`
(`brief.iter_claim_ref_ranges` is the shared parser: ASCII digits only, at most
6 digits per number, ranges and "and"/"or" lists, "claimed"/"reclaim" are not
references). A referenced number that is not in `brief.claims` is unverified.

Law citations are `[id]` where id starts with "usc-" or "mpep-" followed by
[a-z0-9-] (at most 64 characters in all), or a short comma list of such ids
("[usc-101, usc-103]"); each id is unverified when it is not in `provided_ids`.
Any other bracketed text ("[1]", "[snippet]", "[Figure 3]", "[USC-103]", "[]")
is not a law citation and is ignored.

Everything is bounded so a hostile answer cannot slow the check: the regexes
have bounded quantifiers, a range is never expanded in full (the runs of
missing claim numbers are computed once and found per range by bisection, so a
range costs O(log n + MAX_REPORTED); at most MAX_REPORTED missing numbers are
collected), so the lists returned here are short even for "claims 1-999999". `ok` is False
as soon as anything is unverified.
"""

import re
from bisect import bisect_left
from dataclasses import dataclass, field

from backend.core.assist.brief import Brief, iter_claim_ref_ranges

MAX_REPORTED = 50
_LAW_ID_MAX = 64

_ID = rf"(?:usc|mpep)-[a-z0-9-]{{1,{_LAW_ID_MAX - 5}}}"
# "[usc-103]" or a short comma list "[usc-101, usc-103]" (each id is checked).
_LAW_RE = re.compile(rf"\[({_ID}(?:\s{{0,3}},\s{{0,3}}(?:and\s{{1,3}})?{_ID}){{0,9}})\]")
_SPLIT_RE = re.compile(r"[\s,]+(?:and\s+)?")


@dataclass
class CitationCheck:
    ok: bool
    unverified_claims: list[int] = field(default_factory=list)
    unverified_law: list[str] = field(default_factory=list)


def _unverified_claims(answer: str, brief: Brief) -> list[int]:
    ordered = sorted({c.number for c in brief.claims})
    top = ordered[-1] if ordered else 0
    # Maximal runs of missing numbers inside 1..top (at most len(ordered) + 1 runs).
    gap_start: list[int] = []
    gap_end: list[int] = []
    prev = 0
    for n in ordered:
        if n > prev + 1:
            gap_start.append(prev + 1)
            gap_end.append(n - 1)
        prev = n
    missing: set[int] = set()

    def take(numbers: range) -> None:
        for n in numbers:
            if len(missing) >= MAX_REPORTED:
                return
            missing.add(n)

    for start, end in iter_claim_ref_ranges(answer):
        if len(missing) >= MAX_REPORTED:
            break
        lo = max(start, 1)  # same floor as parse_claim_refs; a reversed range is empty
        if lo > end:
            continue
        # Gap runs that overlap lo..end (they are sorted and disjoint), then anything above the top claim.
        i = bisect_left(gap_end, lo)
        while i < len(gap_start) and gap_start[i] <= end and len(missing) < MAX_REPORTED:
            take(range(max(gap_start[i], lo), min(gap_end[i], end) + 1))
            i += 1
        take(range(max(lo, top + 1), end + 1))
    return sorted(missing)


def _unverified_law(answer: str, provided_ids: list[str]) -> list[str]:
    provided = set(provided_ids)
    seen: dict[str, None] = {}
    for m in _LAW_RE.finditer(answer):
        for law_id in _SPLIT_RE.split(m.group(1)):
            if law_id not in provided:
                seen[law_id] = None
        if len(seen) >= MAX_REPORTED:
            break
    return list(seen)[:MAX_REPORTED]


def check_citations(answer: str, brief: Brief, provided_ids: list[str]) -> CitationCheck:
    """Flag claim numbers not on the page and law ids that were not supplied."""
    claims = _unverified_claims(answer, brief)
    law = _unverified_law(answer, provided_ids)
    return CitationCheck(ok=not claims and not law, unverified_claims=claims, unverified_law=law)
