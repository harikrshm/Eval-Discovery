"""Assist console v2 endpoints (builder side): conversation judging, trends, quality, operations, overview and pipeline.

Workspace-token auth (`X-Workspace-Token`) and the workspace's own assist agent, exactly like the other
console routes. Responses carry counts, rates, ids and labels only: never question or answer text.
"""

from dataclasses import asdict
from datetime import datetime
from typing import NamedTuple

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.api.assist_console import _assist_agent, _load_timed_rows, console_filters
from backend.api.deps import get_current_workspace
from backend.api.schemas import (
    AssistConsoleTrendsOut,
    JudgeEvaluateOut,
    OperationsOut,
    OverviewOut,
    PipelineOut,
    QualityOut,
)
from backend.core.assist import alerts as alert_rules
from backend.core.assist import console_filters as cf
from backend.core.assist.adoption import IMPORTED_SOURCE
from backend.core.assist.convo_judge import ANSWERED, LEGAL, REASONS
from backend.db.assist import EVIDENCE_SOURCES
from backend.db.models import (
    Agent,
    AssistCall,
    AssistJudgement,
    Criterion,
    EvalTest,
    Job,
    PromptVersion,
    Run,
    Workspace,
)
from backend.db.session import get_db
from backend.db.telemetry import CALL_KINDS, OUTCOMES
from backend.jobs.conversation_judge import evaluate_now, judge_queue_stats, unjudged_conversations

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


def _counts(views: list[tuple], questions: list[tuple], filters: cf.Filters) -> dict:
    """Page views, questions, re-asked and thumbs-down questions among the rows that pass `filters`."""
    kept = [q for _, q in cf.kept(questions, filters)]
    return {
        "page_views": len(cf.kept(views, filters)),
        "questions": len(kept),
        "reasked": sum(1 for q in kept if q.reasked),
        "rated_down": sum(1 for q in kept if q.rating == "down"),
    }


def _points(views: list[tuple], questions: list[tuple], filters: cf.Filters) -> list[dict]:
    """Zero-filled buckets across the range (layout rules in `cf.bucket_layout`)."""
    kept_views, kept_questions = cf.kept(views, filters), cf.kept(questions, filters)
    layout = cf.bucket_layout(filters, [at for at, _ in kept_views + kept_questions])
    if layout is None:
        return []
    in_bucket = layout.group(kept_questions)
    page_views = layout.group(kept_views)
    return [
        {
            "t": t, "page_views": len(page_views[i]), "questions": len(in_bucket[i]),
            "reasked": sum(1 for q in in_bucket[i] if q.reasked),
            "rated_down": sum(1 for q in in_bucket[i] if q.rating == "down"),
        }
        for i, t in enumerate(layout.labels())
    ]


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


# ---- loading -----------------------------------------------------------------------


class Call(NamedTuple):
    """One model call: label and number columns only."""

    kind: str
    latency_ms: int
    outcome: str
    total_tokens: int | None
    source: str
    prompt_version: int | None


class Judgement(NamedTuple):
    """One verdict: fixed labels, flags and the grade only."""

    answered: str
    concise: bool
    citations_sensible: bool
    legal: str
    grade: int
    reason: str
    source: str
    prompt_version: int


def _load_calls(db: Session, workspace: Workspace) -> list[tuple[datetime, Call]]:
    rows = db.execute(
        select(
            AssistCall.created_at, AssistCall.kind, AssistCall.latency_ms, AssistCall.outcome,
            AssistCall.total_tokens, AssistCall.source, AssistCall.prompt_version,
        ).where(AssistCall.workspace_id == workspace.id)
    )
    return [
        (r.created_at, Call(r.kind, r.latency_ms, r.outcome, r.total_tokens, r.source, r.prompt_version))
        for r in rows
    ]


def _load_judgements(db: Session, workspace: Workspace) -> list[tuple[datetime, Judgement]]:
    rows = db.execute(
        select(
            AssistJudgement.created_at, AssistJudgement.answered, AssistJudgement.concise,
            AssistJudgement.citations_sensible, AssistJudgement.legal, AssistJudgement.grade,
            AssistJudgement.reason, AssistJudgement.source, AssistJudgement.prompt_version,
        ).where(AssistJudgement.workspace_id == workspace.id)
    )
    return [
        (
            r.created_at,
            Judgement(r.answered, bool(r.concise), bool(r.citations_sensible), r.legal, r.grade, r.reason,
                      r.source, r.prompt_version),
        )
        for r in rows
    ]


def _rate(part: int, whole: int) -> float | None:
    """A share rounded to 4 decimals; None (never 0) when nothing could have happened."""
    return None if whole == 0 else round(part / whole, 4)


def _mean_grade(judgements: list[Judgement]) -> float | None:
    return round(sum(j.grade for j in judgements) / len(judgements), 2) if judgements else None


# ---- quality -----------------------------------------------------------------------


def grade_trend(judgements: list[tuple[datetime, Judgement]], layout: cf.Layout | None) -> list[dict]:
    """Average grade and judgement count per bucket; an empty bucket has `avg_grade` null and `n` 0."""
    if layout is None:
        return []
    return [
        {"t": t, "avg_grade": _mean_grade(group), "n": len(group)}
        for t, group in zip(layout.labels(), layout.group(judgements), strict=True)
    ]


def quality_summary(
    judgements: list[tuple[datetime, Judgement]], questions: list[tuple], unjudged: int, layout: cf.Layout | None
) -> dict:
    """The judge's verdicts in aggregate over already-filtered rows.

    Only fixed labels are counted: a stored `reason` outside `convo_judge.REASONS` is reported as `other`
    and an unknown `answered` or `legal` value is counted nowhere, so nothing a model wrote is ever echoed.
    """
    rows = [j for _, j in judgements]
    ratings = [q.rating for _, q in questions]
    return {
        "judged": len(rows),
        "unjudged": unjudged,
        "grade_trend": grade_trend(judgements, layout),
        "answered": {label: sum(1 for j in rows if j.answered == label) for label in ANSWERED},
        "concise_share": _rate(sum(1 for j in rows if j.concise), len(rows)),
        "citations_share": _rate(sum(1 for j in rows if j.citations_sensible), len(rows)),
        "legal": {label: sum(1 for j in rows if j.legal == label) for label in LEGAL},
        "reasons": {
            **{label: sum(1 for j in rows if j.reason == label) for label in REASONS},
            "other": sum(1 for j in rows if j.reason not in REASONS),
        },
        "ratings": {
            "up": ratings.count("up"),
            "down": ratings.count("down"),
            "no_answer": ratings.count("no_answer"),
            "none": sum(1 for r in ratings if r not in ("up", "down", "no_answer")),
        },
    }


def _unjudged_count(db: Session, workspace: Workspace, filters: cf.Filters) -> int:
    """Live conversations without a judgement for their latest version that the filters would show.

    The judge never runs on demo conversations, so a `demo` source has none.
    """
    if filters.source == "demo":
        return 0
    return sum(
        1
        for _, last_at, version in unjudged_conversations(db, workspace.id)
        if cf.in_range(last_at, filters) and (filters.version is None or version == filters.version)
    )


@router.get("/quality", response_model=QualityOut)
def console_quality(
    filters: cf.Filters = Depends(console_filters),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> QualityOut:
    """What the automatic judge found: grades over time, answered/legal/reason counts and thumbs ratings.

    Also the prompt-version markers (the same list `/trends` and `/overview` return) so the grade chart can
    mark when a version went live. Aggregates and fixed labels only. Shares are null when nothing was judged.
    """
    agent = _assist_agent(db, workspace)
    _, questions = _load_timed_rows(db, workspace)
    judgements = cf.kept(_load_judgements(db, workspace), filters, "prompt_version")
    layout = cf.bucket_layout(filters, [at for at, _ in judgements])
    kept_questions = cf.kept(questions, filters)
    summary = quality_summary(judgements, kept_questions, _unjudged_count(db, workspace, filters), layout)
    markers = _version_markers(db, agent.id, questions, filters)
    # true when a demo dataset row feeds any number on the tab (a judgement or a rated question)
    includes_demo = any(j.source == IMPORTED_SOURCE for _, j in judgements) or any(
        q.source == IMPORTED_SOURCE for _, q in kept_questions
    )
    return QualityOut(
        bucket=cf.bucket_for(filters), version_markers=markers, includes_demo=includes_demo, **summary
    )


# ---- operations --------------------------------------------------------------------

FAILED_JOBS_SHOWN = 10
_FAILED_OUTCOMES = ("error", "timeout")  # failed, and not because of rate limiting


def nearest_rank(values: list[int], pct: int) -> int | None:
    """The `pct`th percentile by the nearest-rank rule: the value at rank ceil(pct / 100 * n) of the sorted values.

    Always one of the observed values (no interpolation); None for an empty list.
    """
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, -(-pct * len(ordered) // 100)) - 1]


def operations_summary(calls: list[tuple[datetime, Call]], layout: cf.Layout | None) -> dict:
    """Latency, outcomes, request counts and token use over already-filtered calls.

    Percentiles (p50, p95) use only calls of kind `chat`, by `nearest_rank`; every other number counts all
    kinds. `error_rate` is the share of calls that ended `error` or `timeout`, `rate_limit_rate` the share
    that ended `rate_limited`. Empty buckets and zero denominators give null, never 0.
    """
    chat = [(at, c) for at, c in calls if c.kind == "chat"]
    chat_by_bucket = layout.group(chat) if layout else []
    all_by_bucket = layout.group(calls) if layout else []
    labels = layout.labels() if layout else []
    rows = [c for _, c in calls]
    chat_ms = [c.latency_ms for _, c in chat]
    return {
        "latency": [
            {
                "t": t,
                "p50_ms": nearest_rank([c.latency_ms for c in group], 50),
                "p95_ms": nearest_rank([c.latency_ms for c in group], 95),
                "n": len(group),
            }
            for t, group in zip(labels, chat_by_bucket, strict=True)
        ],
        "outcomes": [
            {"t": t, **{o: sum(1 for c in group if c.outcome == o) for o in OUTCOMES}}
            for t, group in zip(labels, all_by_bucket, strict=True)
        ],
        "requests": [{"t": t, "n": len(group)} for t, group in zip(labels, all_by_bucket, strict=True)],
        "tokens_by_kind": {kind: sum(c.total_tokens or 0 for c in rows if c.kind == kind) for kind in CALL_KINDS},
        "totals": {
            "calls": len(rows),
            "error_rate": _rate(sum(1 for c in rows if c.outcome in _FAILED_OUTCOMES), len(rows)),
            "rate_limit_rate": _rate(sum(1 for c in rows if c.outcome == "rate_limited"), len(rows)),
            "p50_ms": nearest_rank(chat_ms, 50),
            "p95_ms": nearest_rank(chat_ms, 95),
            "tokens": sum(c.total_tokens or 0 for c in rows),
        },
    }


def _recent_failed_jobs(db: Session, workspace: Workspace) -> list[dict]:
    """The workspace's newest failed jobs: id, type, time and the stored fixed error message. Never the payload."""
    rows = db.execute(
        select(Job.id, Job.type, Job.created_at, Job.error)
        .where(Job.workspace_id == workspace.id, Job.status == "failed")
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(FAILED_JOBS_SHOWN)
    )
    return [{"id": r.id, "type": r.type, "created_at": r.created_at, "error": r.error} for r in rows]


@router.get("/operations", response_model=OperationsOut)
def console_operations(
    filters: cf.Filters = Depends(console_filters),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> OperationsOut:
    """Model-call latency, outcomes, request volume and tokens, the judge queue and the newest failed jobs.

    `judge_queue` and `recent_failed_jobs` describe the workspace right now and ignore the filters.
    """
    _assist_agent(db, workspace)
    calls = cf.kept(_load_calls(db, workspace), filters, "prompt_version")
    layout = cf.bucket_layout(filters, [at for at, _ in calls])
    return OperationsOut(
        bucket=cf.bucket_for(filters),
        **operations_summary(calls, layout),
        judge_queue=judge_queue_stats(db, workspace.id),
        recent_failed_jobs=_recent_failed_jobs(db, workspace),
    )


# ---- pipeline ----------------------------------------------------------------------

CYCLE_NOTE = (
    "The loop repeats: a fix that is rejected or fails verification sends you back to the fix step, "
    "and newly flagged conversations start the next round."
)
_STEPS = (
    ("flagged", "Flagged conversations"),
    ("pattern", "Pattern found"),
    ("criterion", "Criterion written"),
    ("tests", "Tests built"),
    ("fix", "Fix suggested"),
    ("review", "Fix reviewed"),
    ("verify", "Fix verified"),
    ("promoted", "Fix promoted"),
)
_FIX_NOTES = {
    "failed_verification": "Last fix failed verification",
    "rejected": "Last fix was rejected",
}


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def pipeline_steps(facts: dict) -> list[dict]:
    """The improvement loop as eight steps: those before the first unfinished one are `done`, it is `current`.

    `facts`: flagged_runs (with `demo_runs`, how many of them are demo dataset runs), has_pattern, criteria,
    tests, live_number and `fix`, the newest suggested prompt
    version as {number, status, verified, required} or None. The newest suggestion decides the fix stage:
    proposed (awaiting review), approved_verifying (being checked) and promoted (or promoted and since
    superseded) move the loop forward; rejected, failed_verification and a never-verified superseded
    suggestion put the loop back at the fix step. Evidence for a later step implies the earlier ones are
    done (discovery consumes the pattern summary). Details are counts and fixed phrases only.
    """
    fix = facts.get("fix")
    stage, note = 0, None
    if fix is not None:
        status = fix["status"]
        if status == "proposed":
            stage = 1
        elif status == "approved_verifying":
            stage = 2
        elif status == "promoted" or (status == "superseded" and fix["verified"]):
            stage = 3
        else:
            note = _FIX_NOTES.get(status, "Last fix was replaced by an edit")
    flagged, criteria, tests = facts["flagged_runs"], facts["criteria"], facts["tests"]
    done = [
        flagged > 0, bool(facts["has_pattern"]), criteria > 0, tests > 0, stage >= 1, stage >= 2, stage >= 3, stage >= 3,
    ]
    # Later evidence implies the earlier steps: discovery consumes the pattern summary and a criterion
    # can be added by hand, so a criterion with no stored pattern still means the pattern step passed.
    done = [d or any(done[i + 1 :]) for i, d in enumerate(done)]
    demo = facts.get("demo_runs", 0)
    flagged_detail = _count(flagged, "flagged conversation", "flagged conversations") if flagged else None
    if flagged_detail and demo:
        flagged_detail += f" ({demo} demo)"
    details = [
        flagged_detail,
        "Pattern found" if facts["has_pattern"] else None,
        _count(criteria, "criterion", "criteria") if criteria else None,
        _count(tests, "test", "tests") if tests else None,
        f"v{fix['number']} suggested" if stage >= 1 else note,
        "Awaiting your review" if stage == 1 else ("Approved" if stage >= 2 else None),
        _count(fix["required"], "required test", "required tests") if stage == 2 else ("Passed" if stage >= 3 else None),
        f"v{facts['live_number']} is live" if stage >= 3 and facts.get("live_number") else None,
    ]
    steps, ahead = [], True
    for (step_id, label), is_done, detail in zip(_STEPS, done, details, strict=True):
        if ahead and is_done:
            state = "done"
        elif ahead:
            state, ahead = "current", False
        else:
            state = "todo"
        steps.append({"id": step_id, "label": label, "state": state, "detail": detail})
    return steps


def _pipeline_facts(db: Session, agent: Agent, filters: cf.Filters | None = None) -> dict:
    """The loop's facts. Flagged conversations are counted through the console's source filter when one is
    given (`filters`), else agent-wide; `demo_runs` is how many of the counted ones are demo dataset runs.
    Pattern, criteria, tests and versions are the agent's own and never filtered."""
    sources = list(db.scalars(select(Run.source).where(Run.agent_id == agent.id, Run.source.in_(EVIDENCE_SOURCES))))
    if filters is not None:
        sources = [source for source in sources if cf.matches(source, filters)]
    flagged = len(sources)
    demo_runs = sum(1 for source in sources if source == IMPORTED_SOURCE)
    criteria = db.scalar(select(func.count()).select_from(Criterion).where(Criterion.agent_id == agent.id))
    tests = db.scalar(
        select(func.count())
        .select_from(EvalTest)
        .join(Criterion, EvalTest.criterion_id == Criterion.id)
        .where(Criterion.agent_id == agent.id)
    )
    latest = db.execute(
        select(PromptVersion.number, PromptVersion.status, PromptVersion.verification)
        .where(PromptVersion.agent_id == agent.id, PromptVersion.origin == "suggested")
        .order_by(PromptVersion.number.desc())
        .limit(1)
    ).first()
    fix = None
    if latest is not None:
        verification = latest.verification if isinstance(latest.verification, dict) else None
        required = verification.get("required") if verification else None
        fix = {
            "number": latest.number, "status": latest.status, "verified": verification is not None,
            "required": len(required) if isinstance(required, list) else 0,
        }
    live = db.scalar(select(PromptVersion.number).where(PromptVersion.id == agent.current_prompt_version_id))
    return {
        "flagged_runs": flagged, "demo_runs": demo_runs, "has_pattern": bool(agent.pattern_summary), "criteria": criteria,
        "tests": tests, "fix": fix, "live_number": live,
    }


def _pipeline(db: Session, agent: Agent, filters: cf.Filters | None = None) -> dict:
    return {"steps": pipeline_steps(_pipeline_facts(db, agent, filters)), "cycle_note": CYCLE_NOTE}


@router.get("/pipeline", response_model=PipelineOut)
def console_pipeline(
    workspace: Workspace = Depends(get_current_workspace), db: Session = Depends(get_db)
) -> PipelineOut:
    """Where the assistant's improvement loop stands, derived from what the Evolution tab reads.

    The loop belongs to the agent, not to a time range, so the range, source and version filters do not apply:
    the flagged step counts every flagged conversation and says how many of them are demo dataset runs.
    """
    return PipelineOut(**_pipeline(db, _assist_agent(db, workspace)))


# ---- overview ----------------------------------------------------------------------


def _pct_change(now: int, before: int | None) -> float | None:
    """Relative change in percent; null without a previous period or when it had nothing to compare against."""
    return None if not before else round((now - before) / before * 100, 1)


def _ok_percent(judgements: list[Judgement]) -> float | None:
    """Percent of judged conversations graded 3 or better; null when none were judged."""
    share = _rate(sum(1 for j in judgements if j.grade >= 3), len(judgements))
    return None if share is None else round(share * 100, 1)


def _points_diff(now: float | None, before: float | None) -> float | None:
    return None if now is None or before is None else round(now - before, 1)


def _kpi(kpi_id: str, label: str, value, unit: str, delta: float | None, delta_good: str, sparkline: list) -> dict:
    return {
        "id": kpi_id, "label": label, "value": value, "unit": unit, "delta": delta,
        "delta_good": delta_good, "sparkline": sparkline,
    }


def _flagged_runs(db: Session, agent: Agent) -> list:
    """The agent's flagged runs (columns only, never the messages): position, source and time."""
    return list(
        db.execute(
            select(Run.position, Run.source, Run.created_at).where(
                Run.agent_id == agent.id, Run.source.in_(EVIDENCE_SOURCES)
            )
        )
    )


def _versions(db: Session, agent: Agent, views: list[tuple], questions: list[tuple], filters: cf.Filters) -> list[int]:
    """Every version number the version select can offer; the version filter itself never narrows it."""
    found: set[int] = set()
    if filters.source != "demo":
        found.update(db.scalars(select(PromptVersion.number).where(PromptVersion.agent_id == agent.id)))
    if filters.source != "live":
        found.update(
            row.assist_version
            for _, row in (*views, *questions)
            if row.source == IMPORTED_SOURCE and row.assist_version is not None
        )
    return sorted(found)


def _reask_rate(questions: list[tuple] | None) -> float | None:
    return None if not questions else _rate(sum(1 for _, q in questions if q.reasked), len(questions))


def _alert_snapshot(
    db: Session,
    workspace: Workspace,
    filters: cf.Filters,
    q_now: list[tuple],
    q_before: list[tuple] | None,
    c_now: list[tuple],
    open_runs: list,
) -> dict:
    """The numbers the alert rules read; None where a number does not exist (no previous period, no calls)."""
    totals = operations_summary(c_now, None)["totals"]
    demo = any(row.source == IMPORTED_SOURCE for rows in (q_now, q_before or [], c_now) for _, row in rows) or any(
        r.source == IMPORTED_SOURCE for r in open_runs
    )
    return {
        "includes_demo": demo,
        "calls": totals["calls"],
        "error_rate": totals["error_rate"],
        "chat_calls": sum(1 for _, c in c_now if c.kind == "chat"),
        "p95_ms": totals["p95_ms"],
        "questions": len(q_now),
        "reask_rate": _reask_rate(q_now),
        "prev_questions": len(q_before) if q_before is not None else None,
        "prev_reask_rate": _reask_rate(q_before),
        "open_flagged": len(open_runs),
        # the judge only ever sees live conversations: a demo-only view has no backlog of its own
        "judge_waiting": None if filters.source == "demo" else judge_queue_stats(db, workspace.id)["waiting_sessions"],
    }


@router.get("/overview", response_model=OverviewOut)
def console_overview(
    filters: cf.Filters = Depends(console_filters),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> OverviewOut:
    """Four KPI tiles with deltas against the previous period, the alerts, the loop and the grade trend.

    Deltas: a count's is the relative change in percent, a percent's is the change in points; both are null
    for the range `all`, for an empty previous period and for open flagged conversations (a backlog, not a
    flow). Sparklines have one value per bucket of the range. The open flagged count ignores the range and
    version filters (flagged runs belong to no version) but honours the source filter.
    """
    agent = _assist_agent(db, workspace)
    views, questions = _load_timed_rows(db, workspace)
    judgements, calls = _load_judgements(db, workspace), _load_calls(db, workspace)
    previous = cf.previous_period(filters)

    q_now = cf.kept(questions, filters)
    j_now = cf.kept(judgements, filters, "prompt_version")
    c_now = cf.kept(calls, filters, "prompt_version")
    q_before = cf.kept(questions, previous) if previous else None
    j_before = cf.kept(judgements, previous, "prompt_version") if previous else None

    runs = _flagged_runs(db, agent)
    open_runs = [r for r in runs if r.position > agent.last_checked_at_run and cf.matches(r.source, filters)]
    new_runs = [(r.created_at, r) for r in runs if cf.in_range(r.created_at, filters) and cf.matches(r.source, filters)]
    layout = cf.bucket_layout(filters, [at for at, _ in (*q_now, *j_now, *new_runs)])

    def per_bucket(timed: list[tuple], measure) -> list:
        return [measure(group) for group in layout.group(timed)] if layout else []

    ok_now = _ok_percent([j for _, j in j_now])
    ok_before = _ok_percent([j for _, j in j_before]) if j_before is not None else None
    live = db.scalar(select(PromptVersion.number).where(PromptVersion.id == agent.current_prompt_version_id))
    kpis = [
        _kpi(
            "questions", "Questions", len(q_now), "count",
            _pct_change(len(q_now), len(q_before) if q_before is not None else None), "up",
            per_bucket(q_now, lambda group: float(len(group))),
        ),
        _kpi("ok_share", "Auto-judged OK", ok_now, "percent", _points_diff(ok_now, ok_before), "up",
             per_bucket(j_now, _ok_percent)),
        _kpi("open_flagged", "Open flagged conversations", len(open_runs), "count", None, "down",
             per_bucket(new_runs, lambda group: float(len(group)))),
        _kpi("live_version", "Live version", f"v{live}" if live else None, "version", None, "up", []),
    ]
    snapshot = _alert_snapshot(db, workspace, filters, q_now, q_before, c_now, open_runs)
    return OverviewOut(
        versions=_versions(db, agent, views, questions, filters),
        kpis=kpis,
        alerts=[asdict(a) for a in alert_rules.compute_alerts(snapshot)],
        loop=_pipeline(db, agent, filters),
        quality_trend=[{"t": p["t"], "avg_grade": p["avg_grade"]} for p in grade_trend(j_now, layout)],
        version_markers=_version_markers(db, agent.id, questions, filters),
    )
