from unittest.mock import patch

import pytest
from sqlalchemy import update

import backend.api.versions as versions_api
from backend.api import limits
from backend.api.limits import RateLimiter
from backend.db.models import Agent, Criterion, EvalResult, EvalTest, Event, Job, PromptVersion
from backend.db.workspaces import create_workspace
from backend.jobs.queue import JOBS_PER_WORKSPACE_PER_HOUR, MAX_QUEUED_JOBS


def _agent(client, auth_headers):
    return client.post("/agents", json={"preset_id": "alex-sales"}, headers=auth_headers).json()["id"]


def _add_version(db_session, agent_id, number, status, origin="suggested", parent_id=None, **fields):
    version = PromptVersion(
        agent_id=agent_id,
        text=f"text {number}",
        parent_id=parent_id,
        origin=origin,
        status=status,
        number=number,
        **fields,
    )
    db_session.add(version)
    db_session.commit()
    return version


def _other_headers(db_session):
    _, token = create_workspace(db_session)
    return {"X-Workspace-Token": token}


@pytest.mark.parametrize("odd", [["strength", "full"], "full", 3])
def test_versions_list_tolerates_a_non_dict_verification(client, auth_headers, db_session, odd):
    agent_id = _agent(client, auth_headers)
    first = db_session.query(PromptVersion).filter_by(agent_id=agent_id).one()
    _add_version(db_session, agent_id, 2, "promoted", parent_id=first.id, verification=odd)

    r = client.get(f"/agents/{agent_id}/prompt-versions", headers=auth_headers)

    assert r.status_code == 200
    assert [row["strength"] for row in r.json()] == [None, None]


def test_versions_list_is_newest_first_with_is_current_and_strength(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    agent = db_session.get(Agent, agent_id)
    first = db_session.get(PromptVersion, agent.current_prompt_version_id)
    second = _add_version(
        db_session, agent_id, 2, "promoted", parent_id=first.id, verification={"strength": "full"}
    )
    third = _add_version(db_session, agent_id, 3, "proposed", parent_id=second.id)
    first.status = "superseded"
    agent.current_prompt_version_id = second.id
    db_session.commit()

    r = client.get(f"/agents/{agent_id}/prompt-versions", headers=auth_headers)
    assert r.status_code == 200
    rows = r.json()
    assert [row["number"] for row in rows] == [3, 2, 1]
    assert [row["is_current"] for row in rows] == [False, True, False]
    assert rows[0] == {
        "id": third.id,
        "number": 3,
        "origin": "suggested",
        "status": "proposed",
        "strength": None,
        "created_at": rows[0]["created_at"],
        "is_current": False,
    }
    assert rows[1]["strength"] == "full"
    assert rows[2]["origin"] == "initial"
    assert rows[2]["status"] == "superseded"


def test_version_detail_returns_text_and_parent_text(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    agent = db_session.get(Agent, agent_id)
    first = db_session.get(PromptVersion, agent.current_prompt_version_id)
    child = _add_version(db_session, agent_id, 2, "proposed", parent_id=first.id)

    r = client.get(f"/prompt-versions/{child.id}", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == child.id
    assert body["number"] == 2
    assert body["text"] == "text 2"
    assert body["parent_text"] == first.text
    assert body["is_current"] is False

    root = client.get(f"/prompt-versions/{first.id}", headers=auth_headers).json()
    assert root["parent_text"] is None
    assert root["is_current"] is True
    assert root["text"] == first.text


def test_version_detail_with_a_missing_parent_row_has_null_parent_text(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    orphan = _add_version(db_session, agent_id, 2, "proposed", parent_id="gone")
    body = client.get(f"/prompt-versions/{orphan.id}", headers=auth_headers).json()
    assert body["parent_text"] is None


def test_versions_routes_are_workspace_scoped(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    version_id = db_session.get(Agent, agent_id).current_prompt_version_id
    other = _other_headers(db_session)
    assert client.get(f"/agents/{agent_id}/prompt-versions", headers=other).status_code == 404
    assert client.get(f"/prompt-versions/{version_id}", headers=other).status_code == 404
    assert client.get("/agents/nope/prompt-versions", headers=auth_headers).status_code == 404
    assert client.get("/prompt-versions/nope", headers=auth_headers).status_code == 404


def test_versions_routes_require_auth(client):
    assert client.get("/agents/x/prompt-versions").status_code == 401
    assert client.get("/prompt-versions/x").status_code == 401


# ---------------------------------------------------------------- approve / reject / restore / report

BUSY = "The test queue is busy. Try again in a minute."
VERIFYING = "A fix is being verified. Wait for it to finish."
NOT_APPROVABLE = "This fix can't be approved in its current state."
NOT_REJECTABLE = "This fix can't be rejected in its current state."
NOT_RESTORABLE = "This version can't be restored."
MISMATCH = "That fix no longer matches its test."
CAP = "Prompt version limit reached for this agent."


def _criterion_row(db_session, agent_id, position=1):
    crit = Criterion(
        agent_id=agent_id,
        position=position,
        name=f"c{position}",
        definition="d",
        good_example="g",
        bad_example="b",
        why_discovered="w",
        source="manual",
    )
    db_session.add(crit)
    db_session.commit()
    return crit


def _test_row(db_session, crit, role="fix"):
    test = EvalTest(criterion_id=crit.id, role=role, scripted_user_messages=["hi"])
    db_session.add(test)
    db_session.commit()
    return test


def _result_row(db_session, test, version_id, verdict, kind="replay"):
    db_session.add(
        EvalResult(test_id=test.id, prompt_version_id=version_id, kind=kind, verdict=verdict, rationale="r")
    )
    db_session.commit()


def _proposal(client, auth_headers, db_session, with_heldout=True, with_regression=True):
    """An agent with a failing fix test (+ held-out, + one passing regression) and a proposed candidate."""
    agent_id = _agent(client, auth_headers)
    agent = db_session.get(Agent, agent_id)
    current = agent.current_prompt_version_id
    crit = _criterion_row(db_session, agent_id)
    fix = _test_row(db_session, crit, "fix")
    _result_row(db_session, fix, current, "fail")
    held = regression = None
    if with_heldout:
        held = _test_row(db_session, crit, "heldout")
        _result_row(db_session, held, current, "fail")
    if with_regression:
        regression = _test_row(db_session, _criterion_row(db_session, agent_id, 2), "fix")
        _result_row(db_session, regression, current, "pass")
    candidate = _add_version(
        db_session,
        agent_id,
        2,
        "proposed",
        parent_id=current,
        source_test_id=fix.id,
        change_report={"why": {"criterion_name": "c1"}, "outcome": None},
    )
    return {
        "agent_id": agent_id,
        "current": current,
        "candidate": candidate,
        "fix": fix,
        "held": held,
        "regression": regression,
    }


def _jobs(db_session, job_type=None):
    db_session.expire_all()
    query = db_session.query(Job)
    if job_type:
        query = query.filter(Job.type == job_type)
    return query.all()


def _reload(db_session, version):
    db_session.expire_all()
    return db_session.get(PromptVersion, version.id)


def _events(db_session, name):
    db_session.expire_all()
    return db_session.query(Event).filter(Event.name == name).all()


def _approve(client, headers, version_id):
    return client.post(f"/prompt-versions/{version_id}/approve", headers=headers)


def _fill_queue(db_session, count=MAX_QUEUED_JOBS):
    for _ in range(count):
        workspace, _token = create_workspace(db_session)
        db_session.add(Job(workspace_id=workspace.id, type="replay", payload={"x": 1}, status="queued"))
    db_session.commit()


def _set_status_after_lock(db_session, version_id, status):
    """A `lock_agent` stand-in that first commits a rival's status change (a lost race, simulated)."""
    real_lock = versions_api.lock_agent

    def lock_after_rival_commit(db, agent):
        db.execute(
            update(PromptVersion)
            .where(PromptVersion.id == version_id)
            .values(status=status)
            .execution_options(synchronize_session=False)
        )
        real_lock(db, agent)

    return lock_after_rival_commit


# ----- approve


def test_approve_happy_path_stores_verification_and_enqueues_one_job_per_required_test(
    client, auth_headers, db_session
):
    p = _proposal(client, auth_headers, db_session)
    r = _approve(client, auth_headers, p["candidate"].id)
    assert r.status_code == 202
    body = r.json()
    assert body["version_id"] == p["candidate"].id
    assert body["status"] == "approved_verifying"
    expected_tests = [p["fix"].id, p["held"].id, p["regression"].id]
    assert [j["test_id"] for j in body["jobs"]] == expected_tests

    version = _reload(db_session, p["candidate"])
    assert version.status == "approved_verifying"
    assert version.verification["strength"] == "full"
    assert version.verification["fix_test_id"] == p["fix"].id
    assert [x["test_id"] for x in version.verification["required"]] == expected_tests

    jobs = {job.id: job for job in _jobs(db_session, "replay")}
    assert set(jobs) == {j["job_id"] for j in body["jobs"]}
    for entry in body["jobs"]:
        assert jobs[entry["job_id"]].payload == {
            "test_id": entry["test_id"],
            "prompt_version_id": p["candidate"].id,
            "system": True,
        }
        assert jobs[entry["job_id"]].status == "queued"
    assert db_session.get(Agent, p["agent_id"]).current_prompt_version_id == p["current"]
    (event,) = _events(db_session, "fix_approved")
    assert event.properties["strength"] == "full"


def test_approve_weaker_without_heldout(client, auth_headers, db_session):
    p = _proposal(client, auth_headers, db_session, with_heldout=False, with_regression=False)
    body = _approve(client, auth_headers, p["candidate"].id).json()
    assert [j["test_id"] for j in body["jobs"]] == [p["fix"].id]
    assert _reload(db_session, p["candidate"]).verification["strength"] == "weaker"


def test_approve_ignores_the_hourly_cap(client, auth_headers, db_session):
    p = _proposal(client, auth_headers, db_session)
    workspace_id = db_session.get(Agent, p["agent_id"]).workspace_id
    for _ in range(JOBS_PER_WORKSPACE_PER_HOUR):
        db_session.add(Job(workspace_id=workspace_id, type="replay", payload={"x": 1}, status="done"))
    db_session.commit()
    r = _approve(client, auth_headers, p["candidate"].id)
    assert r.status_code == 202
    assert len(r.json()["jobs"]) == 3
    # Ordinary work is still refused by the (full) hourly cap.
    assert client.post(f"/tests/{p['fix'].id}/replay", headers=auth_headers).status_code == 429


def test_approve_jobs_do_not_use_up_the_hourly_allowance(client, auth_headers, db_session):
    p = _proposal(client, auth_headers, db_session)
    workspace_id = db_session.get(Agent, p["agent_id"]).workspace_id
    for _ in range(JOBS_PER_WORKSPACE_PER_HOUR - 1):
        db_session.add(Job(workspace_id=workspace_id, type="replay", payload={"x": 1}, status="done"))
    db_session.commit()
    assert _approve(client, auth_headers, p["candidate"].id).status_code == 202
    # 29 counted jobs + 3 exempt replays: one slot is still left.
    assert client.post(f"/tests/{p['fix'].id}/replay", headers=auth_headers).status_code == 202


def test_approve_does_not_call_the_llm_rate_limiter(client, auth_headers, db_session, monkeypatch):
    p = _proposal(client, auth_headers, db_session)
    monkeypatch.setattr(limits, "llm_limiter", RateLimiter(0, 60))
    assert _approve(client, auth_headers, p["candidate"].id).status_code == 202


def test_approve_is_refused_when_the_queue_cap_would_be_exceeded_and_changes_nothing(
    client, auth_headers, db_session
):
    p = _proposal(client, auth_headers, db_session)  # needs 3 jobs
    _fill_queue(db_session, MAX_QUEUED_JOBS - 2)
    r = _approve(client, auth_headers, p["candidate"].id)
    assert r.status_code == 429
    assert r.json()["detail"] == BUSY
    version = _reload(db_session, p["candidate"])
    assert version.status == "proposed"
    assert version.verification is None
    assert all(job.payload == {"x": 1} for job in _jobs(db_session, "replay"))
    assert len(_jobs(db_session, "replay")) == MAX_QUEUED_JOBS - 2


def test_approve_fits_exactly_up_to_the_queue_cap(client, auth_headers, db_session):
    p = _proposal(client, auth_headers, db_session)
    _fill_queue(db_session, MAX_QUEUED_JOBS - 3)
    assert _approve(client, auth_headers, p["candidate"].id).status_code == 202


def test_approve_twice_second_is_409_and_enqueues_nothing_more(client, auth_headers, db_session):
    p = _proposal(client, auth_headers, db_session)
    assert _approve(client, auth_headers, p["candidate"].id).status_code == 202
    again = _approve(client, auth_headers, p["candidate"].id)
    assert again.status_code == 409
    assert again.json()["detail"] == NOT_APPROVABLE
    assert len(_jobs(db_session, "replay")) == 3


def test_approve_while_another_version_is_verifying_is_409(client, auth_headers, db_session):
    p = _proposal(client, auth_headers, db_session)
    other = _add_version(db_session, p["agent_id"], 3, "approved_verifying", parent_id=p["current"])
    r = _approve(client, auth_headers, p["candidate"].id)
    assert r.status_code == 409
    assert r.json()["detail"] == VERIFYING
    assert _reload(db_session, p["candidate"]).status == "proposed"
    assert _reload(db_session, other).status == "approved_verifying"
    assert _jobs(db_session) == []


def test_reject_then_approve_is_409(client, auth_headers, db_session):
    p = _proposal(client, auth_headers, db_session)
    assert client.post(f"/prompt-versions/{p['candidate'].id}/reject", headers=auth_headers).status_code == 200
    r = _approve(client, auth_headers, p["candidate"].id)
    assert r.status_code == 409
    assert r.json()["detail"] == NOT_APPROVABLE
    assert _jobs(db_session) == []


@pytest.mark.parametrize("status", ["rejected", "promoted", "superseded", "failed_verification"])
def test_approve_requires_a_proposed_version(client, auth_headers, db_session, status):
    p = _proposal(client, auth_headers, db_session)
    db_session.execute(update(PromptVersion).where(PromptVersion.id == p["candidate"].id).values(status=status))
    db_session.commit()
    r = _approve(client, auth_headers, p["candidate"].id)
    assert r.status_code == 409
    assert r.json()["detail"] == NOT_APPROVABLE


def test_approve_loses_a_race_with_a_manual_edit_that_supersedes_the_proposal(
    client, auth_headers, db_session
):
    """Simulated: the status flips after the route's first read and before its locked re-read."""
    p = _proposal(client, auth_headers, db_session)
    with patch("backend.api.versions.lock_agent", _set_status_after_lock(db_session, p["candidate"].id, "superseded")):
        r = _approve(client, auth_headers, p["candidate"].id)
    assert r.status_code == 409
    assert r.json()["detail"] == NOT_APPROVABLE
    assert _reload(db_session, p["candidate"]).status == "superseded"
    assert _jobs(db_session) == []


STALE_FIX = "That fix is based on an older prompt. Reject it and suggest a new fix."


def test_approve_is_409_when_the_candidate_is_based_on_an_older_prompt(client, auth_headers, db_session):
    """Simulated: the prompt moved on (a rival commit) while the proposal stayed `proposed`."""
    p = _proposal(client, auth_headers, db_session)
    newer = _add_version(db_session, p["agent_id"], 3, "promoted", origin="manual", parent_id=p["current"])
    db_session.execute(
        update(Agent).where(Agent.id == p["agent_id"]).values(current_prompt_version_id=newer.id)
    )
    db_session.commit()

    r = _approve(client, auth_headers, p["candidate"].id)

    assert r.status_code == 409
    assert r.json()["detail"] == STALE_FIX
    assert _reload(db_session, p["candidate"]).status == "proposed"
    assert _jobs(db_session) == []


def test_approve_after_a_manual_edit_supersedes_the_proposal_is_409(client, auth_headers, db_session):
    p = _proposal(client, auth_headers, db_session)
    r = client.put(f"/agents/{p['agent_id']}/prompt", json={"text": "Edited by hand."}, headers=auth_headers)
    assert r.status_code == 200
    assert _approve(client, auth_headers, p["candidate"].id).status_code == 409
    assert _jobs(db_session) == []


def test_manual_edit_after_approval_is_409_and_leaves_the_verifying_version_untouched(
    client, auth_headers, db_session
):
    p = _proposal(client, auth_headers, db_session)
    assert _approve(client, auth_headers, p["candidate"].id).status_code == 202
    r = client.put(f"/agents/{p['agent_id']}/prompt", json={"text": "Edited by hand."}, headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"] == VERIFYING
    assert _reload(db_session, p["candidate"]).status == "approved_verifying"
    assert db_session.get(Agent, p["agent_id"]).current_prompt_version_id == p["current"]
    assert db_session.query(PromptVersion).filter_by(agent_id=p["agent_id"]).count() == 2


@pytest.mark.parametrize("kind", ["missing", "other_agent", "wrong_role", "no_source"])
def test_approve_is_409_when_the_source_test_no_longer_matches(client, auth_headers, db_session, kind):
    p = _proposal(client, auth_headers, db_session)
    candidate = db_session.get(PromptVersion, p["candidate"].id)
    if kind == "missing":
        candidate.source_test_id = "gone"
    elif kind == "no_source":
        candidate.source_test_id = None
    elif kind == "other_agent":
        other_agent = _agent(client, auth_headers)
        candidate.source_test_id = _test_row(db_session, _criterion_row(db_session, other_agent), "fix").id
    else:  # a held-out test is never a fix test
        candidate.source_test_id = p["held"].id
    db_session.commit()
    r = _approve(client, auth_headers, p["candidate"].id)
    assert r.status_code == 409
    assert r.json()["detail"] == MISMATCH
    assert _reload(db_session, p["candidate"]).status == "proposed"
    assert _jobs(db_session) == []


def test_approve_reuses_an_active_job_with_the_same_payload(client, auth_headers, db_session):
    p = _proposal(client, auth_headers, db_session, with_heldout=False, with_regression=False)
    workspace_id = db_session.get(Agent, p["agent_id"]).workspace_id
    existing = Job(
        workspace_id=workspace_id,
        type="replay",
        payload={"test_id": p["fix"].id, "prompt_version_id": p["candidate"].id, "system": True},
        status="running",
    )
    db_session.add(existing)
    db_session.commit()
    body = _approve(client, auth_headers, p["candidate"].id).json()
    assert body["jobs"] == [{"test_id": p["fix"].id, "job_id": existing.id}]
    assert len(_jobs(db_session, "replay")) == 1


def test_a_reused_active_job_does_not_count_toward_the_queue_cap(client, auth_headers, db_session):
    p = _proposal(client, auth_headers, db_session, with_heldout=False, with_regression=False)
    workspace_id = db_session.get(Agent, p["agent_id"]).workspace_id
    db_session.add(
        Job(
            workspace_id=workspace_id,
            type="replay",
            payload={"test_id": p["fix"].id, "prompt_version_id": p["candidate"].id, "system": True},
            status="queued",
        )
    )
    db_session.commit()
    _fill_queue(db_session, MAX_QUEUED_JOBS - 1)  # with the reusable job the backlog is exactly full
    assert _approve(client, auth_headers, p["candidate"].id).status_code == 202


def test_approve_enqueues_one_job_even_if_a_test_is_required_twice(client, auth_headers, db_session):
    p = _proposal(client, auth_headers, db_session, with_heldout=False, with_regression=False)
    real = versions_api.approve_candidate

    def doubled(db, agent, version):
        required = real(db, agent, version)
        return required + required

    with patch("backend.api.versions.approve_candidate", doubled):
        body = _approve(client, auth_headers, p["candidate"].id).json()
    assert [j["test_id"] for j in body["jobs"]] == [p["fix"].id]
    assert len(_jobs(db_session, "replay")) == 1


def test_a_failure_while_enqueueing_leaves_the_version_proposed(client, auth_headers, db_session):
    p = _proposal(client, auth_headers, db_session)
    real = versions_api.enqueue
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return real(*args, **kwargs)

    with patch("backend.api.versions.enqueue", flaky), pytest.raises(RuntimeError):
        _approve(client, auth_headers, p["candidate"].id)
    version = _reload(db_session, p["candidate"])
    assert version.status == "proposed"
    assert version.verification is None
    assert _jobs(db_session) == []


# ----- reject


def test_reject_marks_the_version_rejected_and_logs(client, auth_headers, db_session):
    p = _proposal(client, auth_headers, db_session)
    r = client.post(f"/prompt-versions/{p['candidate'].id}/reject", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"version_id": p["candidate"].id, "status": "rejected"}
    assert _reload(db_session, p["candidate"]).status == "rejected"
    assert len(_events(db_session, "fix_rejected")) == 1
    assert db_session.get(Agent, p["agent_id"]).current_prompt_version_id == p["current"]


@pytest.mark.parametrize(
    "status", ["approved_verifying", "rejected", "promoted", "superseded", "failed_verification"]
)
def test_reject_is_409_unless_proposed(client, auth_headers, db_session, status):
    p = _proposal(client, auth_headers, db_session)
    db_session.execute(update(PromptVersion).where(PromptVersion.id == p["candidate"].id).values(status=status))
    db_session.commit()
    r = client.post(f"/prompt-versions/{p['candidate'].id}/reject", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"] == NOT_REJECTABLE
    assert _reload(db_session, p["candidate"]).status == status
    assert _events(db_session, "fix_rejected") == []


def test_reject_loses_a_race_with_approve(client, auth_headers, db_session):
    """Simulated: approval commits after the reject route's first read and before its locked re-read."""
    p = _proposal(client, auth_headers, db_session)
    lock = _set_status_after_lock(db_session, p["candidate"].id, "approved_verifying")
    with patch("backend.api.versions.lock_agent", lock):
        r = client.post(f"/prompt-versions/{p['candidate'].id}/reject", headers=auth_headers)
    assert r.status_code == 409
    assert _reload(db_session, p["candidate"]).status == "approved_verifying"


def test_approve_then_reject_exactly_one_wins(client, auth_headers, db_session):
    p = _proposal(client, auth_headers, db_session)
    assert _approve(client, auth_headers, p["candidate"].id).status_code == 202
    assert client.post(f"/prompt-versions/{p['candidate'].id}/reject", headers=auth_headers).status_code == 409
    assert _reload(db_session, p["candidate"]).status == "approved_verifying"


# ----- restore


def test_restore_creates_a_new_numbered_promoted_version_and_supersedes_the_current(
    client, auth_headers, db_session
):
    agent_id = _agent(client, auth_headers)
    agent = db_session.get(Agent, agent_id)
    first = db_session.get(PromptVersion, agent.current_prompt_version_id)
    edit = client.put(f"/agents/{agent_id}/prompt", json={"text": "Second."}, headers=auth_headers)
    assert edit.status_code == 200
    r = client.post(f"/prompt-versions/{first.id}/restore", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["number"] == 3
    assert body["origin"] == "restored"
    assert body["status"] == "promoted"
    assert body["is_current"] is True
    db_session.expire_all()
    new = db_session.get(PromptVersion, body["id"])
    assert new.text == first.text and new.parent_id == first.id
    assert db_session.get(Agent, agent_id).current_prompt_version_id == new.id
    statuses = {v.number: v.status for v in db_session.query(PromptVersion).filter_by(agent_id=agent_id)}
    assert statuses == {1: "superseded", 2: "superseded", 3: "promoted"}
    assert len(_events(db_session, "version_restored")) == 1


def test_restore_supersedes_a_proposed_candidate(client, auth_headers, db_session):
    p = _proposal(client, auth_headers, db_session)
    r = client.post(f"/prompt-versions/{p['current']}/restore", headers=auth_headers)
    assert r.status_code == 200
    assert _reload(db_session, p["candidate"]).status == "superseded"


def test_restore_is_409_while_a_version_is_verifying(client, auth_headers, db_session):
    p = _proposal(client, auth_headers, db_session)
    assert _approve(client, auth_headers, p["candidate"].id).status_code == 202
    r = client.post(f"/prompt-versions/{p['current']}/restore", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"] == VERIFYING
    assert db_session.query(PromptVersion).filter_by(agent_id=p["agent_id"]).count() == 2
    assert db_session.get(Agent, p["agent_id"]).current_prompt_version_id == p["current"]


@pytest.mark.parametrize("status", ["proposed", "rejected", "approved_verifying"])
def test_restore_is_409_for_unrestorable_statuses(client, auth_headers, db_session, status):
    agent_id = _agent(client, auth_headers)
    version = _add_version(db_session, agent_id, 2, status)
    r = client.post(f"/prompt-versions/{version.id}/restore", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"] == NOT_RESTORABLE
    assert db_session.query(PromptVersion).filter_by(agent_id=agent_id).count() == 2
    assert _events(db_session, "version_restored") == []


@pytest.mark.parametrize("status", ["superseded", "failed_verification"])
def test_restore_allows_superseded_and_failed_versions(client, auth_headers, db_session, status):
    agent_id = _agent(client, auth_headers)
    version = _add_version(db_session, agent_id, 2, status, verification={"strength": "weaker", "required": []})
    assert client.post(f"/prompt-versions/{version.id}/restore", headers=auth_headers).status_code == 200


def test_restore_is_409_for_a_suggestion_that_was_never_approved(client, auth_headers, db_session):
    """A proposal that a manual edit superseded was never verified: it must not go live."""
    p = _proposal(client, auth_headers, db_session)
    edit = client.put(f"/agents/{p['agent_id']}/prompt", json={"text": "Edited."}, headers=auth_headers)
    assert edit.status_code == 200
    assert _reload(db_session, p["candidate"]).status == "superseded"
    count = db_session.query(PromptVersion).filter_by(agent_id=p["agent_id"]).count()

    r = client.post(f"/prompt-versions/{p['candidate'].id}/restore", headers=auth_headers)

    assert r.status_code == 409
    assert r.json()["detail"] == NOT_RESTORABLE
    assert db_session.query(PromptVersion).filter_by(agent_id=p["agent_id"]).count() == count
    assert db_session.get(Agent, p["agent_id"]).current_prompt_version_id != p["candidate"].id
    assert _events(db_session, "version_restored") == []


def _fill_versions(db_session, agent_id, count, status, start=2):
    for number in range(start, start + count):
        db_session.add(
            PromptVersion(agent_id=agent_id, text=f"t{number}", origin="manual", status=status, number=number)
        )
    db_session.commit()


def test_restore_is_429_at_the_counted_version_cap(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    _fill_versions(db_session, agent_id, 19, "superseded")  # 20 counted versions
    old = db_session.query(PromptVersion).filter_by(agent_id=agent_id, number=2).one()
    r = client.post(f"/prompt-versions/{old.id}/restore", headers=auth_headers)
    assert r.status_code == 429
    assert r.json()["detail"] == CAP
    assert db_session.query(PromptVersion).filter_by(agent_id=agent_id).count() == 20


def test_restore_is_429_at_the_hard_row_cap(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    old = _add_version(db_session, agent_id, 2, "superseded")
    _fill_versions(db_session, agent_id, 58, "rejected", start=3)  # 60 rows, only 2 counted
    r = client.post(f"/prompt-versions/{old.id}/restore", headers=auth_headers)
    assert r.status_code == 429
    assert r.json()["detail"] == CAP
    assert db_session.query(PromptVersion).filter_by(agent_id=agent_id).count() == 60


def test_restore_just_below_the_caps_succeeds(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    old = _add_version(db_session, agent_id, 2, "superseded", origin="manual")
    _fill_versions(db_session, agent_id, 57, "rejected", start=3)  # 59 rows
    assert client.post(f"/prompt-versions/{old.id}/restore", headers=auth_headers).status_code == 200


def test_restore_loses_a_race_with_an_approval(client, auth_headers, db_session):
    """Simulated: another version becomes approved_verifying after the route's first read."""
    p = _proposal(client, auth_headers, db_session)
    lock = _set_status_after_lock(db_session, p["candidate"].id, "approved_verifying")
    with patch("backend.api.versions.lock_agent", lock):
        r = client.post(f"/prompt-versions/{p['current']}/restore", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"] == VERIFYING
    assert db_session.query(PromptVersion).filter_by(agent_id=p["agent_id"]).count() == 2


# ----- report


def test_report_returns_the_stored_why_and_outcome(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    why = {"criterion_name": "c1", "failing_quote": "no"}
    outcome = {"status": "promoted", "reason": "ok", "table": [], "narrative": None, "explanation_unavailable": False}
    version = _add_version(
        db_session,
        agent_id,
        2,
        "promoted",
        verification={"strength": "full", "required": []},
        change_report={"why": why, "outcome": outcome},
    )
    r = client.get(f"/prompt-versions/{version.id}/report?view=1", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {
        "version_id": version.id,
        "number": 2,
        "status": "promoted",
        "strength": "full",
        "why": why,
        "outcome": outcome,
    }
    (event,) = _events(db_session, "report_viewed")
    assert event.properties == {"status": "promoted"}


@pytest.mark.parametrize("query", ["", "?view=0", "?view=true", "?view=2", "?other=1"])
def test_report_without_view_one_logs_no_event(client, auth_headers, db_session, query):
    agent_id = _agent(client, auth_headers)
    version = _add_version(db_session, agent_id, 2, "proposed", change_report={"why": {"a": 1}, "outcome": None})

    r = client.get(f"/prompt-versions/{version.id}/report{query}", headers=auth_headers)

    assert r.status_code == 200
    assert r.json()["why"] == {"a": 1}
    assert _events(db_session, "report_viewed") == []


def test_report_view_one_logs_one_event_per_request(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    version = _add_version(db_session, agent_id, 2, "proposed", change_report={"why": {"a": 1}, "outcome": None})
    for _ in range(2):
        assert client.get(f"/prompt-versions/{version.id}/report?view=1", headers=auth_headers).status_code == 200
    client.get(f"/prompt-versions/{version.id}/report", headers=auth_headers)
    assert len(_events(db_session, "report_viewed")) == 2


def test_report_outcome_is_null_while_verifying(client, auth_headers, db_session):
    p = _proposal(client, auth_headers, db_session)
    assert _approve(client, auth_headers, p["candidate"].id).status_code == 202
    body = client.get(f"/prompt-versions/{p['candidate'].id}/report", headers=auth_headers).json()
    assert body["status"] == "approved_verifying"
    assert body["outcome"] is None
    assert body["why"] == {"criterion_name": "c1"}
    assert body["strength"] == "full"


@pytest.mark.parametrize("report", [None, "oops", [], {"why": "x", "outcome": 5}, {}])
@pytest.mark.parametrize("verification", [None, "bad", ["x"]])
def test_report_is_defensive_about_malformed_stored_data(client, auth_headers, db_session, report, verification):
    agent_id = _agent(client, auth_headers)
    version = _add_version(db_session, agent_id, 2, "proposed", change_report=report, verification=verification)
    r = client.get(f"/prompt-versions/{version.id}/report", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["why"] is None
    assert body["outcome"] is None
    assert body["strength"] is None
    assert body["number"] == 2


def test_report_still_works_when_event_logging_fails(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    version = _add_version(db_session, agent_id, 2, "proposed", change_report={"why": {"a": 1}, "outcome": None})
    with patch("backend.db.events.log_event", side_effect=RuntimeError("down")):
        r = client.get(f"/prompt-versions/{version.id}/report?view=1", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["why"] == {"a": 1}


# ----- ownership and auth for every new route


def _routes(version_id):
    return [
        ("post", f"/prompt-versions/{version_id}/approve"),
        ("post", f"/prompt-versions/{version_id}/reject"),
        ("post", f"/prompt-versions/{version_id}/restore"),
        ("get", f"/prompt-versions/{version_id}/report"),
    ]


def test_new_routes_are_404_across_workspaces_and_change_nothing(client, auth_headers, db_session):
    p = _proposal(client, auth_headers, db_session)
    other = _other_headers(db_session)
    for method, url in _routes(p["candidate"].id):
        assert getattr(client, method)(url, headers=other).status_code == 404, url
    for method, url in _routes("nope"):
        assert getattr(client, method)(url, headers=auth_headers).status_code == 404, url
    version = _reload(db_session, p["candidate"])
    assert version.status == "proposed" and version.verification is None
    assert _jobs(db_session) == []
    assert db_session.query(PromptVersion).filter_by(agent_id=p["agent_id"]).count() == 2
    assert _events(db_session, "report_viewed") == []


def test_new_routes_require_a_token(client):
    for method, url in _routes("x"):
        assert getattr(client, method)(url).status_code == 401, url
