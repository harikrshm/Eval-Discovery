"""Request quotas and an in-memory sliding-window rate limiter for LLM-backed routes."""

import time
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import HTTPException, Request

from backend.core.config import MAX_PROMPT_CHARS  # noqa: F401  (single source; re-exported)

MAX_AGENTS_PER_WORKSPACE = 5
MAX_RUNS_PER_WORKSPACE = 50
MAX_MESSAGES_PER_REQUEST = 40
MAX_MESSAGE_CHARS = 2000


class RateLimiter:
    """Allow at most `max_calls` per `window_seconds` for each key (per process)."""

    def __init__(
        self,
        max_calls: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._clock = clock
        self._calls: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> bool:
        """Return True and record the call if allowed; return False (recording nothing) if not."""
        now = self._clock()
        calls = self._calls[key]
        while calls and now - calls[0] >= self.window_seconds:
            calls.popleft()
        if len(calls) >= self.max_calls:
            return False
        calls.append(now)
        return True


llm_limiter = RateLimiter(30, 60)


def rate_limit_llm(request: Request) -> None:
    """Raise 429 when the caller exceeds the LLM call budget.

    Plain function: works as a route dependency and when called inside a handler.
    """
    key = request.client.host if request.client else "unknown"
    if not llm_limiter.check(key):
        raise HTTPException(status_code=429, detail="Too many requests. Wait a minute, then try again.")
