"""The one answer pipeline of Patent Assist, shared by the chat route and test replay.

`build_answer_request` turns (brief, history, question, system prompt) into the exact model input and
`finalize_answer` turns the raw model reply into the answer a user gets (truncated to leave room for the
R8 sentence and the citation note, the NOT_LEGAL_ADVICE net, the citation check and note). The chat route
and `replay_assist` both call these, so a replayed test produces exactly what a user would have seen.

`judge_brief_text` renders a brief for the judge (claims first, bounded) so a judge can grade an answer
against the page the assistant could see.

Pure: no I/O, no DB or web imports.
"""

import re
from dataclasses import dataclass

from backend.core.assist.assemble import assemble
from backend.core.assist.brief import Brief
from backend.core.assist.citations import CitationCheck, check_citations, normalize_for_check
from backend.core.assist.intent import Intent, classify_intent
from backend.core.assist.lawpack import NOT_LEGAL_ADVICE, excerpts_for_question
from backend.core.config import MAX_ASSISTANT_MESSAGE_CHARS
from backend.core.prompts import _escape_angles

CITATION_NOTE = "Some citations in this answer could not be verified against the page."
# Replies come back as history turns later, so the FINAL answer must fit the message cap: leave room for
# the two suffixes (and their separators) that may be appended.
REPLY_BUDGET = MAX_ASSISTANT_MESSAGE_CHARS - len(NOT_LEGAL_ADVICE) - len(CITATION_NOTE) - 4

# The tags `assemble` puts around the data. The model sometimes cites them as if they were sources.
_TAG_WORDING = {"page_brief": "the page", "law_excerpts": "the law excerpts", "question": "the question"}
# <name>, </name>, &lt;name&gt; (optionally inside [ ] or fullwidth brackets), and the bare underscore names
# (page_brief, law_excerpts, also inside [ ] or fullwidth brackets). Bounded whitespace keeps it linear.
_TAG_RE = re.compile(
    r"[\u3010\[]?[ \t]{0,3}(?P<close>(?:<|&lt;)[ \t]{0,3}/?[ \t]{0,3})(?P<tag>page_brief|law_excerpts|question)"
    r"[ \t]{0,3}(?:>|&gt;)[ \t]{0,3}[\u3011\]]?"
    r"|[\u3010\[][ \t]{0,3}(?P<tag2>page_brief|law_excerpts|question)[ \t]{0,3}[\u3011\]]"
    r"|\b(?P<tag3>page_brief|law_excerpts)\b",
    re.IGNORECASE,
)


def _tag_replacement(match: re.Match) -> str:
    name = (match.group("tag") or match.group("tag2") or match.group("tag3")).lower()
    if match.group("close") is not None and "/" in match.group("close"):
        return ""  # a closing tag adds nothing: its opening tag already became plain wording
    return _TAG_WORDING[name]


def strip_internal_tags(text: str) -> str:
    """Replace the prompt's own tag names (page_brief, law_excerpts, question) with plain wording."""
    return _TAG_RE.sub(_tag_replacement, text)


JUDGE_BRIEF_CHARS = 9_000
_TRUNCATION_MARKER = "\n[...page text truncated]"


@dataclass(frozen=True)
class AnswerRequest:
    system: str
    messages: list[dict]
    intent: Intent
    provided_law_ids: list[str]


def build_answer_request(brief: Brief, history: list[dict], question: str, system_prompt: str) -> AnswerRequest:
    """The model input for one question: the intent, the fixed law excerpts it picks and the assembled prompt."""
    intent = classify_intent(question)
    # Law text comes only from the fixed pack, chosen by the question's keywords and the intent.
    excerpts = excerpts_for_question(intent, question)
    system, messages = assemble(brief, history, question, law_excerpts=excerpts, system_prompt=system_prompt)
    return AnswerRequest(system, messages, intent, [law_id for law_id, _ in excerpts])


def finalize_answer(
    raw_reply: str, intent: Intent, brief: Brief, provided_law_ids: list[str]
) -> tuple[str, CitationCheck]:
    """The answer a user gets for a raw model reply, and its citation check. Never over the message cap."""
    # Bound first (the strip only ever adds a few characters), strip the prompt's tag names, bound again.
    answer = strip_internal_tags(raw_reply[: REPLY_BUDGET + 1000])[:REPLY_BUDGET]
    if intent is Intent.legal_opinion_request and NOT_LEGAL_ADVICE not in normalize_for_check(answer):
        answer = f"{answer}\n{NOT_LEGAL_ADVICE}"
    check = check_citations(answer, brief, provided_law_ids)
    if not check.ok:
        answer = f"{answer}\n\n{CITATION_NOTE}"
    return answer, check


# The short facts (status, expiry, dates, assignees) and each list the judge may need get a reserved share
# of the cap, so a long claim set can never push them out. Together they take at most half of the cap.
_FACT_CAPS = {"bibliographic": 800, "classifications": 1200, "legal_events": 800, "family": 600, "citations": 1000}
_FACT_CUT = "[truncated]"
_ROWS_PER_CITATION_LIST = 10


def _cut_whole_entities(text: str, limit: int) -> str:
    """`text` cut to at most `limit` characters without splitting a "&lt;" or "&gt;" entity."""
    head = text[:limit]
    amp = head.rfind("&")
    if amp >= len(head) - 3 and ";" not in head[amp:]:
        head = head[:amp]
    return head


def _rows_section(header: str, rows: list[str], budget: int) -> str:
    """`header` plus whole rows that fit in `budget` (rows are already escaped); "[truncated]" when some do not."""
    if not rows:
        return ""
    full = "\n".join([header, *rows])
    if len(full) <= budget:
        return full
    kept: list[str] = []
    used = len(header) + 1 + len(_FACT_CUT)  # the marker is always there once rows are left out
    for row in rows:
        if used + 1 + len(row) > budget:
            break
        kept.append(row)
        used += 1 + len(row)
    text = "\n".join([header, *kept, _FACT_CUT])
    return text if len(text) <= budget else ""


def _fact_sections(brief: Brief, total_budget: int) -> list[str]:
    """Escaped fact blocks in priority order: status and dates, classifications, events, family, citations."""
    esc = _escape_angles
    out: list[str] = []
    remaining = total_budget

    def add(text: str) -> None:
        nonlocal remaining
        if text and len(text) + 2 <= remaining:
            out.append(text)
            remaining -= len(text) + 2

    lines = [
        f"Status: {brief.status}" if brief.status else "",
        f"Expiry: {brief.expiry}" if brief.expiry else "",
        "Dates: " + "; ".join(f"{k}={v}" for k, v in brief.dates.items()) if brief.dates else "",
        "Assignees: " + "; ".join(brief.assignees) if brief.assignees else "",
    ]
    lines = [esc(line) for line in lines if line]
    if lines:
        text = "\n".join(lines)
        if len(text) > min(_FACT_CAPS["bibliographic"], remaining):
            limit = min(_FACT_CAPS["bibliographic"], remaining) - len(_FACT_CUT) - 1
            text = _cut_whole_entities(text, max(limit, 0)) + "\n" + _FACT_CUT
        add(text)

    def section(name: str, header: str, rows: list[str]) -> None:
        add(_rows_section(esc(header), [esc(r) for r in rows], min(_FACT_CAPS[name], remaining - 2)))

    section("classifications", "Classifications (code | title):",
            [" | ".join(x for x in (c.code, c.title or "") if x) for c in brief.classifications])
    section("legal_events", "Legal events (date | event):", [f"{e.date or ''} | {e.text or ''}" for e in brief.legal_events])
    section("family", "Family (number | country | title | priority date | status):",
            [" | ".join(f or "" for f in (r.number, r.country, r.title, r.priority_date, r.status)) for r in brief.family])
    cite_parts: list[str] = []
    for label, rows in (("Patent citations", brief.citations.patent), ("Cited by", brief.citations.cited_by)):
        if rows:
            rendered = [" | ".join(f or "" for f in (c.number, c.date, c.assignee, c.title)) for c in rows]
            cite_parts.append((label, rendered))
    if cite_parts:
        share = max(min(_FACT_CAPS["citations"], remaining - 2) // len(cite_parts), 0)
        texts = [
            _rows_section(esc(f"{label}: {len(rows)}"), [esc(r) for r in rows[:_ROWS_PER_CITATION_LIST]], share)
            for label, rows in cite_parts
        ]
        # fewer rows shown than exist is a cut as well, even when every shown row fit
        texts = [
            t if len(rows) <= _ROWS_PER_CITATION_LIST or t.endswith(_FACT_CUT) or not t else t + "\n" + _FACT_CUT
            for t, (_, rows) in zip(texts, cite_parts)
        ]
        add("\n".join(t for t in texts if t))
    return out


def judge_brief_text(brief: Brief) -> str:
    """The brief as plain text for the judge: title, number, abstract, claims, then the description, then facts.

    The facts a grounded answer can rest on are kept in a reserved share of the cap (at most half), in
    this priority order: status, expiry, dates and assignees; classifications (code and title); legal
    events; family; citations (counts and the first rows). A fact section that does not fit ends with
    "[truncated]" and is cut on whole rows. The rest (claims first) gets the remaining space.

    Every "<" and ">" is escaped here (the judge prompt builder escapes again, which changes nothing), and
    the cap applies to the ESCAPED text: at most JUDGE_BRIEF_CHARS characters as the judge will receive
    them, cut so that no "&lt;" or "&gt;" is split. When the main text is cut, it ends with a marker.
    """
    facts = _fact_sections(brief, JUDGE_BRIEF_CHARS // 2)
    facts_text = "\n\n".join(facts)
    parts = []
    if brief.title:
        parts.append(f"Title: {brief.title}")
    parts.append(f"Patent: {brief.patent_number}")
    if brief.abstract:
        parts.append(f"Abstract: {brief.abstract}")
    parts.extend(f"Claim {claim.number}: {claim.text}" for claim in brief.claims)
    parts.extend(f"Description - {section.heading or 'untitled section'}:\n{section.text}" for section in brief.description)
    text = _escape_angles("\n\n".join(parts))
    budget = JUDGE_BRIEF_CHARS - (len(facts_text) + 2 if facts_text else 0)
    if len(text) > budget:
        text = _cut_whole_entities(text, budget - len(_TRUNCATION_MARKER)) + _TRUNCATION_MARKER
    return text + ("\n\n" + facts_text if facts_text else "")
