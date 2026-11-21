"""Agent-agnostic evaluation criterion generation from a detected pattern."""

import json
from dataclasses import dataclass

from backend.core.config import AgentConfig
from backend.core.prompts import build_rubric_messages
from backend.groq_client import MODEL_CRITERIA, groq_chat
from backend.utils import extract_json

_FIELDS = ("name", "definition", "good_example", "bad_example", "why_discovered")
_DIALOGUE_FIELDS = ("good_example", "bad_example")


@dataclass(frozen=True)
class CriterionDraft:
    name: str
    definition: str
    good_example: str
    bad_example: str
    why_discovered: str


class CriterionGenerationFailed(RuntimeError):
    """Raised when the criterion call or its output is unusable."""


def _join_dialogue(turns: list) -> str:
    """Join a list of {speaker, text|content} objects into "Speaker: 'text'" strings."""
    parts = []
    for turn in turns:
        if not isinstance(turn, dict):
            raise CriterionGenerationFailed("dialogue turns must be objects")
        text = turn.get("text", turn.get("content", ""))
        parts.append(f"{turn.get('speaker', '')}: '{text}'")
    return " ".join(parts)


def _parse(raw: str) -> CriterionDraft:
    try:
        data = json.loads(extract_json(raw))
    except (json.JSONDecodeError, TypeError) as exc:
        raise CriterionGenerationFailed("criterion output was not valid JSON") from exc
    if not isinstance(data, dict):
        raise CriterionGenerationFailed("criterion output must be a JSON object")

    values: dict[str, str] = {}
    for field in _FIELDS:
        value = data.get(field)
        if field in _DIALOGUE_FIELDS and isinstance(value, list):
            value = _join_dialogue(value)
        if not isinstance(value, str) or not value.strip():
            raise CriterionGenerationFailed(f"criterion output is missing '{field}'")
        values[field] = value.strip()
    return CriterionDraft(**values)


def generate_criterion(config: AgentConfig, pattern_summary: str) -> CriterionDraft:
    """Generate one evaluation criterion grounded in the detected pattern."""
    if not pattern_summary or not pattern_summary.strip():
        raise ValueError("pattern_summary must not be blank.")

    system, messages = build_rubric_messages(config, pattern_summary)
    try:
        raw = groq_chat(system, messages, model=MODEL_CRITERIA)
    except Exception as exc:
        raise CriterionGenerationFailed(f"criterion generation call failed: {exc}") from exc
    return _parse(raw)
