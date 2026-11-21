from unittest.mock import patch

import pytest

from backend.api import limits
from backend.core.pattern import PatternCheckFailed, PatternResult, TranscriptTooShort
from backend.db.models import Agent, Event, Run
from backend.db.workspaces import create_workspace


@pytest.fixture(autouse=True)
def _reset_llm_limiter():
    limits.llm_limiter.reset()
    yield
    limits.llm_limiter.reset()


def _agent(client, auth_headers, preset="alex-sales"):
    return client.post("/agents", json={"preset_id": preset}, headers=auth_headers).json()["id"]


def _msgs(first="what is the price?", reply="Let me book a demo."):
    return [
        {"role": "user", "content": first},
        {"role": "assistant", "content": reply},
        {"role": "user", "content": "ok thanks"},
    ]


def _post_run(client, auth_headers, agent_id, messages=None):
    return client.post(
        f"/agents/{agent_id}/runs",
        json={"messages": messages if messages is not None else _msgs()},
        headers=auth_headers,
    )


def _check(client, auth_headers, agent_id):
    return client.post(f"/agents/{agent_id}/check-pattern", headers=auth_headers)


def test_first_run_is_not_due_second_overlapping_run_is(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    first = _post_run(client, auth_headers, agent_id)
    assert first.status_code == 201
    body = first.json()
    assert body["run_count"] == 1
    assert body["check_due"] is False
    assert body["run"]["position"] == 1
    assert body["run"]["messages"] == _msgs()
    row = db_session.get(Agent, agent_id)
    assert db_session.query(Run).one().prompt_version_id == row.current_prompt_version_id

    second = _post_run(client, auth_headers, agent_id).json()
    assert second["run_count"] == 2
    assert second["run"]["position"] == 2
    assert second["check_due"] is True
    events = db_session.query(Event).filter(Event.name == "run_saved").all()
    assert [e.properties for e in events] == [{"position": 1}, {"position": 2}]


def test_overlap_only_in_assistant_messages_is_not_due(client, auth_headers):
    agent_id = _agent(client, auth_headers)
    # "price" appears only in assistant turns: the prefilter looks at user messages only.
    only_assistant = _msgs(first="hello there", reply="I cannot discuss price or pricing.")
    _post_run(client, auth_headers, agent_id, only_assistant)
    second = _post_run(client, auth_headers, agent_id, only_assistant).json()
    assert second["run_count"] == 2
    assert second["check_due"] is False


def test_same_term_repeated_in_one_run_is_not_due(client, auth_headers):
    agent_id = _agent(client, auth_headers)
    repeated = [
        {"role": "user", "content": "price?"},
        {"role": "assistant", "content": "demo?"},
        {"role": "user", "content": "price again, what is the price"},
    ]
    other = _msgs(first="hello", reply="hi")
    _post_run(client, auth_headers, agent_id, repeated)
    second = _post_run(client, auth_headers, agent_id, other).json()
    assert second["run_count"] == 2
    assert second["check_due"] is False


@pytest.mark.parametrize("count", [0, 1, 2, 42])
def test_run_needs_three_to_forty_one_messages(client, auth_headers, count):
    agent_id = _agent(client, auth_headers)
    msgs = [{"role": "user", "content": "price"} for _ in range(count)]
    assert _post_run(client, auth_headers, agent_id, msgs).status_code == 422


def test_run_message_limits_accept_boundaries(client, auth_headers):
    agent_id = _agent(client, auth_headers)
    for count in (3, 40, 41):
        msgs = [{"role": "user", "content": "price"} for _ in range(count)]
        assert _post_run(client, auth_headers, agent_id, msgs).status_code == 201


def test_run_rejects_bad_message_shape(client, auth_headers):
    agent_id = _agent(client, auth_headers)
    bad = _msgs()
    bad[0]["role"] = "system"
    assert _post_run(client, auth_headers, agent_id, bad).status_code == 422
    bad = _msgs()
    bad[0]["content"] = 5
    assert _post_run(client, auth_headers, agent_id, bad).status_code == 422


def test_run_cap_is_429(client, auth_headers, db_session):
    first = _agent(client, auth_headers)
    second = _agent(client, auth_headers, "support")
    for agent_id, n in ((first, 30), (second, 20)):  # cap counts across all agents
        row = db_session.get(Agent, agent_id)
        for pos in range(1, n + 1):
            db_session.add(
                Run(
                    agent_id=agent_id,
                    prompt_version_id=row.current_prompt_version_id,
                    position=pos,
                    messages=_msgs(),
                )
            )
    db_session.commit()
    r = _post_run(client, auth_headers, first)
    assert r.status_code == 429
    assert r.json()["detail"] == "Run limit reached for this workspace."
    assert db_session.query(Run).count() == 50
    assert db_session.query(Event).filter(Event.name == "run_saved").count() == 0


def test_run_cap_is_per_workspace(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    row = db_session.get(Agent, agent_id)
    for pos in range(1, 51):
        db_session.add(
            Run(agent_id=agent_id, prompt_version_id=row.current_prompt_version_id,
                position=pos, messages=_msgs())
        )
    db_session.commit()
    _, token = create_workspace(db_session)
    other = {"X-Workspace-Token": token}
    other_agent = _agent(client, other)
    assert _post_run(client, other, other_agent).status_code == 201


def test_run_of_other_workspace_agent_is_404(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    _, token = create_workspace(db_session)
    other = {"X-Workspace-Token": token}
    assert _post_run(client, other, agent_id).status_code == 404
    with patch("backend.api.runs.detect_pattern") as detect:
        assert _check(client, other, agent_id).status_code == 404
    detect.assert_not_called()
    assert _post_run(client, auth_headers, "does-not-exist").status_code == 404
    assert db_session.query(Run).count() == 0


def test_runs_require_auth(client):
    assert client.post("/agents/x/runs", json={"messages": _msgs()}).status_code == 401
    assert client.post("/agents/x/check-pattern").status_code == 401


def test_check_pattern_found_updates_state_and_logs_event(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    _post_run(client, auth_headers, agent_id)
    _post_run(client, auth_headers, agent_id)
    result = PatternResult(found=True, pattern_summary="You keep asking about price.")
    with patch("backend.api.runs.detect_pattern", return_value=result) as detect:
        r = _check(client, auth_headers, agent_id)
    assert r.status_code == 200
    assert r.json() == {"found": True, "pattern_summary": "You keep asking about price."}
    row = db_session.get(Agent, agent_id)
    db_session.refresh(row)
    assert row.last_checked_at_run == 2
    assert row.pattern_summary == "You keep asking about price."
    assert db_session.query(Event).filter(Event.name == "loop_detected").count() == 1
    config, transcripts = detect.call_args.args
    assert config.name == row.name
    assert config.user_role_label == "Prospect"
    assert config.system_prompt.startswith(row.prompt_versions[0].text[:20])
    assert transcripts == [_msgs(), _msgs()]


def test_check_pattern_not_found_updates_last_checked_only(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    _post_run(client, auth_headers, agent_id)
    _post_run(client, auth_headers, agent_id)
    row = db_session.get(Agent, agent_id)
    row.pattern_summary = "older pattern"
    db_session.commit()
    with patch(
        "backend.api.runs.detect_pattern", return_value=PatternResult(found=False, pattern_summary="")
    ):
        r = _check(client, auth_headers, agent_id)
    assert r.status_code == 200
    assert r.json() == {"found": False, "pattern_summary": ""}
    db_session.refresh(row)
    assert row.last_checked_at_run == 2
    assert row.pattern_summary == "older pattern"
    assert db_session.query(Event).filter(Event.name == "loop_detected").count() == 0


def test_check_pattern_failure_is_502_and_changes_nothing(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    _post_run(client, auth_headers, agent_id)
    _post_run(client, auth_headers, agent_id)
    with patch("backend.api.runs.detect_pattern", side_effect=PatternCheckFailed("boom")):
        r = _check(client, auth_headers, agent_id)
    assert r.status_code == 502
    assert r.json()["detail"] == "Pattern check unavailable, try again."
    row = db_session.get(Agent, agent_id)
    db_session.refresh(row)
    assert row.last_checked_at_run == 0
    assert row.pattern_summary is None
    assert db_session.query(Event).filter(Event.name == "loop_detected").count() == 0


def test_check_pattern_too_short_is_400(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    _post_run(client, auth_headers, agent_id)
    with patch("backend.api.runs.detect_pattern", side_effect=TranscriptTooShort("Transcript 1 is too short")):
        r = _check(client, auth_headers, agent_id)
    assert r.status_code == 400
    assert "too short" in r.json()["detail"].lower()
    row = db_session.get(Agent, agent_id)
    db_session.refresh(row)
    assert row.last_checked_at_run == 0


def test_check_pattern_uses_only_delta_runs(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    for i in range(1, 5):
        _post_run(client, auth_headers, agent_id, _msgs(first=f"run {i} price"))
    row = db_session.get(Agent, agent_id)
    row.last_checked_at_run = 2
    db_session.commit()
    with patch(
        "backend.api.runs.detect_pattern", return_value=PatternResult(found=False, pattern_summary="")
    ) as detect:
        assert _check(client, auth_headers, agent_id).status_code == 200
    transcripts = detect.call_args.args[1]
    assert len(transcripts) == 2
    assert [t[0]["content"] for t in transcripts] == ["run 3 price", "run 4 price"]
    db_session.refresh(row)
    assert row.last_checked_at_run == 4


def test_check_due_uses_only_runs_after_last_check(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    _post_run(client, auth_headers, agent_id)
    _post_run(client, auth_headers, agent_id)
    row = db_session.get(Agent, agent_id)
    row.last_checked_at_run = 2
    db_session.commit()
    third = _post_run(client, auth_headers, agent_id).json()
    assert third["run_count"] == 3
    assert third["check_due"] is False  # only one run since the last check
    fourth = _post_run(client, auth_headers, agent_id).json()
    assert fourth["check_due"] is True


def test_check_pattern_with_no_runs_is_400_empty(client, auth_headers):
    agent_id = _agent(client, auth_headers)
    with patch("backend.api.runs.detect_pattern") as detect:
        r = _check(client, auth_headers, agent_id)
    assert r.status_code == 400
    assert "empty" in r.json()["detail"].lower()
    detect.assert_not_called()


def test_check_pattern_with_all_runs_already_checked_is_400_empty(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    _post_run(client, auth_headers, agent_id)
    row = db_session.get(Agent, agent_id)
    row.last_checked_at_run = 1
    db_session.commit()
    with patch("backend.api.runs.detect_pattern") as detect:
        r = _check(client, auth_headers, agent_id)
    assert r.status_code == 400
    assert "empty" in r.json()["detail"].lower()
    detect.assert_not_called()


def test_check_pattern_is_rate_limited_after_ownership(client, auth_headers, db_session, monkeypatch):
    agent_id = _agent(client, auth_headers)
    calls = []
    monkeypatch.setattr("backend.api.runs.rate_limit_llm", lambda request: calls.append(1))
    _check(client, auth_headers, "does-not-exist")
    assert calls == []
    _check(client, auth_headers, agent_id)
    assert calls == [1]


def test_check_pattern_marks_last_analysed_run_not_newer_count(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    _post_run(client, auth_headers, agent_id)
    _post_run(client, auth_headers, agent_id)
    row = db_session.get(Agent, agent_id)

    def save_run_during_llm_call(config, transcripts):
        # A run saved by another request while the (slow) pattern LLM call is in flight.
        db_session.add(
            Run(
                agent_id=agent_id,
                prompt_version_id=row.current_prompt_version_id,
                position=3,
                messages=_msgs(),
            )
        )
        db_session.commit()
        return PatternResult(found=False, pattern_summary="")

    with patch("backend.api.runs.detect_pattern", side_effect=save_run_during_llm_call) as detect:
        r = _check(client, auth_headers, agent_id)
    assert r.status_code == 200
    assert len(detect.call_args.args[1]) == 2  # only the two runs known at check time were analysed
    db_session.refresh(row)
    assert db_session.query(Run).count() == 3
    assert row.last_checked_at_run == 2  # the unanalysed run 3 stays in the next delta


def test_run_save_accepts_long_assistant_turn_and_state_round_trips(client, auth_headers):
    agent_id = _agent(client, auth_headers)
    msgs = _msgs(reply="a" * 3000)
    r = _post_run(client, auth_headers, agent_id, msgs)
    assert r.status_code == 201
    assert len(r.json()["run"]["messages"][1]["content"]) == 3000
    state = client.get(f"/agents/{agent_id}/state", headers=auth_headers)
    assert state.status_code == 200
    assert len(state.json()["runs"][0]["messages"][1]["content"]) == 3000


def test_run_save_rejects_long_user_turn_and_oversized_total(client, auth_headers):
    agent_id = _agent(client, auth_headers)
    assert _post_run(client, auth_headers, agent_id, _msgs(first="x" * 2001)).status_code == 422
    big = []
    for _ in range(9):
        big += [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a" * 16000}]
    big.append({"role": "user", "content": "q"})
    assert _post_run(client, auth_headers, agent_id, big).status_code == 422


def _turns(sizes):
    """Alternating user/assistant messages (user first) whose contents have the given lengths."""
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": ("q" if i % 2 == 0 else "a") * n}
        for i, n in enumerate(sizes)
    ]


def test_run_save_accepts_a_full_chat_request_plus_its_reply(client, auth_headers):
    agent_id = _agent(client, auth_headers)
    # 40 chat messages plus the reply to the last one is 41 messages.
    assert _post_run(client, auth_headers, agent_id, _turns([1] * 41)).status_code == 201


def test_run_save_accepts_135000_chars(client, auth_headers):
    agent_id = _agent(client, auth_headers)
    run = _turns([2000, 14625] * 8 + [2000])
    assert sum(len(m["content"]) for m in run) == 135000
    assert _post_run(client, auth_headers, agent_id, run).status_code == 201


def test_run_save_total_cap_is_request_cap_plus_one_assistant_message(client, auth_headers):
    agent_id = _agent(client, auth_headers)
    cap = limits.MAX_REQUEST_CHARS + limits.MAX_ASSISTANT_MESSAGE_CHARS
    run = _turns([2000, 16000] * 7 + [2000, cap - 128000])
    assert sum(len(m["content"]) for m in run) == cap
    assert _post_run(client, auth_headers, agent_id, run).status_code == 201
    run[-1]["content"] += "a"
    assert _post_run(client, auth_headers, agent_id, run).status_code == 422


def test_chat_request_limits_are_unchanged():
    from pydantic import ValidationError

    from backend.api.schemas import ChatRequest

    with pytest.raises(ValidationError):
        ChatRequest(messages=_turns([1] * 41))  # 41 messages: still rejected for chat
    # 110000 + X + 1 chars: exactly the request cap at X = 9999, one over at X = 10000.
    ChatRequest(messages=_turns([2000, 16000] * 6 + [2000, 9999, 1]))
    with pytest.raises(ValidationError):
        ChatRequest(messages=_turns([2000, 16000] * 6 + [2000, 10000, 1]))


def test_check_pattern_sends_only_the_ten_most_recent_runs(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    for i in range(1, 16):
        _post_run(client, auth_headers, agent_id, _msgs(first=f"run {i} price"))
    with patch(
        "backend.api.runs.detect_pattern", return_value=PatternResult(found=False, pattern_summary="")
    ) as detect:
        assert _check(client, auth_headers, agent_id).status_code == 200
    transcripts = detect.call_args.args[1]
    assert len(transcripts) == 10
    assert transcripts[0][0]["content"] == "run 6 price"
    assert transcripts[-1][0]["content"] == "run 15 price"
    row = db_session.get(Agent, agent_id)
    db_session.refresh(row)
    assert row.last_checked_at_run == 15


def test_check_pattern_failure_with_many_runs_does_not_advance_marker(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    for i in range(1, 13):
        _post_run(client, auth_headers, agent_id, _msgs(first=f"run {i} price"))
    with patch("backend.api.runs.detect_pattern", side_effect=PatternCheckFailed("boom")):
        assert _check(client, auth_headers, agent_id).status_code == 502
    row = db_session.get(Agent, agent_id)
    db_session.refresh(row)
    assert row.last_checked_at_run == 0


def test_prefilter_rejecting_a_batch_over_the_cap_advances_the_marker(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    # Distinct, trigger-free user text: no two runs share a trigger term.
    for i in range(1, 12):
        _post_run(client, auth_headers, agent_id, _msgs(first=f"zzz{i}", reply="ok"))
    row = db_session.get(Agent, agent_id)
    db_session.refresh(row)
    assert row.last_checked_at_run == 11  # the 11th run exceeded the cap and was rejected: advanced
    last = _post_run(client, auth_headers, agent_id, _msgs(first="zzz12", reply="ok")).json()
    assert last["check_due"] is False


def test_prefilter_rejection_within_the_cap_leaves_the_marker(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    for i in range(1, 6):
        _post_run(client, auth_headers, agent_id, _msgs(first=f"zzz{i}", reply="ok"))
    row = db_session.get(Agent, agent_id)
    db_session.refresh(row)
    assert row.last_checked_at_run == 0
