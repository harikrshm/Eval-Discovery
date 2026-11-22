"""Patent Assist chat API: the one key-authenticated endpoint the Chrome extension calls.

Auth is a shared secret (env ASSIST_KEY, header X-Assist-Key), not a workspace token. ASSIST_KEY is
stripped of surrounding whitespace before use (a blank value counts as unset), so configure it without any. The flow, in order:
auth -> request validation -> rate limits -> assist agent lookup -> US check -> intent -> law excerpts ->
prompt assembly -> model call -> answer checks -> store the question (and flag a bad answer) -> respond.
"""

import hmac
import logging
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from backend.api.agents import CHAT_MAX_TOKENS
from backend.api.limits import MAX_ASSISTANT_MESSAGE_CHARS, rate_limit_assist_session, rate_limit_llm
from backend.api.schemas import AssistChatRequest, AssistChatResponse, CitationCheckOut
from backend.core.assist import ASSIST_MODEL
from backend.core.assist.assemble import NOT_US_ANSWER, assemble
from backend.core.assist.brief import is_us_patent
from backend.core.assist.citations import CitationCheck, check_citations
from backend.core.assist.intent import Intent, classify_intent, primary_section
from backend.core.assist.lawpack import NOT_LEGAL_ADVICE, excerpts_for_question
from backend.db.agents import current_prompt_version
from backend.db.assist import add_question, get_assist_agent, save_flagged_run
from backend.db.session import get_db
from backend.groq_client import groq_chat, request_retries

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assist", tags=["assist"])

ASSIST_KEY_ENV = "ASSIST_KEY"
ASSIST_KEY_HEADER = "X-Assist-Key"
NOT_CONFIGURED = "Assist is not configured."
ASSIST_UNAVAILABLE = "The assistant is not available right now."
CHAT_FAILED = "The assistant could not answer right now. Please try again."
CITATION_NOTE = "Some citations in this answer could not be verified against the page."
FLAGGED_SOURCE = "assist"
_REPLY_BUDGET = MAX_ASSISTANT_MESSAGE_CHARS - len(NOT_LEGAL_ADVICE) - len(CITATION_NOTE) - 4


def require_assist_key(x_assist_key: str | None = Header(default=None)) -> None:
    """401 unless the X-Assist-Key header equals env ASSIST_KEY; 503 when ASSIST_KEY is unset or empty.

    The env var is read per call. The key is compared as bytes in constant time and never logged or returned.
    """
    expected = os.environ.get(ASSIST_KEY_ENV, "").strip().encode("utf-8")
    if not expected:
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED)
    provided = (x_assist_key or "").encode("utf-8")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid assist key.")


def _response(question_id: str, answer: str, intent: Intent, version: int, check) -> AssistChatResponse:
    return AssistChatResponse(
        answer=answer,
        question_id=question_id,
        intent=intent.value,
        assist_version=version,
        citation_check=CitationCheckOut(
            ok=check.ok, unverified_claims=check.unverified_claims, unverified_law=check.unverified_law
        ),
    )


@router.post("/chat", response_model=AssistChatResponse, dependencies=[Depends(require_assist_key)])
def chat(body: AssistChatRequest, request: Request, db: Session = Depends(get_db)) -> AssistChatResponse:
    # Rate limits come before any lookup or write, so a 429 never stores a question.
    # The per-IP/global limiter goes first so a rejected caller cannot create session keys.
    rate_limit_llm(request)
    rate_limit_assist_session(body.session_id)

    try:
        agent, workspace = get_assist_agent(db)
        version = current_prompt_version(agent)
    except LookupError:
        raise HTTPException(status_code=503, detail=ASSIST_UNAVAILABLE) from None
    brief = body.brief
    question = body.question

    if not is_us_patent(brief.patent_number):
        row = add_question(
            db, workspace.id, body.session_id, brief.patent_number, question, Intent.other.value,
            None, NOT_US_ANSWER, True, "live", version.number,
        )
        return _response(row.id, NOT_US_ANSWER, Intent.other, version.number, CitationCheck(ok=True))

    intent = classify_intent(question)
    # Law text comes only from the fixed pack, chosen by the question's keywords and the intent.
    excerpts = excerpts_for_question(intent, question)
    history = [turn.model_dump() for turn in body.history]
    system, messages = assemble(
        brief, history, question, law_excerpts=excerpts, system_prompt=version.text
    )
    try:
        with request_retries(2):
            reply = groq_chat(system, messages, model=ASSIST_MODEL, max_tokens=CHAT_MAX_TOKENS)
        if not reply.strip():
            raise ValueError("empty model reply")
    except Exception:
        logger.warning("Assist chat failed", exc_info=True)
        raise HTTPException(status_code=502, detail=CHAT_FAILED) from None

    # Replies come back as history turns later, so the FINAL answer must fit the message cap: leave
    # room for the two suffixes (and their separators) that may be appended below.
    answer = reply[:_REPLY_BUDGET]
    if intent is Intent.legal_opinion_request and NOT_LEGAL_ADVICE not in answer:
        answer = f"{answer}\n{NOT_LEGAL_ADVICE}"
    check = check_citations(answer, brief, [law_id for law_id, _ in excerpts])
    if not check.ok:
        answer = f"{answer}\n\n{CITATION_NOTE}"

    row = add_question(
        db, workspace.id, body.session_id, brief.patent_number, question, intent.value,
        primary_section(intent), answer, check.ok, "live", version.number,
    )
    if not check.ok:
        try:
            save_flagged_run(
                db, agent, body.session_id,
                messages=[*history, {"role": "user", "content": question}, {"role": "assistant", "content": answer}],
                brief=brief, source=FLAGGED_SOURCE,
            )
        except Exception:
            # The question row is already stored; losing the flagged copy must not fail the user's answer.
            db.rollback()
            logger.warning("Saving the flagged assist run failed", exc_info=True)
    return _response(row.id, answer, intent, version.number, check)
