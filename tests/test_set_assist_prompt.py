from dataclasses import replace

from sqlalchemy import select

from backend.core.assist.lawpack import load_system_prompt
from backend.db.agents import create_agent, current_prompt, current_prompt_version
from backend.db.models import Agent, PromptVersion
from backend.db.workspaces import create_workspace
from scripts import set_assist_prompt, setup_assist
from tests.test_assist_db import script_db  # noqa: F401  (fixture: a migrated temp database)


def _assist_agent(db, prompt="an old prompt"):
    workspace, _ = create_workspace(db)
    config = replace(setup_assist.ASSIST_CONFIG, system_prompt=prompt)
    agent = create_agent(db, workspace, config, setup_assist.ASSIST_ANALYSIS, kind="assist")
    db.commit()
    return agent


def _versions(db, agent):
    return db.scalars(
        select(PromptVersion).where(PromptVersion.agent_id == agent.id).order_by(PromptVersion.number)
    ).all()


def test_an_old_prompt_becomes_a_new_current_version(script_db, monkeypatch, capsys):
    agent = _assist_agent(script_db)
    monkeypatch.setenv("ASSIST_AGENT_ID", agent.id)

    assert set_assist_prompt.main([]) == 0
    assert "Created prompt version 2 and made it current." in capsys.readouterr().out

    script_db.expire_all()
    agent = script_db.get(Agent, agent.id)
    versions = _versions(script_db, agent)
    assert [(v.number, v.status, v.origin) for v in versions] == [
        (1, "superseded", "initial"),
        (2, "promoted", "manual"),
    ]
    assert current_prompt_version(agent).number == 2
    assert current_prompt(agent) == load_system_prompt()


def test_running_it_again_changes_nothing(script_db, monkeypatch, capsys):
    agent = _assist_agent(script_db)
    monkeypatch.setenv("ASSIST_AGENT_ID", agent.id)
    assert set_assist_prompt.main([]) == 0
    capsys.readouterr()

    assert set_assist_prompt.main([]) == 0
    assert "Prompt already current (version 2)." in capsys.readouterr().out
    script_db.expire_all()
    assert len(_versions(script_db, script_db.get(Agent, agent.id))) == 2


def test_an_agent_that_already_has_the_prompt_is_left_alone(script_db, monkeypatch, capsys):
    agent = _assist_agent(script_db, prompt=load_system_prompt())
    monkeypatch.setenv("ASSIST_AGENT_ID", agent.id)
    assert set_assist_prompt.main([]) == 0
    assert "Prompt already current (version 1)." in capsys.readouterr().out
    script_db.expire_all()
    assert len(_versions(script_db, script_db.get(Agent, agent.id))) == 1


def test_an_unset_agent_id_exits_one_with_one_message(script_db, monkeypatch, capsys):
    monkeypatch.delenv("ASSIST_AGENT_ID", raising=False)
    assert set_assist_prompt.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.strip().splitlines()) == 1
    assert "sqlite" not in captured.err
