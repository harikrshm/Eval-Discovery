"""Suggested fix: rewrite an agent's prompt so a failing criterion stops failing.

Pure core module: takes plain arguments and never touches the API or database layers.
The suggester sees only the current prompt, the criterion and the failing result
(rationale and transcript); it is never given any held-out test.
"""

import json
from dataclasses import dataclass

from backend.core.config import MAX_PROMPT_CHARS, AgentConfig, CriterionSpec
from backend.core.prompts import build_suggest_messages
from backend.groq_client import MODEL_JUDGE, groq_chat
from backend.utils import extract_json

_TEXT_FIELDS = ("change_summary", "touched_section", "expected_effect", "risk")


@dataclass(frozen=True)
class SuggestedFix:
    prompt_text: str
    failing_turn_index: int
    change_summary: str
    touched_section: str
    expected_effect: str
    risk: str


class SuggestFailed(RuntimeError):
    """Raised when the suggester call or its output is unusable."""


def _numbered_transcript(config: AgentConfig, transcript: list[dict]) -> str:
    lines = []
    for i, msg in enumerate(transcript):
        speaker = config.user_role_label if msg["role"] == "user" else config.name
        lines.append(f"[{i}] {speaker}: {msg['content']}")
    return "\n".join(lines)


def _parse(raw: str, config: AgentConfig, turn_count: int) -> SuggestedFix:
    try:
        data = json.loads(extract_json(raw))
    except (json.JSONDecodeError, TypeError) as exc:
        raise SuggestFailed("suggester output was not valid JSON") from exc
    if not isinstance(data, dict):
        raise SuggestFailed("suggester output must be a JSON object")

    prompt = data.get("prompt_text")
    if not isinstance(prompt, str) or not prompt.strip():
        raise SuggestFailed("suggester output is missing a prompt")
    prompt = prompt.strip()
    if len(prompt) > MAX_PROMPT_CHARS:
        raise SuggestFailed("suggested prompt is too long")
    if prompt == config.system_prompt.strip():
        raise SuggestFailed("suggested prompt is identical to the current prompt")

    index = data.get("failing_turn_index")
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < turn_count:
        raise SuggestFailed("suggester output has an invalid failing turn index")

    fields = {}
    for key in _TEXT_FIELDS:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise SuggestFailed(f"suggester output is missing {key}")
        fields[key] = value.strip()
    return SuggestedFix(prompt_text=prompt, failing_turn_index=index, **fields)


def suggest_fix(
    config: AgentConfig,
    criterion: CriterionSpec,
    failing_rationale: str,
    failing_transcript: list[dict],
) -> SuggestedFix:
    """Propose a replacement prompt from one failing result.

    ``failing_turn_index`` in the result indexes ``failing_transcript``."""
    system, messages = build_suggest_messages(
        config, criterion, failing_rationale, _numbered_transcript(config, failing_transcript)
    )
    try:
        raw = groq_chat(system, messages, model=MODEL_JUDGE, temperature=0.2)
    except Exception as exc:
        raise SuggestFailed(f"suggester call failed: {exc}") from exc
    return _parse(raw, config, len(failing_transcript))
