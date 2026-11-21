"""Resolve candidate prompt versions that are being verified.

`reconcile_verifying` is the state machine's only exit from `approved_verifying`: it promotes a
candidate (by the deterministic rule in `core.promotion`) or fails it, and it always ends in one
of the two, so a candidate is never stranded. It is idempotent and safe to run from overlapping
worker passes: each version is resolved under the agent row lock after re-checking its status.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.promotion import RequiredTest, build_outcome_rows, decide_promotion
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
_TEST_GONE = "That test no longer exists."
_JOB_FAILED_FALLBACK = "The test failed."


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


def _replay_jobs(db: Session, workspace_id: str, version_id: str, test_ids: set[str]) -> list[Job]:
    """Replay jobs of the workspace for this version and one of these tests, oldest first.

    Matched on `test_id` and `prompt_version_id` only; other payload keys are ignored.
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
            and payload.get("test_id") in test_ids
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
        return f"A test could not be run: {failed.error or _JOB_FAILED_FALLBACK}"
    if len(existing) < len(missing):
        return f"A test could not be run: {_TEST_GONE}"
    if any(
        job.status == "done" and isinstance(job.result, dict) and job.result.get("discarded") is True
        for job in jobs
    ):
        return _DISCARDED_REASON
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

    reason = _incomplete_failure(db, version, workspace_id, missing, approved_at, now)
    if reason is None:
        return None
    try:
        table = _table(db, required, version)
    except Exception:
        logger.exception("Could not build the outcome table for prompt version %s", version.id)
        return malformed
    return _Resolution(False, reason, table)


def _apply(db: Session, version: PromptVersion, resolution: _Resolution) -> None:
    agent = version.agent
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
    _apply(db, version, resolution)
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
