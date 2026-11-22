from datetime import timedelta

import pytest

from backend.api import limits
from backend.db.models import Agent, Criterion, EvalResult, EvalTest, Event, Job, PromptVersion, utcnow
from backend.db.workspaces import create_workspace
from backend.api.limits import RateLimiter
from backend.jobs.queue import JOBS_PER_WORKSPACE_PER_HOUR, MAX_QUEUED_JOBS

CAP_DETAIL = "Test run limit reached for this hour. Try again later."
BUSY_DETAIL = "The test queue is busy. Try again in a minute."
TOO_MANY_DETAIL = "Too many requests. Wait a minute, then try again."

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


def test_run_all_skips_tests_with_a_current_replay(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    version = _version_id(db_session, agent_id)
    cid = _criterion(client, auth_headers, agent_id)
    done = _test(db_session, cid, "fix")
    todo = _test(db_session, cid, "heldout")
    _result(db_session, done, "replay", "pass", version)
    r = client.post(f"/agents/{agent_id}/tests/run-all", headers=auth_headers)
    assert r.status_code == 202
    ids = r.json()["job_ids"]
    assert len(ids) == 1
    assert _jobs(db_session, "replay")[0].payload == {"test_id": todo.id, "prompt_version_id": version}


def test_run_all_reruns_tests_whose_replay_is_stale_or_for_another_version(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    version = _version_id(db_session, agent_id)
    cid = _criterion(client, auth_headers, agent_id)
    stale = _test(db_session, cid, "fix")
    old_version = _test(db_session, cid, "heldout")
    _result(db_session, stale, "replay", "pass", version, stale=True)
    _result(db_session, old_version, "replay", "pass", "older-version")
    r = client.post(f"/agents/{agent_id}/tests/run-all", headers=auth_headers)
    assert r.status_code == 202
    assert len(r.json()["job_ids"]) == 2


def test_run_all_with_every_test_current_is_202_with_no_jobs_even_at_the_cap(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    version = _version_id(db_session, agent_id)
    cid = _criterion(client, auth_headers, agent_id)
    for role in ("fix", "heldout"):
        _result(db_session, _test(db_session, cid, role), "replay", "pass", version)
    _fill_cap_for(db_session, _workspace_id(db_session, agent_id))
    r = client.post(f"/agents/{agent_id}/tests/run-all", headers=auth_headers)
    assert r.status_code == 202
    assert r.json() == {"job_ids": []}
    assert len(_jobs(db_session)) == JOBS_PER_WORKSPACE_PER_HOUR  # nothing new


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


# ---------------------------------------------------------------- LLM rate limit and queue depth


def _exhaust_llm_limiter(monkeypatch):
    monkeypatch.setattr(limits, "llm_limiter", RateLimiter(0, 60))


def _fill_queue(db_session, count=MAX_QUEUED_JOBS):
    """Queued jobs from other workspaces, so the per-workspace hourly cap is untouched."""
    for _i in range(count):
        workspace, _ = create_workspace(db_session)
        db_session.add(Job(workspace_id=workspace.id, type="replay", payload={"x": 1}, status="queued"))
    db_session.commit()


def test_enqueue_routes_are_rate_limited(client, auth_headers, db_session, monkeypatch):
    agent_id = _agent(client, auth_headers)
    cid = _criterion(client, auth_headers, agent_id)
    empty_cid = _criterion(client, auth_headers, agent_id, name="No tests")
    test = _test(db_session, cid)
    _exhaust_llm_limiter(monkeypatch)
    for url in (
        f"/tests/{test.id}/replay",
        f"/agents/{agent_id}/tests/run-all",
        f"/criteria/{empty_cid}/build-tests",
    ):
        r = client.post(url, headers=auth_headers)
        assert r.status_code == 429, url
        assert r.json()["detail"] == TOO_MANY_DETAIL
    assert _jobs(db_session) == []


def test_rate_limit_is_checked_after_ownership(client, auth_headers, db_session, monkeypatch):
    agent_id = _agent(client, auth_headers)
    test = _test(db_session, _criterion(client, auth_headers, agent_id))
    _exhaust_llm_limiter(monkeypatch)
    other = _other_headers(db_session)
    assert client.post(f"/tests/{test.id}/replay", headers=other).status_code == 404
    assert client.post(f"/agents/{agent_id}/tests/run-all", headers=other).status_code == 404


def test_dedupe_hits_are_not_rate_limited(client, auth_headers, db_session, monkeypatch):
    agent_id = _agent(client, auth_headers)
    cid = _criterion(client, auth_headers, agent_id)
    empty_cid = _criterion(client, auth_headers, agent_id, name="No tests")
    test = _test(db_session, cid)
    replay_id = client.post(f"/tests/{test.id}/replay", headers=auth_headers).json()["job_id"]
    build_id = client.post(f"/criteria/{empty_cid}/build-tests", headers=auth_headers).json()["job_id"]
    _exhaust_llm_limiter(monkeypatch)
    again = client.post(f"/tests/{test.id}/replay", headers=auth_headers)
    assert again.status_code == 202
    assert again.json() == {"job_id": replay_id}
    again = client.post(f"/criteria/{empty_cid}/build-tests", headers=auth_headers)
    assert again.status_code == 202
    assert again.json() == {"job_id": build_id}
    # Nothing left to enqueue for run-all either.
    assert client.post(f"/agents/{agent_id}/tests/run-all", headers=auth_headers).json() == {"job_ids": []}


def test_dedupe_hits_ignore_a_full_queue(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    test = _test(db_session, _criterion(client, auth_headers, agent_id))
    job_id = client.post(f"/tests/{test.id}/replay", headers=auth_headers).json()["job_id"]
    _fill_queue(db_session)
    again = client.post(f"/tests/{test.id}/replay", headers=auth_headers)
    assert again.status_code == 202
    assert again.json() == {"job_id": job_id}


def test_replay_and_build_return_429_when_the_queue_is_full(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    cid = _criterion(client, auth_headers, agent_id)
    empty_cid = _criterion(client, auth_headers, agent_id, name="No tests")
    test = _test(db_session, cid)
    _fill_queue(db_session)
    for url in (f"/tests/{test.id}/replay", f"/criteria/{empty_cid}/build-tests"):
        r = client.post(url, headers=auth_headers)
        assert r.status_code == 429, url
        assert r.json()["detail"] == BUSY_DETAIL
    r = client.post(f"/agents/{agent_id}/tests/run-all", headers=auth_headers)
    assert r.status_code == 429
    assert r.json()["detail"] == BUSY_DETAIL
    assert len(_jobs(db_session)) == MAX_QUEUED_JOBS


def test_replay_is_accepted_just_below_the_queue_cap(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    test = _test(db_session, _criterion(client, auth_headers, agent_id))
    _fill_queue(db_session, MAX_QUEUED_JOBS - 1)
    assert client.post(f"/tests/{test.id}/replay", headers=auth_headers).status_code == 202


def test_run_all_is_all_or_nothing_against_the_queue_cap(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    cid = _criterion(client, auth_headers, agent_id)
    _test(db_session, cid, "fix")
    _test(db_session, cid, "heldout")
    _fill_queue(db_session, MAX_QUEUED_JOBS - 1)
    r = client.post(f"/agents/{agent_id}/tests/run-all", headers=auth_headers)
    assert r.status_code == 429
    assert r.json()["detail"] == BUSY_DETAIL
    assert len(_jobs(db_session)) == MAX_QUEUED_JOBS - 1  # nothing partially enqueued


def test_run_all_fits_exactly_up_to_the_queue_cap(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    cid = _criterion(client, auth_headers, agent_id)
    _test(db_session, cid, "fix")
    _test(db_session, cid, "heldout")
    _fill_queue(db_session, MAX_QUEUED_JOBS - 2)
    r = client.post(f"/agents/{agent_id}/tests/run-all", headers=auth_headers)
    assert r.status_code == 202
    assert len(r.json()["job_ids"]) == 2


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


# ---------------------------------------------------------------- Phase 3: suggest-fix, generate-heldout


SUGGEST_ONLY_FIX = "Suggest a fix from the fix test."
NOT_FAILING = "This test isn't failing on the current prompt."
CANDIDATE_OPEN = "A fix is already proposed or being verified."
NEEDS_FIX_ONLY = "This criterion needs a fix test and no held-out test."


def _failing_fix(client, auth_headers, db_session, agent_id=None, role="fix", **criterion):
    """A criterion with one `role` test whose current replay fails; returns (agent_id, cid, test)."""
    agent_id = agent_id or _agent(client, auth_headers)
    cid = _criterion(client, auth_headers, agent_id, **criterion)
    test = _test(db_session, cid, role)
    _result(db_session, test, "replay", "fail", _version_id(db_session, agent_id))
    return agent_id, cid, test


def _candidate(db_session, agent_id, number=2, status="proposed", origin="suggested", **fields):
    agent = db_session.get(Agent, agent_id)
    version = PromptVersion(
        agent_id=agent_id,
        text=f"candidate text {number}",
        parent_id=agent.current_prompt_version_id,
        origin=origin,
        status=status,
        number=number,
        **fields,
    )
    db_session.add(version)
    db_session.commit()
    return version


def test_suggest_fix_enqueues_once_and_returns_the_active_job_on_repeat(client, auth_headers, db_session):
    _, _, test = _failing_fix(client, auth_headers, db_session)
    r = client.post(f"/tests/{test.id}/suggest-fix", headers=auth_headers)
    assert r.status_code == 202
    again = client.post(f"/tests/{test.id}/suggest-fix", headers=auth_headers)
    assert again.status_code == 202
    assert again.json() == r.json()
    jobs = _jobs(db_session, "suggest_fix")
    assert [j.id for j in jobs] == [r.json()["job_id"]]
    assert jobs[0].payload == {"test_id": test.id}


def test_suggest_fix_on_a_heldout_test_is_409(client, auth_headers, db_session):
    _, _, test = _failing_fix(client, auth_headers, db_session, role="heldout")
    r = client.post(f"/tests/{test.id}/suggest-fix", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"] == SUGGEST_ONLY_FIX
    assert _jobs(db_session) == []


def test_suggest_fix_on_a_passing_or_unrun_test_is_409(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    version = _version_id(db_session, agent_id)
    passing = _test(db_session, _criterion(client, auth_headers, agent_id, name="a"))
    _result(db_session, passing, "replay", "pass", version)
    unrun = _test(db_session, _criterion(client, auth_headers, agent_id, name="b"))
    old_baseline = _test(db_session, _criterion(client, auth_headers, agent_id, name="c"))
    _result(db_session, old_baseline, "baseline", "fail", "older-version")
    for test in (passing, unrun, old_baseline):
        r = client.post(f"/tests/{test.id}/suggest-fix", headers=auth_headers)
        assert r.status_code == 409
        assert r.json()["detail"] == NOT_FAILING
    assert _jobs(db_session) == []


def test_suggest_fix_with_an_open_candidate_is_409(client, auth_headers, db_session):
    agent_id, _, test = _failing_fix(client, auth_headers, db_session)
    candidate = _candidate(db_session, agent_id, status="proposed")
    r = client.post(f"/tests/{test.id}/suggest-fix", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"] == CANDIDATE_OPEN
    candidate.status = "approved_verifying"
    db_session.commit()
    r = client.post(f"/tests/{test.id}/suggest-fix", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"] == CANDIDATE_OPEN
    assert _jobs(db_session) == []


def test_suggest_fix_is_allowed_after_a_candidate_was_rejected(client, auth_headers, db_session):
    agent_id, _, test = _failing_fix(client, auth_headers, db_session)
    _candidate(db_session, agent_id, status="rejected")
    assert client.post(f"/tests/{test.id}/suggest-fix", headers=auth_headers).status_code == 202


def test_suggest_fix_429_at_the_hourly_cap_and_not_for_a_dedupe_hit(client, auth_headers, db_session):
    agent_id, _, test = _failing_fix(client, auth_headers, db_session)
    job_id = client.post(f"/tests/{test.id}/suggest-fix", headers=auth_headers).json()["job_id"]
    _fill_cap_for(db_session, _workspace_id(db_session, agent_id))
    again = client.post(f"/tests/{test.id}/suggest-fix", headers=auth_headers)
    assert again.status_code == 202
    assert again.json() == {"job_id": job_id}

    _, _, other_test = _failing_fix(client, auth_headers, db_session, agent_id=agent_id, name="Other")
    r = client.post(f"/tests/{other_test.id}/suggest-fix", headers=auth_headers)
    assert r.status_code == 429
    assert r.json()["detail"] == CAP_DETAIL


def test_suggest_fix_and_generate_heldout_429_when_the_queue_is_full(client, auth_headers, db_session):
    agent_id, cid, test = _failing_fix(client, auth_headers, db_session)
    _fill_queue(db_session)
    for url in (f"/tests/{test.id}/suggest-fix", f"/criteria/{cid}/generate-heldout"):
        r = client.post(url, headers=auth_headers)
        assert r.status_code == 429, url
        assert r.json()["detail"] == BUSY_DETAIL
    assert len(_jobs(db_session)) == MAX_QUEUED_JOBS


def test_suggest_fix_and_generate_heldout_are_rate_limited(client, auth_headers, db_session, monkeypatch):
    _, cid, test = _failing_fix(client, auth_headers, db_session)
    _exhaust_llm_limiter(monkeypatch)
    for url in (f"/tests/{test.id}/suggest-fix", f"/criteria/{cid}/generate-heldout"):
        r = client.post(url, headers=auth_headers)
        assert r.status_code == 429, url
        assert r.json()["detail"] == TOO_MANY_DETAIL
    assert _jobs(db_session) == []


def test_dedupe_hits_of_the_new_routes_ignore_limiter_and_full_queue(
    client, auth_headers, db_session, monkeypatch
):
    _, cid, test = _failing_fix(client, auth_headers, db_session)
    suggest_id = client.post(f"/tests/{test.id}/suggest-fix", headers=auth_headers).json()["job_id"]
    generate_id = client.post(f"/criteria/{cid}/generate-heldout", headers=auth_headers).json()["job_id"]
    _fill_queue(db_session)
    _exhaust_llm_limiter(monkeypatch)
    again = client.post(f"/tests/{test.id}/suggest-fix", headers=auth_headers)
    assert (again.status_code, again.json()) == (202, {"job_id": suggest_id})
    again = client.post(f"/criteria/{cid}/generate-heldout", headers=auth_headers)
    assert (again.status_code, again.json()) == (202, {"job_id": generate_id})


def test_generate_heldout_enqueues_once_with_the_criterion_payload(client, auth_headers, db_session):
    _, cid, _ = _failing_fix(client, auth_headers, db_session)
    r = client.post(f"/criteria/{cid}/generate-heldout", headers=auth_headers)
    assert r.status_code == 202
    again = client.post(f"/criteria/{cid}/generate-heldout", headers=auth_headers)
    assert again.json() == r.json()
    jobs = _jobs(db_session, "generate_heldout")
    assert len(jobs) == 1
    assert jobs[0].payload == {"criterion_id": cid}


def test_generate_heldout_is_409_without_a_fix_test_or_with_a_heldout_test(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    no_tests = _criterion(client, auth_headers, agent_id, name="none")
    has_both = _criterion(client, auth_headers, agent_id, name="both")
    _test(db_session, has_both, "fix")
    _test(db_session, has_both, "heldout")
    heldout_only = _criterion(client, auth_headers, agent_id, name="heldout only")
    _test(db_session, heldout_only, "heldout")
    for cid in (no_tests, has_both, heldout_only):
        r = client.post(f"/criteria/{cid}/generate-heldout", headers=auth_headers)
        assert r.status_code == 409, cid
        assert r.json()["detail"] == NEEDS_FIX_ONLY
    assert _jobs(db_session) == []


def test_generate_heldout_429_at_the_hourly_cap(client, auth_headers, db_session):
    agent_id, cid, _ = _failing_fix(client, auth_headers, db_session)
    _fill_cap_for(db_session, _workspace_id(db_session, agent_id))
    r = client.post(f"/criteria/{cid}/generate-heldout", headers=auth_headers)
    assert r.status_code == 429
    assert r.json()["detail"] == CAP_DETAIL


def test_new_routes_check_ownership_before_anything_else(client, auth_headers, db_session, monkeypatch):
    agent_id, cid, test = _failing_fix(client, auth_headers, db_session)
    _fill_cap_for(db_session, _workspace_id(db_session, agent_id))
    _exhaust_llm_limiter(monkeypatch)
    other = _other_headers(db_session)
    assert client.post(f"/tests/{test.id}/suggest-fix", headers=other).status_code == 404
    assert client.post(f"/criteria/{cid}/generate-heldout", headers=other).status_code == 404
    assert client.post("/tests/nope/suggest-fix", headers=auth_headers).status_code == 404
    assert client.post("/criteria/nope/generate-heldout", headers=auth_headers).status_code == 404
    assert _jobs(db_session, "suggest_fix") == []
    assert _jobs(db_session, "generate_heldout") == []


def test_new_routes_require_auth(client):
    assert client.post("/tests/x/suggest-fix").status_code == 401
    assert client.post("/criteria/x/generate-heldout").status_code == 401


# ---------------------------------------------------------------- Phase 3: suite additions


def test_suite_flags_origin_can_suggest_fix_and_can_generate_heldout(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    version = _version_id(db_session, agent_id)
    failing = _criterion(client, auth_headers, agent_id, name="failing")
    fix = _test(db_session, failing, "fix")
    _result(db_session, fix, "replay", "fail", version)
    passing = _criterion(client, auth_headers, agent_id, name="passing")
    pfix = _test(db_session, passing, "fix")
    pheld = _test(db_session, passing, "heldout")
    pheld.origin = "generated"
    db_session.commit()
    _result(db_session, pfix, "replay", "pass", version)
    _result(db_session, pheld, "replay", "fail", version)
    _criterion(client, auth_headers, agent_id, name="empty")

    body = client.get(f"/agents/{agent_id}/tests", headers=auth_headers).json()
    c_failing, c_passing, c_empty = body["criteria"]
    assert c_failing["criterion_id"] == failing
    assert c_failing["can_generate_heldout"] is True
    assert c_failing["heldout_job_id"] is None
    assert c_failing["tests"][0]["origin"] == "run"
    assert c_failing["tests"][0]["can_suggest_fix"] is True
    assert c_failing["tests"][0]["suggest_job_id"] is None
    assert c_passing["can_generate_heldout"] is False
    fix_row, held_row = c_passing["tests"]
    assert fix_row["can_suggest_fix"] is False  # passing
    # Review Focus 1: a failing held-out test never offers a fix.
    assert held_row["origin"] == "generated"
    assert held_row["can_suggest_fix"] is False
    assert c_empty["can_generate_heldout"] is False
    assert c_empty["tests"] == []


def test_suite_can_suggest_fix_uses_an_unreplayed_failing_baseline_on_the_current_version(
    client, auth_headers, db_session
):
    agent_id = _agent(client, auth_headers)
    test = _test(db_session, _criterion(client, auth_headers, agent_id), "fix")
    _result(db_session, test, "baseline", "fail", _version_id(db_session, agent_id))
    row = client.get(f"/agents/{agent_id}/tests", headers=auth_headers).json()["criteria"][0]["tests"][0]
    assert row["can_suggest_fix"] is True


def test_suite_can_suggest_fix_is_false_with_an_open_candidate(client, auth_headers, db_session):
    agent_id, _, _ = _failing_fix(client, auth_headers, db_session)
    _candidate(db_session, agent_id, status="proposed")
    row = client.get(f"/agents/{agent_id}/tests", headers=auth_headers).json()["criteria"][0]["tests"][0]
    assert row["can_suggest_fix"] is False


def test_suite_reports_active_suggest_and_generate_jobs(client, auth_headers, db_session):
    agent_id, cid, test = _failing_fix(client, auth_headers, db_session)
    suggest_id = client.post(f"/tests/{test.id}/suggest-fix", headers=auth_headers).json()["job_id"]
    generate_id = client.post(f"/criteria/{cid}/generate-heldout", headers=auth_headers).json()["job_id"]
    criterion = client.get(f"/agents/{agent_id}/tests", headers=auth_headers).json()["criteria"][0]
    assert criterion["tests"][0]["suggest_job_id"] == suggest_id
    assert criterion["heldout_job_id"] == generate_id
    assert criterion["can_generate_heldout"] is False


def _suite_candidate(client, auth_headers, agent_id):
    return client.get(f"/agents/{agent_id}/tests", headers=auth_headers).json()["candidate"]


def test_suite_candidate_is_null_without_versions_to_show(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    assert _suite_candidate(client, auth_headers, agent_id) is None
    _candidate(db_session, agent_id, number=2, status="rejected")
    _candidate(db_session, agent_id, number=3, status="superseded")
    _candidate(db_session, agent_id, number=4, status="promoted", origin="restored")
    assert _suite_candidate(client, auth_headers, agent_id) is None


def test_suite_candidate_proposed(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    candidate = _candidate(db_session, agent_id, number=2, status="proposed")
    shown = _suite_candidate(client, auth_headers, agent_id)
    assert shown["version_id"] == candidate.id
    assert shown["number"] == 2
    assert shown["status"] == "proposed"
    assert shown["origin"] == "suggested"
    assert shown["strength"] is None
    assert shown["progress"] is None
    assert shown["parent_number"] == 1
    assert shown["created_at"]


def test_suite_candidate_verifying_reports_progress_on_that_version(client, auth_headers, db_session):
    agent_id, _, fix = _failing_fix(client, auth_headers, db_session)
    other = _test(db_session, _criterion(client, auth_headers, agent_id, name="reg"), "fix")
    third = _test(db_session, _criterion(client, auth_headers, agent_id, name="reg2"), "fix")
    candidate = _candidate(
        db_session,
        agent_id,
        status="approved_verifying",
        verification={
            "strength": "weaker",
            "fix_test_id": fix.id,
            "approved_at": "2026-01-01T00:00:00",
            "required": [
                {"test_id": fix.id, "role": "fix", "before": "fail"},
                {"test_id": other.id, "role": "regression", "before": "pass"},
                {"test_id": third.id, "role": "regression", "before": "pass"},
            ],
        },
    )
    _result(db_session, fix, "replay", "pass", candidate.id)
    _result(db_session, other, "replay", "pass", candidate.id, stale=True)  # stale: not counted
    _result(db_session, third, "replay", "pass", _version_id(db_session, agent_id))  # other version
    shown = _suite_candidate(client, auth_headers, agent_id)
    assert shown["status"] == "approved_verifying"
    assert shown["strength"] == "weaker"
    assert shown["progress"] == {"done": 1, "total": 3}


@pytest.mark.parametrize(
    "verification",
    [
        "bad",
        ["x"],
        5,
        {"required": "nope"},
        {"required": None},
        {"required": 5},
        {"required": [None, 5, "x", {"role": "fix"}, {"test_id": 7}]},
        {"strength": ["full"], "required": []},
    ],
)
def test_suite_candidate_with_malformed_verification_never_fails_and_has_no_progress(
    client, auth_headers, db_session, verification
):
    agent_id, _, fix = _failing_fix(client, auth_headers, db_session)
    _candidate(db_session, agent_id, status="approved_verifying", verification=verification)

    r = client.get(f"/agents/{agent_id}/tests", headers=auth_headers)

    assert r.status_code == 200
    shown = r.json()["candidate"]
    assert shown["status"] == "approved_verifying"
    assert shown["progress"] is None
    assert shown["strength"] is None


def test_suite_candidate_progress_counts_only_well_formed_required_items(client, auth_headers, db_session):
    agent_id, _, fix = _failing_fix(client, auth_headers, db_session)
    other = _test(db_session, _criterion(client, auth_headers, agent_id, name="reg"), "fix")
    candidate = _candidate(
        db_session,
        agent_id,
        status="approved_verifying",
        verification={
            "strength": "weaker",
            "required": [
                {"test_id": fix.id, "role": "fix", "before": "fail"},
                {"role": "regression"},  # no test_id: ignored
                "junk",
                {"test_id": other.id, "role": "regression", "before": "pass"},
                {"test_id": "gone", "role": "regression", "before": "pass"},  # counts toward total only
            ],
        },
    )
    _result(db_session, fix, "replay", "pass", candidate.id)

    assert _suite_candidate(client, auth_headers, agent_id)["progress"] == {"done": 1, "total": 3}


def test_suite_candidate_after_resolution_shows_the_latest_promoted_or_failed_suggestion(
    client, auth_headers, db_session
):
    agent_id = _agent(client, auth_headers)
    _candidate(
        db_session, agent_id, number=2, status="failed_verification",
        verification={"strength": "full", "required": []},
    )
    promoted = _candidate(
        db_session, agent_id, number=3, status="promoted",
        verification={"strength": "full", "required": []},
    )
    _candidate(db_session, agent_id, number=4, status="rejected")
    shown = _suite_candidate(client, auth_headers, agent_id)
    assert shown["version_id"] == promoted.id
    assert shown["status"] == "promoted"
    assert shown["strength"] == "full"
    assert shown["progress"] is None


def test_suite_candidate_failed_verification_is_shown(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    failed = _candidate(db_session, agent_id, status="failed_verification")
    shown = _suite_candidate(client, auth_headers, agent_id)
    assert shown["version_id"] == failed.id
    assert shown["status"] == "failed_verification"


def test_suite_open_candidate_wins_over_a_resolved_one(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    _candidate(db_session, agent_id, number=2, status="promoted")
    open_one = _candidate(db_session, agent_id, number=3, status="proposed")
    assert _suite_candidate(client, auth_headers, agent_id)["version_id"] == open_one.id
