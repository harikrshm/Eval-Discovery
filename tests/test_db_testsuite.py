from datetime import timedelta

import pytest

from backend.db.models import (
    Agent,
    Criterion,
    EvalResult,
    EvalTest,
    PromptVersion,
    Workspace,
    utcnow,
)
from backend.db.testsuite import (
    agent_tests,
    criterion_status_for,
    current_replay,
    get_owned_criterion,
    get_owned_test,
    latest_baseline,
    mark_criterion_results_stale,
    stale_test_count,
)


def _workspace(db, token="h"):
    ws = Workspace(token_hash=token, expires_at=utcnow())
    db.add(ws)
    db.flush()
    agent = Agent(workspace_id=ws.id, name="Alex", user_role_label="Customer")
    db.add(agent)
    db.flush()
    pv = PromptVersion(agent_id=agent.id, text="p", origin="initial", status="promoted")
    db.add(pv)
    db.flush()
    agent.current_prompt_version_id = pv.id
    return ws, agent, pv


def _criterion(db, agent, position=1):
    crit = Criterion(
        agent_id=agent.id,
        position=position,
        name=f"c{position}",
        definition="d",
        good_example="g",
        bad_example="b",
        why_discovered="w",
        source="discovered",
        judge_check={"ok": True},
    )
    db.add(crit)
    db.flush()
    return crit


def _test(db, crit, role="fix"):
    t = EvalTest(criterion_id=crit.id, role=role, scripted_user_messages=["hi"])
    db.add(t)
    db.flush()
    return t


_clock = [0]


def _result(db, test, pv_id, kind="replay", verdict="pass", stale=False):
    _clock[0] += 1
    r = EvalResult(
        test_id=test.id,
        prompt_version_id=pv_id,
        kind=kind,
        verdict=verdict,
        rationale="r",
        stale=stale,
        created_at=utcnow() + timedelta(seconds=_clock[0]),
    )
    db.add(r)
    db.flush()
    return r


def test_ownership_for_owner_and_foreign_workspace(db_session):
    ws, agent, _ = _workspace(db_session)
    other_ws, _, _ = _workspace(db_session, token="h2")
    crit = _criterion(db_session, agent)
    test = _test(db_session, crit)
    assert get_owned_criterion(db_session, ws, crit.id) is crit
    assert get_owned_test(db_session, ws, test.id) is test
    for fn, ident in ((get_owned_criterion, crit.id), (get_owned_test, test.id)):
        with pytest.raises(LookupError):
            fn(db_session, other_ws, ident)
        with pytest.raises(LookupError):
            fn(db_session, ws, "missing")


def test_agent_tests_ordered_by_criterion_position_then_fix_before_heldout(db_session):
    _, agent, _ = _workspace(db_session)
    c2 = _criterion(db_session, agent, position=2)
    c1 = _criterion(db_session, agent, position=1)
    t2h = _test(db_session, c2, "heldout")
    t2f = _test(db_session, c2, "fix")
    t1h = _test(db_session, c1, "heldout")
    t1f = _test(db_session, c1, "fix")
    assert agent_tests(db_session, agent) == [t1f, t1h, t2f, t2h]


def test_agent_tests_excludes_other_agents(db_session):
    _, agent, _ = _workspace(db_session)
    _, other, _ = _workspace(db_session, token="h2")
    _test(db_session, _criterion(db_session, other))
    assert agent_tests(db_session, agent) == []


def test_current_replay_ignores_baseline_stale_and_other_versions_and_returns_newest(db_session):
    _, agent, pv = _workspace(db_session)
    test = _test(db_session, _criterion(db_session, agent))
    assert current_replay(db_session, test, pv.id) is None
    _result(db_session, test, pv.id, kind="baseline", verdict="fail")
    _result(db_session, test, pv.id, stale=True)
    _result(db_session, test, "other-version")
    assert current_replay(db_session, test, pv.id) is None
    older = _result(db_session, test, pv.id, verdict="fail")
    newest = _result(db_session, test, pv.id, verdict="pass")
    assert current_replay(db_session, test, pv.id) is newest
    assert older is not newest


def test_latest_baseline_returns_newest_baseline_only(db_session):
    _, agent, pv = _workspace(db_session)
    test = _test(db_session, _criterion(db_session, agent))
    assert latest_baseline(db_session, test) is None
    _result(db_session, test, pv.id, kind="replay")
    _result(db_session, test, pv.id, kind="baseline", verdict="fail")
    newest = _result(db_session, test, pv.id, kind="baseline", verdict="fail")
    assert latest_baseline(db_session, test) is newest


def test_stale_test_count(db_session):
    _, agent, pv = _workspace(db_session)
    crit = _criterion(db_session, agent)
    t1, t2 = _test(db_session, crit, "fix"), _test(db_session, crit, "heldout")
    assert stale_test_count(db_session, agent) == 2
    _result(db_session, t1, pv.id)
    assert stale_test_count(db_session, agent) == 1
    _result(db_session, t2, pv.id, stale=True)
    assert stale_test_count(db_session, agent) == 1
    _result(db_session, t2, pv.id)
    assert stale_test_count(db_session, agent) == 0


def test_mark_criterion_results_stale_leaves_baselines_and_other_criteria(db_session):
    _, agent, pv = _workspace(db_session)
    crit = _criterion(db_session, agent, 1)
    other = _criterion(db_session, agent, 2)
    test = _test(db_session, crit)
    other_test = _test(db_session, other)
    baseline = _result(db_session, test, pv.id, kind="baseline", verdict="fail")
    replay = _result(db_session, test, pv.id)
    other_replay = _result(db_session, other_test, pv.id)
    mark_criterion_results_stale(db_session, crit)
    db_session.flush()
    assert replay.stale is True
    assert baseline.stale is False
    assert other_replay.stale is False
    assert crit.judge_check is None
    assert other.judge_check == {"ok": True}
    assert current_replay(db_session, test, pv.id) is None


def test_mark_criterion_results_stale_bumps_only_that_criterions_revision(db_session):
    _, agent, _ = _workspace(db_session)
    crit = _criterion(db_session, agent, 1)
    other = _criterion(db_session, agent, 2)
    db_session.refresh(crit)
    assert crit.revision == 0
    mark_criterion_results_stale(db_session, crit)
    mark_criterion_results_stale(db_session, crit)
    db_session.flush()
    db_session.refresh(crit)
    db_session.refresh(other)
    assert crit.revision == 2
    assert other.revision == 0


def test_criterion_status_for_end_to_end(db_session):
    _, agent, pv = _workspace(db_session)
    crit = _criterion(db_session, agent)
    assert criterion_status_for(db_session, crit, pv.id) == "untested"  # no tests
    fix, held = _test(db_session, crit, "fix"), _test(db_session, crit, "heldout")
    assert criterion_status_for(db_session, crit, pv.id) == "untested"
    _result(db_session, fix, pv.id, verdict="pass")
    assert criterion_status_for(db_session, crit, pv.id) == "untested"
    _result(db_session, held, pv.id, verdict="pass")
    assert criterion_status_for(db_session, crit, pv.id) == "passing"
    _result(db_session, held, pv.id, verdict="fail")
    assert criterion_status_for(db_session, crit, pv.id) == "failing"
    # a newer prompt version has no replays yet
    assert criterion_status_for(db_session, crit, "v2") == "untested"
