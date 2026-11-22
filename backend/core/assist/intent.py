"""Keyword intent rules for Patent Assist. No model call, no I/O.

classify_intent reads the question and returns the FIRST intent whose patterns
match, in the order of INTENT_RULES (the priority). Matching is
case-insensitive. The keyword lists are plain module constants so the builder
can tune them; the table-driven tests in tests/test_assist_assemble.py pin the
behaviour.

Priority, highest first: legal_opinion_request, claim_scope, claim_explain,
status_family, citations_prior_art, classification, law_concept,
navigation_help, then other. A question that asks for a validity or
infringement conclusion therefore always lands in legal_opinion_request, even
when it also mentions claims.
"""

import re
from enum import Enum

# The seven sections the browser extension tracks (and reports in the brief's
# completeness map).
SECTION_NAMES = (
    "claims",
    "description",
    "classifications",
    "citations",
    "family",
    "legal_events",
    "landscapes",
)


class Intent(str, Enum):
    claim_explain = "claim_explain"
    claim_scope = "claim_scope"
    status_family = "status_family"
    citations_prior_art = "citations_prior_art"
    classification = "classification"
    law_concept = "law_concept"
    navigation_help = "navigation_help"
    legal_opinion_request = "legal_opinion_request"
    other = "other"


# ---- keyword lists (regex fragments, matched case-insensitively) -------------

LEGAL_OPINION_PATTERNS = (
    r"\bis\b[^?.!]*\bvalid\b",  # "is it valid", "is claim 1 valid"
    r"\binvalid",
    r"\binfring",
    r"\bfreedom to operate\b",
    r"\bfto\b",
    r"\bcan i (?:use|make|sell|copy|build)\b",
    r"\bwould (?:this|it|that) hold up\b",
    r"\bshould i (?:file|patent|apply)\b",
    r"\bpatentable\b",
    r"\benforceable\b",
    r"\blegal advice\b",
)

CLAIM_SCOPE_PATTERNS = (
    r"\bscope\b",
    r"\bcovers?\b",
    r"\bcovered\b",
    r"\bcoverage\b",
    r"\bprotects?\b",
    r"\bprotection\b",
    r"\bbroadest\b",
    r"\bnarrowest\b",
    r"\bdesign(?:ing)? around\b",
    r"\blimitations?\b",
)

CLAIM_EXPLAIN_PATTERNS = (
    r"\bclaims?\b",
    r"\bindependent\b",
    r"\bdependent\b",
    r"\bplain (?:english|language|terms)\b",
    r"\bsummar",
    r"\bwhat is this patent about\b",
    r"\bwhat does this patent (?:do|say|claim)\b",
)

STATUS_FAMILY_PATTERNS = (
    r"\bstatus\b",
    r"\bexpir",
    r"\bmaintenance fees?\b",
    r"\blegal events?\b",
    r"\bfamily\b",
    r"\bcontinuations?\b",
    r"\bdivisionals?\b",
    r"\bpriority date\b",
    r"\bfiling date\b",
    r"\bgrant date\b",
    r"\blapse",
    r"\babandon",
    r"\bin force\b",
    r"\bstill (?:active|alive)\b",
)

CITATIONS_PRIOR_ART_PATTERNS = (
    r"\bcit(?:e|es|ed|ing|ation|ations)\b",
    r"\bprior art\b",
    r"\breferences?\b",
    r"\bexaminer\b",
)

CLASSIFICATION_PATTERNS = (
    r"\bclassif",
    r"\bcpc\b",
    r"\bipc\b",
    r"\buspc\b",
    r"\bclass code\b",
    r"\bcategor",
    r"\b[a-h][0-9]{2}[a-z]\b",  # a class symbol such as G06F or A61K
)

LAW_CONCEPT_PATTERNS = (
    r"\b35\s*u\.?\s*s\.?\s*c\b",
    r"\busc\b",
    "§",
    r"\bsection\s+(?:101|102|103|112|271)\b",
    r"\bmpep\b",
    r"\bobvious",
    r"\bnovelty\b",
    r"\banticipat",
    r"\benablement\b",
    r"\bwritten description\b",
    r"\bindefinite",
    r"\bdefiniteness\b",
    r"\beligib",
    r"\babstract idea\b",
    r"\bdoctrine of equivalents\b",
    r"\bprosecution history\b",
    r"\bestoppel\b",
    r"\bmeans[- ]plus[- ]function\b",
    r"\bcomprising\b",
)

NAVIGATION_HELP_PATTERNS = (
    r"\bwhere (?:do|can) i\b",
    r"\bhow (?:do|can) i (?:find|use|get|see|open)\b",
    r"\bwhat does this button\b",
    r"\bwhich button\b",
    r"\bhow to navigate\b",
)

# Priority order: the first intent with a matching pattern wins.
INTENT_RULES: tuple[tuple[Intent, tuple[re.Pattern, ...]], ...] = tuple(
    (intent, tuple(re.compile(p, re.IGNORECASE) for p in patterns))
    for intent, patterns in (
        (Intent.legal_opinion_request, LEGAL_OPINION_PATTERNS),
        (Intent.claim_scope, CLAIM_SCOPE_PATTERNS),
        (Intent.claim_explain, CLAIM_EXPLAIN_PATTERNS),
        (Intent.status_family, STATUS_FAMILY_PATTERNS),
        (Intent.citations_prior_art, CITATIONS_PRIOR_ART_PATTERNS),
        (Intent.classification, CLASSIFICATION_PATTERNS),
        (Intent.law_concept, LAW_CONCEPT_PATTERNS),
        (Intent.navigation_help, NAVIGATION_HELP_PATTERNS),
    )
)


def classify_intent(question: str) -> Intent:
    """Return the first matching intent in priority order, else Intent.other."""
    for intent, patterns in INTENT_RULES:
        if any(p.search(question) for p in patterns):
            return intent
    return Intent.other


# The main Google Patents section a question is about (None when no single
# tracked section is the main one).
_PRIMARY_SECTION = {
    Intent.claim_explain: "claims",
    Intent.claim_scope: "claims",
    Intent.classification: "classifications",
    Intent.status_family: "legal_events",
    Intent.citations_prior_art: "citations",
}


def primary_section(intent: Intent) -> str | None:
    return _PRIMARY_SECTION.get(intent)
