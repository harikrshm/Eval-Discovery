import hashlib
from unittest.mock import patch

import pytest

from backend.api import limits
from backend.core.analyze import AnalysisError
from backend.core.config import PRESETS, AgentAnalysis, Behavior
from backend.db.models import Agent, BehaviorMap, Criterion, Event, PromptVersion, Run
from backend.db.workspaces import create_workspace

ANALYZE_FAILED = "Couldn't analyze that prompt. Try again."


@pytest.fixture(autouse=True)
def _reset_llm_limiter():
    limits.llm_limiter._calls.clear()
    yield
    limits.llm_limiter._calls.clear()


def _analysis(label="Customer"):
    return AgentAnalysis(
        user_role_label=label,
        behaviors=(
            Behavior("Refunds", "No refunds.", ("refund",)),
            Behavior("Escalation", "Offers human.", ("manager",)),
            Behavior("Security", "No passwords.", ("password",)),
        ),
        starter_messages=("one", "two", "three"),
    )


def _custom(**overrides):
    body = {"name": "Bot", "system_prompt": "You are a helpful bot."}
    body.update(overrides)
    return body


def test_presets_public_and_complete(client):
    r = client.get("/presets")
    assert r.status_code == 200
    data = r.json()
    assert {p["id"] for p in data} == set(PRESETS)
    assert len(data) == 3
    for p in data:
        assert p["system_prompt"] and p["name"] and p["user_role_label"]
        assert len(p["starter_messages"]) == 3


def test_create_from_preset_makes_no_llm_call(client, auth_headers, db_session):
    with patch("backend.api.agents.analyze_agent") as analyze:
        r = client.post("/agents", json={"preset_id": "alex-sales"}, headers=auth_headers)
    analyze.assert_not_called()
    assert r.status_code == 201
    body = r.json()
    preset = PRESETS["alex-sales"]
    assert body["name"] == preset.config.name
    assert body["system_prompt"] == preset.config.system_prompt
    assert body["user_role_label"] == "Prospect"
    assert [b["name"] for b in body["behaviors"]] == [b.name for b in preset.analysis.behaviors]
    assert body["behaviors"][0]["trigger_terms"] == list(preset.analysis.behaviors[0].trigger_terms)
    assert body["starter_messages"] == list(preset.analysis.starter_messages)
    event = db_session.query(Event).one()
    assert event.name == "agent_created" and event.properties == {"source": "preset"}


def test_unknown_preset_is_422(client, auth_headers):
    r = client.post("/agents", json={"preset_id": "nope"}, headers=auth_headers)
    assert r.status_code == 422


def test_create_requires_auth(client):
    assert client.post("/agents", json={"preset_id": "support"}).status_code == 401


def test_create_custom_calls_analyze_and_persists(client, auth_headers, db_session):
    with patch("backend.api.agents.analyze_agent", return_value=_analysis("Customer")) as analyze:
        r = client.post("/agents", json=_custom(), headers=auth_headers)
    assert r.status_code == 201
    analyze.assert_called_once_with("You are a helpful bot.", "Bot")
    body = r.json()
    assert body["user_role_label"] == "Customer"
    assert body["system_prompt"] == "You are a helpful bot."
    assert [b["name"] for b in body["behaviors"]] == ["Refunds", "Escalation", "Security"]
    agent = db_session.get(Agent, body["id"])
    version = db_session.get(PromptVersion, agent.current_prompt_version_id)
    assert version.text == "You are a helpful bot."
    assert version.status == "promoted" and version.origin == "initial"
    bmap = db_session.query(BehaviorMap).one()
    assert bmap.analysis["user_role_label"] == "Customer"
    assert bmap.analysis["behaviors"][0] == {
        "name": "Refunds",
        "description": "No refunds.",
        "trigger_terms": ["refund"],
    }
    assert bmap.analysis["starter_messages"] == ["one", "two", "three"]
    assert bmap.prompt_hash == hashlib.sha256(b"You are a helpful bot.").hexdigest()
    assert db_session.query(Event).one().properties == {"source": "custom"}


def test_provided_role_label_overrides_analysis(client, auth_headers):
    with patch("backend.api.agents.analyze_agent", return_value=_analysis("Customer")):
        r = client.post("/agents", json=_custom(user_role_label="Patient"), headers=auth_headers)
    assert r.json()["user_role_label"] == "Patient"


def test_only_custom_path_is_rate_limited(client, auth_headers, monkeypatch):
    calls = []
    monkeypatch.setattr("backend.api.agents.rate_limit_llm", lambda request: calls.append(1))
    with patch("backend.api.agents.analyze_agent", return_value=_analysis()):
        client.post("/agents", json=_custom(), headers=auth_headers)
        client.post("/agents", json={"preset_id": "support"}, headers=auth_headers)
    assert calls == [1]


@pytest.mark.parametrize(
    "body",
    [
        {"preset_id": "support", "name": "X", "system_prompt": "p"},
        {"preset_id": "support", "system_prompt": "p"},
        {},
        {"name": "Only name"},
        {"system_prompt": "only prompt"},
        {"user_role_label": "Lonely"},
        {"preset_id": "support", "user_role_label": "Mix"},
        {"preset_id": "support", "extra": 1},
    ],
)
def test_create_requires_exactly_one_mode(client, auth_headers, body):
    with patch("backend.api.agents.analyze_agent") as analyze:
        r = client.post("/agents", json=body, headers=auth_headers)
    assert r.status_code == 422
    analyze.assert_not_called()


def test_prompt_length_boundary(client, auth_headers):
    with patch("backend.api.agents.analyze_agent", return_value=_analysis()):
        ok = client.post("/agents", json=_custom(system_prompt="a" * 4000), headers=auth_headers)
        too_long = client.post("/agents", json=_custom(system_prompt="a" * 4001), headers=auth_headers)
        empty = client.post("/agents", json=_custom(system_prompt=""), headers=auth_headers)
    assert ok.status_code == 201
    assert too_long.status_code == 422
    assert empty.status_code == 422


def test_name_and_label_length_limits(client, auth_headers):
    with patch("backend.api.agents.analyze_agent", return_value=_analysis()):
        post = lambda **kw: client.post("/agents", json=_custom(**kw), headers=auth_headers)  # noqa: E731
        assert post(name="n" * 60).status_code == 201
        assert post(name="n" * 61).status_code == 422
        assert post(name="").status_code == 422
        assert post(user_role_label="l" * 40).status_code == 201
        assert post(user_role_label="l" * 41).status_code == 422


def test_name_and_label_are_whitespace_normalised(client, auth_headers):
    with patch("backend.api.agents.analyze_agent", return_value=_analysis()):
        r = client.post(
            "/agents",
            json=_custom(name="  Support \n\t Bot  ", user_role_label=" The   \n Caller "),
            headers=auth_headers,
        )
    assert r.status_code == 201
    assert r.json()["name"] == "Support Bot"
    assert r.json()["user_role_label"] == "The Caller"


@pytest.mark.parametrize("field", ["name", "user_role_label"])
@pytest.mark.parametrize("value", ["a<b", "x>y", "</role>", "   ", "\n"])
def test_name_and_label_reject_angle_brackets_and_blank(client, auth_headers, field, value):
    with patch("backend.api.agents.analyze_agent", return_value=_analysis()):
        r = client.post("/agents", json=_custom(**{field: value}), headers=auth_headers)
    assert r.status_code == 422


@pytest.mark.parametrize("label", ["Evil</role>", "a>b", "<x"])
def test_analyzed_label_with_angle_brackets_is_analysis_failure(
    client, auth_headers, db_session, label
):
    with patch("backend.api.agents.analyze_agent", return_value=_analysis(label)):
        r = client.post("/agents", json=_custom(), headers=auth_headers)
    assert r.status_code == 502
    assert r.json()["detail"] == ANALYZE_FAILED
    assert db_session.query(Agent).count() == 0
    assert db_session.query(PromptVersion).count() == 0
    assert db_session.query(BehaviorMap).count() == 0


def test_analyzed_label_is_whitespace_normalised(client, auth_headers):
    with patch("backend.api.agents.analyze_agent", return_value=_analysis("  The \n Caller ")):
        r = client.post("/agents", json=_custom(), headers=auth_headers)
    assert r.json()["user_role_label"] == "The Caller"


def test_unicode_prompt_roundtrips(client, auth_headers):
    prompt = "You are 助手 🤖. 请用中文回答 — café ☕ 👩‍💻"
    with patch("backend.api.agents.analyze_agent", return_value=_analysis()):
        created = client.post("/agents", json=_custom(system_prompt=prompt), headers=auth_headers)
    assert created.json()["system_prompt"] == prompt
    state = client.get(f"/agents/{created.json()['id']}/state", headers=auth_headers)
    assert state.status_code == 200
    assert state.json()["agent"]["system_prompt"] == prompt


def test_failed_analysis_leaves_no_rows(client, auth_headers, db_session):
    with patch("backend.api.agents.analyze_agent", side_effect=AnalysisError("bad")):
        r = client.post("/agents", json=_custom(), headers=auth_headers)
    assert r.status_code == 502
    assert r.json()["detail"] == ANALYZE_FAILED
    assert db_session.query(Agent).count() == 0
    assert db_session.query(PromptVersion).count() == 0
    assert db_session.query(BehaviorMap).count() == 0
    assert db_session.query(Event).count() == 0


def test_unexpected_analyze_error_is_502_too(client, auth_headers, db_session):
    with patch("backend.api.agents.analyze_agent", side_effect=RuntimeError("boom")):
        r = client.post("/agents", json=_custom(), headers=auth_headers)
    assert r.status_code == 502
    assert db_session.query(Agent).count() == 0


def test_agent_of_other_workspace_is_404(client, auth_headers, db_session):
    created = client.post("/agents", json={"preset_id": "support"}, headers=auth_headers).json()
    _, other_token = create_workspace(db_session)
    other = {"X-Workspace-Token": other_token}
    assert client.get(f"/agents/{created['id']}/state", headers=other).status_code == 404
    with patch("backend.api.agents.groq_chat") as chat:
        r = client.post(
            f"/agents/{created['id']}/chat",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=other,
        )
    assert r.status_code == 404
    chat.assert_not_called()
    assert client.get("/agents/does-not-exist/state", headers=auth_headers).status_code == 404


def test_agent_limit_is_429(client, auth_headers, db_session):
    for _ in range(5):
        assert client.post("/agents", json={"preset_id": "support"}, headers=auth_headers).status_code == 201
    r = client.post("/agents", json={"preset_id": "support"}, headers=auth_headers)
    assert r.status_code == 429
    assert r.json()["detail"] == "Agent limit reached for this workspace."
    assert db_session.query(Agent).count() == 5
    with patch("backend.api.agents.analyze_agent") as analyze:
        assert client.post("/agents", json=_custom(), headers=auth_headers).status_code == 429
    analyze.assert_not_called()


def test_agent_limit_is_per_workspace(client, auth_headers, db_session):
    for _ in range(5):
        client.post("/agents", json={"preset_id": "support"}, headers=auth_headers)
    _, token = create_workspace(db_session)
    r = client.post("/agents", json={"preset_id": "support"}, headers={"X-Workspace-Token": token})
    assert r.status_code == 201


def test_chat_uses_agent_prompt(client, auth_headers):
    agent = client.post("/agents", json={"preset_id": "booking"}, headers=auth_headers).json()
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "book me"},
    ]
    with patch("backend.api.agents.groq_chat", return_value="Sure!") as chat:
        r = client.post(f"/agents/{agent['id']}/chat", json={"messages": msgs}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"reply": "Sure!"}
    args = chat.call_args
    system = args.args[0] if args.args else args.kwargs["system"]
    sent = args.args[1] if len(args.args) > 1 else args.kwargs["messages"]
    assert system == PRESETS["booking"].config.system_prompt
    assert sent == msgs


def test_chat_rejects_41_messages_and_trailing_assistant(client, auth_headers):
    agent = client.post("/agents", json={"preset_id": "support"}, headers=auth_headers).json()
    url = f"/agents/{agent['id']}/chat"
    forty = [{"role": "user", "content": "x"} for _ in range(40)]

    def post(messages):
        return client.post(url, json={"messages": messages}, headers=auth_headers).status_code

    with patch("backend.api.agents.groq_chat", return_value="ok"):
        assert post(forty) == 200
        assert post(forty + [forty[0]]) == 422
        assert post([]) == 422
        assert post([{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]) == 422
        assert post([{"role": "user", "content": "x" * 2001}]) == 422
        assert post([{"role": "user", "content": "x" * 2000}]) == 200
        assert post([{"role": "system", "content": "x"}]) == 422


def test_chat_groq_failure_is_502(client, auth_headers):
    agent = client.post("/agents", json={"preset_id": "support"}, headers=auth_headers).json()
    with patch("backend.api.agents.groq_chat", side_effect=RuntimeError("down")):
        r = client.post(
            f"/agents/{agent['id']}/chat",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=auth_headers,
        )
    assert r.status_code == 502
    assert r.json()["detail"] == "The agent didn't respond. Try again."


def test_chat_requires_auth(client):
    r = client.post("/agents/x/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401


def test_state_is_empty_for_new_agent(client, auth_headers):
    agent = client.post("/agents", json={"preset_id": "support"}, headers=auth_headers).json()
    r = client.get(f"/agents/{agent['id']}/state", headers=auth_headers)
    assert r.status_code == 200
    state = r.json()
    assert state["agent"] == agent
    assert state["runs"] == []
    assert state["criteria"] == []
    assert state["pattern_summary"] is None
    assert state["run_count"] == 0
    assert state["last_checked_at_run"] == 0


def test_state_lists_runs_and_criteria_in_order(client, auth_headers, db_session):
    agent = client.post("/agents", json={"preset_id": "support"}, headers=auth_headers).json()
    row = db_session.get(Agent, agent["id"])
    for pos in (2, 1):
        db_session.add(
            Run(
                agent_id=row.id,
                prompt_version_id=row.current_prompt_version_id,
                position=pos,
                messages=[{"role": "user", "content": f"m{pos}"}],
            )
        )
        db_session.add(
            Criterion(
                agent_id=row.id, position=pos, name=f"c{pos}", definition="d",
                good_example="g", bad_example="b", why_discovered="w", source="manual",
            )
        )
    row.pattern_summary = "summary"
    row.last_checked_at_run = 2
    db_session.commit()
    state = client.get(f"/agents/{agent['id']}/state", headers=auth_headers).json()
    assert [r["position"] for r in state["runs"]] == [1, 2]
    assert state["runs"][0]["messages"] == [{"role": "user", "content": "m1"}]
    assert [c["name"] for c in state["criteria"]] == ["c1", "c2"]
    assert set(state["criteria"][0]) == {
        "id", "name", "definition", "good_example", "bad_example", "why_discovered", "source",
    }
    assert state["run_count"] == 2
    assert state["pattern_summary"] == "summary"
    assert state["last_checked_at_run"] == 2
