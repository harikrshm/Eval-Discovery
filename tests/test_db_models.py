import pytest
from sqlalchemy.exc import IntegrityError

from backend.db.models import (
    Agent,
    BehaviorMap,
    Criterion,
    EvalResult,
    EvalTest,
    Event,
    Job,
    PromptVersion,
    Run,
    Workspace,
    utcnow,
)
from backend.db.session import Base, normalize_database_url


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


def test_workspace_delete_cascades_cleanly_with_foreign_keys_enforced():
    """Postgres enforces FKs (SQLite does not by default); the ORM must delete in a safe order.

    Run references both agent and prompt_version; without a relationship to prompt_version the
    ORM may delete the prompt version first, so the DB cascade removes the run and the ORM's own
    DELETE then matches 0 rows (an SAWarning).
    """
    import warnings

    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from backend.core.config import PRESETS
    from backend.db.agents import create_agent
    from backend.db.workspaces import create_workspace

    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enforce_fks(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        workspace, _ = create_workspace(db)
        preset = PRESETS["support"]
        agent = create_agent(db, workspace, preset.config, preset.analysis)
        db.add(
            Run(agent_id=agent.id, prompt_version_id=agent.current_prompt_version_id, position=1, messages=[])
        )
        db.commit()
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            db.delete(workspace)
            db.commit()
        assert db.query(Run).count() == 0
    finally:
        db.close()
        engine.dispose()


def _criterion(agent_id, position=1):
    return Criterion(
        agent_id=agent_id,
        position=position,
        name="n",
        definition="d",
        good_example="g",
        bad_example="b",
        why_discovered="w",
        source="discovered",
    )


def test_criterion_role_unique(db_session):
    _, agent, _ = _make_agent(db_session)
    crit = _criterion(agent.id)
    db_session.add(crit)
    db_session.flush()
    db_session.add(EvalTest(criterion_id=crit.id, role="fix", scripted_user_messages=["hi"]))
    db_session.commit()
    db_session.add(EvalTest(criterion_id=crit.id, role="heldout", scripted_user_messages=["hi"]))
    db_session.commit()
    db_session.add(EvalTest(criterion_id=crit.id, role="fix", scripted_user_messages=["x"]))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_new_column_defaults(db_session):
    ws, agent, pv = _make_agent(db_session)
    crit = _criterion(agent.id)
    db_session.add(crit)
    db_session.flush()
    test = EvalTest(criterion_id=crit.id, role="fix", scripted_user_messages=["hi"])
    db_session.add(test)
    db_session.flush()
    result = EvalResult(
        test_id=test.id, prompt_version_id=pv.id, kind="replay", verdict="pass", rationale="ok"
    )
    job = Job(workspace_id=ws.id, type="replay", payload={})
    db_session.add_all([result, job])
    db_session.commit()
    assert pv.number == 1
    assert crit.judge_check is None
    assert test.source_run_id is None and test.created_at is not None
    assert result.stale is False and result.transcript is None
    assert job.status == "queued" and job.attempts == 0
    assert job.result is None and job.error is None and job.locked_at is None


def test_workspace_delete_with_tests_results_and_jobs_cascades_with_foreign_keys_enforced():
    import warnings

    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from backend.core.config import PRESETS
    from backend.db.agents import create_agent
    from backend.db.workspaces import create_workspace

    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enforce_fks(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        workspace, _ = create_workspace(db)
        preset = PRESETS["support"]
        agent = create_agent(db, workspace, preset.config, preset.analysis)
        pv_id = agent.current_prompt_version_id
        run = Run(agent_id=agent.id, prompt_version_id=pv_id, position=1, messages=[])
        crit = _criterion(agent.id)
        db.add_all([run, crit])
        db.flush()
        fix = EvalTest(criterion_id=crit.id, source_run_id=run.id, role="fix", scripted_user_messages=["a"])
        held = EvalTest(criterion_id=crit.id, source_run_id=run.id, role="heldout", scripted_user_messages=["b"])
        db.add_all([fix, held])
        db.flush()
        db.add_all(
            [
                EvalResult(test_id=fix.id, prompt_version_id=pv_id, kind="baseline", verdict="fail", rationale="r"),
                EvalResult(test_id=fix.id, prompt_version_id=pv_id, kind="replay", verdict="pass", rationale="r"),
                EvalResult(test_id=held.id, prompt_version_id=pv_id, kind="replay", verdict="fail", rationale="r"),
                Job(workspace_id=workspace.id, type="replay", payload={"x": 1}),
            ]
        )
        db.commit()
        assert db.query(EvalResult).count() == 3
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            db.delete(workspace)
            db.commit()
        for model in (EvalTest, EvalResult, Job, Criterion, Run, Agent, PromptVersion, Workspace):
            assert db.query(model).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_deleting_criterion_cascades_to_tests_and_results(db_session):
    _, agent, pv = _make_agent(db_session)
    crit = _criterion(agent.id)
    db_session.add(crit)
    db_session.flush()
    test = EvalTest(criterion_id=crit.id, role="fix", scripted_user_messages=["a"])
    db_session.add(test)
    db_session.flush()
    db_session.add(
        EvalResult(test_id=test.id, prompt_version_id=pv.id, kind="replay", verdict="pass", rationale="r")
    )
    db_session.commit()
    db_session.delete(crit)
    db_session.commit()
    assert db_session.query(EvalTest).count() == 0
    assert db_session.query(EvalResult).count() == 0


def test_phase3_column_defaults(db_session):
    _, agent, pv = _make_agent(db_session)
    crit = _criterion(agent.id)
    db_session.add(crit)
    db_session.flush()
    test = EvalTest(criterion_id=crit.id, role="fix", scripted_user_messages=["hi"])
    db_session.add(test)
    db_session.commit()
    assert pv.change_report is None and pv.verification is None and pv.source_test_id is None
    assert test.origin == "run"
    assert crit.revision == 0


def test_prompt_version_number_unique_per_agent(db_session):
    _, agent, pv = _make_agent(db_session)
    db_session.commit()
    db_session.add(PromptVersion(agent_id=agent.id, text="x", origin="suggested", status="proposed", number=1))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    db_session.add(PromptVersion(agent_id=agent.id, text="x", origin="suggested", status="proposed", number=2))
    db_session.commit()


def test_prompt_version_number_may_repeat_across_agents(db_session):
    ws, agent, _ = _make_agent(db_session)
    other = Agent(workspace_id=ws.id, name="Bo", user_role_label="Customer")
    db_session.add(other)
    db_session.flush()
    db_session.add(PromptVersion(agent_id=other.id, text="y", origin="initial", status="promoted", number=1))
    db_session.commit()
    assert db_session.query(PromptVersion).filter_by(number=1).count() == 2
