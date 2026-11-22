from datetime import timedelta

import pytest
from sqlalchemy import update

from backend.core.config import PRESETS
from backend.core.promotion import RequiredTest
from backend.db.agents import (
    MAX_PROMPT_VERSIONS_PER_AGENT,
    MAX_VERSION_ROWS_PER_AGENT,
    create_agent,
    new_prompt_version,
    prompt_version_count,
    prompt_version_row_count,
)
from backend.db.candidates import (
    CandidateOpen,
    ParentChanged,
    VersionLimitReached,
    approve_candidate,
    compute_required,
    create_proposed_version,
    failing_result_for,
    get_owned_version,
    open_candidate,
    reject_candidate,
    restore_version,
    verifying_version,
)
from backend.db.models import Agent, Criterion, EvalResult, EvalTest, PromptVersion, utcnow
from backend.db.workspaces import create_workspace

WHY = {"criterion_name": "c", "failing_test_id": "t", "failing_turn_index": 1}


def _agent(db):
    workspace, _ = create_workspace(db)
    preset = PRESETS["support"]
    agent = create_agent(db, workspace, preset.config, preset.analysis)
    return workspace, agent


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
    )
    db.add(crit)
    db.flush()
    return crit


def _test(db, crit, role="fix"):
    test = EvalTest(criterion_id=crit.id, role=role, scripted_user_messages=["hi"])
    db.add(test)
    db.flush()
    return test


_clock = [0]


def _result(db, test, pv_id, kind="replay", verdict="pass", stale=False):
    _clock[0] += 1
    result = EvalResult(
        test_id=test.id,
        prompt_version_id=pv_id,
        kind=kind,
        verdict=verdict,
        rationale="r",
        stale=stale,
        created_at=utcnow() + timedelta(seconds=_clock[0]),
    )
    db.add(result)
    db.flush()
    return result


def _propose(db, agent, test, text="candidate"):
    version = create_proposed_version(db, agent, text, test.id, WHY)
    db.flush()
    return version


def _failing_fix(db, agent):
    crit = _criterion(db, agent)
    fix = _test(db, crit, "fix")
    _result(db, fix, agent.current_prompt_version_id, kind="baseline", verdict="fail")
    return crit, fix


# --- ownership ---


def test_get_owned_version_own_foreign_and_missing(db_session):
    workspace, agent = _agent(db_session)
    other, _ = create_workspace(db_session)
    version = agent.prompt_versions[0]
    assert get_owned_version(db_session, workspace, version.id) is version
    with pytest.raises(LookupError):
        get_owned_version(db_session, other, version.id)
    with pytest.raises(LookupError):
        get_owned_version(db_session, workspace, "missing")


# --- open / verifying ---


def test_open_candidate_and_verifying_version(db_session):
    _, agent = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    assert open_candidate(db_session, agent) is None
    assert verifying_version(db_session, agent) is None
    version = _propose(db_session, agent, fix)
    assert open_candidate(db_session, agent) is version
    assert verifying_version(db_session, agent) is None
    approve_candidate(db_session, agent, version)
    assert open_candidate(db_session, agent) is version
    assert verifying_version(db_session, agent) is version
    version.status = "promoted"
    assert open_candidate(db_session, agent) is None
    assert verifying_version(db_session, agent) is None


# --- failing_result_for ---


def test_failing_result_for_current_failing_replay(db_session):
    _, agent = _agent(db_session)
    current = agent.current_prompt_version_id
    test = _test(db_session, _criterion(db_session, agent))
    replay = _result(db_session, test, current, verdict="fail")
    assert failing_result_for(db_session, test, current) is replay


def test_failing_result_for_current_passing_replay_beats_failing_baseline(db_session):
    _, agent = _agent(db_session)
    current = agent.current_prompt_version_id
    test = _test(db_session, _criterion(db_session, agent))
    _result(db_session, test, current, kind="baseline", verdict="fail")
    _result(db_session, test, current, verdict="pass")
    assert failing_result_for(db_session, test, current) is None


def test_failing_result_for_baseline_on_current_version(db_session):
    _, agent = _agent(db_session)
    current = agent.current_prompt_version_id
    test = _test(db_session, _criterion(db_session, agent))
    baseline = _result(db_session, test, current, kind="baseline", verdict="fail")
    assert failing_result_for(db_session, test, current) is baseline


def test_failing_result_for_baseline_on_older_version_is_none(db_session):
    _, agent = _agent(db_session)
    test = _test(db_session, _criterion(db_session, agent))
    _result(db_session, test, "older-version", kind="baseline", verdict="fail")
    assert failing_result_for(db_session, test, agent.current_prompt_version_id) is None


def test_failing_result_for_passing_baseline_is_none(db_session):
    _, agent = _agent(db_session)
    current = agent.current_prompt_version_id
    test = _test(db_session, _criterion(db_session, agent))
    _result(db_session, test, current, kind="baseline", verdict="pass")
    assert failing_result_for(db_session, test, current) is None


def test_failing_result_for_ignores_stale_replay_and_falls_back_to_baseline(db_session):
    _, agent = _agent(db_session)
    current = agent.current_prompt_version_id
    test = _test(db_session, _criterion(db_session, agent))
    baseline = _result(db_session, test, current, kind="baseline", verdict="fail")
    _result(db_session, test, current, verdict="pass", stale=True)
    assert failing_result_for(db_session, test, current) is baseline


def test_failing_result_for_not_applicable_is_none(db_session):
    _, agent = _agent(db_session)
    current = agent.current_prompt_version_id
    test = _test(db_session, _criterion(db_session, agent))
    _result(db_session, test, current, verdict="not_applicable")
    assert failing_result_for(db_session, test, current) is None


# --- create_proposed_version ---


def test_create_proposed_version_shape_and_numbering(db_session):
    _, agent = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    first_id = agent.current_prompt_version_id
    agent.prompt_versions[0].number = 4  # a gap: numbering follows the maximum
    version = create_proposed_version(db_session, agent, "new text", fix.id, WHY)
    assert version.status == "proposed"
    assert version.origin == "suggested"
    assert version.number == 5
    assert version.parent_id == first_id
    assert version.text == "new text"
    assert version.source_test_id == fix.id
    assert version.change_report == {"why": WHY, "outcome": None}
    assert version.verification is None
    assert agent.current_prompt_version_id == first_id  # chat keeps the current version


def test_create_proposed_version_with_the_expected_parent_succeeds(db_session):
    _, agent = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    current = agent.current_prompt_version_id
    version = create_proposed_version(db_session, agent, "t", fix.id, WHY, expected_parent_id=current)
    assert version.parent_id == current


def test_create_proposed_version_refuses_when_the_current_version_moved_on(db_session):
    """Simulated race: a rival commit changed the current version after the caller read it."""
    _, agent = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    old_current = agent.current_prompt_version_id
    newer = PromptVersion(agent_id=agent.id, text="newer", origin="manual", status="promoted", number=2)
    db_session.add(newer)
    db_session.flush()
    db_session.execute(
        update(Agent)
        .where(Agent.id == agent.id)
        .values(current_prompt_version_id=newer.id)
        .execution_options(synchronize_session=False)
    )
    assert agent.current_prompt_version_id == old_current  # stale in memory
    with pytest.raises(ParentChanged):
        create_proposed_version(db_session, agent, "t", fix.id, WHY, expected_parent_id=old_current)
    assert prompt_version_row_count(db_session, agent) == 2  # nothing stored


def test_create_proposed_version_without_an_expected_parent_uses_the_current_one(db_session):
    _, agent = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    newer = PromptVersion(agent_id=agent.id, text="newer", origin="manual", status="promoted", number=2)
    db_session.add(newer)
    db_session.flush()
    agent.current_prompt_version_id = newer.id
    assert create_proposed_version(db_session, agent, "t", fix.id, WHY).parent_id == newer.id


def test_create_proposed_version_numbers_after_rejected_rows(db_session):
    _, agent = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    first = _propose(db_session, agent, fix)
    reject_candidate(db_session, first)
    second = _propose(db_session, agent, fix)
    assert (first.number, second.number) == (2, 3)


def test_create_proposed_version_refused_while_a_candidate_is_open(db_session):
    _, agent = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    version = _propose(db_session, agent, fix)
    with pytest.raises(CandidateOpen):
        create_proposed_version(db_session, agent, "again", fix.id, WHY)
    approve_candidate(db_session, agent, version)
    with pytest.raises(CandidateOpen):
        create_proposed_version(db_session, agent, "again", fix.id, WHY)
    assert prompt_version_row_count(db_session, agent) == 2


def _add_rows(db, agent, count, status):
    highest = max(v.number for v in agent.prompt_versions)
    for offset in range(1, count + 1):
        db.add(
            PromptVersion(
                agent_id=agent.id, text="x", origin="manual", status=status, number=highest + offset
            )
        )
    db.flush()
    db.expire(agent, ["prompt_versions"])


def test_version_cap_counts_everything_but_rejected_and_failed(db_session):
    _, agent = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    assert MAX_PROMPT_VERSIONS_PER_AGENT == 20
    _add_rows(db_session, agent, 10, "rejected")
    _add_rows(db_session, agent, 5, "failed_verification")
    _add_rows(db_session, agent, 18, "superseded")  # 1 promoted + 18 = 19 counted
    assert prompt_version_count(db_session, agent) == 19
    assert prompt_version_row_count(db_session, agent) == 34
    version = _propose(db_session, agent, fix)  # the 20th counted version is allowed
    assert prompt_version_count(db_session, agent) == 20
    reject_candidate(db_session, version)
    assert prompt_version_count(db_session, agent) == 19
    _add_rows(db_session, agent, 1, "superseded")  # 20 counted
    with pytest.raises(VersionLimitReached):
        create_proposed_version(db_session, agent, "over", fix.id, WHY)


def test_hard_row_cap_of_sixty(db_session):
    _, agent = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    assert MAX_VERSION_ROWS_PER_AGENT == 60
    _add_rows(db_session, agent, 59, "rejected")
    assert prompt_version_count(db_session, agent) == 1
    assert prompt_version_row_count(db_session, agent) == 60
    with pytest.raises(VersionLimitReached):
        create_proposed_version(db_session, agent, "over", fix.id, WHY)


# --- compute_required ---


def test_compute_required_fix_only_is_weaker(db_session):
    _, agent = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    assert compute_required(db_session, agent, fix) == [RequiredTest(fix.id, "fix", "fail")]


def test_compute_required_before_is_none_without_a_stored_result_on_the_current_version(db_session):
    _, agent = _agent(db_session)
    fix = _test(db_session, _criterion(db_session, agent))
    assert compute_required(db_session, agent, fix) == [RequiredTest(fix.id, "fix", None)]
    _result(db_session, fix, "older-version", kind="baseline", verdict="fail")  # a baseline of another version
    assert compute_required(db_session, agent, fix) == [RequiredTest(fix.id, "fix", None)]


def test_compute_required_heldout_that_already_passes_is_not_credited_with_a_change(db_session):
    """After a manual edit and Run all, the held-out test passes on the current version: before is pass."""
    _, agent = _agent(db_session)
    current = agent.current_prompt_version_id
    crit, fix = _failing_fix(db_session, agent)
    held = _test(db_session, crit, "heldout")
    _result(db_session, held, "older-version", kind="baseline", verdict="fail")
    _result(db_session, held, "older-version", verdict="fail")
    _result(db_session, held, current, verdict="pass")
    required = compute_required(db_session, agent, fix)
    assert required == [RequiredTest(fix.id, "fix", "fail"), RequiredTest(held.id, "heldout", "pass")]


def test_compute_required_before_prefers_the_current_replay_over_the_baseline(db_session):
    _, agent = _agent(db_session)
    current = agent.current_prompt_version_id
    crit, fix = _failing_fix(db_session, agent)  # baseline fail on the current version
    _result(db_session, fix, current, verdict="fail")
    held = _test(db_session, crit, "heldout")
    _result(db_session, held, current, kind="baseline", verdict="fail")
    _result(db_session, held, current, verdict="pass", stale=True)  # stale replays are ignored
    required = compute_required(db_session, agent, fix)
    assert required == [RequiredTest(fix.id, "fix", "fail"), RequiredTest(held.id, "heldout", "fail")]


def test_compute_required_with_heldout_is_full(db_session):
    _, agent = _agent(db_session)
    crit, fix = _failing_fix(db_session, agent)
    held = _test(db_session, crit, "heldout")
    _result(db_session, held, agent.current_prompt_version_id, verdict="fail")
    required = compute_required(db_session, agent, fix)
    assert required == [RequiredTest(fix.id, "fix", "fail"), RequiredTest(held.id, "heldout", "fail")]


def test_compute_required_regressions_are_only_currently_passing_other_tests(db_session):
    _, agent = _agent(db_session)
    current = agent.current_prompt_version_id
    crit, fix = _failing_fix(db_session, agent)
    held = _test(db_session, crit, "heldout")
    passing = _test(db_session, _criterion(db_session, agent, 2))
    failing = _test(db_session, _criterion(db_session, agent, 3))
    stale = _test(db_session, _criterion(db_session, agent, 4))
    never_run = _test(db_session, _criterion(db_session, agent, 5))
    not_applicable = _test(db_session, _criterion(db_session, agent, 6))
    old_version_only = _test(db_session, _criterion(db_session, agent, 7))
    other_passing_heldout = _test(db_session, _criterion(db_session, agent, 8), "heldout")
    _result(db_session, held, current, verdict="fail")
    _result(db_session, passing, current, verdict="pass")
    _result(db_session, failing, current, verdict="fail")
    _result(db_session, stale, current, verdict="pass", stale=True)
    _result(db_session, not_applicable, current, verdict="not_applicable")
    _result(db_session, old_version_only, "older-version", verdict="pass")
    _result(db_session, other_passing_heldout, current, verdict="pass")
    assert never_run is not None
    required = compute_required(db_session, agent, fix)
    assert [(r.role, r.before) for r in required] == [
        ("fix", "fail"),
        ("heldout", "fail"),
        ("regression", "pass"),
        ("regression", "pass"),
    ]
    assert {r.test_id for r in required if r.role == "regression"} == {passing.id, other_passing_heldout.id}


def test_compute_required_never_lists_the_fix_test_twice(db_session):
    _, agent = _agent(db_session)
    crit = _criterion(db_session, agent)
    odd = _test(db_session, crit, "heldout")  # the held-out lookup would find the fix test itself
    _result(db_session, odd, agent.current_prompt_version_id, kind="baseline", verdict="fail")
    required = compute_required(db_session, agent, odd)
    assert required == [RequiredTest(odd.id, "fix", "fail")]
    assert len({r.test_id for r in required}) == len(required)


def test_compute_required_ignores_other_agents_tests(db_session):
    _, agent = _agent(db_session)
    _, other = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    foreign = _test(db_session, _criterion(db_session, other))
    _result(db_session, foreign, other.current_prompt_version_id, verdict="pass")
    assert [r.role for r in compute_required(db_session, agent, fix)] == ["fix"]


# --- approve / reject ---


def test_approve_candidate_stores_the_exact_verification_shape(db_session):
    _, agent = _agent(db_session)
    current = agent.current_prompt_version_id
    crit, fix = _failing_fix(db_session, agent)
    held = _test(db_session, crit, "heldout")
    _result(db_session, held, current, verdict="fail")
    regression = _test(db_session, _criterion(db_session, agent, 2))
    _result(db_session, regression, current, verdict="pass")
    version = _propose(db_session, agent, fix)
    before = utcnow()
    required = approve_candidate(db_session, agent, version)
    db_session.flush()
    assert version.status == "approved_verifying"
    expected_required = [
        {"test_id": fix.id, "role": "fix", "before": "fail"},
        {"test_id": held.id, "role": "heldout", "before": "fail"},
        {"test_id": regression.id, "role": "regression", "before": "pass"},
    ]
    assert required == [RequiredTest(r["test_id"], r["role"], r["before"]) for r in expected_required]
    assert set(version.verification) == {"strength", "fix_test_id", "approved_at", "required"}
    assert version.verification["strength"] == "full"
    assert version.verification["fix_test_id"] == fix.id
    assert version.verification["required"] == expected_required
    from datetime import datetime

    approved_at = datetime.fromisoformat(version.verification["approved_at"])
    assert approved_at.tzinfo is None and before <= approved_at <= utcnow()
    import json

    assert json.loads(json.dumps(version.verification)) == version.verification
    assert agent.current_prompt_version_id == current  # not promoted yet


def test_approve_candidate_weaker_without_heldout(db_session):
    _, agent = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    version = _propose(db_session, agent, fix)
    approve_candidate(db_session, agent, version)
    assert version.verification["strength"] == "weaker"


@pytest.mark.parametrize("status", ["approved_verifying", "rejected", "promoted", "superseded", "failed_verification"])
def test_approve_candidate_requires_a_proposed_version(db_session, status):
    _, agent = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    version = _propose(db_session, agent, fix)
    version.status = status
    with pytest.raises(ValueError):
        approve_candidate(db_session, agent, version)
    assert version.verification is None


def test_approve_candidate_sees_a_status_changed_by_another_transaction(db_session):
    _, agent = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    version = _propose(db_session, agent, fix)
    db_session.execute(
        update(PromptVersion)
        .where(PromptVersion.id == version.id)
        .values(status="rejected")
        .execution_options(synchronize_session=False)
    )
    assert version.status == "proposed"  # stale in memory
    with pytest.raises(ValueError):
        approve_candidate(db_session, agent, version)
    assert version.status == "rejected"


def test_approve_candidate_refuses_a_version_of_another_agent(db_session):
    _, agent = _agent(db_session)
    _, other = _agent(db_session)
    _, fix = _failing_fix(db_session, other)
    version = _propose(db_session, other, fix)
    with pytest.raises(ValueError):
        approve_candidate(db_session, agent, version)


def test_reject_candidate(db_session):
    _, agent = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    version = _propose(db_session, agent, fix)
    reject_candidate(db_session, version)
    assert version.status == "rejected"
    assert open_candidate(db_session, agent) is None
    with pytest.raises(ValueError):
        reject_candidate(db_session, version)


def test_reject_candidate_refuses_a_verifying_version(db_session):
    _, agent = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    version = _propose(db_session, agent, fix)
    approve_candidate(db_session, agent, version)
    with pytest.raises(ValueError):
        reject_candidate(db_session, version)
    assert version.status == "approved_verifying"


def test_reject_after_a_concurrent_approve_loses(db_session):
    _, agent = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    version = _propose(db_session, agent, fix)
    db_session.execute(
        update(PromptVersion)
        .where(PromptVersion.id == version.id)
        .values(status="approved_verifying")
        .execution_options(synchronize_session=False)
    )
    with pytest.raises(ValueError):
        reject_candidate(db_session, version)


# --- restore ---


def test_restore_version_creates_a_new_promoted_copy(db_session):
    _, agent = _agent(db_session)
    first = agent.prompt_versions[0]
    second = new_prompt_version(db_session, agent, "second text")
    restored = restore_version(db_session, agent, first)
    db_session.flush()
    assert restored.number == 3
    assert restored.origin == "restored"
    assert restored.status == "promoted"
    assert restored.text == first.text
    assert restored.parent_id == first.id
    assert agent.current_prompt_version_id == restored.id
    assert second.status == "superseded"
    assert first.status == "superseded"
    assert [v.status for v in sorted(agent.prompt_versions, key=lambda v: v.number)] == [
        "superseded",
        "superseded",
        "promoted",
    ]


def test_restore_version_supersedes_a_proposed_version(db_session):
    _, agent = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    first = agent.prompt_versions[0]
    second = new_prompt_version(db_session, agent, "second")
    proposed = _propose(db_session, agent, fix)
    restore_version(db_session, agent, first)
    assert proposed.status == "superseded"
    assert second.status == "superseded"
    assert open_candidate(db_session, agent) is None


@pytest.mark.parametrize("status", ["superseded", "failed_verification", "promoted"])
def test_restore_version_allowed_statuses(db_session, status):
    _, agent = _agent(db_session)
    new_prompt_version(db_session, agent, "second")
    old = agent.prompt_versions[0]
    old.status = status
    restored = restore_version(db_session, agent, old)
    assert restored.status == "promoted"
    assert agent.current_prompt_version_id == restored.id


@pytest.mark.parametrize("status", ["proposed", "rejected", "approved_verifying"])
def test_restore_version_rejects_other_statuses(db_session, status):
    _, agent = _agent(db_session)
    version = PromptVersion(
        agent_id=agent.id, text="x", origin="suggested", status=status, number=2, parent_id=None
    )
    db_session.add(version)
    db_session.flush()
    current = agent.current_prompt_version_id
    with pytest.raises(ValueError):
        restore_version(db_session, agent, version)
    assert agent.current_prompt_version_id == current
    assert prompt_version_row_count(db_session, agent) == 2


def test_restore_version_refuses_a_suggestion_that_was_never_approved(db_session):
    _, agent = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    proposal = _propose(db_session, agent, fix)
    new_prompt_version(db_session, agent, "manual edit")  # supersedes the proposal
    assert proposal.status == "superseded" and proposal.verification is None
    current = agent.current_prompt_version_id
    with pytest.raises(ValueError):
        restore_version(db_session, agent, proposal)
    assert agent.current_prompt_version_id == current
    assert prompt_version_row_count(db_session, agent) == 3


def test_restore_version_allows_a_suggestion_that_was_verified(db_session):
    _, agent = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    suggestion = _propose(db_session, agent, fix)
    approve_candidate(db_session, agent, suggestion)
    suggestion.status = "failed_verification"
    restored = restore_version(db_session, agent, suggestion)
    assert restored.status == "promoted" and restored.text == suggestion.text


def test_restore_version_refuses_a_version_of_another_agent(db_session):
    _, agent = _agent(db_session)
    _, other = _agent(db_session)
    with pytest.raises(ValueError):
        restore_version(db_session, agent, other.prompt_versions[0])


def test_restore_version_refused_while_a_candidate_is_verifying(db_session):
    _, agent = _agent(db_session)
    _, fix = _failing_fix(db_session, agent)
    first = agent.prompt_versions[0]
    version = _propose(db_session, agent, fix)
    approve_candidate(db_session, agent, version)
    with pytest.raises(CandidateOpen):
        restore_version(db_session, agent, first)
    assert version.status == "approved_verifying"
