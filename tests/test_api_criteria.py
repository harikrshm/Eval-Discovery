from unittest.mock import patch

import pytest

from backend.api import limits
from backend.core.criteria import CriterionDraft, CriterionGenerationFailed
from backend.db.models import Agent, Criterion, Event
from backend.db.workspaces import create_workspace

DRAFT = CriterionDraft(
    name="Answers the pricing question",
    definition="The agent gives a direct answer when asked about price.",
    good_example="User: 'price?' Agent: 'It starts at $10.'",
    bad_example="User: 'price?' Agent: 'Let me book a demo.'",
    why_discovered="You asked about price in every run and got deflected.",
)

MANUAL = {
    "name": "Stays polite",
    "definition": "The agent never snaps at the user.",
    "good_example": "Happy to help.",
    "bad_example": "Read the docs.",
}


@pytest.fixture(autouse=True)
def _reset_llm_limiter():
    limits.llm_limiter.reset()
    yield
    limits.llm_limiter.reset()


def _agent(client, auth_headers):
    return client.post("/agents", json={"preset_id": "alex-sales"}, headers=auth_headers).json()["id"]


def _set_summary(db_session, agent_id, summary="You keep asking about price."):
    row = db_session.get(Agent, agent_id)
    row.pattern_summary = summary
    db_session.commit()
    return row


def _discover(client, auth_headers, agent_id):
    return client.post(f"/agents/{agent_id}/criteria/discover", headers=auth_headers)


def _other_headers(db_session):
    _, token = create_workspace(db_session)
    return {"X-Workspace-Token": token}


def test_discover_stores_criterion_clears_summary_and_logs(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    row = _set_summary(db_session, agent_id)
    with patch("backend.api.criteria.generate_criterion", return_value=DRAFT) as gen:
        r = _discover(client, auth_headers, agent_id)
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == DRAFT.name
    assert body["definition"] == DRAFT.definition
    assert body["good_example"] == DRAFT.good_example
    assert body["bad_example"] == DRAFT.bad_example
    assert body["why_discovered"] == DRAFT.why_discovered
    assert body["source"] == "discovered"
    config, summary = gen.call_args.args
    assert summary == "You keep asking about price."
    assert config.name == row.name
    assert config.user_role_label == "Prospect"
    assert config.system_prompt == row.prompt_versions[0].text
    stored = db_session.query(Criterion).one()
    assert stored.id == body["id"]
    assert stored.agent_id == agent_id
    assert stored.position == 1
    assert stored.source == "discovered"
    db_session.refresh(row)
    assert row.pattern_summary is None
    events = db_session.query(Event).filter(Event.name == "criterion_created").all()
    assert [e.properties for e in events] == [{"source": "discovered"}]


def test_discover_without_summary_is_409(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    with patch("backend.api.criteria.generate_criterion") as gen:
        r = _discover(client, auth_headers, agent_id)
    assert r.status_code == 409
    assert r.json()["detail"] == "No pattern to build from yet."
    gen.assert_not_called()
    assert db_session.query(Criterion).count() == 0


def test_discover_failure_is_502_and_keeps_summary(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    row = _set_summary(db_session, agent_id)
    with patch("backend.api.criteria.generate_criterion", side_effect=CriterionGenerationFailed("boom")):
        r = _discover(client, auth_headers, agent_id)
    assert r.status_code == 502
    assert r.json()["detail"] == "Criteria generation unavailable, try again."
    db_session.refresh(row)
    assert row.pattern_summary == "You keep asking about price."
    assert db_session.query(Criterion).count() == 0
    assert db_session.query(Event).filter(Event.name == "criterion_created").count() == 0


def test_discover_other_workspace_agent_is_404(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    _set_summary(db_session, agent_id)
    other = _other_headers(db_session)
    with patch("backend.api.criteria.generate_criterion") as gen:
        assert _discover(client, other, agent_id).status_code == 404
        assert _discover(client, auth_headers, "does-not-exist").status_code == 404
    gen.assert_not_called()


def test_discover_is_rate_limited_after_ownership(client, auth_headers, db_session, monkeypatch):
    agent_id = _agent(client, auth_headers)
    calls = []
    monkeypatch.setattr("backend.api.criteria.rate_limit_llm", lambda request: calls.append(1))
    _discover(client, auth_headers, "does-not-exist")
    assert calls == []
    _discover(client, auth_headers, agent_id)  # 409, but ownership passed
    assert calls == [1]


def test_manual_add(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    r = client.post(f"/agents/{agent_id}/criteria", json=MANUAL, headers=auth_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Stays polite"
    assert body["why_discovered"] == "(manually added)"
    assert body["source"] == "manual"
    stored = db_session.query(Criterion).one()
    assert stored.position == 1
    assert stored.agent_id == agent_id
    events = db_session.query(Event).filter(Event.name == "criterion_created").all()
    assert [e.properties for e in events] == [{"source": "manual"}]


def test_manual_add_requires_all_fields_and_forbids_extras(client, auth_headers):
    agent_id = _agent(client, auth_headers)
    for key in MANUAL:
        partial = {k: v for k, v in MANUAL.items() if k != key}
        r = client.post(f"/agents/{agent_id}/criteria", json=partial, headers=auth_headers)
        assert r.status_code == 422
    r = client.post(
        f"/agents/{agent_id}/criteria", json={**MANUAL, "source": "discovered"}, headers=auth_headers
    )
    assert r.status_code == 422
    r = client.post(f"/agents/{agent_id}/criteria", json={**MANUAL, "name": ""}, headers=auth_headers)
    assert r.status_code == 422


def test_manual_add_other_workspace_agent_is_404(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    other = _other_headers(db_session)
    assert client.post(f"/agents/{agent_id}/criteria", json=MANUAL, headers=other).status_code == 404
    assert db_session.query(Criterion).count() == 0


def test_oversize_fields_are_422_and_boundaries_accepted(client, auth_headers):
    agent_id = _agent(client, auth_headers)
    url = f"/agents/{agent_id}/criteria"
    assert client.post(url, json={**MANUAL, "name": "n" * 201}, headers=auth_headers).status_code == 422
    for field in ("definition", "good_example", "bad_example"):
        r = client.post(url, json={**MANUAL, field: "x" * 2001}, headers=auth_headers)
        assert r.status_code == 422
    ok = {**MANUAL, "name": "n" * 200, "definition": "d" * 2000, "good_example": "g" * 2000, "bad_example": "b" * 2000}
    assert client.post(url, json=ok, headers=auth_headers).status_code == 201


def test_positions_increase_in_creation_order(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    client.post(f"/agents/{agent_id}/criteria", json=MANUAL, headers=auth_headers)
    _set_summary(db_session, agent_id)
    with patch("backend.api.criteria.generate_criterion", return_value=DRAFT):
        _discover(client, auth_headers, agent_id)
    client.post(f"/agents/{agent_id}/criteria", json={**MANUAL, "name": "Third"}, headers=auth_headers)
    rows = db_session.query(Criterion).order_by(Criterion.position).all()
    assert [(c.position, c.source) for c in rows] == [(1, "manual"), (2, "discovered"), (3, "manual")]
    state = client.get(f"/agents/{agent_id}/state", headers=auth_headers).json()
    assert [c["name"] for c in state["criteria"]] == ["Stays polite", DRAFT.name, "Third"]


def _make(client, auth_headers, agent_id):
    return client.post(f"/agents/{agent_id}/criteria", json=MANUAL, headers=auth_headers).json()["id"]


def test_patch_updates_only_given_fields(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    cid = _make(client, auth_headers, agent_id)
    r = client.patch(
        f"/criteria/{cid}", json={"name": "Renamed", "why_discovered": "Because."}, headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json() == {
        "id": cid,
        "name": "Renamed",
        "definition": MANUAL["definition"],
        "good_example": MANUAL["good_example"],
        "bad_example": MANUAL["bad_example"],
        "why_discovered": "Because.",
        "source": "manual",
    }
    stored = db_session.get(Criterion, cid)
    db_session.refresh(stored)
    assert stored.name == "Renamed"
    assert stored.definition == MANUAL["definition"]
    assert stored.position == 1


def test_patch_empty_body_is_noop(client, auth_headers):
    agent_id = _agent(client, auth_headers)
    cid = _make(client, auth_headers, agent_id)
    r = client.patch(f"/criteria/{cid}", json={}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["name"] == MANUAL["name"]
    assert r.json()["definition"] == MANUAL["definition"]


def test_patch_rejects_bad_bodies(client, auth_headers):
    agent_id = _agent(client, auth_headers)
    cid = _make(client, auth_headers, agent_id)
    url = f"/criteria/{cid}"
    assert client.patch(url, json={"name": "n" * 201}, headers=auth_headers).status_code == 422
    assert client.patch(url, json={"name": ""}, headers=auth_headers).status_code == 422
    assert client.patch(url, json={"definition": "x" * 2001}, headers=auth_headers).status_code == 422
    assert client.patch(url, json={"why_discovered": "x" * 2001}, headers=auth_headers).status_code == 422
    assert client.patch(url, json={"source": "discovered"}, headers=auth_headers).status_code == 422
    assert client.patch(url, json={"name": None}, headers=auth_headers).status_code == 422


def test_patch_other_workspace_or_missing_criterion_is_404(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    cid = _make(client, auth_headers, agent_id)
    other = _other_headers(db_session)
    assert client.patch(f"/criteria/{cid}", json={"name": "Hijack"}, headers=other).status_code == 404
    assert client.patch("/criteria/does-not-exist", json={"name": "x"}, headers=auth_headers).status_code == 404
    db_session.expire_all()
    assert db_session.get(Criterion, cid).name == MANUAL["name"]


def test_criteria_routes_require_auth(client):
    assert client.post("/agents/x/criteria/discover").status_code == 401
    assert client.post("/agents/x/criteria", json=MANUAL).status_code == 401
    assert client.patch("/criteria/x", json={}).status_code == 401
