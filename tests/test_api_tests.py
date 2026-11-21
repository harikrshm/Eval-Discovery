from datetime import timedelta

import pytest

from backend.api import limits
from backend.db.models import Agent, Criterion, EvalResult, EvalTest, Event, Job, utcnow
from backend.db.workspaces import create_workspace
from backend.jobs.queue import JOBS_PER_WORKSPACE_PER_HOUR

CAP_DETAIL = "Test run limit reached for this hour. Try again later."

CRITERION = {
    "name": "Answers the pricing question",
    "definition": "The agent gives a direct answer when asked about price.",
    "good_example": "It starts at $10.",
    "bad_example": "Let me book a demo.",
}


@pytest.fixture(autouse=True)
def _reset_llm_limiter():
    limits.llm_limiter.reset()
    yield
    limits.llm_limiter.reset()


def _agent(client, auth_headers):
    return client.post("/agents", json={"preset_id": "alex-sales"}, headers=auth_headers).json()["id"]


def _criterion(client, auth_headers, agent_id, **overrides):
    body = {**CRITERION, **overrides}
    return client.post(f"/agents/{agent_id}/criteria", json=body, headers=auth_headers).json()["id"]


def _test(db_session, criterion_id, role="fix", messages=("hi",)):
    test = EvalTest(
        criterion_id=criterion_id,
        role=role,
        source_run_id="run-1",
        scripted_user_messages=list(messages),
    )
    db_session.add(test)
    db_session.commit()
    return test


def _result(db_session, test, kind, verdict, version_id, stale=False, transcript=None, rationale="because"):
    row = EvalResult(
        test_id=test.id,
        prompt_version_id=version_id,
        kind=kind,
        verdict=verdict,
        rationale=rationale,
        transcript=transcript,
        stale=stale,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _version_id(db_session, agent_id):
    return db_session.get(Agent, agent_id).current_prompt_version_id


def _other_headers(db_session):
    _, token = create_workspace(db_session)
    return {"X-Workspace-Token": token}


def _fill_cap_for(db_session, workspace_id, count=JOBS_PER_WORKSPACE_PER_HOUR):
    for _ in range(count):
        db_session.add(Job(workspace_id=workspace_id, type="replay", payload={"x": 1}, status="done"))
    db_session.commit()


def _workspace_id(db_session, agent_id):
    return db_session.get(Agent, agent_id).workspace_id


def _jobs(db_session, job_type=None):
    db_session.expire_all()
    query = db_session.query(Job)
    if job_type:
        query = query.filter(Job.type == job_type)
    return query.order_by(Job.created_at, Job.id).all()


# ---------------------------------------------------------------- suite


def test_suite_reports_status_stale_count_baselines_and_current(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    version = _version_id(db_session, agent_id)
    failing_cid = _criterion(client, auth_headers, agent_id, name="Failing one")
    empty_cid = _criterion(client, auth_headers, agent_id, name="No tests")
    fix = _test(db_session, failing_cid, "fix")
    held = _test(db_session, failing_cid, "heldout")
    baseline = _result(
        db_session, fix, "baseline", "fail", "old-version", transcript=[{"role": "user", "content": "x" * 5000}]
    )
    _result(db_session, fix, "replay", "pass", version, transcript=[{"role": "user", "content": "hi"}])
    # A stale newer replay must not be `current`.
    _result(db_session, fix, "replay", "fail", version, stale=True)
    current = _result(db_session, held, "replay", "fail", version)

    r = client.get(f"/agents/{agent_id}/tests", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["prompt_version_id"] == version
    assert body["prompt_version_number"] == 1
    assert body["stale_count"] == 0
    first, second = body["criteria"]
    assert first["criterion_id"] == failing_cid
    assert first["status"] == "failing"
    assert first["active_job_id"] is None
    assert first["judge_check"] is None
    assert [t["role"] for t in first["tests"]] == ["fix", "heldout"]
    fix_out, held_out = first["tests"]
    assert fix_out["id"] == fix.id
    assert fix_out["source_run_id"] == "run-1"
    assert fix_out["baseline"]["id"] == baseline.id
    assert fix_out["baseline"]["kind"] == "baseline"
    assert fix_out["baseline"]["transcript"][0]["content"] == "x" * 5000
    assert fix_out["current"]["verdict"] == "pass"
    assert fix_out["current"]["stale"] is False
    assert fix_out["current"]["transcript"] == [{"role": "user", "content": "hi"}]
    assert held_out["baseline"] is None
    assert held_out["current"]["id"] == current.id
    assert held_out["current"]["rationale"] == "because"
    assert held_out["current"]["prompt_version_id"] == version
    assert "created_at" in held_out["current"]
    assert second["criterion_id"] == empty_cid
    assert second["status"] == "untested"
    assert second["tests"] == []


def test_suite_passing_status_and_stale_count_after_prompt_edit(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    version = _version_id(db_session, agent_id)
    cid = _criterion(client, auth_headers, agent_id)
    test = _test(db_session, cid)
    _result(db_session, test, "replay", "pass", version)
    body = client.get(f"/agents/{agent_id}/tests", headers=auth_headers).json()
    assert body["criteria"][0]["status"] == "passing"
    assert body["stale_count"] == 0

    client.put(f"/agents/{agent_id}/prompt", json={"text": "A new prompt."}, headers=auth_headers)
    body = client.get(f"/agents/{agent_id}/tests", headers=auth_headers).json()
    assert body["prompt_version_number"] == 2
    assert body["stale_count"] == 1
    assert body["criteria"][0]["status"] == "untested"
    assert body["criteria"][0]["tests"][0]["current"] is None


def test_suite_with_no_criteria_is_empty(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    body = client.get(f"/agents/{agent_id}/tests", headers=auth_headers).json()
    assert body["criteria"] == []
    assert body["stale_count"] == 0


def test_suite_shows_judge_check_and_active_jobs(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    version = _version_id(db_session, agent_id)
    cid = _criterion(client, auth_headers, agent_id)
    db_session.get(Criterion, cid).judge_check = {"ok": False, "good": "fail"}
    test = _test(db_session, cid)
    db_session.commit()
    build = Job(
        workspace_id=_workspace_id(db_session, agent_id), type="build_tests", payload={"criterion_id": cid}
    )
    replay = Job(
        workspace_id=_workspace_id(db_session, agent_id),
        type="replay",
        payload={"test_id": test.id, "prompt_version_id": version},
        status="running",
    )
    finished = Job(
        workspace_id=_workspace_id(db_session, agent_id),
        type="replay",
        payload={"test_id": test.id, "prompt_version_id": "other"},
        status="done",
    )
    db_session.add_all([build, replay, finished])
    db_session.commit()
    crit = client.get(f"/agents/{agent_id}/tests", headers=auth_headers).json()["criteria"][0]
    assert crit["judge_check"] == {"ok": False, "good": "fail"}
    assert crit["active_job_id"] == build.id
    assert crit["tests"][0]["active_job_id"] == replay.id


# ---------------------------------------------------------------- replay


def test_replay_enqueues_once_and_second_call_returns_same_job(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    version = _version_id(db_session, agent_id)
    test = _test(db_session, _criterion(client, auth_headers, agent_id))
    first = client.post(f"/tests/{test.id}/replay", headers=auth_headers)
    second = client.post(f"/tests/{test.id}/replay", headers=auth_headers)
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json() == second.json()
    jobs = _jobs(db_session, "replay")
    assert len(jobs) == 1
    assert jobs[0].id == first.json()["job_id"]
    assert jobs[0].payload == {"test_id": test.id, "prompt_version_id": version}
    assert jobs[0].status == "queued"
    events = db_session.query(Event).filter(Event.name == "replay_started").all()
    assert len(events) == 1


def test_replay_after_prompt_edit_uses_new_version(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    test = _test(db_session, _criterion(client, auth_headers, agent_id))
    first = client.post(f"/tests/{test.id}/replay", headers=auth_headers).json()["job_id"]
    client.put(f"/agents/{agent_id}/prompt", json={"text": "A new prompt."}, headers=auth_headers)
    second = client.post(f"/tests/{test.id}/replay", headers=auth_headers).json()["job_id"]
    assert first != second
    assert _jobs(db_session, "replay")[1].payload["prompt_version_id"] == _version_id(db_session, agent_id)


# ---------------------------------------------------------------- run-all


def test_run_all_enqueues_only_tests_without_active_jobs(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    version = _version_id(db_session, agent_id)
    cid = _criterion(client, auth_headers, agent_id)
    fix = _test(db_session, cid, "fix")
    held = _test(db_session, cid, "heldout")
    existing = client.post(f"/tests/{fix.id}/replay", headers=auth_headers).json()["job_id"]
    r = client.post(f"/agents/{agent_id}/tests/run-all", headers=auth_headers)
    assert r.status_code == 202
    ids = r.json()["job_ids"]
    assert len(ids) == 1
    assert existing not in ids
    jobs = {j.id: j for j in _jobs(db_session, "replay")}
    assert len(jobs) == 2
    assert jobs[ids[0]].payload == {"test_id": held.id, "prompt_version_id": version}
    # Running again enqueues nothing new.
    assert client.post(f"/agents/{agent_id}/tests/run-all", headers=auth_headers).json() == {"job_ids": []}
    assert len(_jobs(db_session, "replay")) == 2


def test_run_all_with_no_tests_returns_empty(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    r = client.post(f"/agents/{agent_id}/tests/run-all", headers=auth_headers)
    assert r.status_code == 202
    assert r.json() == {"job_ids": []}


def test_run_all_over_cap_enqueues_nothing(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    cid = _criterion(client, auth_headers, agent_id)
    _test(db_session, cid, "fix")
    _test(db_session, cid, "heldout")
    _fill_cap_for(db_session, _workspace_id(db_session, agent_id), JOBS_PER_WORKSPACE_PER_HOUR - 1)
    r = client.post(f"/agents/{agent_id}/tests/run-all", headers=auth_headers)
    assert r.status_code == 429
    assert r.json()["detail"] == CAP_DETAIL
    assert len(_jobs(db_session)) == JOBS_PER_WORKSPACE_PER_HOUR - 1


# ---------------------------------------------------------------- hourly cap


def test_replay_and_build_return_429_at_the_hourly_cap(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    cid = _criterion(client, auth_headers, agent_id)
    empty_cid = _criterion(client, auth_headers, agent_id, name="No tests")
    test = _test(db_session, cid)
    _fill_cap_for(db_session, _workspace_id(db_session, agent_id))
    for url in (f"/tests/{test.id}/replay", f"/criteria/{empty_cid}/build-tests"):
        r = client.post(url, headers=auth_headers)
        assert r.status_code == 429
        assert r.json()["detail"] == CAP_DETAIL
    r = client.post(f"/agents/{agent_id}/tests/run-all", headers=auth_headers)
    assert r.status_code == 429
    assert len(_jobs(db_session)) == JOBS_PER_WORKSPACE_PER_HOUR


def test_jobs_older_than_an_hour_do_not_count_toward_the_cap(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    test = _test(db_session, _criterion(client, auth_headers, agent_id))
    workspace_id = _workspace_id(db_session, agent_id)
    old = utcnow() - timedelta(hours=1, minutes=5)
    for _ in range(JOBS_PER_WORKSPACE_PER_HOUR):
        db_session.add(
            Job(workspace_id=workspace_id, type="replay", payload={"x": 1}, status="done", created_at=old)
        )
    db_session.commit()
    assert client.post(f"/tests/{test.id}/replay", headers=auth_headers).status_code == 202


def test_cap_is_per_workspace(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    test = _test(db_session, _criterion(client, auth_headers, agent_id))
    other = _other_headers(db_session)
    other_agent = _agent(client, other)
    _fill_cap_for(db_session, _workspace_id(db_session, other_agent))
    assert client.post(f"/tests/{test.id}/replay", headers=auth_headers).status_code == 202


# ---------------------------------------------------------------- build-tests


def test_build_tests_enqueues_and_returns_active_job_on_repeat(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    cid = _criterion(client, auth_headers, agent_id)
    first = client.post(f"/criteria/{cid}/build-tests", headers=auth_headers)
    second = client.post(f"/criteria/{cid}/build-tests", headers=auth_headers)
    assert first.status_code == 202
    assert first.json() == second.json()
    jobs = _jobs(db_session, "build_tests")
    assert len(jobs) == 1
    assert jobs[0].payload == {"criterion_id": cid}
    assert jobs[0].id == first.json()["job_id"]


def test_build_tests_is_409_when_tests_exist(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    cid = _criterion(client, auth_headers, agent_id)
    _test(db_session, cid)
    r = client.post(f"/criteria/{cid}/build-tests", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"] == "Tests already exist for this criterion."
    assert _jobs(db_session) == []


# ---------------------------------------------------------------- jobs


def test_get_job_returns_status_result_and_error(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    workspace_id = _workspace_id(db_session, agent_id)
    done = Job(
        workspace_id=workspace_id, type="build_tests", payload={"criterion_id": "c"}, status="done",
        result={"tests_created": 2},
    )
    failed = Job(
        workspace_id=workspace_id, type="replay", payload={}, status="failed", error="Judge unavailable."
    )
    db_session.add_all([done, failed])
    db_session.commit()
    assert client.get(f"/jobs/{done.id}", headers=auth_headers).json() == {
        "id": done.id,
        "type": "build_tests",
        "status": "done",
        "result": {"tests_created": 2},
        "error": None,
    }
    assert client.get(f"/jobs/{failed.id}", headers=auth_headers).json() == {
        "id": failed.id,
        "type": "replay",
        "status": "failed",
        "result": None,
        "error": "Judge unavailable.",
    }


# ---------------------------------------------------------------- isolation and auth


def test_foreign_workspace_gets_404_on_every_route(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    cid = _criterion(client, auth_headers, agent_id)
    test = _test(db_session, _criterion(client, auth_headers, agent_id, name="Other"))
    job = Job(workspace_id=_workspace_id(db_session, agent_id), type="replay", payload={})
    db_session.add(job)
    db_session.commit()
    other = _other_headers(db_session)
    before = len(_jobs(db_session))

    assert client.get(f"/agents/{agent_id}/tests", headers=other).status_code == 404
    assert client.post(f"/tests/{test.id}/replay", headers=other).status_code == 404
    assert client.post(f"/agents/{agent_id}/tests/run-all", headers=other).status_code == 404
    assert client.post(f"/criteria/{cid}/build-tests", headers=other).status_code == 404
    assert client.get(f"/jobs/{job.id}", headers=other).status_code == 404
    assert len(_jobs(db_session)) == before


def test_missing_ids_are_404(client, auth_headers):
    assert client.get("/agents/nope/tests", headers=auth_headers).status_code == 404
    assert client.post("/tests/nope/replay", headers=auth_headers).status_code == 404
    assert client.post("/agents/nope/tests/run-all", headers=auth_headers).status_code == 404
    assert client.post("/criteria/nope/build-tests", headers=auth_headers).status_code == 404
    assert client.get("/jobs/nope", headers=auth_headers).status_code == 404


def test_routes_require_auth(client):
    assert client.get("/agents/x/tests").status_code == 401
    assert client.post("/tests/x/replay").status_code == 401
    assert client.post("/agents/x/tests/run-all").status_code == 401
    assert client.post("/criteria/x/build-tests").status_code == 401
    assert client.get("/jobs/x").status_code == 401
