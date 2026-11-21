import pytest
from sqlalchemy.exc import IntegrityError

from backend.db.models import (
    Agent,
    BehaviorMap,
    Criterion,
    Event,
    PromptVersion,
    Run,
    Workspace,
    utcnow,
)
from backend.db.session import normalize_database_url


def _make_agent(db_session):
    ws = Workspace(token_hash="hash-1", expires_at=utcnow())
    db_session.add(ws)
    db_session.flush()
    agent = Agent(workspace_id=ws.id, name="Alex", user_role_label="Customer")
    db_session.add(agent)
    db_session.flush()
    pv = PromptVersion(agent_id=agent.id, text="You are Alex.", origin="initial", status="promoted")
    db_session.add(pv)
    db_session.flush()
    return ws, agent, pv


def test_normalize_database_url():
    assert normalize_database_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_database_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_database_url("sqlite:///x.db") == "sqlite:///x.db"


def test_deleting_workspace_cascades_to_everything(db_session):
    ws, agent, pv = _make_agent(db_session)
    db_session.add_all(
        [
            BehaviorMap(agent_id=agent.id, prompt_hash="abc", analysis={"k": "v"}),
            Run(agent_id=agent.id, prompt_version_id=pv.id, position=1, messages=[{"role": "user", "content": "hi"}]),
            Criterion(
                agent_id=agent.id,
                position=1,
                name="n",
                definition="d",
                good_example="g",
                bad_example="b",
                why_discovered="w",
                source="discovered",
            ),
            Event(workspace_id=ws.id, name="visit", properties={"a": 1}),
        ]
    )
    db_session.commit()
    for model in (Agent, PromptVersion, BehaviorMap, Run, Criterion, Event, Workspace):
        assert db_session.query(model).count() == 1

    db_session.delete(ws)
    db_session.commit()
    for model in (Agent, PromptVersion, BehaviorMap, Run, Criterion, Event, Workspace):
        assert db_session.query(model).count() == 0


def test_run_position_unique_per_agent(db_session):
    _, agent, pv = _make_agent(db_session)
    db_session.add(Run(agent_id=agent.id, prompt_version_id=pv.id, position=1, messages=[]))
    db_session.commit()
    db_session.add(Run(agent_id=agent.id, prompt_version_id=pv.id, position=1, messages=[]))
    with pytest.raises(IntegrityError):
        db_session.commit()
