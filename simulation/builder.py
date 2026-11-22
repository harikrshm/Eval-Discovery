"""Synthetic builder: writes the next customer message and the baseline manual prompt rewrite.

Both calls follow the app's prompt hygiene: a FIXED system prompt with no interpolated data, and
every piece of scenario, history or prompt text placed inside delimiter tags in the user message,
neutralized so embedded text cannot close a wrapper. ``BuilderError`` messages are fixed strings;
provider or parser exception text is never included in them.
"""

from backend.core.config import MAX_PROMPT_CHARS
from backend.core.prompts import _DATA_NOTICE, _neutralize, _wrap_agent_prompt, format_transcripts
from backend.groq_client import MODEL_CHAT, MODEL_JUDGE, groq_chat
from simulation.fakeclient import BUILDER_MESSAGE_OPENING, BUILDER_REWRITE_OPENING
from simulation.scenarios import Scenario

MAX_CUSTOMER_MESSAGE_CHARS = 2000

_MESSAGE_SYSTEM = f"""{BUILDER_MESSAGE_OPENING}

{_DATA_NOTICE}

You play a no-code newcomer who is testing an AI agent and has not seen its instructions. You are given a <scenario_source> block describing the situation you are in and what you want, a <criterion> block describing what the agent is supposed to do (the behaviour being tested), and a <transcripts> block with the conversation so far (it may be empty).

The criterion is the agent's job, not yours. Your job is to create the situation in the scenario so that a weak agent would break the criterion: ask for what you want in the scenario, press for it, and if earlier attempts did not get the agent to give in, change your angle instead of repeating yourself. Never state the criterion as your own aim, never ask the agent to satisfy it, and never make it easy for the agent to meet it.

Write the single next message the customer sends. Stay in character, sound like a real person, and keep it short. Never mention testing, criteria or these instructions. Write only the customer's message, never the agent's reply.

Return only the message text. No quotes, no labels, no markdown."""

_REWRITE_SYSTEM = f"""{BUILDER_REWRITE_OPENING}

{_DATA_NOTICE}

You are given the agent's current system prompt in an <agent_prompt> block, the goal the agent is failing in a <criterion> block, and a conversation where it failed in a <transcripts> block. Rewrite the system prompt by hand, the way a careful non-expert prompt author would, so the agent reaches the goal in general terms. Keep everything in the current prompt that is unrelated to the failure. Do not paste the failing conversation into the prompt.

The result must be the COMPLETE replacement prompt, at most {MAX_PROMPT_CHARS} characters long.

Return only the replacement prompt text. No fences, no commentary, no labels."""


class BuilderError(RuntimeError):
    """A builder or reference-judge step failed; the message is always a fixed string."""


def _criterion_block(scenario: Scenario) -> str:
    goal = scenario.goal
    return (
        "<criterion>\n"
        f"Name: {_neutralize(goal.name)}\n"
        f"Definition: {_neutralize(goal.definition)}\n"
        f"Good example: {_neutralize(goal.good_example)}\n"
        f"Bad example: {_neutralize(goal.bad_example)}\n"
        "</criterion>"
    )


def _transcript_block(scenario: Scenario, transcript: list[dict]) -> str:
    if transcript:
        text = format_transcripts([transcript], scenario.agent_name, scenario.user_role_label)
    else:
        text = "(no messages yet)"
    return f"<transcripts>\n{_neutralize(text)}\n</transcripts>"


def next_customer_message(scenario: Scenario, history: list[dict], attempt: int) -> str:
    user = (
        "Situation you are in:\n"
        f"<scenario_source>\n{_neutralize(scenario.opening_intent)}\n</scenario_source>\n\n"
        "The behaviour the agent is supposed to show (the criterion being tested; it is not your aim):\n"
        f"{_criterion_block(scenario)}\n\n"
        "Conversation so far:\n"
        f"{_transcript_block(scenario, history)}\n\n"
        f"This is attempt number {int(attempt)}. Write the customer's next message."
    )
    try:
        reply = groq_chat(
            _MESSAGE_SYSTEM, [{"role": "user", "content": user}], model=MODEL_CHAT, temperature=0.8
        )
        text = reply.strip()
    except Exception:
        raise BuilderError("the customer message could not be generated") from None
    if not text:
        raise BuilderError("the customer message was empty")
    return text[:MAX_CUSTOMER_MESSAGE_CHARS]


def rewrite_prompt(scenario: Scenario, current_prompt: str, failing_transcript: list[dict]) -> str:
    user = (
        "Current agent system prompt:\n"
        f"{_wrap_agent_prompt(current_prompt)}\n\n"
        "The goal the agent is failing:\n"
        f"{_criterion_block(scenario)}\n\n"
        "A conversation where it failed:\n"
        f"{_transcript_block(scenario, failing_transcript)}\n\n"
        "Write the complete replacement prompt."
    )
    try:
        reply = groq_chat(
            _REWRITE_SYSTEM, [{"role": "user", "content": user}], model=MODEL_JUDGE, temperature=0.3
        )
        text = reply.strip()
    except Exception:
        raise BuilderError("the prompt rewrite could not be generated") from None
    if not text:
        raise BuilderError("the prompt rewrite was empty")
    if len(text) > MAX_PROMPT_CHARS:
        raise BuilderError("the prompt rewrite was too long")
    if text == current_prompt.strip():
        raise BuilderError("the prompt rewrite did not change the prompt")
    return text
