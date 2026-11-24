"""Model-call telemetry: token usage from groq_chat, AssistCall rows for chat and insights, retention, migration."""

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import groq
import httpx
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select, text

from backend import groq_client
from backend.db import telemetry
from backend.db.agents import create_agent, current_prompt_version
from backend.db.assist import add_question
from backend.db.models import AssistCall, AssistQuestion, Workspace, utcnow
from backend.db.session import Base
from backend.db.workspaces import create_workspace
from scripts import setup_assist

ROOT = Path(__file__).resolve().parent.parent
KEY = "test-assist-key-0123456789"
HEADERS = {"X-Assist-Key": KEY}
INSIGHTS_URL = "/assist/console/insights"


class FakeClient:
    """Stands in for backend.groq_client.client wholesale."""

    def __init__(self, reply="Claim 1 describes a widget.", error=None, usage=None):
        self.reply = reply
        self.error = error
        self.usage = usage
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, max_retries):
        return self

    def _create(self, **kwargs):
        if self.error is not None:
            raise self.error
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply), finish_reason="stop")]
        )
        if self.usage is not None:
            response.usage = self.usage
        return response


def _call():
    return groq_client.groq_chat("sys", [{"role": "user", "content": "hi"}])


# ---- token usage from groq_chat ---------------------------------------------


def test_groq_chat_exposes_usage(monkeypatch):
    usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18)
    monkeypatch.setattr(groq_client, "client", FakeClient(usage=usage))
    _call()
    assert groq_client.last_usage.get() == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}

    monkeypatch.setattr(groq_client, "client", FakeClient())  # no usage attribute at all
    _call()
    assert groq_client.last_usage.get() == {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}


def test_groq_chat_keeps_only_real_ints_as_usage(monkeypatch):
    usage = SimpleNamespace(prompt_tokens=True, completion_tokens="7", total_tokens=18)
    monkeypatch.setattr(groq_client, "client", FakeClient(usage=usage))
    _call()
    assert groq_client.last_usage.get() == {"prompt_tokens": None, "completion_tokens": None, "total_tokens": 18}


def test_failed_call_clears_usage(monkeypatch):
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3)
    monkeypatch.setattr(groq_client, "client", FakeClient(usage=usage))
    _call()
    assert groq_client.last_usage.get() is not None
    monkeypatch.setattr(groq_client, "client", FakeClient(error=RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        _call()
    assert groq_client.last_usage.get() is None


# ---- chat and insights record a call ----------------------------------------


@pytest.fixture(autouse=True)
def _assist_key(monkeypatch):
    monkeypatch.setenv("ASSIST_KEY", KEY)


@pytest.fixture
def assist_agent(db_session, monkeypatch):
    workspace, _ = create_workspace(db_session)
    agent = create_agent(
        db_session, workspace, setup_assist.ASSIST_CONFIG, setup_assist.ASSIST_ANALYSIS, kind="assist"
    )
    monkeypatch.setenv("ASSIST_AGENT_ID", agent.id)
    return agent


def chat_body(session_id="session-abcdef123"):
    return {
        "session_id": session_id,
        "brief": {
            "patent_number": "US10000000B2",
            "title": "Widget holder",
            "abstract": "A holder for widgets.",
            "claims": [
                {"number": 1, "text": "A widget holder comprising a base.", "depends_on": [], "independent": True}
            ],
            "completeness": {"claims": True, "description": True},
        },
        "history": [],
        "question": "Explain claim 1",
    }


def calls(db):
    return list(db.scalars(select(AssistCall).order_by(AssistCall.created_at)))


def test_chat_records_ok_call(client, db_session, assist_agent, monkeypatch):
    monkeypatch.setattr("backend.api.assist.groq_chat", lambda *a, **k: "Claim 1 describes a widget.")
    response = client.post("/assist/chat", json=chat_body(), headers=HEADERS)
    assert response.status_code == 200
    (row,) = calls(db_session)
    assert row.kind == "chat" and row.outcome == "ok" and row.source == "live"
    assert row.latency_ms >= 0 and row.attempts == 1
    assert row.workspace_id == assist_agent.workspace_id
    assert row.prompt_version == current_prompt_version(assist_agent).number
    assert row.model


def test_chat_records_token_usage_from_the_model_call(client, db_session, assist_agent, monkeypatch):
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120)
    monkeypatch.setattr(groq_client, "client", FakeClient(usage=usage))
    assert client.post("/assist/chat", json=chat_body(), headers=HEADERS).status_code == 200
    (row,) = calls(db_session)
    assert (row.prompt_tokens, row.completion_tokens, row.total_tokens) == (100, 20, 120)


@pytest.mark.parametrize(
    "error, outcome",
    [
        (
            groq.RateLimitError(
                "slow down",
                response=httpx.Response(429, request=httpx.Request("POST", "http://x")),
                body=None,
            ),
            "rate_limited",
        ),
        (groq.APITimeoutError(request=httpx.Request("POST", "http://x")), "timeout"),
        (RuntimeError("boom"), "error"),
    ],
)
def test_chat_records_rate_limited_and_timeout_and_error(client, db_session, assist_agent, monkeypatch, error, outcome):
    def failing(*args, **kwargs):
        raise error

    monkeypatch.setattr("backend.api.assist.groq_chat", failing)
    response = client.post("/assist/chat", json=chat_body(), headers=HEADERS)
    assert response.status_code == 502
    assert response.json() == {"detail": "The assistant could not answer right now. Please try again."}
    (row,) = calls(db_session)
    assert row.kind == "chat" and row.outcome == outcome
    assert row.total_tokens is None


def test_blank_reply_is_recorded_as_an_error(client, db_session, assist_agent, monkeypatch):
    monkeypatch.setattr("backend.api.assist.groq_chat", lambda *a, **k: "   ")
    assert client.post("/assist/chat", json=chat_body(), headers=HEADERS).status_code == 502
    (row,) = calls(db_session)
    assert row.outcome == "error"


def test_our_own_rate_limit_is_not_a_provider_call(client, db_session, assist_agent, monkeypatch):
    from backend.api import limits

    monkeypatch.setattr(limits, "global_llm_limiter", limits.RateLimiter(1, 60))
    monkeypatch.setattr("backend.api.assist.groq_chat", lambda *a, **k: "ok answer")
    assert client.post("/assist/chat", json=chat_body(), headers=HEADERS).status_code == 200
    assert client.post("/assist/chat", json=chat_body("session-second-1"), headers=HEADERS).status_code == 429
    assert len(calls(db_session)) == 1


def test_telemetry_failure_never_breaks_chat(client, db_session, assist_agent, monkeypatch):
    def broken_insert(*args, **kwargs):
        raise RuntimeError("telemetry insert failed")

    monkeypatch.setattr(telemetry, "AssistCall", broken_insert)
    monkeypatch.setattr("backend.api.assist.groq_chat", lambda *a, **k: "Claim 1 describes a widget.")
    response = client.post("/assist/chat", json=chat_body(), headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["answer"] == "Claim 1 describes a widget."
    assert len(list(db_session.scalars(select(AssistQuestion)))) == 1
    assert calls(db_session) == []


def test_telemetry_failure_leaves_the_session_usable(db_session, assist_agent, monkeypatch):
    def broken_insert(*args, **kwargs):
        raise RuntimeError("telemetry insert failed")

    with monkeypatch.context() as patch:
        patch.setattr(telemetry, "AssistCall", broken_insert)
        telemetry.record_call(
            db_session, workspace_id=assist_agent.workspace_id, kind="chat", model="m", started=0.0, outcome="ok"
        )
    assert db_session.scalar(select(Workspace.id)) == assist_agent.workspace_id
    telemetry.record_call(
        db_session, workspace_id=assist_agent.workspace_id, kind="chat", model="m", started=0.0, outcome="ok"
    )
    assert len(calls(db_session)) == 1


def test_a_failing_database_write_is_swallowed_and_logged(db_session, assist_agent, caplog):
    # An unknown workspace violates the foreign key only on engines that enforce it; a NULL kind always fails.
    with caplog.at_level("WARNING"):
        telemetry.record_call(
            db_session, workspace_id=assist_agent.workspace_id, kind=None, model="m", started=0.0, outcome="ok"
        )
    assert calls(db_session) == []
    assert db_session.scalar(select(Workspace.id)) == assist_agent.workspace_id
    assert any("telemetry" in r.getMessage().lower() for r in caplog.records)


def test_classify_error():
    request = httpx.Request("POST", "http://x")
    assert telemetry.classify_error(
        groq.RateLimitError("x", response=httpx.Response(429, request=request), body=None)
    ) == "rate_limited"
    assert telemetry.classify_error(groq.APITimeoutError(request=request)) == "timeout"
    assert telemetry.classify_error(RuntimeError("x")) == "error"
    assert telemetry.classify_error(ValueError("empty")) == "error"
    assert {"ok", "error", "rate_limited", "timeout"} == set(telemetry.OUTCOMES)
    assert {"chat", "judge", "insights"} == set(telemetry.CALL_KINDS)


def seed_insights_data(db):
    workspace, token = create_workspace(db)
    create_agent(db, workspace, setup_assist.ASSIST_CONFIG, setup_assist.ASSIST_ANALYSIS, kind="assist")
    add_question(db, workspace.id, "s-1", "US1B2", "Is claim 3 broad?", "claim_explain", "claims", "answer", True, "live", 1)
    return workspace, {"X-Workspace-Token": token}


def test_insights_records_call(client, db_session, monkeypatch):
    workspace, headers = seed_insights_data(db_session)
    usage = SimpleNamespace(prompt_tokens=50, completion_tokens=10, total_tokens=60)
    monkeypatch.setattr(groq_client, "client", FakeClient(reply="- 1 question was recorded.", usage=usage))
    assert client.post(INSIGHTS_URL, headers=headers).status_code == 200
    (row,) = calls(db_session)
    assert row.kind == "insights" and row.outcome == "ok" and row.workspace_id == workspace.id
    assert row.model == groq_client.MODEL_JUDGE and row.attempts == 1
    assert row.total_tokens == 60 and row.prompt_version is None and row.source == "live"


def test_insights_failure_is_recorded_with_its_outcome(client, db_session, monkeypatch):
    _, headers = seed_insights_data(db_session)
    monkeypatch.setattr(
        groq_client, "client",
        FakeClient(error=groq.APITimeoutError(request=httpx.Request("POST", "http://x"))),
    )
    assert client.post(INSIGHTS_URL, headers=headers).status_code == 502
    (row,) = calls(db_session)
    assert row.kind == "insights" and row.outcome == "timeout"


def test_insights_blank_reply_is_recorded_as_an_error(client, db_session, monkeypatch):
    _, headers = seed_insights_data(db_session)
    monkeypatch.setattr(groq_client, "client", FakeClient(reply="  "))
    assert client.post(INSIGHTS_URL, headers=headers).status_code == 502
    (row,) = calls(db_session)
    assert row.kind == "insights" and row.outcome == "error"


# ---- retention ---------------------------------------------------------------


def test_purge_old_calls_keeps_recent(db_session):
    workspace, _ = create_workspace(db_session)
    now = utcnow()
    for days in (89, 91):
        db_session.add(
            AssistCall(
                workspace_id=workspace.id, created_at=now - timedelta(days=days), kind="chat", model="m",
                latency_ms=1, outcome="ok", source="live",
            )
        )
    db_session.commit()
    assert telemetry.purge_old_calls(db_session, now=now) == 1
    (left,) = calls(db_session)
    assert left.created_at == now - timedelta(days=89)


def test_deleting_a_workspace_deletes_its_calls(db_session):
    workspace, _ = create_workspace(db_session)
    telemetry.record_call(
        db_session, workspace_id=workspace.id, kind="chat", model="m", started=0.0, outcome="ok"
    )
    assert len(calls(db_session)) == 1
    db_session.delete(workspace)
    db_session.commit()
    assert calls(db_session) == []


# ---- migration ---------------------------------------------------------------


def test_migration_0006_upgrades_and_downgrades(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'c.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    command.upgrade(cfg, "0005")
    engine = create_engine(url)
    try:
        assert "assist_call" not in inspect(engine).get_table_names()
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO workspace (id, token_hash, created_at, expires_at) "
                     "VALUES ('w', 'h', '2026-01-01', '2026-01-01')")
            )
        command.upgrade(cfg, "head")
        insp = inspect(engine)
        assert "assist_call" in insp.get_table_names()
        columns = {c["name"] for c in insp.get_columns("assist_call")}
        assert columns == {
            "id", "workspace_id", "created_at", "kind", "model", "latency_ms", "outcome", "attempts",
            "prompt_tokens", "completion_tokens", "total_tokens", "prompt_version", "source",
        }
        indexed = {tuple(i["column_names"]) for i in insp.get_indexes("assist_call")}
        assert ("created_at",) in indexed and ("workspace_id",) in indexed
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO assist_call (id, workspace_id, created_at, kind, model, latency_ms, outcome, "
                     "attempts, source) VALUES ('c', 'w', '2026-01-01', 'chat', 'm', 5, 'ok', 1, 'live')")
            )
        with engine.connect() as conn:
            assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []

        command.downgrade(cfg, "0005")
        assert "assist_call" not in inspect(engine).get_table_names()
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM workspace")).scalar() == 1
        command.upgrade(cfg, "head")
        assert "assist_call" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
