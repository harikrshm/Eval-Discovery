"""The independent reference judge.

It grades a transcript against the scenario's own ``goal`` through the app's judge, with an agent
config that has an empty system prompt: the judge never sees the agent's prompt, and nothing the
app discovers (criteria, patterns, suggestions) feeds into it.
"""

from backend.core.config import AgentConfig
from backend.core.judge import judge
from simulation.builder import BuilderError
from simulation.scenarios import Scenario


def judge_preview(scenario: Scenario, transcript: list[dict]) -> str:
    """Return the reference verdict ("pass", "fail" or "not_applicable") for one transcript."""
    config = AgentConfig(scenario.agent_name, "", scenario.user_role_label)
    try:
        return judge(config, scenario.goal, transcript).verdict
    except Exception:
        raise BuilderError("the reference judge could not grade the transcript") from None
