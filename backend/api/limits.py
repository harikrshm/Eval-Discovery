"""Request quotas and an in-memory sliding-window rate limiter for LLM-backed routes."""

import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import HTTPException, Request

from backend.core.config import (  # noqa: F401  (single source; re-exported)
    MAX_AGENTS_PER_WORKSPACE,
    MAX_PROMPT_CHARS,
)

MAX_RUNS_PER_WORKSPACE = 50
MAX_MESSAGES_PER_REQUEST = 40
MAX_MESSAGE_CHARS = 2000  # user-authored messages
MAX_ASSISTANT_MESSAGE_CHARS = 16000  # model replies, which the user cannot shorten
MAX_REQUEST_CHARS = 120000  # sum over all messages in one request
MAX_CRITERIA_PER_AGENT = 30
MAX_PATTERN_RUNS = 10


class RateLimiter:
    """Allow at most `max_calls` per `window_seconds` for each key (per process).

    Thread-safe. Keys whose calls have all expired are dropped, so memory stays bounded
    by the number of keys active within one window (swept every `sweep_every` checks).
    """

    def __init__(
        self,
        max_calls: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sweep_every: int = 256,
    ) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._clock = clock
        self._sweep_every = sweep_every
        self._lock = threading.Lock()
        self._calls: dict[str, deque[float]] = {}
        self._checks = 0

    def _expire(self, calls: deque[float], now: float) -> None:
        while calls and now - calls[0] >= self.window_seconds:
            calls.popleft()

    def _sweep(self, now: float) -> None:
        for key in list(self._calls):
            calls = self._calls[key]
            self._expire(calls, now)
            if not calls:
                del self._calls[key]

    def check(self, key: str) -> bool:
        """Return True and record the call if allowed; return False (recording nothing) if not."""
        with self._lock:
            now = self._clock()
            self._checks += 1
            if self._checks % self._sweep_every == 0:
                self._sweep(now)
            calls = self._calls.get(key)
            if calls is not None:
                self._expire(calls, now)
                if not calls:
                    del self._calls[key]
                    calls = None
            if len(calls or ()) >= self.max_calls:
                return False
            if calls is None:
                calls = self._calls[key] = deque()
            calls.append(now)
            return True

    def reset(self) -> None:
        """Forget all recorded calls."""
        with self._lock:
            self._calls.clear()
            self._checks = 0


llm_limiter = RateLimiter(30, 60)


def rate_limit_llm(request: Request) -> None:
    """Raise 429 when the caller exceeds the LLM call budget.

    Plain function: works as a route dependency and when called inside a handler.
    """
    key = request.client.host if request.client else "unknown"
    if not llm_limiter.check(key):
        raise HTTPException(status_code=429, detail="Too many requests. Wait a minute, then try again.")
