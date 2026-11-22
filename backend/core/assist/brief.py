"""Brief schema, claim dependency tree and US-number check for Patent Assist.

The brief is UNTRUSTED input from a browser extension: every cap below is
enforced by validation and over-cap input is rejected with an error that names
the field. Nothing is ever silently truncated.

Claim-reference rules (the JavaScript extractor ports exactly these rules and
is tested against the same cases):

1. A reference is the word "claim" or "claims" (case-insensitive, whole word)
   followed by whitespace and a reference list.
2. A reference list is one or more items joined by a separator. An item is a
   number N or a range "N-M" (also an en/em dash, "N to M" or "N through M").
   Numbers are ASCII digits only ([0-9], never Unicode digits such as the
   fullwidth ones) and at most 6 digits long; a longer digit run is not a
   number at all, so "claim 1234567" yields no reference (and a very long run
   cannot make int() raise). "claim 1.5" reads claim 1. A reversed range
   ("3-1") yields nothing. "claimed", "unclaim 1" and "reclaim 2" are not
   references: "claim" must start at a word boundary where only ASCII
   letters, digits and "_" count as word characters (JavaScript word-boundary semantics).
   A separator is a comma, "and", "or", or a comma followed by "and"/"or"
   ("claims 1, 2 and 3", "claim 3 or 4", "any of claims 1, 3").
   Wording before "claim(s)" ("any one of", "according to") is ignored.
3. The list stops at the first thing that is not a separator plus an item, so
   "claim 1, wherein 5 widgets" references only claim 1. (Known limit: a bare
   number right after a separator is read as a claim number.)
4. A claim only depends on LOWER-numbered claims. Referenced numbers that are
   not lower than the claim's own number (forward and self references) are
   ignored, and a range is clamped to the claims below the claim's own number.
5. A claim can contain several references; depends_on is the sorted union of
   them, without duplicates. independent = (depends_on is empty).

is_us_patent accepts only the compact form (US10000000B2, US20160266243A1;
ASCII only). Callers, and the extension, must normalise titles such as
"US 10,000,000 B2" (drop spaces and commas) before calling it.

Short free-text fields are capped at MAX_SHORT_CHARS, the abstract at
MAX_ABSTRACT_CHARS; the 600k total cap is only the last line of defence.

Pure module: no I/O, no DB or web imports.
"""

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_CLAIMS = 200
MAX_CLAIM_CHARS = 8000
MAX_DESCRIPTION_SECTIONS = 40
MAX_SECTION_CHARS = 20000
MAX_LIST_ITEMS = 300
MAX_BRIEF_JSON_CHARS = 600_000
MAX_SHORT_CHARS = 500
MAX_ABSTRACT_CHARS = 20_000
MAX_NUMBER_DIGITS = 6

DATE_KEYS = ("priority", "filing", "publication", "grant")

ClaimNumber = Annotated[int, Field(ge=1)]
Short = Annotated[str, Field(max_length=MAX_SHORT_CHARS)]
OptShort = Annotated[str | None, Field(max_length=MAX_SHORT_CHARS)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClassificationItem(_Strict):
    code: Short
    title: OptShort = None
    path: Annotated[list[Short], Field(max_length=MAX_LIST_ITEMS)] = []


class Claim(_Strict):
    number: ClaimNumber
    text: Annotated[str, Field(max_length=MAX_CLAIM_CHARS)]
    depends_on: Annotated[list[ClaimNumber], Field(max_length=MAX_CLAIMS)] = []
    independent: bool = True


class DescriptionSection(_Strict):
    heading: OptShort = None
    text: Annotated[str, Field(max_length=MAX_SECTION_CHARS)]


class CitationRow(_Strict):
    number: OptShort = None
    date: OptShort = None
    assignee: OptShort = None
    title: OptShort = None


class Citations(_Strict):
    patent: Annotated[list[CitationRow], Field(max_length=MAX_LIST_ITEMS)] = []
    cited_by: Annotated[list[CitationRow], Field(max_length=MAX_LIST_ITEMS)] = []


class FamilyRow(_Strict):
    number: OptShort = None
    country: OptShort = None
    title: OptShort = None
    priority_date: OptShort = None
    status: OptShort = None


class LegalEvent(_Strict):
    date: OptShort = None
    text: OptShort = None


class Brief(_Strict):
    patent_number: Annotated[str, Field(min_length=1, max_length=64)]
    title: OptShort = None
    abstract: Annotated[str | None, Field(max_length=MAX_ABSTRACT_CHARS)] = None
    status: OptShort = None
    expiry: OptShort = None
    dates: Annotated[dict[str, Short], Field(max_length=len(DATE_KEYS))] = {}
    inventors: Annotated[list[Short], Field(max_length=MAX_LIST_ITEMS)] = []
    assignees: Annotated[list[Short], Field(max_length=MAX_LIST_ITEMS)] = []
    classifications: Annotated[list[ClassificationItem], Field(max_length=MAX_LIST_ITEMS)] = []
    landscapes: Annotated[list[Short], Field(max_length=MAX_LIST_ITEMS)] = []
    claims: Annotated[list[Claim], Field(max_length=MAX_CLAIMS)] = []
    description: Annotated[list[DescriptionSection], Field(max_length=MAX_DESCRIPTION_SECTIONS)] = []
    citations: Citations = Citations()
    family: Annotated[list[FamilyRow], Field(max_length=MAX_LIST_ITEMS)] = []
    legal_events: Annotated[list[LegalEvent], Field(max_length=MAX_LIST_ITEMS)] = []
    completeness: Annotated[dict[Short, bool], Field(max_length=MAX_LIST_ITEMS)] = {}

    @field_validator("dates")
    @classmethod
    def _known_date_keys(cls, v: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(v) - set(DATE_KEYS))
        if unknown:
            raise ValueError(f"dates: unknown keys {unknown}; allowed {list(DATE_KEYS)}")
        return v

    @model_validator(mode="after")
    def _total_size(self) -> "Brief":
        size = len(self.model_dump_json())
        if size > MAX_BRIEF_JSON_CHARS:
            raise ValueError(f"brief: serialized size {size} exceeds the {MAX_BRIEF_JSON_CHARS} character cap")
        return self


# ---- claim dependency tree ------------------------------------------------

_NUM = rf"[0-9]{{1,{MAX_NUMBER_DIGITS}}}(?![0-9])"
_RANGE_SEP = r"(?:\s*[-\u2013\u2014]\s*|\s+(?:to|through)\s+)"
_ITEM = rf"{_NUM}(?:{_RANGE_SEP}{_NUM})?"
_LIST_SEP = r"(?:\s*,\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+)"
# ASCII-only word boundary like JavaScript's, not Python's Unicode-aware one.
_REF_RE = re.compile(rf"(?<![A-Za-z0-9_])claims?\s+({_ITEM}(?:{_LIST_SEP}{_ITEM})*)", re.IGNORECASE)
_ITEM_RE = re.compile(rf"([0-9]+)(?:{_RANGE_SEP}([0-9]+))?")


def parse_claim_refs(number: int, text: str) -> list[int]:
    """Return the sorted, de-duplicated lower-numbered claims that `text` references."""
    refs: set[int] = set()
    for ref in _REF_RE.finditer(text):
        for item in _ITEM_RE.finditer(ref.group(1)):
            start = int(item.group(1))
            end = int(item.group(2)) if item.group(2) else start
            # Only lower-numbered claims count; clamping also bounds huge ranges.
            for n in range(max(start, 1), min(end, number - 1) + 1):
                refs.add(n)
    return sorted(refs)


def build_claim_tree(claims: list[tuple[int, str]]) -> list[Claim]:
    """Build Claim objects (input order) with depends_on and independent set from the text."""
    out = []
    for number, text in claims:
        depends_on = parse_claim_refs(number, text)
        out.append(Claim(number=number, text=text, depends_on=depends_on, independent=not depends_on))
    return out


# ---- US number check ------------------------------------------------------

_US_RE = re.compile(r"US[0-9]{6,11}(?:[A-Z][0-9]?)?", re.IGNORECASE | re.ASCII)


def is_us_patent(patent_number: str) -> bool:
    """True for US granted or application numbers such as US10000000B2, US20160266243A1."""
    return _US_RE.fullmatch(patent_number.strip()) is not None
