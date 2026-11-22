"""Resolve candidate prompt versions that are being verified.

`reconcile_verifying` is the state machine's only exit from `approved_verifying`: it promotes a
candidate (by the deterministic rule in `core.promotion`) or fails it, and it always ends in one
of the two, so a candidate is never stranded. It is idempotent and safe to run from overlapping
worker passes: each version is resolved under the agent row lock after re-checking its status.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.core.promotion import RequiredTest, build_outcome_rows, decide_promotion, early_failure
from backend.db.agents import lock_agent
from backend.db.events import try_log_event
from backend.db.models import EvalResult, EvalTest, Job, PromptVersion, utcnow
from backend.db.testsuite import current_replay
from backend.jobs import queue

logger = logging.getLogger(__name__)

VERIFICATION_TIMEOUT_MINUTES = 30

_MALFORMED_REASON = "Verification could not be completed."
_TIMEOUT_REASON = "Verification timed out."
_DISCARDED_REASON = "A criterion changed during verification."
_PARENT_CHANGED_REASON = "The prompt changed during verification."
_TEST_GONE = "That test no longer exists."
_JOB_FAILED_FALLBACK = "The test failed."
_CANCELLED_ERROR = "Cancelled: verification already failed."


@dataclass
class _Resolution:
    promote: bool
    reason: str
    table: list[dict]


def _parse_verification(verification) -> tuple[list[RequiredTest], datetime]:
    """The required tests and approval time from the stored JSON; raises on any malformed value."""
    if not isinstance(verification, dict):
        raise ValueError("verification is not an object")
    entries = verification["required"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("verification has no required tests")
    required = []
    for entry in entries:
        test_id, role, before = entry["test_id"], entry["role"], entry.get("before")
        if not isinstance(test_id, str) or not isinstance(role, str):
            raise ValueError("malformed required test")
        if before is not None and not isinstance(before, str):
            raise ValueError("malformed required test")
        required.append(RequiredTest(test_id, role, before))
    approved_at = datetime.fromisoformat(verification["approved_at"])
    if approved_at.tzinfo is not None:
        approved_at = approved_at.astimezone(timezone.utc).replace(tzinfo=None)
    return required, approved_at


def _replay_jobs(db: Session, workspace_id: str, version_id: str, test_ids: set[str] | None) -> list[Job]:
    """Replay jobs of the workspace for this version and one of these tests, oldest first.

    Matched on `test_id` and `prompt_version_id` only; other payload keys are ignored.
    `test_ids=None` matches every test (used when the required list cannot be read).
    """
    jobs = db.scalars(
        select(Job)
        .where(Job.workspace_id == workspace_id, Job.type == "replay")
        .order_by(Job.created_at, Job.id)
    )
    matched = []
    for job in jobs:
        payload = job.payload
        if (
            isinstance(payload, dict)
            and payload.get("prompt_version_id") == version_id
            and (test_ids is None or payload.get("test_id") in test_ids)
        ):
            matched.append(job)
    return matched


def _table(db: Session, required: list[RequiredTest], version: PromptVersion) -> list[dict]:
    """The before -> after rows from the stored current replay results (missing ones stay empty)."""
    names: dict[str, str] = {}
    after: dict[str, str] = {}
    rationales: dict[str, str] = {}
    transcripts: dict[str, list] = {}
    for item in required:
        test = db.get(EvalTest, item.test_id)
        if test is None:
            continue
        names[item.test_id] = test.criterion.name
        result = current_replay(db, test, version.id)
        if result is not None:
            after[item.test_id] = result.verdict
            rationales[item.test_id] = result.rationale
            transcripts[item.test_id] = result.transcript or []
    return build_outcome_rows(required, names, after, rationales, transcripts)


def _job_failure(jobs: list[Job]) -> str | None:
    """The failure reason a test's replay jobs decide (failed or discarded), assuming none is active."""
    failed = next((job for job in jobs if job.status == "failed"), None)
    if failed is not None:
        return f"A test could not be run: {failed.error or _JOB_FAILED_FALLBACK}"
    if any(
        job.status == "done" and isinstance(job.result, dict) and job.result.get("discarded") is True
        for job in jobs
    ):
        return _DISCARDED_REASON
    return None


def _decisive_job_failure(
    db: Session, version: PromptVersion, workspace_id: str, missing: list[RequiredTest]
) -> str | None:
    """Why a missing fix or held-out test already decides the outcome, from its own replay jobs only.

    Decisive when one of its jobs failed or was discarded and none is queued or running. A missing
    regression result is never decisive by itself.
    """
    for item in missing:
        if item.role not in ("fix", "heldout") or db.get(EvalTest, item.test_id) is None:
            continue
        jobs = _replay_jobs(db, workspace_id, version.id, {item.test_id})
        if any(job.status in ("queued", "running") for job in jobs):
            continue
        reason = _job_failure(jobs)
        if reason is not None:
            return reason
    return None


def _incomplete_failure(
    db: Session, version: PromptVersion, workspace_id: str, missing: list[RequiredTest], approved_at: datetime, now: datetime
) -> str | None:
    """Why a candidate with missing results must fail now, or None when it should keep waiting."""
    existing = [m for m in missing if db.get(EvalTest, m.test_id) is not None]
    jobs = _replay_jobs(db, workspace_id, version.id, {m.test_id for m in existing})
    if any(job.status in ("queued", "running") for job in jobs):
        return None
    failed = next((job for job in jobs if job.status == "failed"), None)
    if failed is not None:
        return _job_failure([failed])
    if len(existing) < len(missing):
        return f"A test could not be run: {_TEST_GONE}"
    reason = _job_failure(jobs)
    if reason is not None:
        return reason
    if now - approved_at > timedelta(minutes=VERIFICATION_TIMEOUT_MINUTES):
        return _TIMEOUT_REASON
    return None


def _evaluate(db: Session, version: PromptVersion, workspace_id: str, now: datetime) -> _Resolution | None:
    """The version's resolution, or None while it should keep waiting."""
    malformed = _Resolution(False, _MALFORMED_REASON, [])
    try:
        required, approved_at = _parse_verification(version.verification)
    except Exception:
        logger.exception("Malformed verification data on prompt version %s", version.id)
        return malformed

    results: dict[str, EvalResult | None] = {}
    for item in required:
        test = db.get(EvalTest, item.test_id)
        results[item.test_id] = current_replay(db, test, version.id) if test is not None else None
    missing = [item for item in required if results[item.test_id] is None]

    if not missing:
        try:
            decision = decide_promotion(required, {tid: r.verdict for tid, r in results.items()})
            table = _table(db, required, version)
        except Exception:
            logger.exception("Could not decide promotion for prompt version %s", version.id)
            return malformed
        return _Resolution(decision.promote, decision.reason, table)

    # Decided by the stored verdicts alone (never by jobs still running): fail now.
    try:
        early = early_failure(required, {tid: r.verdict for tid, r in results.items() if r is not None})
    except Exception:
        logger.exception("Could not check for an early failure of prompt version %s", version.id)
        return malformed
    reason = early.reason if early is not None else None
    if reason is None:
        reason = _decisive_job_failure(db, version, workspace_id, missing)
    if reason is None:
        reason = _incomplete_failure(db, version, workspace_id, missing, approved_at, now)
    if reason is None:
        return None
    try:
        table = _table(db, required, version)
    except Exception:
        logger.exception("Could not build the outcome table for prompt version %s", version.id)
        return malformed
    return _Resolution(False, reason, table)


def _cancel_queued_replays(db: Session, version: PromptVersion, workspace_id: str) -> None:
    """Fail the version's still-queued replay jobs so no model calls are spent on a failed candidate.

    Running jobs are left alone (their handler no-ops for a resolved version). The update re-checks
    `status = 'queued'`, so a job a worker claims at the same moment is never overwritten.
    """
    try:
        required, _ = _parse_verification(version.verification)
        test_ids: set[str] | None = {item.test_id for item in required}
    except Exception:
        test_ids = None  # unreadable required list: cancel every queued replay of this version
    queued = [job.id for job in _replay_jobs(db, workspace_id, version.id, test_ids) if job.status == "queued"]
    if not queued:
        return
    db.execute(
        update(Job)
        .where(Job.id.in_(queued), Job.status == "queued")
        .values(status="failed", error=_CANCELLED_ERROR, locked_at=None)
        .execution_options(synchronize_session="fetch")
    )


def _apply(db: Session, version: PromptVersion, resolution: _Resolution) -> _Resolution:
    """Store the resolution and return the one actually applied (a promotion can turn into a failure)."""
    agent = version.agent
    if resolution.promote and version.parent_id != agent.current_prompt_version_id:
        # The candidate was written against another prompt than the live one: never replace it.
        resolution = _Resolution(False, _PARENT_CHANGED_REASON, resolution.table)
    if resolution.promote:
        previous = db.get(PromptVersion, agent.current_prompt_version_id)
        if previous is not None and previous.id != version.id and previous.status == "promoted":
            previous.status = "superseded"
        for sibling in db.scalars(
            select(PromptVersion).where(PromptVersion.agent_id == agent.id, PromptVersion.status == "proposed")
        ):
            sibling.status = "superseded"
        version.status = "promoted"
        agent.current_prompt_version_id = version.id
    else:
        version.status = "failed_verification"
        _cancel_queued_replays(db, version, agent.workspace_id)
    report = version.change_report if isinstance(version.change_report, dict) else {}
    # A new dict: SQLAlchemy does not detect in-place changes to a JSON column.
    version.change_report = {
        "why": report.get("why"),
        "outcome": {
            "status": version.status,
            "reason": resolution.reason,
            "table": resolution.table,
            "narrative": None,
            "explanation_unavailable": False,
        },
    }
    db.flush()
    db.expire(agent, ["prompt_versions"])
    return resolution


def _resolve(db: Session, version: PromptVersion, now: datetime) -> bool:
    """Resolve one version under the agent lock; False when it was already resolved or must wait."""
    agent = version.agent
    version_id, workspace_id = version.id, agent.workspace_id
    lock_agent(db, agent)
    db.expire_all()
    db.refresh(version)
    if version.status != "approved_verifying":
        db.rollback()  # release the lock; another transaction resolved it first
        return False
    resolution = _evaluate(db, version, workspace_id, now)
    if resolution is None:
        db.rollback()
        return False
    strength = version.verification.get("strength") if isinstance(version.verification, dict) else None
    resolution = _apply(db, version, resolution)
    # Commits the resolution together with the report job.
    queue.enqueue(db, workspace_id, "change_report", {"prompt_version_id": version_id})
    if resolution.promote:
        try_log_event(db, workspace_id, "fix_verified", {"strength": strength})
        try_log_event(db, workspace_id, "fix_promoted")
    else:
        try_log_event(db, workspace_id, "fix_verification_failed", {"reason": resolution.reason})
    return True


def reconcile_verifying(db: Session, now: datetime | None = None) -> list[str]:
    """Resolve every `approved_verifying` version that can be; return the ids resolved in this call.

    Idempotent: a resolved version is never touched again. Commits its own work, one version at a
    time, so a bad version cannot block the others.
    """
    now = now or utcnow()
    ids = list(
        db.scalars(
            select(PromptVersion.id)
            .where(PromptVersion.status == "approved_verifying")
            .order_by(PromptVersion.created_at, PromptVersion.id)
        )
    )
    db.rollback()  # end the read transaction; each version gets its own locked one
    resolved: list[str] = []
    for version_id in ids:
        try:
            version = db.get(PromptVersion, version_id)
            if version is not None and _resolve(db, version, now):
                resolved.append(version_id)
        except Exception:
            db.rollback()
            logger.exception("Could not reconcile prompt version %s", version_id)
    return resolved
