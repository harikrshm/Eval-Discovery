from backend.core.config import PRESETS, AgentAnalysis, Behavior
import pytest

from backend.core.config import MAX_AGENTS_PER_WORKSPACE
from backend.db.agents import AgentLimitReached, agent_count, create_agent, current_behaviors
from backend.db.models import Agent, BehaviorMap, PromptVersion
from backend.db.workspaces import create_workspace


def test_current_behaviors_rebuilds_behavior_objects_from_json(db_session):
    workspace, _ = create_workspace(db_session)
    preset = PRESETS["alex-sales"]
    agent = create_agent(db_session, workspace, preset.config, preset.analysis)
    # Stored analysis is JSON (lists), the helper must hand back real Behavior objects with tuples.
    assert agent.behavior_maps[0].analysis["behaviors"][0]["trigger_terms"] == list(
        preset.analysis.behaviors[0].trigger_terms
    )
    assert current_behaviors(agent) == list(preset.analysis.behaviors)


def test_current_behaviors_custom_analysis(db_session):
    workspace, _ = create_workspace(db_session)
    preset = PRESETS["support"]
    analysis = AgentAnalysis(
        user_role_label="Customer",
        behaviors=(Behavior("Refunds", "No refunds.", ("refund", "money back")),),
        starter_messages=("a", "b", "c"),
    )
    agent = create_agent(db_session, workspace, preset.config, analysis)
    assert current_behaviors(agent) == [Behavior("Refunds", "No refunds.", ("refund", "money back"))]


def test_create_agent_enforces_quota_with_no_partial_rows(db_session):
    workspace, _ = create_workspace(db_session)
    preset = PRESETS["support"]
    for _ in range(MAX_AGENTS_PER_WORKSPACE):
        create_agent(db_session, workspace, preset.config, preset.analysis)
    with pytest.raises(AgentLimitReached):
        create_agent(db_session, workspace, preset.config, preset.analysis)
    assert db_session.query(Agent).count() == MAX_AGENTS_PER_WORKSPACE
    assert db_session.query(PromptVersion).count() == MAX_AGENTS_PER_WORKSPACE
    assert db_session.query(BehaviorMap).count() == MAX_AGENTS_PER_WORKSPACE


def test_agent_count_counts_only_that_workspaces_agents(db_session):
    first, _ = create_workspace(db_session)
    second, _ = create_workspace(db_session)
    preset = PRESETS["support"]
    assert agent_count(db_session, first) == 0
    create_agent(db_session, first, preset.config, preset.analysis)
    create_agent(db_session, first, preset.config, preset.analysis)
    create_agent(db_session, second, preset.config, preset.analysis)
    assert agent_count(db_session, first) == 2
    assert agent_count(db_session, second) == 1


def test_db_layer_does_not_import_the_api_layer():
    import ast
    from pathlib import Path

    import backend.db

    for path in Path(backend.db.__file__).parent.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            assert not any(n.startswith("backend.api") for n in names), path.name
