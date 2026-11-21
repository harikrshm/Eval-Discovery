"""Held-out scenario generator: write a new customer scenario that probes the same
criterion as an existing fix test but differs from it.

Pure core module: takes plain arguments and never touches the API or database layers.
The fix test's messages and earlier rejected scenarios are shown to the model only as
delimited data, and a result that repeats any of them is a failed attempt.
"""

import json

from backend.core.config import CriterionSpec
from backend.core.prompts import build_scenario_messages
from backend.groq_client import MODEL_JUDGE, groq_chat
from backend.utils import extract_json

MIN_SCENARIO_MESSAGES = 2
MAX_SCENARIO_MESSAGES = 5
MAX_MESSAGE_CHARS = 2000


class GenerateFailed(RuntimeError):
    """Raised when the generator call or its output is unusable. Messages are fixed
    strings and never include provider or model text."""


def _normalize(messages: list[str]) -> tuple[str, ...]:
    return tuple(" ".join(m.lower().split()) for m in messages)


def _parse(raw: str) -> list[str]:
    try:
        data = json.loads(extract_json(raw))
    except (json.JSONDecodeError, TypeError) as exc:
        raise GenerateFailed("generator output was not valid JSON") from exc
    messages = data.get("messages") if isinstance(data, dict) else data
    if not isinstance(messages, list):
        raise GenerateFailed("generator output has no message list")
    if not MIN_SCENARIO_MESSAGES <= len(messages) <= MAX_SCENARIO_MESSAGES:
        raise GenerateFailed("generator output has the wrong number of messages")
    cleaned = []
    for message in messages:
        if not isinstance(message, str):
            raise GenerateFailed("generator output has a non-text message")
        message = message.strip()
        if not message or len(message) > MAX_MESSAGE_CHARS:
            raise GenerateFailed("generator output has an empty or over-long message")
        cleaned.append(message)
    return cleaned


def generate_scenario(
    criterion: CriterionSpec, fix_messages: list[str], avoid: list[list[str]]
) -> list[str]:
    """Return 2-5 user messages for a new scenario, or raise ``GenerateFailed``.

    A scenario equal to ``fix_messages`` or to any scenario in ``avoid`` (compared as whole
    message lists, case-insensitive and whitespace-normalized) is rejected."""
    system, messages = build_scenario_messages(criterion, fix_messages, avoid)
    try:
        raw = groq_chat(system, messages, model=MODEL_JUDGE, temperature=0.7)
    except Exception as exc:
        raise GenerateFailed("generator call failed") from exc
    scenario = _parse(raw)
    key = _normalize(scenario)
    if key == _normalize(fix_messages) or any(key == _normalize(a) for a in avoid):
        raise GenerateFailed("generated scenario repeats an existing scenario")
    return scenario
