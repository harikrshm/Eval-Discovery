import json

from backend.db.agents import new_prompt_version
from backend.db.models import Agent, Criterion, EvalResult, EvalTest
from backend.db.workspaces import create_workspace

TOP_KEYS = {"format", "version", "agent", "prompt", "criteria"}
AGENT_KEYS = {"name", "user_role_label"}
PROMPT_KEYS = {"text", "version_number"}
CRITERION_KEYS = {"id", "name", "definition", "good_example", "bad_example", "tests"}
TEST_KEYS = {"id", "role", "origin", "scripted_user_messages", "current_verdict"}


def _agent(client, auth_headers):
    return client.post("/agents", json={"preset_id": "alex-sales"}, headers=auth_headers).json()["id"]


def _criterion(client, auth_headers, agent_id, name="Answers pricing"):
    body = {
        "name": name,
        "definition": f"definition of {name}",
        "good_example": f"good {name}",
        "bad_example": f"bad {name}",
    }
    return client.post(f"/agents/{agent_id}/criteria", json=body, headers=auth_headers).json()["id"]


def _test(db_session, criterion_id, role="fix", origin="run", messages=("hi",)):
    test = EvalTest(
        criterion_id=criterion_id,
        role=role,
        origin=origin,
        source_run_id="secret-run-id",
        scripted_user_messages=list(messages),
    )
    db_session.add(test)
    db_session.commit()
    return test


def _result(db_session, test, verdict, version_id, kind="replay", stale=False):
    db_session.add(
        EvalResult(
            test_id=test.id,
            prompt_version_id=version_id,
            kind=kind,
            verdict=verdict,
            rationale="RATIONALE-MARKER-7731",
            transcript=[{"role": "assistant", "content": "TRANSCRIPT-MARKER-4412"}],
            stale=stale,
        )
    )
    db_session.commit()


def _version_id(db_session, agent_id):
    return db_session.get(Agent, agent_id).current_prompt_version_id


def _export(client, auth_headers, agent_id):
    return client.get(f"/agents/{agent_id}/export", headers=auth_headers)


def test_export_has_exact_key_sets(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    cid = _criterion(client, auth_headers, agent_id)
    test = _test(db_session, cid, messages=("one", "two"))
    _result(db_session, test, "pass", _version_id(db_session, agent_id))

    response = _export(client, auth_headers, agent_id)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == TOP_KEYS
    assert body["format"] == "eval-discovery.suite"
    assert body["version"] == 1
    assert set(body["agent"]) == AGENT_KEYS
    assert set(body["prompt"]) == PROMPT_KEYS
    assert body["prompt"]["version_number"] == 1
    assert len(body["criteria"]) == 1
    criterion = body["criteria"][0]
    assert set(criterion) == CRITERION_KEYS
    assert criterion["id"] == cid
    assert criterion["definition"] == "definition of Answers pricing"
    assert criterion["good_example"] == "good Answers pricing"
    assert criterion["bad_example"] == "bad Answers pricing"
    assert len(criterion["tests"]) == 1
    exported = criterion["tests"][0]
    assert set(exported) == TEST_KEYS
    assert exported["id"] == test.id
    assert exported["role"] == "fix"
    assert exported["origin"] == "run"
    assert exported["scripted_user_messages"] == ["one", "two"]
    assert exported["current_verdict"] == "pass"


def test_export_agent_and_prompt_values(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    agent = db_session.get(Agent, agent_id)
    new_prompt_version(db_session, agent, "Brand new prompt text")
    db_session.commit()

    body = _export(client, auth_headers, agent_id).json()

    assert body["agent"] == {"name": agent.name, "user_role_label": agent.user_role_label}
    assert body["prompt"] == {"text": "Brand new prompt text", "version_number": 2}
    assert body["criteria"] == []


def test_current_verdict_values_and_null_cases(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    version = _version_id(db_session, agent_id)
    cid = _criterion(client, auth_headers, agent_id)
    names = ("pass", "fail", "n_a", "none", "stale", "other", "baseline")
    # One test per role is unique per criterion, so spread them over criteria.
    cids = {name: _criterion(client, auth_headers, agent_id, name=name) for name in names}
    tests = {name: _test(db_session, cids[name], messages=(name,)) for name in names}
    _result(db_session, tests["pass"], "pass", version)
    _result(db_session, tests["fail"], "fail", version)
    _result(db_session, tests["n_a"], "not_applicable", version)
    _result(db_session, tests["stale"], "pass", version, stale=True)
    _result(db_session, tests["other"], "pass", "some-other-version")
    _result(db_session, tests["baseline"], "pass", version, kind="baseline")

    body = _export(client, auth_headers, agent_id).json()

    verdicts = {
        t["scripted_user_messages"][0]: t["current_verdict"] for c in body["criteria"] for t in c["tests"]
    }
    assert verdicts == {
        "pass": "pass",
        "fail": "fail",
        "n_a": "not_applicable",
        "none": None,
        "stale": None,
        "other": None,
        "baseline": None,
    }
    assert next(c for c in body["criteria"] if c["id"] == cid)["tests"] == []


def test_results_of_an_older_version_are_not_current(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    old_version = _version_id(db_session, agent_id)
    cid = _criterion(client, auth_headers, agent_id)
    test = _test(db_session, cid)
    _result(db_session, test, "pass", old_version)
    new_prompt_version(db_session, db_session.get(Agent, agent_id), "v2 text")
    db_session.commit()

    body = _export(client, auth_headers, agent_id).json()

    assert body["prompt"]["version_number"] == 2
    assert body["criteria"][0]["tests"][0]["current_verdict"] is None


def test_generated_heldout_test_is_included(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    cid = _criterion(client, auth_headers, agent_id)
    _test(db_session, cid, role="heldout", origin="generated", messages=("generated one",))
    _test(db_session, cid, role="fix", origin="run")

    tests = _export(client, auth_headers, agent_id).json()["criteria"][0]["tests"]

    assert [(t["role"], t["origin"]) for t in tests] == [("fix", "run"), ("heldout", "generated")]
    assert tests[1]["scripted_user_messages"] == ["generated one"]


def test_criterion_without_tests_has_empty_list(client, auth_headers):
    agent_id = _agent(client, auth_headers)
    _criterion(client, auth_headers, agent_id)

    criterion = _export(client, auth_headers, agent_id).json()["criteria"][0]

    assert criterion["tests"] == []


def test_ordering_by_position_then_fix_before_heldout(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    first = _criterion(client, auth_headers, agent_id, name="First")
    second = _criterion(client, auth_headers, agent_id, name="Second")
    # Create the later criterion's tests first, and each heldout before its fix, to defeat insertion order.
    _test(db_session, second, role="heldout", messages=("second-heldout",))
    _test(db_session, second, role="fix", messages=("second-fix",))
    _test(db_session, first, role="heldout", messages=("first-heldout",))
    _test(db_session, first, role="fix", messages=("first-fix",))

    body = _export(client, auth_headers, agent_id).json()

    assert [c["id"] for c in body["criteria"]] == [first, second]
    assert [t["scripted_user_messages"][0] for t in body["criteria"][0]["tests"]] == ["first-fix", "first-heldout"]
    assert [t["scripted_user_messages"][0] for t in body["criteria"][1]["tests"]] == [
        "second-fix",
        "second-heldout",
    ]


def test_criteria_follow_position_not_creation_order(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    a = _criterion(client, auth_headers, agent_id, name="A")
    b = _criterion(client, auth_headers, agent_id, name="B")
    db_session.get(Criterion, a).position = 2
    db_session.get(Criterion, b).position = 1
    db_session.commit()

    body = _export(client, auth_headers, agent_id).json()

    assert [c["id"] for c in body["criteria"]] == [b, a]


def test_export_never_leaks_results_or_internal_data(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    version = _version_id(db_session, agent_id)
    cid = _criterion(client, auth_headers, agent_id)
    test = _test(db_session, cid)
    _result(db_session, test, "fail", version)
    _result(db_session, test, "fail", version, kind="baseline")
    db_session.get(Criterion, cid).judge_check = {"verdict": "JUDGE-CHECK-MARKER-9090"}
    db_session.commit()
    workspace_id = db_session.get(Agent, agent_id).workspace_id

    text = json.dumps(_export(client, auth_headers, agent_id).json())

    for leaked in (
        "RATIONALE-MARKER-7731",
        "TRANSCRIPT-MARKER-4412",
        "JUDGE-CHECK-MARKER-9090",
        "secret-run-id",
        "source_run_id",
        "judge_check",
        "rationale",
        "transcript",
        workspace_id,
        agent_id,
        version,
    ):
        assert leaked not in text


def test_foreign_workspace_gets_404(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    _, other_token = create_workspace(db_session)

    response = client.get(f"/agents/{agent_id}/export", headers={"X-Workspace-Token": other_token})

    assert response.status_code == 404


def test_missing_agent_gets_404(client, auth_headers):
    assert _export(client, auth_headers, "no-such-agent").status_code == 404


def test_missing_token_gets_401(client, auth_headers):
    agent_id = _agent(client, auth_headers)

    assert client.get(f"/agents/{agent_id}/export").status_code == 401
