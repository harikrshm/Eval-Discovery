from unittest.mock import patch

import pytest

from backend.api import limits
from backend.core.config import MAX_PROMPT_CHARS, PRESETS
from backend.db.models import Agent, PromptVersion, Run
from backend.db.workspaces import create_workspace

CAP_MESSAGE = "Prompt version limit reached for this agent."


@pytest.fixture(autouse=True)
def _reset_llm_limiter():
    limits.llm_limiter.reset()
    yield
    limits.llm_limiter.reset()


def _agent(client, auth_headers, preset="alex-sales"):
    return client.post("/agents", json={"preset_id": preset}, headers=auth_headers).json()["id"]


def _put(client, headers, agent_id, text):
    return client.put(f"/agents/{agent_id}/prompt", json={"text": text}, headers=headers)


def test_edit_creates_version_two_and_supersedes_the_first(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    r = _put(client, auth_headers, agent_id, "You are a terse sales agent.")
    assert r.status_code == 200
    body = r.json()
    assert body["changed"] is True
    assert body["version_number"] == 2
    assert body["text"] == "You are a terse sales agent."

    versions = {v.number: v for v in db_session.query(PromptVersion).filter_by(agent_id=agent_id)}
    assert sorted(versions) == [1, 2]
    assert body["version_id"] == versions[2].id
    assert versions[2].parent_id == versions[1].id
    assert versions[2].origin == "manual"
    assert versions[2].status == "promoted"
    assert versions[1].status == "superseded"
    db_session.expire_all()
    assert db_session.get(Agent, agent_id).current_prompt_version_id == versions[2].id


def test_state_returns_new_prompt_and_version_number(client, auth_headers):
    agent_id = _agent(client, auth_headers)
    first = client.get(f"/agents/{agent_id}/state", headers=auth_headers).json()["agent"]
    assert first["prompt_version_number"] == 1
    _put(client, auth_headers, agent_id, "New prompt text.")
    agent = client.get(f"/agents/{agent_id}/state", headers=auth_headers).json()["agent"]
    assert agent["system_prompt"] == "New prompt text."
    assert agent["prompt_version_number"] == 2
    assert agent["prompt_version_id"] != first["prompt_version_id"]


def test_chat_uses_the_edited_prompt(client, auth_headers):
    agent_id = _agent(client, auth_headers)
    _put(client, auth_headers, agent_id, "Answer in one word.")
    with patch("backend.api.agents.groq_chat", return_value="Yes.") as chat:
        r = client.post(
            f"/agents/{agent_id}/chat",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=auth_headers,
        )
    assert r.status_code == 200
    assert chat.call_args.args[0] == "Answer in one word."


def test_identical_text_is_a_noop(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    same = PRESETS["alex-sales"].config.system_prompt
    r = _put(client, auth_headers, agent_id, same)
    assert r.status_code == 200
    body = r.json()
    assert body["changed"] is False
    assert body["version_number"] == 1
    assert body["text"] == same
    assert db_session.query(PromptVersion).filter_by(agent_id=agent_id).count() == 1
    row = db_session.get(Agent, agent_id)
    assert body["version_id"] == row.current_prompt_version_id


def test_editing_back_to_an_old_text_still_makes_a_new_version(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    original = PRESETS["alex-sales"].config.system_prompt
    _put(client, auth_headers, agent_id, "Something else.")
    r = _put(client, auth_headers, agent_id, original)
    assert r.json()["changed"] is True
    assert r.json()["version_number"] == 3


@pytest.mark.parametrize(
    "body",
    [
        {"text": ""},
        {"text": "   \n\t "},
        {"text": "x" * (MAX_PROMPT_CHARS + 1)},
        {"text": "ok", "extra": 1},
        {},
        {"text": None},
        {"text": 5},
    ],
)
def test_bad_bodies_are_422_and_write_nothing(client, auth_headers, db_session, body):
    agent_id = _agent(client, auth_headers)
    r = client.put(f"/agents/{agent_id}/prompt", json=body, headers=auth_headers)
    assert r.status_code == 422
    assert db_session.query(PromptVersion).filter_by(agent_id=agent_id).count() == 1


def test_max_length_text_is_accepted(client, auth_headers):
    agent_id = _agent(client, auth_headers)
    r = _put(client, auth_headers, agent_id, "x" * MAX_PROMPT_CHARS)
    assert r.status_code == 200
    assert r.json()["changed"] is True


def test_twenty_first_version_is_429(client, auth_headers, db_session):
    assert limits.MAX_PROMPT_VERSIONS_PER_AGENT == 20
    agent_id = _agent(client, auth_headers)
    for i in range(2, 21):
        assert _put(client, auth_headers, agent_id, f"Prompt {i}").status_code == 200
    assert db_session.query(PromptVersion).filter_by(agent_id=agent_id).count() == 20
    r = _put(client, auth_headers, agent_id, "Prompt 21")
    assert r.status_code == 429
    assert r.json()["detail"] == CAP_MESSAGE
    assert db_session.query(PromptVersion).filter_by(agent_id=agent_id).count() == 20
    # The cap is per agent, and an identical text is still a harmless no-op at the cap.
    assert _put(client, auth_headers, _agent(client, auth_headers), "Fresh").status_code == 200
    same = _put(client, auth_headers, agent_id, "Prompt 20")
    assert same.status_code == 200 and same.json()["changed"] is False


def test_other_workspace_or_unknown_agent_is_404(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    _, token = create_workspace(db_session)
    other = {"X-Workspace-Token": token}
    assert _put(client, other, agent_id, "Hijack").status_code == 404
    assert _put(client, auth_headers, "does-not-exist", "x").status_code == 404
    assert db_session.query(PromptVersion).filter_by(agent_id=agent_id).count() == 1


def test_requires_auth(client):
    assert client.put("/agents/x/prompt", json={"text": "hi"}).status_code == 401


def test_existing_runs_keep_their_original_prompt_version(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    messages = [
        {"role": "user", "content": "price?"},
        {"role": "assistant", "content": "Let me book a demo."},
        {"role": "user", "content": "ok thanks"},
    ]
    client.post(f"/agents/{agent_id}/runs", json={"messages": messages}, headers=auth_headers)
    first_version = db_session.get(Agent, agent_id).current_prompt_version_id
    _put(client, auth_headers, agent_id, "A different prompt.")
    client.post(f"/agents/{agent_id}/runs", json={"messages": messages}, headers=auth_headers)
    runs = db_session.query(Run).filter_by(agent_id=agent_id).order_by(Run.position).all()
    db_session.expire_all()
    assert runs[0].prompt_version_id == first_version
    assert runs[1].prompt_version_id == db_session.get(Agent, agent_id).current_prompt_version_id
    assert runs[0].prompt_version_id != runs[1].prompt_version_id


def test_two_sequential_puts_of_the_same_text_make_one_version(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    first = _put(client, auth_headers, agent_id, "Same new text.")
    second = _put(client, auth_headers, agent_id, "Same new text.")
    assert first.json()["changed"] is True
    assert second.json()["changed"] is False
    assert second.json()["version_id"] == first.json()["version_id"]
    assert db_session.query(PromptVersion).filter_by(agent_id=agent_id).count() == 2


# ---- verifying guard, row cap and chat during verification

VERIFYING = "A fix is being verified. Wait for it to finish."


def _add(db_session, agent_id, number, status, parent_id=None):
    version = PromptVersion(
        agent_id=agent_id,
        text=f"text {number}",
        parent_id=parent_id,
        origin="suggested",
        status=status,
        number=number,
    )
    db_session.add(version)
    db_session.commit()
    return version


def test_put_is_409_while_a_fix_is_being_verified(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    current = db_session.get(Agent, agent_id).current_prompt_version_id
    candidate = _add(db_session, agent_id, 2, "approved_verifying", parent_id=current)
    r = _put(client, auth_headers, agent_id, "A new prompt.")
    assert r.status_code == 409
    assert r.json()["detail"] == VERIFYING
    db_session.expire_all()
    assert db_session.get(PromptVersion, candidate.id).status == "approved_verifying"
    assert db_session.get(Agent, agent_id).current_prompt_version_id == current
    assert db_session.query(PromptVersion).filter_by(agent_id=agent_id).count() == 2


def test_put_with_a_proposed_candidate_still_works_and_supersedes_it(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    candidate = _add(db_session, agent_id, 2, "proposed")
    assert _put(client, auth_headers, agent_id, "A new prompt.").status_code == 200
    db_session.expire_all()
    assert db_session.get(PromptVersion, candidate.id).status == "superseded"


def test_put_is_429_at_the_hard_row_cap_even_when_few_versions_count(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    for number in range(2, 61):
        _add(db_session, agent_id, number, "rejected")
    assert db_session.query(PromptVersion).filter_by(agent_id=agent_id).count() == 60
    r = _put(client, auth_headers, agent_id, "Prompt 61")
    assert r.status_code == 429
    assert r.json()["detail"] == CAP_MESSAGE
    assert db_session.query(PromptVersion).filter_by(agent_id=agent_id).count() == 60


def test_put_counts_only_non_rejected_versions_toward_the_twenty_cap(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    for number in range(2, 22):
        _add(db_session, agent_id, number, "rejected")
    assert _put(client, auth_headers, agent_id, "Still fine").status_code == 200


def test_chat_keeps_using_the_current_prompt_while_a_candidate_is_verifying(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    current = db_session.get(Agent, agent_id).current_prompt_version_id
    _add(db_session, agent_id, 2, "approved_verifying", parent_id=current)
    with patch("backend.api.agents.groq_chat", return_value="Yes.") as chat:
        r = client.post(
            f"/agents/{agent_id}/chat",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=auth_headers,
        )
    assert r.status_code == 200
    assert chat.call_args.args[0] == PRESETS["alex-sales"].config.system_prompt
    assert chat.call_args.args[0] != "text 2"
