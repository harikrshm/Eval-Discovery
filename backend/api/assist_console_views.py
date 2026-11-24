"""Assist console v2 endpoints (builder side): conversation judging and, in later tasks, telemetry and trends.

Workspace-token auth (`X-Workspace-Token`) and the workspace's own assist agent, exactly like the other
console routes. Responses carry counts, rates, ids and labels only: never question or answer text.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.assist_console import _assist_agent, _load_timed_rows, console_filters
from backend.api.deps import get_current_workspace
from backend.api.schemas import AssistConsoleTrendsOut, JudgeEvaluateOut
from backend.core.assist import console_filters as cf
from backend.core.assist.adoption import IMPORTED_SOURCE
from backend.db.models import PromptVersion, Workspace, utcnow
from backend.db.session import get_db
from backend.jobs.conversation_judge import evaluate_now

router = APIRouter(prefix="/assist/console", tags=["assist-console"])


@router.post("/judge/evaluate", response_model=JudgeEvaluateOut)
def judge_evaluate(
    workspace: Workspace = Depends(get_current_workspace), db: Session = Depends(get_db)
) -> JudgeEvaluateOut:
    """Queue one judgement for the oldest waiting conversation now, without waiting for idle time.

    The hourly cap and the one-judge-job-at-a-time rule still apply; `waiting` counts the rest.
    """
    _assist_agent(db, workspace)
    return JudgeEvaluateOut(**evaluate_now(db, workspace.id))


HOUR, DAY = timedelta(hours=1), timedelta(days=1)


def _day_start(at: datetime) -> datetime:
    return at.replace(hour=0, minute=0, second=0, microsecond=0)


def _counts(views: list[tuple], questions: list[tuple], filters: cf.Filters) -> dict:
    """Page views, questions, re-asked and thumbs-down questions among the rows that pass `filters`."""
    kept_views = [1 for at, v in views if cf.keep(at, v.source, v.assist_version, filters)]
    kept = [q for at, q in questions if cf.keep(at, q.source, q.assist_version, filters)]
    return {
        "page_views": len(kept_views),
        "questions": len(kept),
        "reasked": sum(1 for q in kept if q.reasked),
        "rated_down": sum(1 for q in kept if q.rating == "down"),
    }


def _points(views: list[tuple], questions: list[tuple], filters: cf.Filters) -> list[dict]:
    """Zero-filled buckets across the range.

    A bounded range is split into whole buckets counted back from the request time (24 hourly buckets
    for 24h, 7 daily for 7d, 30 for 30d). `all` runs in calendar days from the oldest row to now; with no
    rows at all it has no points.
    """
    step = HOUR if cf.bucket_for(filters) == "hour" else DAY
    kept_views = [at for at, v in views if cf.keep(at, v.source, v.assist_version, filters)]
    kept_questions = [(at, q) for at, q in questions if cf.keep(at, q.source, q.assist_version, filters)]
    stamps = kept_views + [at for at, _ in kept_questions]
    if filters.since is not None and filters.until is not None:
        start, size = filters.since, int((filters.until - filters.since) / step)
    elif stamps:
        start = _day_start(min(stamps))
        size = (_day_start(max(utcnow(), *stamps)) - start).days + 1
    else:
        return []
    buckets = [
        {"t": (start + i * step).isoformat(), "page_views": 0, "questions": 0, "reasked": 0, "rated_down": 0}
        for i in range(size)
    ]

    def bucket(at: datetime) -> dict | None:
        index = int((at - start) / step)
        return buckets[index] if 0 <= index < size else None

    for at in kept_views:
        if (point := bucket(at)) is not None:
            point["page_views"] += 1
    for at, q in kept_questions:
        if (point := bucket(at)) is not None:
            point["questions"] += 1
            point["reasked"] += 1 if q.reasked else 0
            point["rated_down"] += 1 if q.rating == "down" else 0
    return buckets


def _version_markers(db: Session, agent_id: str, questions: list[tuple], filters: cf.Filters) -> list[dict]:
    """Live markers from the agent's prompt versions, demo markers from the first imported question per version.

    Demo markers are derived on the fly from the generated data and never stored.
    """
    found: list[tuple[datetime, int, str]] = []
    if filters.source != "demo":
        for number, created_at in db.execute(
            select(PromptVersion.number, PromptVersion.created_at).where(PromptVersion.agent_id == agent_id)
        ):
            found.append((created_at, number, "live"))
    if filters.source != "live":
        first: dict[int, datetime] = {}
        for at, q in questions:
            if q.source == IMPORTED_SOURCE and q.assist_version is not None:
                if q.assist_version not in first or at < first[q.assist_version]:
                    first[q.assist_version] = at
        found += [(at, version, "demo") for version, at in first.items()]
    return [
        {"at": at.isoformat(), "version": version, "source": source,
         "label": f"v{version}" if source == "live" else f"v{version} (demo)"}
        for at, version, source in sorted(found)
        if cf.in_range(at, filters)
    ]


@router.get("/trends", response_model=AssistConsoleTrendsOut)
def console_trends(
    filters: cf.Filters = Depends(console_filters),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> AssistConsoleTrendsOut:
    """Counts per hour/day bucket, totals, the previous period's totals and assistant version markers.

    Counts only: no question or answer text. `previous` is null for the range `all`.
    """
    agent = _assist_agent(db, workspace)
    views, questions = _load_timed_rows(db, workspace)
    previous = cf.previous_period(filters)
    return AssistConsoleTrendsOut(
        bucket=cf.bucket_for(filters),
        totals=_counts(views, questions, filters),
        previous=_counts(views, questions, previous) if previous is not None else None,
        points=_points(views, questions, filters),
        version_markers=_version_markers(db, agent.id, questions, filters),
    )
