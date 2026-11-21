"""Tests API: the suite view, replay, run-all, build-tests and job status. All routes are workspace-scoped."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_workspace
from backend.api.limits import rate_limit_llm
from backend.api.schemas import (
    CriterionSuiteOut,
    EvalResultOut,
    EvalTestOut,
    JobAccepted,
    JobOut,
    RunAllOut,
    SuiteOut,
)
from backend.db.agents import current_prompt_version, get_owned_agent
from backend.db.events import try_log_event
from backend.db.models import Agent, Criterion, EvalResult, EvalTest, Job, Workspace, utcnow
from backend.db.session import get_db
from backend.db.testsuite import (
    agent_tests,
    criterion_status_for,
    current_replay,
    get_owned_criterion,
    get_owned_test,
    latest_baseline,
    stale_test_count,
)
from backend.jobs.queue import (
    JOBS_PER_WORKSPACE_PER_HOUR,
    MAX_QUEUED_JOBS,
    active_job_for,
    enqueue,
    jobs_started_since,
    queued_job_count,
)

router = APIRouter(tags=["tests"])

RUN_LIMIT = "Test run limit reached for this hour. Try again later."
QUEUE_BUSY = "The test queue is busy. Try again in a minute."
TESTS_EXIST = "Tests already exist for this criterion."


def _agent_or_404(db: Session, workspace: Workspace, agent_id: str) -> Agent:
    try:
        return get_owned_agent(db, workspace, agent_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Agent not found.") from None


def _jobs_left_this_hour(db: Session, workspace: Workspace) -> int:
    used = jobs_started_since(db, workspace.id, utcnow() - timedelta(hours=1))
    return max(JOBS_PER_WORKSPACE_PER_HOUR - used, 0)


def _check_queue_room(db: Session, needed: int = 1) -> None:
    """429 when enqueueing `needed` more jobs would push the global backlog past MAX_QUEUED_JOBS."""
    if queued_job_count(db) + needed > MAX_QUEUED_JOBS:
        raise HTTPException(status_code=429, detail=QUEUE_BUSY)


def _result_out(result: EvalResult | None) -> EvalResultOut | None:
    if result is None:
        return None
    return EvalResultOut(
        id=result.id,
        kind=result.kind,
        verdict=result.verdict,
        rationale=result.rationale,
        transcript=result.transcript,
        stale=result.stale,
        prompt_version_id=result.prompt_version_id,
        created_at=result.created_at,
    )


def _replay_payload(test: EvalTest, prompt_version_id: str) -> dict:
    return {"test_id": test.id, "prompt_version_id": prompt_version_id}


def _active_id(job: Job | None) -> str | None:
    return job.id if job else None


@router.get("/agents/{agent_id}/tests", response_model=SuiteOut)
def get_suite(
    agent_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> SuiteOut:
    agent = _agent_or_404(db, workspace, agent_id)
    version = current_prompt_version(agent)
    tests_by_criterion: dict[str, list[EvalTest]] = {}
    for test in agent_tests(db, agent):
        tests_by_criterion.setdefault(test.criterion_id, []).append(test)

    criteria = []
    for criterion in sorted(agent.criteria, key=lambda c: c.position):
        tests = [
            EvalTestOut(
                id=test.id,
                role=test.role,
                source_run_id=test.source_run_id,
                baseline=_result_out(latest_baseline(db, test)),
                current=_result_out(current_replay(db, test, version.id)),
                active_job_id=_active_id(
                    active_job_for(db, workspace.id, "replay", _replay_payload(test, version.id))
                ),
            )
            for test in tests_by_criterion.get(criterion.id, [])
        ]
        build_job = active_job_for(db, workspace.id, "build_tests", {"criterion_id": criterion.id})
        criteria.append(
            CriterionSuiteOut(
                criterion_id=criterion.id,
                status=criterion_status_for(db, criterion, version.id),
                judge_check=criterion.judge_check,
                active_job_id=_active_id(build_job),
                tests=tests,
            )
        )
    return SuiteOut(
        prompt_version_id=version.id,
        prompt_version_number=version.number,
        stale_count=stale_test_count(db, agent),
        criteria=criteria,
    )


@router.post("/tests/{test_id}/replay", status_code=202, response_model=JobAccepted)
def replay(
    test_id: str,
    request: Request,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> JobAccepted:
    try:
        test = get_owned_test(db, workspace, test_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Test not found.") from None
    payload = _replay_payload(test, test.criterion.agent.current_prompt_version_id)
    existing = active_job_for(db, workspace.id, "replay", payload)
    if existing is not None:
        return JobAccepted(job_id=existing.id)
    if _jobs_left_this_hour(db, workspace) < 1:
        raise HTTPException(status_code=429, detail=RUN_LIMIT)
    _check_queue_room(db)
    rate_limit_llm(request)
    job = enqueue(db, workspace.id, "replay", payload)
    try_log_event(db, workspace.id, "replay_started", {"role": test.role})
    return JobAccepted(job_id=job.id)


@router.post("/agents/{agent_id}/tests/run-all", status_code=202, response_model=RunAllOut)
def run_all(
    agent_id: str,
    request: Request,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> RunAllOut:
    agent = _agent_or_404(db, workspace, agent_id)
    version_id = agent.current_prompt_version_id
    pending = [
        test
        for test in agent_tests(db, agent)
        if current_replay(db, test, version_id) is None
        and active_job_for(db, workspace.id, "replay", _replay_payload(test, version_id)) is None
    ]
    if len(pending) > _jobs_left_this_hour(db, workspace):
        raise HTTPException(status_code=429, detail=RUN_LIMIT)
    if not pending:
        return RunAllOut(job_ids=[])
    _check_queue_room(db, len(pending))
    rate_limit_llm(request)
    job_ids = []
    for test in pending:
        job_ids.append(enqueue(db, workspace.id, "replay", _replay_payload(test, version_id)).id)
        try_log_event(db, workspace.id, "replay_started", {"role": test.role})
    return RunAllOut(job_ids=job_ids)


@router.post("/criteria/{criterion_id}/build-tests", status_code=202, response_model=JobAccepted)
def build_tests(
    criterion_id: str,
    request: Request,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> JobAccepted:
    try:
        criterion: Criterion = get_owned_criterion(db, workspace, criterion_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Criterion not found.") from None
    test_count = db.scalar(
        select(func.count()).select_from(EvalTest).where(EvalTest.criterion_id == criterion.id)
    )
    if test_count:
        raise HTTPException(status_code=409, detail=TESTS_EXIST)
    payload = {"criterion_id": criterion.id}
    existing = active_job_for(db, workspace.id, "build_tests", payload)
    if existing is not None:
        return JobAccepted(job_id=existing.id)
    if _jobs_left_this_hour(db, workspace) < 1:
        raise HTTPException(status_code=429, detail=RUN_LIMIT)
    _check_queue_room(db)
    rate_limit_llm(request)
    return JobAccepted(job_id=enqueue(db, workspace.id, "build_tests", payload).id)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(
    job_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> JobOut:
    job = db.get(Job, job_id)
    if job is None or job.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Job not found.")
    return JobOut(id=job.id, type=job.type, status=job.status, result=job.result, error=job.error)
