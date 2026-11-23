"""The one answer pipeline of Patent Assist, shared by the chat route and test replay.

`build_answer_request` turns (brief, history, question, system prompt) into the exact model input and
`finalize_answer` turns the raw model reply into the answer a user gets (truncated to leave room for the
R8 sentence and the citation note, the NOT_LEGAL_ADVICE net, the citation check and note). The chat route
and `replay_assist` both call these, so a replayed test produces exactly what a user would have seen.

`judge_brief_text` renders a brief for the judge (claims first, bounded) so a judge can grade an answer
against the page the assistant could see.

Pure: no I/O, no DB or web imports.
"""

from dataclasses import dataclass

from backend.core.assist.assemble import assemble
from backend.core.assist.brief import Brief
from backend.core.assist.citations import CitationCheck, check_citations
from backend.core.assist.intent import Intent, classify_intent
from backend.core.assist.lawpack import NOT_LEGAL_ADVICE, excerpts_for_question
from backend.core.config import MAX_ASSISTANT_MESSAGE_CHARS
from backend.core.prompts import _escape_angles

CITATION_NOTE = "Some citations in this answer could not be verified against the page."
# Replies come back as history turns later, so the FINAL answer must fit the message cap: leave room for
# the two suffixes (and their separators) that may be appended.
REPLY_BUDGET = MAX_ASSISTANT_MESSAGE_CHARS - len(NOT_LEGAL_ADVICE) - len(CITATION_NOTE) - 4

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
    answer = raw_reply[:REPLY_BUDGET]
    if intent is Intent.legal_opinion_request and NOT_LEGAL_ADVICE not in answer:
        answer = f"{answer}\n{NOT_LEGAL_ADVICE}"
    check = check_citations(answer, brief, provided_law_ids)
    if not check.ok:
        answer = f"{answer}\n\n{CITATION_NOTE}"
    return answer, check


def judge_brief_text(brief: Brief) -> str:
    """The brief as plain text for the judge: title, number, abstract, claims, then the description.

    Every "<" and ">" is escaped here (the judge prompt builder escapes again, which changes nothing), and
    the cap applies to the ESCAPED text: at most JUDGE_BRIEF_CHARS characters as the judge will receive
    them, cut so that no "&lt;" or "&gt;" is split. When cut, the text ends with a marker.
    """
    parts = []
    if brief.title:
        parts.append(f"Title: {brief.title}")
    parts.append(f"Patent: {brief.patent_number}")
    if brief.abstract:
        parts.append(f"Abstract: {brief.abstract}")
    parts.extend(f"Claim {claim.number}: {claim.text}" for claim in brief.claims)
    parts.extend(f"Description - {section.heading or 'untitled section'}:\n{section.text}" for section in brief.description)
    text = _escape_angles("\n\n".join(parts))
    if len(text) <= JUDGE_BRIEF_CHARS:
        return text
    head = text[: JUDGE_BRIEF_CHARS - len(_TRUNCATION_MARKER)]
    amp = head.rfind("&")
    if amp >= len(head) - 3 and ";" not in head[amp:]:  # the cut fell inside an entity: drop the partial one
        head = head[:amp]
    return head + _TRUNCATION_MARKER
