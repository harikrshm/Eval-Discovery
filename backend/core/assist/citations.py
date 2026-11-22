"""Deterministic citation check for Patent Assist answers. No model call, no I/O.

The model is told to cite claims as "claim N" and law as "[id]". This module
checks those citations against what the page and the law excerpts actually
contained, so a made-up claim number or law id can be flagged to the user.

Claim references use exactly the grammar of `brief.parse_claim_refs`
(`brief.iter_claim_ref_ranges` is the shared parser: ASCII digits only, at most
6 digits per number, ranges and "and"/"or" lists, "claimed"/"reclaim" are not
references). A referenced number that is not in `brief.claims` is unverified.

Law citations are `[id]` where id is 1-64 characters of [a-z0-9-]; the id is
unverified when it is not in `provided_ids`. Any other bracketed text
("[Figure 3]", "[USC-103]", "[a b]", "[]") is not id-shaped and is ignored.

Everything is bounded so a hostile answer cannot slow the check: the regexes
have bounded quantifiers, a range is never expanded in full (membership is
checked by bisection, and at most MAX_REPORTED missing numbers are collected),
so the lists returned here are short even for "claims 1-999999". `ok` is False
as soon as anything is unverified.
"""

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field

from backend.core.assist.brief import Brief, iter_claim_ref_ranges

MAX_REPORTED = 50
_LAW_ID_MAX = 64

_LAW_RE = re.compile(rf"\[([a-z0-9-]{{1,{_LAW_ID_MAX}}})\]")


@dataclass
class CitationCheck:
    ok: bool
    unverified_claims: list[int] = field(default_factory=list)
    unverified_law: list[str] = field(default_factory=list)


def _unverified_claims(answer: str, brief: Brief) -> list[int]:
    existing = {c.number for c in brief.claims}
    ordered = sorted(existing)
    top = ordered[-1] if ordered else 0
    missing: set[int] = set()
    for start, end in iter_claim_ref_ranges(answer):
        if len(missing) >= MAX_REPORTED:
            break
        lo = max(start, 1)  # same floor as parse_claim_refs; a reversed range is empty
        if lo > end:
            continue
        if end <= top and bisect_right(ordered, end) - bisect_left(ordered, lo) == end - lo + 1:
            continue  # every claim in lo..end exists
        n = lo
        while n <= end and len(missing) < MAX_REPORTED:
            if n not in existing:
                missing.add(n)
            n += 1
    return sorted(missing)


def _unverified_law(answer: str, provided_ids: list[str]) -> list[str]:
    provided = set(provided_ids)
    seen: dict[str, None] = {}
    for m in _LAW_RE.finditer(answer):
        law_id = m.group(1)
        if law_id not in provided:
            seen[law_id] = None
            if len(seen) >= MAX_REPORTED:
                break
    return list(seen)


def check_citations(answer: str, brief: Brief, provided_ids: list[str]) -> CitationCheck:
    """Flag claim numbers not on the page and law ids that were not supplied."""
    claims = _unverified_claims(answer, brief)
    law = _unverified_law(answer, provided_ids)
    return CitationCheck(ok=not claims and not law, unverified_claims=claims, unverified_law=law)
