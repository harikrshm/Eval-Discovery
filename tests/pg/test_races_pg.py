"""Concurrent requests on a real Postgres: the agent row lock must serialize competing transitions.

Each racing request runs on its own thread, its own TestClient and its own database session, and
all of them are released together by a barrier. Which request wins is not asserted (both orders are
legitimate); the invariants are.
"""

from dataclasses import dataclass

import pytest
from sqlalchemy import func, select

from backend.core.config import PRESETS
from backend.db.agents import create_agent
from backend.db.candidates import create_proposed_version
from backend.db.models import Agent, Criterion, EvalResult, EvalTest, Job, PromptVersion
from backend.db.workspaces import create_workspace
from tests.pg import pgutil

pytestmark = pytest.mark.pg

ROUNDS = 5
SUCCESS = (200, 202)


@dataclass
class Round:
    headers: dict
    workspace_id: str
    agent_id: str
    v1_id: str
    v2_id: str


def _setup_round(sessions) -> Round:
    """A workspace with an agent: v1 promoted and current, v2 proposed from a failing fix test."""
    with sessions() as db:
        workspace, token = create_workspace(db)
        preset = PRESETS["support"]
        agent = create_agent(db, workspace, preset.config, preset.analysis)
        v1_id = agent.current_prompt_version_id
        criterion = Criterion(
            agent_id=agent.id,
            position=1,
            name="Politeness",
            definition="d",
            good_example="g",
            bad_example="b",
            why_discovered="w",
            source="discovered",
        )
        db.add(criterion)
        db.flush()
        test = EvalTest(criterion_id=criterion.id, role="fix", scripted_user_messages=["hi"])
        db.add(test)
        db.flush()
        db.add(
            EvalResult(
                test_id=test.id,
                prompt_version_id=v1_id,
                kind="replay",
                verdict="fail",
                rationale="still wrong",
                transcript=[],
                stale=False,
            )
        )
        db.commit()
        v2 = create_proposed_version(
            db,
            agent,
            preset.config.system_prompt + "\nBe more polite.",
            test.id,
            {"criterion_name": "Politeness", "failing_test_id": test.id, "failing_turn_index": 1},
        )
        db.commit()
        return Round({"X-Workspace-Token": token}, workspace.id, agent.id, v1_id, v2.id)


@dataclass
class State:
    versions: dict[str, PromptVersion]  # by id
    current_id: str
    replay_jobs: int

    def with_status(self, status: str) -> list[PromptVersion]:
        return [v for v in self.versions.values() if v.status == status]


def _state(sessions, rnd: Round) -> State:
    with sessions() as db:
        versions = {
            v.id: v
            for v in db.scalars(select(PromptVersion).where(PromptVersion.agent_id == rnd.agent_id))
        }
        current_id = db.get(Agent, rnd.agent_id).current_prompt_version_id
        replay_jobs = db.scalar(
            select(func.count()).select_from(Job).where(Job.workspace_id == rnd.workspace_id, Job.type == "replay")
        )
    return State(versions, current_id, replay_jobs)


def _assert_consistent(state: State) -> None:
    """The invariants that hold after any interleaving."""
    assert len(state.with_status("approved_verifying")) <= 1
    assert len(state.with_status("promoted")) == 1
    assert state.versions[state.current_id].status == "promoted"
    open_versions = state.with_status("proposed") + state.with_status("approved_verifying")
    assert len(open_versions) <= 1
    assert len({v.number for v in state.versions.values()}) == len(state.versions)


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_two_concurrent_approvals_one_wins(pg_sessions, pg_client_factory, round_no):
    rnd = _setup_round(pg_sessions)
    path = f"/prompt-versions/{rnd.v2_id}/approve"

    responses = pgutil.race(pg_client_factory, [("POST", path, None)] * 2, rnd.headers)

    codes = sorted(r.status_code for r in responses)
    assert codes == [202, 409], [(r.status_code, r.text) for r in responses]
    state = _state(pg_sessions, rnd)
    _assert_consistent(state)
    assert state.versions[rnd.v2_id].status == "approved_verifying"
    assert state.current_id == rnd.v1_id
    assert state.replay_jobs == 1  # one approval, one required test, one replay job


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_approve_racing_a_manual_prompt_edit_exactly_one_succeeds(pg_sessions, pg_client_factory, round_no):
    rnd = _setup_round(pg_sessions)
    requests = [
        ("POST", f"/prompt-versions/{rnd.v2_id}/approve", None),
        ("PUT", f"/agents/{rnd.agent_id}/prompt", {"text": "A totally new prompt."}),
    ]

    approve, edit = pgutil.race(pg_client_factory, requests, rnd.headers)

    outcome = [(approve.status_code, approve.text), (edit.status_code, edit.text)]
    succeeded = [r for r in (approve, edit) if r.status_code in SUCCESS]
    assert len(succeeded) == 1, outcome
    assert [r.status_code for r in (approve, edit) if r not in succeeded] == [409], outcome
    state = _state(pg_sessions, rnd)
    _assert_consistent(state)

    if approve in succeeded:  # approval first: the edit is refused while the fix is verifying
        assert approve.status_code == 202
        assert state.versions[rnd.v2_id].status == "approved_verifying"
        assert state.current_id == rnd.v1_id
        assert state.replay_jobs == 1
    else:  # edit first: it supersedes v1 and the proposal, which can then no longer be approved
        assert edit.json()["changed"] is True
        assert state.versions[rnd.v2_id].status == "superseded"
        assert state.versions[rnd.v1_id].status == "superseded"
        assert state.with_status("approved_verifying") == []
        assert state.current_id == edit.json()["version_id"]
        assert state.versions[state.current_id].origin == "manual"
        assert state.replay_jobs == 0


def test_pg_client_reads_through_a_new_session_per_request(pg_sessions, pg_client):
    rnd = _setup_round(pg_sessions)

    response = pg_client.get(f"/agents/{rnd.agent_id}/prompt-versions", headers=rnd.headers)

    assert response.status_code == 200
    assert [(v["number"], v["status"]) for v in response.json()] == [(2, "proposed"), (1, "promoted")]
