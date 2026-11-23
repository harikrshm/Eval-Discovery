"""Assist console read APIs (builder side): adoption metrics, version comparison and flagged runs.

Workspace-token auth (`X-Workspace-Token`), never the assist key. Everything is scoped to the
workspace's own assist agent. Responses carry counts, rates, ids and labels only: no question or
answer text, no brief, no messages, no keys.
"""

import logging
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_workspace
from backend.api.limits import rate_limit_assist_insights
from backend.api.schemas import (
    AssistConsoleMetricsOut,
    AssistConsoleStatusOut,
    AssistInsightsOut,
    FlaggedQuestionOut,
    FlaggedRunOut,
)
from backend.core.assist.adoption import IMPORTED_SOURCE, PV, Q, compute_adoption, compute_version_comparison
from backend.core.assist.insights import (
    build_insights_messages,
    check_grounded,
    flatten_metrics,
    hotspots,
    imported_total,
    metrics_for_prompt,
    select_texts,
)
from backend.core.assist.intent import SECTION_NAMES, Intent
from backend.db.assist import EVIDENCE_SOURCES
from backend.db.models import Agent, AssistPageView, AssistQuestion, PromptVersion, Run, Workspace
from backend.db.session import get_db
from backend.groq_client import MODEL_JUDGE, groq_chat, request_retries

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assist/console", tags=["assist-console"])

NO_ASSIST_AGENT = "No assistant agent in this workspace."
DEMO_LABEL = "demo dataset"
NO_INSIGHT_DATA = "Not enough data yet."
INSIGHTS_FAILED = "The insights could not be generated right now. Please try again."
INSIGHTS_MAX_TOKENS = 700
INSIGHTS_TEMPERATURE = 0.2
MAX_BRIEF_CHARS = 2500
SAMPLE_SCAN_ROWS = 40  # newest rows scanned per hotspot to find up to 3 distinct texts
DEFAULT_FLAGGED_LIMIT = 50
MAX_FLAGGED_LIMIT = 200
_INTENTS = {i.value for i in Intent}


def _assist_agent(db: Session, workspace: Workspace) -> Agent:
    agent = db.scalar(
        select(Agent)
        .where(Agent.workspace_id == workspace.id, Agent.kind == "assist")
        .order_by(Agent.created_at, Agent.id)
        .limit(1)
    )
    if agent is None:
        raise HTTPException(status_code=404, detail=NO_ASSIST_AGENT)
    return agent


def _run_count(db: Session, agent: Agent, *sources: str) -> int:
    query = select(func.count()).select_from(Run).where(Run.agent_id == agent.id)
    if sources:
        query = query.where(Run.source.in_(sources))
    return db.scalar(query)


@router.get("/status", response_model=AssistConsoleStatusOut)
def console_status(
    workspace: Workspace = Depends(get_current_workspace), db: Session = Depends(get_db)
) -> AssistConsoleStatusOut:
    agent = _assist_agent(db, workspace)
    number = db.scalar(
        select(PromptVersion.number).where(PromptVersion.id == agent.current_prompt_version_id)
    )
    return AssistConsoleStatusOut(
        agent_id=agent.id,
        agent_name=agent.name,
        current_version_number=number,
        run_count=_run_count(db, agent),
        flagged_run_count=_run_count(db, agent, *EVIDENCE_SOURCES),
        imported_run_count=_run_count(db, agent, IMPORTED_SOURCE),
    )


def _load_questions(db: Session, workspace_id: str) -> list:
    """Label columns only (never text, answer or rating_reason), oldest first."""
    return list(
        db.execute(
            select(
                AssistQuestion.id,
                AssistQuestion.session_id,
                AssistQuestion.patent_number,
                AssistQuestion.intent,
                AssistQuestion.section_focus,
                AssistQuestion.rating,
                AssistQuestion.reasked,
                AssistQuestion.citation_ok,
                AssistQuestion.source,
                AssistQuestion.assist_version,
            )
            .where(AssistQuestion.workspace_id == workspace_id)
            .order_by(AssistQuestion.created_at, AssistQuestion.id)
        )
    )


def _compute_adoption(db: Session, workspace: Workspace) -> tuple[dict, list]:
    views = [
        PV(r.session_id, r.sections or {}, r.dwell_ms or 0, bool(r.bounce), r.source, r.assist_version)
        for r in db.execute(
            select(
                AssistPageView.session_id,
                AssistPageView.sections,
                AssistPageView.dwell_ms,
                AssistPageView.bounce,
                AssistPageView.source,
                AssistPageView.assist_version,
            ).where(AssistPageView.workspace_id == workspace.id)
        )
    ]
    questions = [
        Q(r.session_id, r.intent, r.section_focus, r.rating, bool(r.reasked), bool(r.citation_ok),
          r.source, r.assist_version)
        for r in _load_questions(db, workspace.id)
    ]
    return compute_adoption(views, questions), questions


@router.get("/metrics", response_model=AssistConsoleMetricsOut)
def console_metrics(
    workspace: Workspace = Depends(get_current_workspace), db: Session = Depends(get_db)
) -> AssistConsoleMetricsOut:
    _assist_agent(db, workspace)
    adoption, questions = _compute_adoption(db, workspace)
    return AssistConsoleMetricsOut(
        adoption=adoption,
        version_comparison=compute_version_comparison(questions),
        data_source_label=DEMO_LABEL if imported_total(adoption) else None,
        sources=adoption["sources"],
    )


def _hotspot_samples(db: Session, workspace: Workspace, adoption: dict) -> dict[str, list[str]]:
    """Up to 3 distinct, cleaned question texts per hotspot, this workspace only, newest first."""
    samples: dict[str, list[str]] = {}
    for kind, name in hotspots(adoption):
        column = AssistQuestion.section_focus if kind == "section" else AssistQuestion.intent
        texts = db.scalars(
            select(AssistQuestion.text)
            .where(AssistQuestion.workspace_id == workspace.id, column == name)
            .order_by(AssistQuestion.created_at.desc(), AssistQuestion.id.desc())
            .limit(SAMPLE_SCAN_ROWS)
        )
        picked = select_texts(texts)
        if picked:
            samples[f"{kind}:{name}"] = picked
    return samples


@router.post("/insights", response_model=AssistInsightsOut)
def console_insights(
    request: Request,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> AssistInsightsOut:
    """A model-written brief grounded in the computed aggregates (plus a few anonymised questions).

    `grounded` comes from a BEST-EFFORT check that every figure in the brief can be explained by the
    aggregates: a safety net against invented numbers, not a proof of truth (it does not check that a
    figure is attached to the right metric, trends, vague quantities or causes). Question samples are
    redacted by regex only, which cannot catch obfuscated emails, unspaced IBANs, names or addresses.
    Rate limit: its own small per-IP budget plus the process-wide LLM budget, never the chat limiter.
    """
    _assist_agent(db, workspace)
    adoption, _ = _compute_adoption(db, workspace)
    if adoption["totals"]["page_views"] == 0 and adoption["totals"]["questions"] == 0:
        raise HTTPException(status_code=409, detail=NO_INSIGHT_DATA)
    rate_limit_assist_insights(request)
    samples = _hotspot_samples(db, workspace, adoption)
    system, messages = build_insights_messages(adoption, samples)
    try:
        with request_retries(2):
            reply = groq_chat(
                system, messages, model=MODEL_JUDGE, max_tokens=INSIGHTS_MAX_TOKENS,
                temperature=INSIGHTS_TEMPERATURE,
            )
    except Exception:
        logger.warning("Assist insights failed", exc_info=True)
        raise HTTPException(status_code=502, detail=INSIGHTS_FAILED) from None
    brief = reply.strip()[:MAX_BRIEF_CHARS] if isinstance(reply, str) else ""
    if not brief:
        raise HTTPException(status_code=502, detail=INSIGHTS_FAILED)
    grounded, warnings = check_grounded(brief, adoption, samples)
    return AssistInsightsOut(
        brief=brief,
        counts_used=flatten_metrics(metrics_for_prompt(adoption)),
        data_source_label=DEMO_LABEL if imported_total(adoption) else None,
        grounded=grounded,
        warnings=warnings,
    )


def _reasons(rows: list) -> list[str]:
    found = set()
    for q in rows:
        if q.rating == "down":
            found.add("rated_down")
        if q.rating == "no_answer":
            found.add("no_answer")
        if q.reasked:
            found.add("reasked")
        if not q.citation_ok:
            found.add("citation_unverified")
    return [r for r in ("rated_down", "no_answer", "reasked", "citation_unverified") if r in found]


@router.get("/flagged", response_model=list[FlaggedRunOut])
def console_flagged(
    intent: str | None = None,
    section: str | None = None,
    limit: Annotated[int, Query(ge=1)] = DEFAULT_FLAGGED_LIMIT,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> list[FlaggedRunOut]:
    """Flagged runs (source assist or imported), newest first; `limit` is capped at 200.

    A run matches the filters when any question of its session has that intent / section focus.
    """
    if intent is not None and intent not in _INTENTS:
        raise HTTPException(status_code=422, detail="Unknown intent.")
    if section is not None and section not in SECTION_NAMES:
        raise HTTPException(status_code=422, detail="Unknown section.")
    limit = min(limit, MAX_FLAGGED_LIMIT)
    agent = _assist_agent(db, workspace)

    by_session = defaultdict(list)
    for row in _load_questions(db, workspace.id):
        by_session[row.session_id].append(row)

    # Columns only: the run's messages and brief snapshot are never loaded.
    runs = db.execute(
        select(Run.id, Run.position, Run.source, Run.session_id, Run.created_at)
        .where(Run.agent_id == agent.id, Run.source.in_(EVIDENCE_SOURCES))
        .order_by(Run.created_at.desc(), Run.position.desc())
    )
    out = []
    for run in runs:
        rows = by_session.get(run.session_id, [])
        if intent is not None and not any(q.intent == intent for q in rows):
            continue
        if section is not None and not any(q.section_focus == section for q in rows):
            continue
        out.append(
            FlaggedRunOut(
                run_id=run.id,
                position=run.position,
                source=run.source,
                session_id=run.session_id,
                patent_number=rows[-1].patent_number if rows else None,
                intents=sorted({q.intent for q in rows}),
                sections=sorted({q.section_focus for q in rows if q.section_focus}),
                reasons=_reasons(rows),
                created_at=run.created_at,
                questions=[
                    FlaggedQuestionOut(
                        question_id=q.id,
                        intent=q.intent,
                        section_focus=q.section_focus,
                        rating=q.rating,
                        reasked=bool(q.reasked),
                        citation_ok=bool(q.citation_ok),
                        assist_version=q.assist_version,
                    )
                    for q in rows
                ],
            )
        )
        if len(out) >= limit:
            break
    return out
