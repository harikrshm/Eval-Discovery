from unittest.mock import patch

import pytest

from backend.api import limits
from backend.core.criteria import CriterionDraft, CriterionGenerationFailed
from backend.db.models import Agent, Criterion, EvalResult, EvalTest, Event, Job
from backend.db.workspaces import create_workspace
from backend.jobs.queue import JOBS_PER_WORKSPACE_PER_HOUR, MAX_QUEUED_JOBS

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
    job = db_session.query(Job).one()
    assert body["job_id"] == job.id
    assert job.type == "build_tests"
    assert job.status == "queued"
    assert job.payload == {"criterion_id": body["id"]}
    assert job.workspace_id == row.workspace_id
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


def test_discover_at_the_hourly_job_cap_still_creates_the_criterion_without_a_job(
    client, auth_headers, db_session
):
    agent_id = _agent(client, auth_headers)
    row = _set_summary(db_session, agent_id)
    for _ in range(JOBS_PER_WORKSPACE_PER_HOUR):
        db_session.add(Job(workspace_id=row.workspace_id, type="replay", payload={}, status="done"))
    db_session.commit()
    with patch("backend.api.criteria.generate_criterion", return_value=DRAFT):
        r = _discover(client, auth_headers, agent_id)
    assert r.status_code == 201
    assert r.json()["job_id"] is None
    assert db_session.query(Criterion).count() == 1
    assert db_session.query(Job).filter(Job.type == "build_tests").count() == 0


def test_discover_with_a_full_queue_still_creates_the_criterion_without_a_job(
    client, auth_headers, db_session
):
    agent_id = _agent(client, auth_headers)
    _set_summary(db_session, agent_id)
    for _ in range(MAX_QUEUED_JOBS):
        other, _token = create_workspace(db_session)
        db_session.add(Job(workspace_id=other.id, type="replay", payload={}, status="queued"))
    db_session.commit()
    with patch("backend.api.criteria.generate_criterion", return_value=DRAFT):
        r = _discover(client, auth_headers, agent_id)
    assert r.status_code == 201
    assert r.json()["job_id"] is None
    assert db_session.query(Criterion).count() == 1
    assert db_session.query(Job).filter(Job.type == "build_tests").count() == 0


def test_discover_survives_an_enqueue_failure(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    _set_summary(db_session, agent_id)
    with (
        patch("backend.api.criteria.generate_criterion", return_value=DRAFT),
        patch("backend.api.criteria.enqueue", side_effect=RuntimeError("db hiccup")),
    ):
        r = _discover(client, auth_headers, agent_id)
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == DRAFT.name
    assert body["job_id"] is None
    db_session.expire_all()
    assert db_session.query(Criterion).one().id == body["id"]
    assert db_session.query(Job).count() == 0


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


def _with_replay_result(db_session, cid):
    test = EvalTest(criterion_id=cid, role="fix", scripted_user_messages=["hi"])
    db_session.add(test)
    db_session.flush()
    db_session.add(
        EvalResult(test_id=test.id, prompt_version_id="pv", kind="replay", verdict="pass", rationale="r")
    )
    criterion = db_session.get(Criterion, cid)
    criterion.judge_check = {"ok": True}
    db_session.commit()


def _replay_stale_and_check(db_session, cid):
    db_session.expire_all()
    result = db_session.query(EvalResult).one()
    return result.stale, db_session.get(Criterion, cid).judge_check


@pytest.mark.parametrize("field", ["definition", "good_example", "bad_example"])
def test_patch_of_a_judged_field_marks_results_stale_and_clears_judge_check(
    client, auth_headers, db_session, field
):
    agent_id = _agent(client, auth_headers)
    cid = _make(client, auth_headers, agent_id)
    _with_replay_result(db_session, cid)
    r = client.patch(f"/criteria/{cid}", json={field: "Something new."}, headers=auth_headers)
    assert r.status_code == 200
    assert _replay_stale_and_check(db_session, cid) == (True, None)


def test_patch_of_name_only_leaves_results_and_check_alone(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    cid = _make(client, auth_headers, agent_id)
    _with_replay_result(db_session, cid)
    client.patch(f"/criteria/{cid}", json={"name": "Renamed", "why_discovered": "Because."}, headers=auth_headers)
    assert _replay_stale_and_check(db_session, cid) == (False, {"ok": True})


def test_patch_with_unchanged_values_leaves_results_and_check_alone(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    cid = _make(client, auth_headers, agent_id)
    _with_replay_result(db_session, cid)
    body = {k: MANUAL[k] for k in ("definition", "good_example", "bad_example")}
    r = client.patch(f"/criteria/{cid}", json=body, headers=auth_headers)
    assert r.status_code == 200
    assert _replay_stale_and_check(db_session, cid) == (False, {"ok": True})


def _revision(db_session, cid):
    db_session.expire_all()
    return db_session.get(Criterion, cid).revision


def test_patch_of_definition_bumps_revision_and_name_edit_does_not(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    cid = _make(client, auth_headers, agent_id)
    assert _revision(db_session, cid) == 0
    client.patch(f"/criteria/{cid}", json={"name": "Renamed"}, headers=auth_headers)
    assert _revision(db_session, cid) == 0
    client.patch(f"/criteria/{cid}", json={"definition": "Something new."}, headers=auth_headers)
    assert _revision(db_session, cid) == 1
    client.patch(f"/criteria/{cid}", json={"definition": "Something new."}, headers=auth_headers)
    assert _revision(db_session, cid) == 1  # unchanged value: no bump


def test_patch_only_invalidates_the_edited_criterion(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    edited = _make(client, auth_headers, agent_id)
    other = _make(client, auth_headers, agent_id)
    _with_replay_result(db_session, other)
    client.patch(f"/criteria/{edited}", json={"definition": "Changed."}, headers=auth_headers)
    assert _replay_stale_and_check(db_session, other) == (False, {"ok": True})


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


def test_manual_criteria_are_capped_per_agent(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    for _ in range(limits.MAX_CRITERIA_PER_AGENT):
        r = client.post(f"/agents/{agent_id}/criteria", json=MANUAL, headers=auth_headers)
        assert r.status_code == 201
    r = client.post(f"/agents/{agent_id}/criteria", json=MANUAL, headers=auth_headers)
    assert r.status_code == 429
    assert r.json()["detail"] == "Criteria limit reached for this agent."
    other = _agent(client, auth_headers)
    assert client.post(f"/agents/{other}/criteria", json=MANUAL, headers=auth_headers).status_code == 201


def test_discover_is_capped_before_any_llm_call(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    for _ in range(limits.MAX_CRITERIA_PER_AGENT):
        client.post(f"/agents/{agent_id}/criteria", json=MANUAL, headers=auth_headers)
    _set_summary(db_session, agent_id)
    with patch("backend.api.criteria.generate_criterion") as gen:
        r = _discover(client, auth_headers, agent_id)
    assert r.status_code == 429
    gen.assert_not_called()


# ---- verifying guard


VERIFYING = "A fix is being verified. Wait for it to finish."


def _start_verifying(db_session, agent_id):
    from backend.db.models import PromptVersion

    agent = db_session.get(Agent, agent_id)
    version = PromptVersion(
        agent_id=agent_id,
        text="candidate",
        parent_id=agent.current_prompt_version_id,
        origin="suggested",
        status="approved_verifying",
        number=2,
    )
    db_session.add(version)
    db_session.commit()
    return version


@pytest.mark.parametrize("field", ["definition", "good_example", "bad_example"])
def test_patch_of_a_judged_field_is_409_while_a_fix_is_being_verified(client, auth_headers, db_session, field):
    agent_id = _agent(client, auth_headers)
    cid = _make(client, auth_headers, agent_id)
    _with_replay_result(db_session, cid)
    _start_verifying(db_session, agent_id)
    r = client.patch(f"/criteria/{cid}", json={field: "Something new."}, headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"] == VERIFYING
    db_session.expire_all()
    assert getattr(db_session.get(Criterion, cid), field) == MANUAL[field]
    assert db_session.get(Criterion, cid).revision == 0
    assert _replay_stale_and_check(db_session, cid) == (False, {"ok": True})


def test_patch_of_name_only_still_works_while_a_fix_is_being_verified(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    cid = _make(client, auth_headers, agent_id)
    _start_verifying(db_session, agent_id)
    r = client.patch(f"/criteria/{cid}", json={"name": "Renamed", "why_discovered": "Because."}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"


def test_patch_with_an_unchanged_judged_value_still_works_while_a_fix_is_being_verified(
    client, auth_headers, db_session
):
    agent_id = _agent(client, auth_headers)
    cid = _make(client, auth_headers, agent_id)
    _start_verifying(db_session, agent_id)
    r = client.patch(
        f"/criteria/{cid}", json={"definition": MANUAL["definition"], "name": "Renamed"}, headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"


def test_patch_of_a_judged_field_works_when_only_a_proposal_is_open(client, auth_headers, db_session):
    from backend.db.models import PromptVersion

    agent_id = _agent(client, auth_headers)
    cid = _make(client, auth_headers, agent_id)
    version = _start_verifying(db_session, agent_id)
    version.status = "proposed"
    db_session.commit()
    assert db_session.query(PromptVersion).filter_by(status="proposed").count() == 1
    r = client.patch(f"/criteria/{cid}", json={"definition": "New."}, headers=auth_headers)
    assert r.status_code == 200


def test_patch_guard_only_looks_at_the_criterions_own_agent(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    other_agent = _agent(client, auth_headers)
    cid = _make(client, auth_headers, agent_id)
    _start_verifying(db_session, other_agent)
    assert client.patch(f"/criteria/{cid}", json={"definition": "New."}, headers=auth_headers).status_code == 200
