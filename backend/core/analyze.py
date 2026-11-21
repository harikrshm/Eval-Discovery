"""Map a pasted agent prompt to testable behaviors via a single LLM call."""

import json

from backend.core.config import AgentAnalysis, Behavior
from backend.core.prompts import build_analyze_messages
from backend.groq_client import MODEL_PATTERN, groq_chat
from backend.utils import extract_json

MIN_BEHAVIORS = 3
MAX_BEHAVIORS = 8
MAX_TERMS = 12
STARTER_COUNT = 3
MAX_LABEL_CHARS = 40


class AnalysisError(Exception):
    """Raised when the agent prompt could not be analyzed into a valid result."""


def _clean_str(value, what: str) -> str:
    if not isinstance(value, str):
        raise AnalysisError(f"{what} must be a string")
    return value.strip()


def _parse_behavior(raw) -> Behavior:
    if not isinstance(raw, dict):
        raise AnalysisError("behavior must be an object")
    name = _clean_str(raw.get("name"), "behavior name")
    description = _clean_str(raw.get("description", ""), "behavior description")
    terms_raw = raw.get("trigger_terms")
    if not isinstance(terms_raw, list):
        raise AnalysisError("trigger_terms must be a list")
    terms: list[str] = []
    for t in terms_raw:
        term = _clean_str(t, "trigger term").lower()
        if term and term not in terms:
            terms.append(term)
    terms = terms[:MAX_TERMS]
    if not name:
        raise AnalysisError("behavior name is empty")
    if not terms:
        raise AnalysisError(f"behavior {name!r} has no trigger terms")
    return Behavior(name=name, description=description, trigger_terms=tuple(terms))


def _parse(text: str) -> AgentAnalysis:
    try:
        data = json.loads(extract_json(text))
    except (json.JSONDecodeError, TypeError) as exc:
        raise AnalysisError("model output was not valid JSON") from exc
    if not isinstance(data, dict):
        raise AnalysisError("model output was not a JSON object")

    label = _clean_str(data.get("user_role_label"), "user_role_label")
    if not label or len(label) > MAX_LABEL_CHARS:
        raise AnalysisError("user_role_label must be 1-40 characters")

    behaviors_raw = data.get("behaviors")
    if not isinstance(behaviors_raw, list) or not (
        MIN_BEHAVIORS <= len(behaviors_raw) <= MAX_BEHAVIORS
    ):
        raise AnalysisError(f"expected {MIN_BEHAVIORS}-{MAX_BEHAVIORS} behaviors")
    behaviors = tuple(_parse_behavior(b) for b in behaviors_raw)

    starters_raw = data.get("starter_messages")
    if not isinstance(starters_raw, list) or len(starters_raw) != STARTER_COUNT:
        raise AnalysisError(f"expected exactly {STARTER_COUNT} starter messages")
    starters = tuple(_clean_str(s, "starter message") for s in starters_raw)
    if not all(starters):
        raise AnalysisError("starter messages must be non-empty")

    return AgentAnalysis(user_role_label=label, behaviors=behaviors, starter_messages=starters)


def analyze_agent(system_prompt: str, name: str) -> AgentAnalysis:
    """Analyze a pasted agent prompt. Raises AnalysisError on any failure."""
    system, messages = build_analyze_messages(system_prompt)
    try:
        text = groq_chat(system, messages, model=MODEL_PATTERN)
    except Exception as exc:
        raise AnalysisError(f"analysis call failed: {exc}") from exc
    return _parse(text)
