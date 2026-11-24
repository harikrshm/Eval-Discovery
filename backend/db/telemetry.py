"""Model-call telemetry: one AssistCall row per provider call, written so it can never break the caller."""

import logging
import time
from datetime import datetime, timedelta

import groq
from sqlalchemy import delete
from sqlalchemy.orm import Session

from backend import groq_client
from backend.db.models import AssistCall, utcnow

logger = logging.getLogger(__name__)

CALL_KINDS = ("chat", "judge", "insights")
OUTCOMES = ("ok", "error", "rate_limited", "timeout")
RETENTION_DAYS = 90


def classify_error(exc: BaseException) -> str:
    """The outcome label for a failed model call."""
    if isinstance(exc, groq.RateLimitError):
        return "rate_limited"
    if isinstance(exc, groq.APITimeoutError):
        return "timeout"
    return "error"


def record_call(
    db: Session,
    *,
    workspace_id: str,
    kind: str,
    model: str,
    started: float,
    outcome: str,
    prompt_version: int | None = None,
    attempts: int = 1,
    source: str = "live",
) -> None:
    """Store one model call. Never raises: a failure is logged and the session stays usable.

    `started` is a time.monotonic() value taken before the call; the stored latency is the whole time the
    caller waited (including provider retries). Token counts come from groq_client.last_usage, which the
    model call made in this context just set (None when that call failed or the provider sent no usage).

    `attempts` is the number of tries the caller made. The provider SDK retries invisibly inside one
    groq_chat call, so chat and insights always store 1; the judge stores its job's attempt count.
    Our own rate-limiter 429s are not provider calls and are not recorded.
    """
    try:
        latency_ms = max(0, int((time.monotonic() - started) * 1000))
        usage = groq_client.last_usage.get() or {}
        # The savepoint isolates the insert: if it fails only the insert is undone, the caller's session
        # keeps whatever else it holds and stays usable.
        with db.begin_nested():
            db.add(
                AssistCall(
                    workspace_id=workspace_id,
                    kind=kind,
                    model=model,
                    latency_ms=latency_ms,
                    outcome=outcome,
                    attempts=attempts,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    prompt_version=prompt_version,
                    source=source,
                )
            )
    except Exception:
        logger.warning("Recording the model-call telemetry failed", exc_info=True)
        return
    try:
        db.commit()
    except Exception:
        logger.warning("Committing the model-call telemetry failed", exc_info=True)
        try:
            db.rollback()
        except Exception:
            logger.warning("Rolling back after a telemetry failure failed", exc_info=True)


def purge_old_calls(db: Session, now: datetime | None = None, days: int = RETENTION_DAYS) -> int:
    """Delete calls older than `days` days; returns how many were deleted."""
    cutoff = (now or utcnow()) - timedelta(days=days)
    result = db.execute(delete(AssistCall).where(AssistCall.created_at < cutoff))
    db.commit()
    return result.rowcount or 0
