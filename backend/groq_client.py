import os
import re
import threading
import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar

from groq import Groq
from dotenv import load_dotenv

# Tests (and any caller that already has its environment) set GROQ_SKIP_DOTENV=1 to never read a .env file.
if not os.getenv("GROQ_SKIP_DOTENV"):
    load_dotenv()

MODEL_CHAT    = "openai/gpt-oss-20b"
MODEL_PATTERN = "openai/gpt-oss-120b"
MODEL_CRITERIA = "qwen/qwen3.8-27b"
MODEL_JUDGE = "openai/gpt-oss-120b"

_api_key = os.getenv("GROQ_API_KEY") or "sk-test-placeholder"
# Bound every call: the SDK defaults (10 min timeout) could hold the single job worker for minutes.
# Five retries ride out the free tier's per-minute token limit on the 120B model (the SDK backs off
# and honours Retry-After) without failing a verification on one 429.
GROQ_TIMEOUT_SECONDS = 45.0
GROQ_MAX_RETRIES = 5


def _make_client(api_key: str) -> Groq:
    return Groq(api_key=api_key, timeout=GROQ_TIMEOUT_SECONDS, max_retries=GROQ_MAX_RETRIES)


client = _make_client(_api_key)

# Synchronous request routes cannot afford five backed-off retries; they set a lower bound for their block.
request_retry_limit: ContextVar[int | None] = ContextVar("groq_request_retry_limit", default=None)


@contextmanager
def request_retries(n: int):
    """Bound the SDK's retries for groq_chat calls made inside the block; restored on exit."""
    token = request_retry_limit.set(n)
    try:
        yield
    finally:
        request_retry_limit.reset(token)


# The finish_reason of the last groq_chat call made in this context ("length" = the reply hit max_tokens).
last_finish_reason: ContextVar[str | None] = ContextVar("groq_last_finish_reason", default=None)

# Token usage of the last groq_chat call made in this context: {"prompt_tokens", "completion_tokens",
# "total_tokens"}, each a real int or None. None as a whole = no successful call yet (reset at the start of every
# call, so a failed call never leaves the previous call's usage behind).
last_usage: ContextVar[dict | None] = ContextVar("groq_last_usage", default=None)


def _usage_int(usage, name: str) -> int | None:
    value = getattr(usage, name, None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class TokenPacer:
    """Optional per-model sliding-window token pacing. A call waits at most max_wait_seconds, then proceeds."""

    def __init__(self, limit: int, window_seconds: float = 60.0, max_wait_seconds: float = 20.0,
                 clock=time.monotonic, sleep=time.sleep):
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_wait_seconds = max_wait_seconds
        self._clock = clock
        self._sleep = sleep
        self._records: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _prune(self, model: str, now: float) -> deque:
        records = self._records.setdefault(model, deque())
        while records and records[0][0] <= now - self.window_seconds:
            records.popleft()
        return records

    def acquire(self, model: str, estimate: int) -> None:
        with self._lock:
            now = self._clock()
            records = self._prune(model, now)
            used = sum(tokens for _, tokens in records)
            if not records or used + estimate <= self.limit:
                return
            wait = min(records[0][0] + self.window_seconds - now, self.max_wait_seconds)
        if wait > 0:
            self._sleep(wait)

    def record(self, model: str, tokens: int) -> None:
        with self._lock:
            now = self._clock()
            self._prune(model, now).append((now, tokens))


def _pacer_from_env() -> "TokenPacer | None":
    raw = (os.getenv("GROQ_TPM_LIMIT") or "").strip()
    try:
        limit = int(raw)
    except ValueError:
        return None
    return TokenPacer(limit) if limit > 0 else None


_pacer: "TokenPacer | None" = _pacer_from_env()


def configure_pacing(limit: int | None, *, clock=None, sleep=None) -> None:
    """Enable pacing with the given per-model tokens-per-minute limit, or disable it with None."""
    global _pacer
    if limit is None:
        _pacer = None
        return
    kwargs = {}
    if clock is not None:
        kwargs["clock"] = clock
    if sleep is not None:
        kwargs["sleep"] = sleep
    _pacer = TokenPacer(limit, **kwargs)


def _estimate_tokens(system: str, messages: list[dict], max_tokens: int | None) -> int:
    chars = len(system) + sum(len(str(m.get("content") or "")) for m in messages)
    return chars // 4 + (max_tokens if max_tokens is not None else 1000)


def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks that reasoning models may leak into responses."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def groq_chat(
    system: str,
    messages: list[dict],
    model: str = MODEL_CHAT,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> str:
    """Send messages to Groq and return the assistant reply text, stripped of thinking blocks.

    Side channels (ContextVars): last_finish_reason, and last_usage (reset to None here, set on success).
    """
    last_usage.set(None)
    full_messages = [{"role": "system", "content": system}] + messages
    kwargs = {"model": model, "messages": full_messages}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if temperature is not None:
        kwargs["temperature"] = temperature
    if model == MODEL_CRITERIA:
        kwargs["reasoning_format"] = "hidden"
    active = client
    retries = request_retry_limit.get()
    if retries is not None and hasattr(client, "with_options"):
        active = client.with_options(max_retries=retries)
    pacer = _pacer
    estimate = _estimate_tokens(system, messages, max_tokens)
    if pacer is not None:
        pacer.acquire(model, estimate)
    response = active.chat.completions.create(**kwargs)
    if pacer is not None:
        total = getattr(getattr(response, "usage", None), "total_tokens", None)
        pacer.record(model, total if isinstance(total, int) and not isinstance(total, bool) else estimate)
    usage = getattr(response, "usage", None)
    last_usage.set({name: _usage_int(usage, name) for name in ("prompt_tokens", "completion_tokens", "total_tokens")})
    reason = getattr(response.choices[0], "finish_reason", None)
    last_finish_reason.set(reason if isinstance(reason, str) else None)
    return _strip_thinking(response.choices[0].message.content)
