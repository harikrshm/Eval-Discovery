import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.core.assist.brief import Brief
from backend.core.assist.lawpack import load_system_prompt
from backend.db.agents import create_agent, current_prompt
from backend.db.assist import (
    add_question,
    get_assist_agent,
    mark_reasked,
    save_flagged_run,
    set_rating,
    upsert_page_view,
)
from backend.db.models import (
    Agent,
    AssistPageView,
    AssistQuestion,
    Criterion,
    EvalTest,
    Run,
    Workspace,
    utcnow,
)
from backend.db.session import Base
from backend.db.workspaces import create_workspace, hash_token
from scripts import setup_assist

ROOT = Path(__file__).resolve().parent.parent
MESSAGES = [{"role": "user", "content": "Explain claim 1"}, {"role": "assistant", "content": "It covers..."}]


def _agent(db, kind="general"):
    workspace, _ = create_workspace(db)
    agent = create_agent(db, workspace, setup_assist.ASSIST_CONFIG, setup_assist.ASSIST_ANALYSIS, kind=kind)
    return workspace, agent


def test_new_columns_default_for_existing_code_paths(db_session):
    _, agent = _agent(db_session)
    assert agent.kind == "general"
    run = Run(agent_id=agent.id, prompt_version_id=agent.current_prompt_version_id, position=1, messages=[])
    db_session.add(run)
    db_session.commit()
    assert (run.source, run.session_id, run.brief_snapshot) == ("manual", None, None)


def test_test_case_has_a_brief_snapshot_column(db_session):
    _, agent = _agent(db_session)
    criterion = Criterion(
        agent_id=agent.id, position=1, name="n", definition="d", good_example="g", bad_example="b",
        why_discovered="w", source="discovered",
    )
    db_session.add(criterion)
    db_session.flush()
    case = EvalTest(criterion_id=criterion.id, role="fix", scripted_user_messages=["hi"])
    db_session.add(case)
    db_session.commit()
    assert case.brief_snapshot is None
    case.brief_snapshot = {"patent_number": "US1"}
    db_session.commit()
    assert db_session.get(EvalTest, case.id).brief_snapshot == {"patent_number": "US1"}


def test_unique_agent_session_rejects_a_duplicate_but_allows_many_nulls(db_session):
    _, agent = _agent(db_session)
    pv = agent.current_prompt_version_id

    def run(position, session_id):
        return Run(agent_id=agent.id, prompt_version_id=pv, position=position, messages=[], session_id=session_id)

    db_session.add_all([run(1, None), run(2, None), run(3, "s1")])
    db_session.commit()
    db_session.add(run(4, "s1"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_get_assist_agent_reads_the_env_at_call_time(db_session, monkeypatch):
    workspace, agent = _agent(db_session, kind="assist")
    monkeypatch.delenv("ASSIST_AGENT_ID", raising=False)
    with pytest.raises(LookupError) as unset:
        get_assist_agent(db_session)
    monkeypatch.setenv("ASSIST_AGENT_ID", agent.id)
    found, found_ws = get_assist_agent(db_session)
    assert (found.id, found_ws.id) == (agent.id, workspace.id)
    monkeypatch.setenv("ASSIST_AGENT_ID", "no-such-agent")
    with pytest.raises(LookupError) as unknown:
        get_assist_agent(db_session)
    assert str(unset.value) == str(unknown.value) != ""


def test_get_assist_agent_refuses_a_general_agent(db_session, monkeypatch):
    _, general = _agent(db_session, kind="general")
    monkeypatch.setenv("ASSIST_AGENT_ID", general.id)
    with pytest.raises(LookupError):
        get_assist_agent(db_session)


def test_upsert_page_view_updates_by_session_id(db_session):
    workspace, _ = _agent(db_session, kind="assist")
    started = datetime(2026, 1, 1, 12, 0, 0)
    first = upsert_page_view(
        db_session, workspace.id, "s1", "US123", 1000, {"claims": 500}, False, 1, "extension", started_at=started
    )
    assert first.started_at == started
    second = upsert_page_view(
        db_session, workspace.id, "s1", "US123", 9000, {"claims": 4000, "abstract": 100}, True, 2, "extension"
    )
    assert db_session.query(AssistPageView).count() == 1
    row = db_session.get(AssistPageView, "s1")
    assert (row.dwell_ms, row.bounce, row.assist_version) == (9000, True, 1)  # version: first seen
    assert row.sections == {"claims": 4000, "abstract": 100}
    assert row.started_at == started
    assert row.updated_at > started and second is row


def test_upsert_page_view_never_moves_a_session_between_workspaces(db_session):
    workspace, _ = _agent(db_session, kind="assist")
    other, _ = create_workspace(db_session)
    upsert_page_view(db_session, workspace.id, "s1", "US1", 1, {}, False, None, "extension")
    with pytest.raises(LookupError):
        upsert_page_view(db_session, other.id, "s1", "US1", 2, {}, False, None, "extension")


def test_add_question_rate_and_reask(db_session):
    workspace, _ = _agent(db_session, kind="assist")
    stamp = datetime(2026, 2, 3, 4, 5, 6)
    q = add_question(
        db_session, workspace.id, "s1", "US123", "What is claim 1?", "claim_explain", "claims",
        "It covers [claim 1].", True, "extension", 3, created_at=stamp,
    )
    assert (q.rating, q.rating_reason, q.reasked, q.created_at) == (None, None, False, stamp)
    defaulted = add_question(
        db_session, workspace.id, "s1", "US123", "q", "other", None, "a", False, "import", 3
    )
    assert defaulted.created_at <= utcnow()
    set_rating(db_session, q.id, "down", "too vague")
    mark_reasked(db_session, q.id)
    row = db_session.get(AssistQuestion, q.id)
    assert (row.rating, row.rating_reason, row.reasked) == ("down", "too vague", True)
    with pytest.raises(LookupError):
        set_rating(db_session, "missing", "up", None)
    with pytest.raises(LookupError):
        mark_reasked(db_session, "missing")


def test_save_flagged_run_creates_then_upserts(db_session):
    _, agent = _agent(db_session, kind="assist")
    brief = Brief(patent_number="US123")
    first = save_flagged_run(db_session, agent, "s1", MESSAGES, brief, "extension")
    assert first.position == 1
    assert first.prompt_version_id == agent.current_prompt_version_id
    assert first.brief_snapshot == brief.model_dump()
    assert (first.source, first.session_id, first.messages) == ("extension", "s1", MESSAGES)

    second = save_flagged_run(db_session, agent, "s2", MESSAGES[:1], {"patent_number": "US9"}, "extension")
    assert second.position == 2 and second.brief_snapshot == {"patent_number": "US9"}

    again = save_flagged_run(db_session, agent, "s1", MESSAGES[:1], {"patent_number": "US5"}, "import")
    assert again.id == first.id and again.position == 1
    assert db_session.query(Run).count() == 2
    row = db_session.get(Run, first.id)
    assert row.messages == MESSAGES[:1]
    assert (row.brief_snapshot, row.source) == ({"patent_number": "US5"}, "import")


def test_save_flagged_run_uses_the_current_version_and_strips_extra_message_keys(db_session):
    from backend.db.agents import new_prompt_version

    _, agent = _agent(db_session, kind="assist")
    new_prompt_version(db_session, agent, "v2 text")
    db_session.commit()
    run = save_flagged_run(
        db_session, agent, "s1", [{"role": "user", "content": "hi", "extra": 1}], Brief(patent_number="US1"), "x"
    )
    assert run.prompt_version_id == agent.current_prompt_version_id
    assert run.messages == [{"role": "user", "content": "hi"}]


def test_save_flagged_run_retries_once_on_a_position_conflict(db_session, monkeypatch):
    _, agent = _agent(db_session, kind="assist")
    save_flagged_run(db_session, agent, "s1", MESSAGES, {"patent_number": "US1"}, "x")
    real_scalar = db_session.scalar
    calls = {"n": 0}

    def stale_count(statement, *args, **kwargs):
        # The first position count is stale (0), as if another save landed in between.
        if "count" in str(statement).lower():
            calls["n"] += 1
            if calls["n"] == 1:
                return 0
        return real_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "scalar", stale_count)
    run = save_flagged_run(db_session, agent, "s2", MESSAGES, {"patent_number": "US2"}, "x")
    assert run.position == 2 and calls["n"] == 2


def test_deleting_a_workspace_cascades_to_the_assist_rows(db_session):
    workspace, agent = _agent(db_session, kind="assist")
    upsert_page_view(db_session, workspace.id, "s1", "US1", 1, {}, False, 1, "extension")
    add_question(db_session, workspace.id, "s1", "US1", "q", "other", None, "a", True, "extension", 1)
    save_flagged_run(db_session, agent, "s1", MESSAGES, {"patent_number": "US1"}, "extension")
    other, _ = create_workspace(db_session)
    upsert_page_view(db_session, other.id, "s9", "US9", 1, {}, False, 1, "extension")

    db_session.delete(db_session.get(Workspace, workspace.id))
    db_session.commit()

    assert db_session.query(AssistQuestion).count() == 0
    assert [v.session_id for v in db_session.query(AssistPageView)] == ["s9"]
    assert db_session.query(Run).count() == 0 and db_session.query(Agent).count() == 0


# --- setup script ---------------------------------------------------------------------------


@pytest.fixture
def script_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'setup.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    monkeypatch.setenv("DATABASE_URL", url)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_setup_creates_one_assist_agent_with_a_far_future_workspace(script_db, capsys):
    before = utcnow()
    assert setup_assist.main([]) == 0
    out = capsys.readouterr().out

    agents = script_db.scalars(select(Agent)).all()
    assert len(agents) == 1 and agents[0].kind == "assist" and agents[0].name == "Patent Assist"
    workspace = script_db.get(Workspace, agents[0].workspace_id)
    assert workspace.expires_at > before + timedelta(days=3600)
    assert current_prompt(agents[0]) == load_system_prompt()
    assert f"ASSIST_AGENT_ID={agents[0].id}" in out
    assert "ASSIST_KEY" in out
    token_lines = [line for line in out.splitlines() if line.startswith("Workspace token:")]
    assert len(token_lines) == 1
    token = token_lines[0].split(": ", 1)[1].strip()
    assert workspace.token_hash == hash_token(token)
    assert out.count(token) == 1  # printed once, nowhere else


def test_setup_refuses_a_second_run_without_force(script_db, capsys):
    assert setup_assist.main([]) == 0
    capsys.readouterr()
    assert setup_assist.main([]) == 1
    captured = capsys.readouterr()
    assert "already exists" in captured.err
    assert "Workspace token" not in captured.out
    assert script_db.query(Agent).count() == 1


def test_setup_force_creates_a_second_agent_and_warns(script_db, capsys):
    assert setup_assist.main([]) == 0
    capsys.readouterr()
    assert setup_assist.main(["--force"]) == 0
    out = capsys.readouterr()
    assert script_db.query(Agent).count() == 2
    assert "ASSIST_AGENT_ID selects which" in out.out + out.err


def test_setup_reports_an_unmigrated_database_without_the_url(tmp_path, monkeypatch, capsys):
    url = f"sqlite:///{tmp_path / 'empty.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    assert setup_assist.main([]) == 1
    err = capsys.readouterr().err
    assert "alembic upgrade head" in err and str(tmp_path) not in err


def test_setup_runs_as_a_script_from_another_directory(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["DATABASE_URL"] = f"sqlite:///{tmp_path / 'cli.db'}"
    env["GROQ_SKIP_DOTENV"] = "1"
    migrate = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", "head"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert migrate.returncode == 0, migrate.stderr
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "setup_assist.py")],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ASSIST_AGENT_ID=" in result.stdout
