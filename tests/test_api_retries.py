"""Synchronous request routes bound the SDK's retries (2); job handlers keep the client default."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend import groq_client
from backend.api import limits
from backend.core.config import AgentAnalysis, Behavior
from backend.core.criteria import CriterionDraft, CriterionGenerationFailed
from backend.core.judge import JudgeFailed
from backend.core.pattern import PatternCheckFailed, PatternResult
from backend.db.models import Agent, Criterion, PromptVersion, Run, Workspace, utcnow
from backend.groq_client import groq_chat
from backend.jobs import handlers
from backend.jobs.queue import enqueue


class RecordingClient:
    """Stands in for backend.groq_client.client wholesale; with_options records n and returns itself."""

    def __init__(self):
        self.retries: list[int] = []
        self.creates = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, max_retries):
        self.retries.append(max_retries)
        return self

    def _create(self, **kwargs):
        self.creates += 1
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="canned reply"))])


@pytest.fixture
def fake(monkeypatch):
    rec = RecordingClient()
    monkeypatch.setattr(groq_client, "client", rec)
    return rec


@pytest.fixture(autouse=True)
def _reset_llm_limiter():
    limits.llm_limiter.reset()
    yield
    limits.llm_limiter.reset()


ANALYSIS = AgentAnalysis(
    user_role_label="Customer",
    behaviors=(
        Behavior("Refunds", "No refunds.", ("refund",)),
        Behavior("Escalation", "Offers human.", ("manager",)),
        Behavior("Security", "No passwords.", ("password",)),
    ),
    starter_messages=("one", "two", "three"),
)
DRAFT = CriterionDraft(name="Stays polite", definition="d", good_example="g", bad_example="b", why_discovered="w")


def _preset_agent(client, auth_headers):
    return client.post("/agents", json={"preset_id": "alex-sales"}, headers=auth_headers).json()["id"]


def _calling_groq(result=None, error=None):
    """A stand-in for a pure core function: makes exactly one groq_chat call, then returns or raises."""

    def run(*args, **kwargs):
        groq_chat("sys", [{"role": "user", "content": "hi"}])
        if error is not None:
            raise error
        return result

    return run


def test_chat_route_bounds_retries(client, auth_headers, fake):
    agent_id = _preset_agent(client, auth_headers)
    r = client.post(
        f"/agents/{agent_id}/chat",
        json={"messages": [{"role": "user", "content": "hi"}]},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["reply"] == "canned reply"
    assert fake.retries == [2]
    assert fake.creates == 1


def test_create_agent_route_bounds_retries(client, auth_headers, fake, monkeypatch):
    monkeypatch.setattr("backend.api.agents.analyze_agent", _calling_groq(ANALYSIS))
    r = client.post("/agents", json={"name": "Bot", "system_prompt": "You help."}, headers=auth_headers)
    assert r.status_code == 201
    assert fake.retries == [2]


def test_discover_route_bounds_retries(client, auth_headers, db_session, fake, monkeypatch):
    agent_id = _preset_agent(client, auth_headers)
    db_session.get(Agent, agent_id).pattern_summary = "You keep asking about price."
    db_session.commit()
    monkeypatch.setattr("backend.api.criteria.generate_criterion", _calling_groq(DRAFT))
    r = client.post(f"/agents/{agent_id}/criteria/discover", headers=auth_headers)
    assert r.status_code == 201
    assert fake.retries == [2]


def test_check_pattern_route_bounds_retries(client, auth_headers, fake, monkeypatch):
    agent_id = _preset_agent(client, auth_headers)
    msgs = [
        {"role": "user", "content": "what is the price?"},
        {"role": "assistant", "content": "Let me book a demo."},
        {"role": "user", "content": "ok thanks"},
    ]
    assert client.post(f"/agents/{agent_id}/runs", json={"messages": msgs}, headers=auth_headers).status_code == 201
    monkeypatch.setattr(
        "backend.api.runs.detect_pattern", _calling_groq(PatternResult(found=False, pattern_summary=""))
    )
    r = client.post(f"/agents/{agent_id}/check-pattern", headers=auth_headers)
    assert r.status_code == 200
    assert fake.retries == [2]


def test_limit_is_restored_when_a_route_fails(client, auth_headers, db_session, fake, monkeypatch):
    agent_id = _preset_agent(client, auth_headers)
    db_session.get(Agent, agent_id).pattern_summary = "You keep asking about price."
    db_session.commit()
    monkeypatch.setattr(
        "backend.api.criteria.generate_criterion", _calling_groq(error=CriterionGenerationFailed("boom"))
    )
    r = client.post(f"/agents/{agent_id}/criteria/discover", headers=auth_headers)
    assert r.status_code == 502
    assert fake.retries == [2]
    assert groq_client.request_retry_limit.get() is None

    # A later call outside any request block must not inherit the limit.
    groq_chat("sys", [{"role": "user", "content": "hi"}])
    assert fake.retries == [2]


def test_limit_is_restored_when_check_pattern_fails(client, auth_headers, fake, monkeypatch):
    agent_id = _preset_agent(client, auth_headers)
    msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}, {"role": "user", "content": "b"}]
    client.post(f"/agents/{agent_id}/runs", json={"messages": msgs}, headers=auth_headers)
    monkeypatch.setattr("backend.api.runs.detect_pattern", _calling_groq(error=PatternCheckFailed("boom")))
    assert client.post(f"/agents/{agent_id}/check-pattern", headers=auth_headers).status_code == 502
    assert groq_client.request_retry_limit.get() is None


def test_job_handler_path_does_not_bound_retries(db_session, fake, monkeypatch):
    ws = Workspace(token_hash="h", expires_at=utcnow())
    db_session.add(ws)
    db_session.flush()
    agent = Agent(workspace_id=ws.id, name="Alex", user_role_label="Customer")
    db_session.add(agent)
    db_session.flush()
    pv = PromptVersion(agent_id=agent.id, text="be nice", origin="initial", status="promoted")
    db_session.add(pv)
    db_session.flush()
    agent.current_prompt_version_id = pv.id
    crit = Criterion(
        agent_id=agent.id, position=1, name="Stays polite", definition="d",
        good_example="g", bad_example="b", why_discovered="w", source="discovered",
    )
    db_session.add(crit)
    db_session.add(
        Run(agent_id=agent.id, prompt_version_id=pv.id, position=1,
            messages=[{"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}])
    )
    db_session.commit()

    def sanity_via_groq(*args, **kwargs):
        groq_chat("sys", [{"role": "user", "content": "hi"}])
        raise JudgeFailed("stop here")

    monkeypatch.setattr(handlers, "sanity_check", sanity_via_groq)
    monkeypatch.setattr(handlers, "judge", MagicMock(side_effect=JudgeFailed("stop here")))
    job = enqueue(db_session, ws.id, "build_tests", {"criterion_id": crit.id})

    try:
        handlers.handle_build_tests(db_session, job)
    except Exception:
        pass  # only the retry bookkeeping matters here

    assert fake.creates >= 1
    assert fake.retries == []
