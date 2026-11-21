from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import limits
from backend.api.limits import RateLimiter, rate_limit_llm
from backend.core import config


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_allows_max_calls_then_blocks():
    limiter = RateLimiter(3, 60, clock=FakeClock())
    assert [limiter.check("a") for _ in range(4)] == [True, True, True, False]


def test_allows_again_after_window():
    clock = FakeClock()
    limiter = RateLimiter(2, 60, clock=clock)
    assert limiter.check("a") and limiter.check("a")
    assert not limiter.check("a")
    clock.now = 61
    assert limiter.check("a")


def test_blocked_calls_are_not_recorded():
    clock = FakeClock()
    limiter = RateLimiter(1, 60, clock=clock)
    assert limiter.check("a")
    clock.now = 30
    assert not limiter.check("a")
    clock.now = 61  # only the first (allowed) call counts; it has expired
    assert limiter.check("a")


def test_keys_are_independent():
    limiter = RateLimiter(1, 60, clock=FakeClock())
    assert limiter.check("a")
    assert not limiter.check("a")
    assert limiter.check("b")


def test_constants():
    assert limits.MAX_PROMPT_CHARS == config.MAX_PROMPT_CHARS
    assert limits.MAX_AGENTS_PER_WORKSPACE == 5
    assert limits.MAX_RUNS_PER_WORKSPACE == 50
    assert limits.MAX_MESSAGES_PER_REQUEST == 40
    assert limits.MAX_MESSAGE_CHARS == 2000


def test_rate_limit_llm_raises_429_after_limit(monkeypatch):
    monkeypatch.setattr(limits, "llm_limiter", RateLimiter(2, 60, clock=FakeClock()))
    request = SimpleNamespace(client=SimpleNamespace(host="1.2.3.4"))
    rate_limit_llm(request)
    rate_limit_llm(request)
    with pytest.raises(HTTPException) as exc:
        rate_limit_llm(request)
    assert exc.value.status_code == 429
    assert exc.value.detail == "Too many requests. Wait a minute, then try again."


def test_rate_limit_llm_keys_by_client_host(monkeypatch):
    monkeypatch.setattr(limits, "llm_limiter", RateLimiter(1, 60, clock=FakeClock()))
    rate_limit_llm(SimpleNamespace(client=SimpleNamespace(host="1.1.1.1")))
    rate_limit_llm(SimpleNamespace(client=SimpleNamespace(host="2.2.2.2")))


def test_rate_limit_llm_handles_missing_client(monkeypatch):
    monkeypatch.setattr(limits, "llm_limiter", RateLimiter(1, 60, clock=FakeClock()))
    request = SimpleNamespace(client=None)
    rate_limit_llm(request)
    with pytest.raises(HTTPException):
        rate_limit_llm(request)
