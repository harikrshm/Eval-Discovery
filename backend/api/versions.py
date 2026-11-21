"""Prompt version history, and the state transitions of a candidate prompt version.

Approve, reject and restore each take the agent row lock first and re-read the version under it, so
two requests racing on one agent are serialized: exactly one wins and the other sees its result (409).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_workspace
from backend.api.limits import FIX_VERIFYING, check_version_caps
from backend.api.schemas import (
    ApprovedJob,
    ApproveOut,
    RejectOut,
    ReportOut,
    VersionDetailOut,
    VersionOut,
)
from backend.api.tests import QUEUE_BUSY
from backend.core.promotion import RequiredTest
from backend.db.agents import get_owned_agent, lock_agent
from backend.db.candidates import (
    RESTORABLE_STATUSES,
    CandidateOpen,
    approve_candidate,
    compute_required,
    get_owned_version,
    reject_candidate,
    restore_version,
    verifying_version,
)
from backend.db.events import try_log_event
from backend.db.models import EvalTest, PromptVersion, Workspace
from backend.db.session import get_db
from backend.jobs.queue import MAX_QUEUED_JOBS, active_job_for, enqueue, queued_job_count

router = APIRouter(tags=["versions"])

NOT_APPROVABLE = "This fix can't be approved in its current state."
NOT_REJECTABLE = "This fix can't be rejected in its current state."
NOT_RESTORABLE = "This version can't be restored."
FIX_MISMATCH = "That fix no longer matches its test."


def version_out(version: PromptVersion, current_id: str | None) -> VersionOut:
    return VersionOut(
        id=version.id,
        number=version.number,
        origin=version.origin,
        status=version.status,
        strength=(version.verification or {}).get("strength"),
        created_at=version.created_at,
        is_current=version.id == current_id,
    )


@router.get("/agents/{agent_id}/prompt-versions", response_model=list[VersionOut])
def list_versions(
    agent_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> list[VersionOut]:
    try:
        agent = get_owned_agent(db, workspace, agent_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Agent not found.") from None
    versions = db.scalars(
        select(PromptVersion).where(PromptVersion.agent_id == agent.id).order_by(PromptVersion.number.desc())
    )
    return [version_out(v, agent.current_prompt_version_id) for v in versions]


@router.get("/prompt-versions/{version_id}", response_model=VersionDetailOut)
def get_version(
    version_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> VersionDetailOut:
    try:
        version = get_owned_version(db, workspace, version_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Version not found.") from None
    parent = db.get(PromptVersion, version.parent_id) if version.parent_id else None
    base = version_out(version, version.agent.current_prompt_version_id)
    return VersionDetailOut(
        **base.model_dump(),
        text=version.text,
        parent_text=parent.text if parent is not None and parent.agent_id == version.agent_id else None,
    )


def _owned_version_or_404(db: Session, workspace: Workspace, version_id: str) -> PromptVersion:
    try:
        return get_owned_version(db, workspace, version_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Version not found.") from None


def _commit_or_rollback(db: Session) -> None:
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def _replay_payload(test_id: str, version_id: str) -> dict:
    # "system" exempts the job from the hourly cap (jobs_started_since skips it).
    return {"test_id": test_id, "prompt_version_id": version_id, "system": True}


def _unique_by_test(required: list[RequiredTest]) -> list[RequiredTest]:
    seen: set[str] = set()
    unique = []
    for item in required:
        if item.test_id not in seen:
            seen.add(item.test_id)
            unique.append(item)
    return unique


@router.post("/prompt-versions/{version_id}/approve", status_code=202, response_model=ApproveOut)
def approve(
    version_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ApproveOut:
    version = _owned_version_or_404(db, workspace, version_id)
    agent = version.agent
    lock_agent(db, agent)
    db.refresh(version)  # the status as committed by whoever held the lock before us
    if version.status != "proposed":
        raise HTTPException(status_code=409, detail=NOT_APPROVABLE)
    if verifying_version(db, agent) is not None:
        raise HTTPException(status_code=409, detail=FIX_VERIFYING)
    fix_test = db.get(EvalTest, version.source_test_id) if version.source_test_id else None
    if fix_test is None or fix_test.role != "fix" or fix_test.criterion.agent_id != agent.id:
        raise HTTPException(status_code=409, detail=FIX_MISMATCH)

    # Queue cap first: nothing may change when the backlog cannot take the replays.
    needed = [
        item
        for item in _unique_by_test(compute_required(db, agent, fix_test))
        if active_job_for(db, workspace.id, "replay", _replay_payload(item.test_id, version.id)) is None
    ]
    if queued_job_count(db) + len(needed) > MAX_QUEUED_JOBS:
        raise HTTPException(status_code=429, detail=QUEUE_BUSY)

    try:
        try:
            required = approve_candidate(db, agent, version)
        except ValueError:
            raise HTTPException(status_code=409, detail=NOT_APPROVABLE) from None
        except LookupError:
            raise HTTPException(status_code=409, detail=FIX_MISMATCH) from None
        jobs = []
        for item in _unique_by_test(required):
            payload = _replay_payload(item.test_id, version.id)
            job = active_job_for(db, workspace.id, "replay", payload)
            if job is None:
                job = enqueue(db, workspace.id, "replay", payload, commit=False)
            jobs.append(ApprovedJob(test_id=item.test_id, job_id=job.id))
        db.commit()  # one commit: the status change and its jobs land together or not at all
    except Exception:
        db.rollback()
        raise
    try_log_event(
        db,
        workspace.id,
        "fix_approved",
        {"strength": (version.verification or {}).get("strength"), "required": len(jobs)},
    )
    return ApproveOut(version_id=version.id, status=version.status, jobs=jobs)


@router.post("/prompt-versions/{version_id}/reject", response_model=RejectOut)
def reject(
    version_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> RejectOut:
    version = _owned_version_or_404(db, workspace, version_id)
    lock_agent(db, version.agent)
    db.refresh(version)
    if version.status != "proposed":
        raise HTTPException(status_code=409, detail=NOT_REJECTABLE)
    try:
        reject_candidate(db, version)
    except ValueError:
        raise HTTPException(status_code=409, detail=NOT_REJECTABLE) from None
    _commit_or_rollback(db)
    try_log_event(db, workspace.id, "fix_rejected", {})
    return RejectOut(version_id=version.id, status=version.status)


@router.post("/prompt-versions/{version_id}/restore", response_model=VersionOut)
def restore(
    version_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> VersionOut:
    version = _owned_version_or_404(db, workspace, version_id)
    agent = version.agent
    lock_agent(db, agent)
    db.refresh(version)
    if version.status not in RESTORABLE_STATUSES:
        raise HTTPException(status_code=409, detail=NOT_RESTORABLE)
    if verifying_version(db, agent) is not None:
        raise HTTPException(status_code=409, detail=FIX_VERIFYING)
    check_version_caps(db, agent)
    try:
        restored = restore_version(db, agent, version)
    except CandidateOpen:
        raise HTTPException(status_code=409, detail=FIX_VERIFYING) from None
    except ValueError:
        raise HTTPException(status_code=409, detail=NOT_RESTORABLE) from None
    _commit_or_rollback(db)
    try_log_event(db, workspace.id, "version_restored", {})
    return version_out(restored, agent.current_prompt_version_id)


def _dict_or_none(value: object) -> dict | None:
    return value if isinstance(value, dict) else None


@router.get("/prompt-versions/{version_id}/report", response_model=ReportOut)
def report(
    version_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ReportOut:
    version = _owned_version_or_404(db, workspace, version_id)
    stored = _dict_or_none(version.change_report) or {}
    strength = (_dict_or_none(version.verification) or {}).get("strength")
    out = ReportOut(
        version_id=version.id,
        number=version.number,
        status=version.status,
        strength=strength if isinstance(strength, str) else None,
        why=_dict_or_none(stored.get("why")),
        outcome=_dict_or_none(stored.get("outcome")),
    )
    try_log_event(db, workspace.id, "report_viewed", {"status": version.status})
    return out
