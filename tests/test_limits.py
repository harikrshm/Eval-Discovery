from unittest.mock import patch

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
    assert limits.MAX_AGENTS_PER_WORKSPACE == config.MAX_AGENTS_PER_WORKSPACE == 5
    assert limits.MAX_RUNS_PER_WORKSPACE == 50
    assert limits.MAX_MESSAGES_PER_REQUEST == 40
    assert limits.MAX_MESSAGE_CHARS == 2000


def test_rate_limit_llm_raises_429_after_limit(monkeypatch):
    monkeypatch.setattr(limits, "llm_limiter", RateLimiter(2, 60, clock=FakeClock()))
    request = _req(host="1.2.3.4")
    rate_limit_llm(request)
    rate_limit_llm(request)
    with pytest.raises(HTTPException) as exc:
        rate_limit_llm(request)
    assert exc.value.status_code == 429
    assert exc.value.detail == "Too many requests. Wait a minute, then try again."


def test_rate_limit_llm_keys_by_client_host(monkeypatch):
    monkeypatch.setattr(limits, "llm_limiter", RateLimiter(1, 60, clock=FakeClock()))
    rate_limit_llm(_req(host="1.1.1.1"))
    rate_limit_llm(_req(host="2.2.2.2"))


def test_rate_limit_llm_handles_missing_client(monkeypatch):
    monkeypatch.setattr(limits, "llm_limiter", RateLimiter(1, 60, clock=FakeClock()))
    request = SimpleNamespace(client=None, headers={})
    rate_limit_llm(request)
    with pytest.raises(HTTPException):
        rate_limit_llm(request)


def test_call_exactly_window_old_is_outside_the_window():
    clock = FakeClock()
    limiter = RateLimiter(1, 60, clock=clock)
    assert limiter.check("a")
    clock.now = 59.999
    assert not limiter.check("a")
    clock.now = 60
    assert limiter.check("a")


def test_zero_max_calls_always_rejects_and_stores_nothing():
    limiter = RateLimiter(0, 60, clock=FakeClock())
    assert [limiter.check("a") for _ in range(3)] == [False, False, False]
    assert limiter._calls == {}


def test_empty_string_key_works():
    limiter = RateLimiter(1, 60, clock=FakeClock())
    assert limiter.check("")
    assert not limiter.check("")
    assert limiter.check("x")


def test_expired_keys_are_pruned_by_sweep():
    clock = FakeClock()
    limiter = RateLimiter(2, 60, clock=clock, sweep_every=5)
    for i in range(4):
        assert limiter.check(f"ip-{i}")
    assert len(limiter._calls) == 4
    clock.now = 120  # every recorded call has expired
    assert limiter.check("fresh")  # 5th check triggers the sweep
    assert set(limiter._calls) == {"fresh"}


def test_reset_clears_all_state():
    limiter = RateLimiter(1, 60, clock=FakeClock())
    assert limiter.check("a")
    assert not limiter.check("a")
    limiter.reset()
    assert limiter._calls == {}
    assert limiter.check("a")


def test_threaded_checks_never_exceed_max_calls():
    import threading

    limiter = RateLimiter(10, 60, clock=FakeClock())
    results: list[bool] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker():
        try:
            barrier.wait()
            local = [limiter.check("shared") for _ in range(50)]
            results.extend(local)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(results) == 400
    assert sum(results) == 10


def _req(host="9.9.9.9", xff=None):
    headers = {} if xff is None else {"x-forwarded-for": xff}
    return SimpleNamespace(client=SimpleNamespace(host=host), headers=headers)


def test_client_key_uses_rightmost_forwarded_entry_by_default(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    assert limits.client_key(_req(xff="6.6.6.6, 1.2.3.4")) == "1.2.3.4"
    assert limits.client_key(_req(xff="1.2.3.4")) == "1.2.3.4"


def test_client_key_hops_counts_from_the_right(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    assert limits.client_key(_req(xff="6.6.6.6, 1.2.3.4, 10.0.0.1")) == "1.2.3.4"


def test_client_key_zero_hops_ignores_header(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "0")
    assert limits.client_key(_req(host="9.9.9.9", xff="1.2.3.4")) == "9.9.9.9"


def test_client_key_falls_back_to_client_host(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    assert limits.client_key(_req(host="9.9.9.9")) == "9.9.9.9"
    assert limits.client_key(_req(host="9.9.9.9", xff="")) == "9.9.9.9"
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "3")
    assert limits.client_key(_req(host="9.9.9.9", xff="1.2.3.4, 5.6.7.8")) == "9.9.9.9"
    assert limits.client_key(SimpleNamespace(client=None, headers={})) == "unknown"


def test_client_key_bad_hops_value_uses_default(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "lots")
    assert limits.client_key(_req(xff="6.6.6.6, 1.2.3.4")) == "1.2.3.4"


def test_forged_leftmost_xff_shares_one_bucket(client, auth_headers, monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    agent = client.post("/agents", json={"preset_id": "support"}, headers=auth_headers).json()
    url = f"/agents/{agent['id']}/chat"
    body = {"messages": [{"role": "user", "content": "hi"}]}
    statuses = []
    with patch("backend.api.agents.groq_chat", return_value="ok"):
        for i in range(31):
            headers = {**auth_headers, "X-Forwarded-For": f"10.0.{i}.1, 203.0.113.7"}
            statuses.append(client.post(url, json=body, headers=headers).status_code)
    assert statuses[:30] == [200] * 30
    assert statuses[30] == 429


def test_workspace_creation_is_rate_limited_per_ip(client, monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    codes = [client.post("/workspaces").status_code for _ in range(21)]
    assert codes[:20] == [201] * 20
    assert codes[20] == 429
    other = client.post("/workspaces", headers={"X-Forwarded-For": "198.51.100.2"})
    assert other.status_code == 201


def test_global_llm_backstop_trips_across_ips(monkeypatch):
    monkeypatch.setattr(limits, "llm_limiter", RateLimiter(100, 60, clock=FakeClock()))
    monkeypatch.setattr(limits, "global_llm_limiter", RateLimiter(3, 60, clock=FakeClock()))
    for i in range(3):
        rate_limit_llm(_req(host=f"1.1.1.{i}"))
    with pytest.raises(HTTPException) as exc:
        rate_limit_llm(_req(host="1.1.1.99"))
    assert exc.value.status_code == 429


def test_global_budget_is_env_overridable(monkeypatch):
    monkeypatch.setenv("LLM_GLOBAL_PER_MINUTE", "7")
    assert limits.make_global_limiter().max_calls == 7
    monkeypatch.setenv("LLM_GLOBAL_PER_MINUTE", "junk")
    assert limits.make_global_limiter().max_calls == 600
