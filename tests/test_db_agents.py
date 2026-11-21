from backend.core.config import PRESETS, AgentAnalysis, Behavior
from backend.db.agents import create_agent, current_behaviors
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
