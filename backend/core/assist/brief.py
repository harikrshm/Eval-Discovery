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

DATE_KEYS = ("priority", "filing", "publication", "grant")

ClaimNumber = Annotated[int, Field(ge=1)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClassificationItem(_Strict):
    code: str
    title: str | None = None
    path: Annotated[list[str], Field(max_length=MAX_LIST_ITEMS)] = []


class Claim(_Strict):
    number: ClaimNumber
    text: Annotated[str, Field(max_length=MAX_CLAIM_CHARS)]
    depends_on: Annotated[list[ClaimNumber], Field(max_length=MAX_CLAIMS)] = []
    independent: bool = True


class DescriptionSection(_Strict):
    heading: str | None = None
    text: Annotated[str, Field(max_length=MAX_SECTION_CHARS)]


class CitationRow(_Strict):
    number: str | None = None
    date: str | None = None
    assignee: str | None = None
    title: str | None = None


class Citations(_Strict):
    patent: Annotated[list[CitationRow], Field(max_length=MAX_LIST_ITEMS)] = []
    cited_by: Annotated[list[CitationRow], Field(max_length=MAX_LIST_ITEMS)] = []


class FamilyRow(_Strict):
    number: str | None = None
    country: str | None = None
    title: str | None = None
    priority_date: str | None = None
    status: str | None = None


class LegalEvent(_Strict):
    date: str | None = None
    text: str | None = None


class Brief(_Strict):
    patent_number: Annotated[str, Field(min_length=1, max_length=64)]
    title: str | None = None
    abstract: str | None = None
    status: str | None = None
    expiry: str | None = None
    dates: Annotated[dict[str, str], Field(max_length=len(DATE_KEYS))] = {}
    inventors: Annotated[list[str], Field(max_length=MAX_LIST_ITEMS)] = []
    assignees: Annotated[list[str], Field(max_length=MAX_LIST_ITEMS)] = []
    classifications: Annotated[list[ClassificationItem], Field(max_length=MAX_LIST_ITEMS)] = []
    landscapes: Annotated[list[str], Field(max_length=MAX_LIST_ITEMS)] = []
    claims: Annotated[list[Claim], Field(max_length=MAX_CLAIMS)] = []
    description: Annotated[list[DescriptionSection], Field(max_length=MAX_DESCRIPTION_SECTIONS)] = []
    citations: Citations = Citations()
    family: Annotated[list[FamilyRow], Field(max_length=MAX_LIST_ITEMS)] = []
    legal_events: Annotated[list[LegalEvent], Field(max_length=MAX_LIST_ITEMS)] = []
    completeness: Annotated[dict[str, bool], Field(max_length=MAX_LIST_ITEMS)] = {}

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

_RANGE_SEP = r"(?:\s*[-–—]\s*|\s+(?:to|through)\s+)"
_ITEM = rf"\d+(?:{_RANGE_SEP}\d+)?"
_LIST_SEP = r"(?:\s*,\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+)"
_REF_RE = re.compile(rf"\bclaims?\s+({_ITEM}(?:{_LIST_SEP}{_ITEM})*)", re.IGNORECASE)
_ITEM_RE = re.compile(rf"(\d+)(?:{_RANGE_SEP}(\d+))?")


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

_US_RE = re.compile(r"^US\d{6,11}(?:[A-Z]\d?)?$", re.IGNORECASE)


def is_us_patent(patent_number: str) -> bool:
    """True for US granted or application numbers such as US10000000B2, US20160266243A1."""
    return bool(_US_RE.match(patent_number.strip()))
