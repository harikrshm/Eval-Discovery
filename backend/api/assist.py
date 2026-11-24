"""Patent Assist API: the key-authenticated endpoints the Chrome extension calls (chat and its signals).

Auth is a shared secret (env ASSIST_KEY, header X-Assist-Key), not a workspace token. ASSIST_KEY is
stripped of surrounding whitespace before use (a blank value counts as unset), so configure it without any. The flow, in order:
auth -> request validation -> rate limits -> assist agent lookup -> US check -> intent -> law excerpts ->
prompt assembly -> model call -> answer checks -> store the question (and flag a bad answer) -> respond.

The signal endpoints (page-views, ratings, reasks) are write-only: they answer 204 and never read data back.
Their flow is auth -> request validation -> rate limits -> assist agent lookup -> write.
"""

import hmac
import logging
import os
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from backend.api.limits import (
    rate_limit_assist_feedback,
    rate_limit_assist_heartbeat,
    rate_limit_assist_session,
    rate_limit_llm,
)
from backend.api.schemas import (
    AssistChatRequest,
    AssistChatResponse,
    AssistPageViewRequest,
    AssistRatingRequest,
    AssistReaskRequest,
    CitationCheckOut,
    ReferenceOut,
)
from backend.core.assist import ASSIST_MODEL
from backend.core.assist.answer import CITATION_NOTE, build_answer_request, finalize_answer  # noqa: F401  (CITATION_NOTE is re-exported)
from backend.core.assist.assemble import NOT_US_ANSWER
from backend.core.assist.brief import is_us_patent
from backend.core.assist.citations import CitationCheck
from backend.core.assist.intent import Intent, primary_section
from backend.core.assist.references import Reference, extract_references
from backend.core.config import CHAT_MAX_TOKENS
from backend.db.agents import current_prompt_version
from backend.db.assist import (
    MAX_ASSIST_RUNS,
    SessionConflict,
    add_question,
    count_runs,
    get_assist_agent,
    has_run_for_session,
    mark_reasked,
    save_flagged_run,
    set_rating,
    session_is_imported,
    upsert_page_view,
)
from backend.db.models import Agent, AssistQuestion
from backend.db.session import get_db
from backend.db.telemetry import classify_error, record_call
from backend.groq_client import groq_chat, request_retries

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assist", tags=["assist"])

ASSIST_KEY_ENV = "ASSIST_KEY"
ASSIST_KEY_HEADER = "X-Assist-Key"
NOT_CONFIGURED = "Assist is not configured."
ASSIST_UNAVAILABLE = "The assistant is not available right now."
CHAT_FAILED = "The assistant could not answer right now. Please try again."
FLAGGED_SOURCE = "assist"
QUESTION_NOT_FOUND = "Question not found."
SESSION_CONFLICT = "This session cannot be updated."
BRIEF_MISMATCH = "The brief does not match the question."


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


def _response(
    question_id: str, answer: str, intent: Intent, version: int, check, references: list[Reference] | None = None
) -> AssistChatResponse:
    return AssistChatResponse(
        answer=answer,
        question_id=question_id,
        intent=intent.value,
        assist_version=version,
        citation_check=CitationCheckOut(
            ok=check.ok,
            unverified_claims=check.unverified_claims,
            unverified_law=check.unverified_law,
            unverified_paragraphs=check.unverified_paragraphs,
            unverified_sections=check.unverified_sections,
        ),
        references=[
            ReferenceOut(start=r.start, end=r.end, kind=r.kind, target=r.target, label=r.label)
            for r in references or []
        ],
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
    # Imported sessions stay imported: refuse before spending a model call on a question that cannot be stored.
    if session_is_imported(db, body.session_id):
        raise HTTPException(status_code=409, detail=SESSION_CONFLICT)

    if not is_us_patent(brief.patent_number):
        row = add_question(
            db, workspace.id, body.session_id, brief.patent_number, question, Intent.other.value,
            None, NOT_US_ANSWER, True, "live", version.number,
        )
        return _response(row.id, NOT_US_ANSWER, Intent.other, version.number, CitationCheck(ok=True))

    history = [turn.model_dump() for turn in body.history]
    req = build_answer_request(brief, history, question, version.text)
    intent = req.intent
    started = time.monotonic()
    try:
        with request_retries(2):
            reply = groq_chat(req.system, req.messages, model=ASSIST_MODEL, max_tokens=CHAT_MAX_TOKENS)
        if not reply.strip():
            raise ValueError("empty model reply")
    except Exception as exc:
        logger.warning("Assist chat failed", exc_info=True)
        record_call(
            db, workspace_id=workspace.id, kind="chat", model=ASSIST_MODEL, started=started,
            outcome=classify_error(exc), prompt_version=version.number,
        )
        raise HTTPException(status_code=502, detail=CHAT_FAILED) from None
    record_call(
        db, workspace_id=workspace.id, kind="chat", model=ASSIST_MODEL, started=started,
        outcome="ok", prompt_version=version.number,
    )

    answer, check = finalize_answer(reply, intent, brief, req.provided_law_ids)

    try:
        row = add_question(
            db, workspace.id, body.session_id, brief.patent_number, question, intent.value,
            primary_section(intent), answer, check.ok, "live", version.number,
        )
    except SessionConflict:  # the session was imported while the model was answering
        raise HTTPException(status_code=409, detail=SESSION_CONFLICT) from None
    if not check.ok:
        # The question row is already stored; losing the flagged copy must not fail the user's answer.
        _save_flagged(
            db, agent, body.session_id,
            [*history, {"role": "user", "content": question}, {"role": "assistant", "content": answer}], brief,
        )
    return _response(row.id, answer, intent, version.number, check, extract_references(answer, brief))


@router.post("/page-views", status_code=204, dependencies=[Depends(require_assist_key)])
def page_view(body: AssistPageViewRequest, request: Request, db: Session = Depends(get_db)) -> Response:
    # The per-IP limiter goes first (inside) so a rejected caller cannot create session keys.
    rate_limit_assist_heartbeat(request, body.session_id)
    agent, workspace = _assist_agent_or_503(db)
    try:
        # bounce=None: the server decides from the merged values; the client's own flag is ignored.
        upsert_page_view(
            db, workspace.id, body.session_id, body.patent_number, body.dwell_ms, body.sections,
            None, current_prompt_version(agent).number, "live",
        )
    except SessionConflict:
        raise HTTPException(status_code=409, detail=SESSION_CONFLICT) from None
    return Response(status_code=204)


@router.post("/ratings", status_code=204, dependencies=[Depends(require_assist_key)])
def rating(body: AssistRatingRequest, request: Request, db: Session = Depends(get_db)) -> Response:
    rate_limit_assist_feedback(request)
    agent, workspace = _assist_agent_or_503(db)
    question = _question_or_404(db, workspace.id, body.question_id)
    _check_brief_matches(question, body)
    reason = (body.reason or "").strip() or None
    set_rating(db, question.id, body.rating, reason)
    if body.rating != "up":
        _flag_conversation(db, agent, question, body)
    return Response(status_code=204)


@router.post("/reasks", status_code=204, dependencies=[Depends(require_assist_key)])
def reask(body: AssistReaskRequest, request: Request, db: Session = Depends(get_db)) -> Response:
    rate_limit_assist_feedback(request)
    agent, workspace = _assist_agent_or_503(db)
    question = _question_or_404(db, workspace.id, body.question_id)
    _check_brief_matches(question, body)
    mark_reasked(db, question.id)
    _flag_conversation(db, agent, question, body)
    return Response(status_code=204)


def _assist_agent_or_503(db: Session):
    try:
        return get_assist_agent(db)
    except LookupError:
        raise HTTPException(status_code=503, detail=ASSIST_UNAVAILABLE) from None


def _question_or_404(db: Session, workspace_id: str, question_id: str) -> AssistQuestion:
    """The question, only when it belongs to the assist workspace (one fixed 404 for every miss)."""
    question = db.get(AssistQuestion, question_id)
    if question is None or question.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail=QUESTION_NOT_FOUND)
    return question


def _check_brief_matches(question: AssistQuestion, body) -> None:
    """422 (nothing written) when the brief is for a different patent than the stored question."""
    if body.brief.patent_number.strip().upper() != question.patent_number.strip().upper():
        raise HTTPException(status_code=422, detail=BRIEF_MISMATCH)


def _flag_conversation(db: Session, agent: Agent, question: AssistQuestion, body) -> None:
    """Save the conversation as the question's session run. The run belongs to the STORED session."""
    _save_flagged(db, agent, question.session_id, [turn.model_dump() for turn in body.conversation], body.brief)


def _save_flagged(db: Session, agent: Agent, session_id: str, messages: list[dict], brief) -> None:
    """Save a flagged conversation as the session's run (rating, re-ask and citation-failure paths alike).

    A new session is skipped once the workspace holds MAX_ASSIST_RUNS runs; the signal is still recorded.
    A failed save never fails the call (the signal or answer is already stored).
    """
    try:
        if count_runs(db, agent) >= MAX_ASSIST_RUNS and not has_run_for_session(db, agent, session_id):
            logger.warning("Assist flagged-run cap reached; conversation not saved")
            return
        save_flagged_run(db, agent, session_id, messages=messages, brief=brief, source=FLAGGED_SOURCE)
    except Exception:
        db.rollback()
        logger.warning("Saving the flagged assist run failed", exc_info=True)
