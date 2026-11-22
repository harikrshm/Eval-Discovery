from unittest.mock import patch

import pytest

from backend.api import limits
from backend.db.session import get_db
from backend.main import app
from simulation.appdriver import AppDriver, DriverError
from simulation.clients import install_client
from simulation.fakeclient import Behavior, FakeGroq
from simulation.scenarios import SCENARIOS

SCENARIO = SCENARIOS["support-refund"]
LIMITERS = ("llm_limiter", "global_llm_limiter", "workspace_limiter")


@pytest.fixture
def fake():
    model = FakeGroq(Behavior(SCENARIO.guardrail_marker, SCENARIO.violation_text))
    with install_client(model):
        yield model


def _find_test(suite: dict, role: str) -> dict:
    for criterion in suite["criteria"]:
        for test in criterion["tests"]:
            if test["role"] == role:
                return test
    raise AssertionError(f"no {role} test in suite")


def _tests(suite: dict) -> list[dict]:
    return [test for criterion in suite["criteria"] for test in criterion["tests"]]


def test_full_flywheel_flow_through_the_real_routes(fake):
    with AppDriver() as driver:
        agent_id = driver.create_agent(SCENARIO.agent_name, SCENARIO.weak_prompt)
        assert isinstance(agent_id, str) and agent_id
        text, number = driver.agent_prompt(agent_id)
        assert text == SCENARIO.weak_prompt and number == 1

        history = [{"role": "user", "content": "My blender arrived broken, I want my money back."}]
        reply = driver.chat(agent_id, history)
        assert SCENARIO.violation_text in reply

        for _ in range(2):
            first = [{"role": "user", "content": "My blender arrived broken, I want my money back."}]
            first_reply = driver.chat(agent_id, first)
            convo = first + [
                {"role": "assistant", "content": first_reply},
                {"role": "user", "content": "So I will definitely get a refund?"},
            ]
            convo.append({"role": "assistant", "content": driver.chat(agent_id, convo)})
            saved = driver.save_run(agent_id, convo)
            assert saved["run"]["messages"] == convo
        assert saved["run_count"] == 2

        pattern = driver.check_pattern(agent_id)
        assert pattern["found"] is True and SCENARIO.violation_text in pattern["pattern_summary"]

        # build_tests judges the newest 8 runs and builds a held-out test from a second failing
        # one. Seven clean runs push the first failing run out of that window, so the suite gets
        # a fix test only and generate_heldout has something to do.
        for n in range(7):
            driver.save_run(
                agent_id,
                [
                    {"role": "user", "content": f"Where is my parcel {n}?"},
                    {"role": "assistant", "content": "It ships tomorrow."},
                    {"role": "user", "content": "Thanks."},
                ],
            )

        criterion = driver.discover(agent_id)
        assert criterion["id"] and criterion["name"]
        assert criterion["job_id"]
        assert driver.job(criterion["job_id"])["status"] == "queued"

        assert driver.drain() >= 1
        assert driver.job(criterion["job_id"])["status"] == "done"
        suite = driver.suite(agent_id)
        fix = _find_test(suite, "fix")
        assert fix["baseline"]["verdict"] == "fail"
        assert fix["current"] is None

        job_id = driver.replay_test(fix["id"])
        assert driver.job(job_id)["status"] == "queued"
        assert driver.drain() == 1
        assert driver.job(job_id)["status"] == "done"
        fix = _find_test(driver.suite(agent_id), "fix")
        assert fix["current"]["verdict"] == "fail"
        assert fix["current"]["kind"] == "replay"

        job_id = driver.generate_heldout(criterion["id"])
        driver.drain()
        assert driver.job(job_id)["result"]["created"] is True
        heldout = _find_test(driver.suite(agent_id), "heldout")
        assert heldout["origin"] == "generated"

        job_id = driver.suggest_fix(fix["id"])
        driver.drain()
        assert driver.job(job_id)["status"] == "done"
        candidate = driver.suite(agent_id)["candidate"]
        assert candidate["status"] == "proposed"

        approved = driver.approve(candidate["version_id"])
        assert approved["jobs"]
        assert driver.drain() >= len(approved["jobs"])

        new_text, new_number = driver.agent_prompt(agent_id)
        assert new_number == 2 > number
        assert SCENARIO.guardrail_marker in new_text and new_text != SCENARIO.weak_prompt
        assert driver.suite(agent_id)["candidate"]["status"] == "promoted"


def test_reject_and_edit_prompt(fake):
    with AppDriver() as driver:
        agent_id = driver.create_agent(SCENARIO.agent_name, SCENARIO.weak_prompt)
        text, number = driver.agent_prompt(agent_id)
        state_version = driver.suite(agent_id)["prompt_version_id"]

        unchanged = driver.edit_prompt(agent_id, text, state_version)
        assert unchanged["changed"] is False and unchanged["version_number"] == number

        edited = driver.edit_prompt(agent_id, text + "\nBe brief.", state_version)
        assert edited["changed"] is True and edited["version_number"] == 2
        assert driver.agent_prompt(agent_id) == (text + "\nBe brief.", 2)

        with pytest.raises(DriverError) as info:  # the base version moved on
            driver.edit_prompt(agent_id, "Another prompt.", state_version)
        assert info.value.status == 409

        with pytest.raises(DriverError) as info:  # not a candidate
            driver.reject(edited["version_id"])
        assert info.value.status == 409


def test_driver_error_carries_status_and_detail(fake):
    with AppDriver() as driver:
        with pytest.raises(DriverError) as info:
            driver.check_pattern("no-such-agent")
        assert info.value.status == 404
        assert info.value.detail == "Agent not found."
        assert "404" in str(info.value) and "Agent not found." in str(info.value)

        agent_id = driver.create_agent(SCENARIO.agent_name, SCENARIO.weak_prompt)
        with pytest.raises(DriverError) as info:
            driver.discover(agent_id)  # no pattern yet
        assert info.value.status == 409

        with pytest.raises(DriverError) as info:
            driver.create_agent("", "")  # fails request validation
        assert info.value.status == 422


def test_limiters_and_db_override_are_restored_on_exit(fake):
    before = {name: getattr(limits, name).max_calls for name in LIMITERS}
    instances = {name: getattr(limits, name) for name in LIMITERS}
    assert get_db not in app.dependency_overrides
    with AppDriver():
        assert get_db in app.dependency_overrides
        for name in LIMITERS:
            assert getattr(limits, name) is instances[name]
            assert getattr(limits, name).max_calls > 100_000
    assert get_db not in app.dependency_overrides
    assert {name: getattr(limits, name).max_calls for name in LIMITERS} == before
    assert all(getattr(limits, name) is instances[name] for name in LIMITERS)


def test_limiters_are_restored_when_the_body_raises(fake):
    before = {name: getattr(limits, name).max_calls for name in LIMITERS}
    with pytest.raises(RuntimeError):
        with AppDriver():
            raise RuntimeError("boom")
    assert {name: getattr(limits, name).max_calls for name in LIMITERS} == before
    assert get_db not in app.dependency_overrides


def test_existing_db_override_is_put_back(fake):
    sentinel = lambda: None  # noqa: E731
    app.dependency_overrides[get_db] = sentinel
    try:
        with AppDriver():
            assert app.dependency_overrides[get_db] is not sentinel
        assert app.dependency_overrides[get_db] is sentinel
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_driver_exceeds_the_per_ip_llm_limit(fake):
    with AppDriver() as driver:
        agent_id = driver.create_agent(SCENARIO.agent_name, SCENARIO.weak_prompt)
        for _ in range(40):  # the real per-IP limit is 30 a minute
            driver.chat(agent_id, [{"role": "user", "content": "hi"}])


def test_driver_does_not_install_a_model_client():
    import backend.groq_client as groq_client

    original = groq_client.client
    with AppDriver():
        assert groq_client.client is original
    assert groq_client.client is original


def test_drain_counts_jobs_and_needs_two_idle_passes():
    with AppDriver() as driver:
        with patch("backend.jobs.worker.run_one", side_effect=[True, False, True, True, False, False]) as run_one:
            assert driver.drain() == 3
        assert run_one.call_count == 6
        with patch("backend.jobs.worker.run_one", return_value=False) as run_one:
            assert driver.drain() == 0
        assert run_one.call_count == 2


def test_drain_raises_at_the_cap_when_a_job_keeps_requeueing():
    with AppDriver() as driver:
        with patch("backend.jobs.worker.run_one", return_value=True) as run_one:
            with pytest.raises(DriverError) as info:
                driver.drain(max_passes=7)
        assert run_one.call_count == 7
        assert info.value.status is None
        assert "7" in str(info.value)


def test_drain_runs_the_worker_on_the_shared_session():
    with AppDriver() as driver:
        with patch("backend.jobs.worker.run_one", return_value=False) as run_one:
            driver.drain()
        assert run_one.call_args.args[0] is driver._session
