"""Keyword prefilter and LLM pattern detection across test transcripts."""

import json
from collections.abc import Sequence
from dataclasses import dataclass

from backend.core.config import AgentConfig, Behavior
from backend.core.prompts import build_pattern_messages
from backend.groq_client import MODEL_PATTERN, groq_chat
from backend.utils import extract_json

MIN_TURNS_PER_TRANSCRIPT = 3


@dataclass(frozen=True)
class PatternResult:
    found: bool
    pattern_summary: str


class TranscriptTooShort(ValueError):
    """Raised when a transcript has fewer than MIN_TURNS_PER_TRANSCRIPT turns."""


class PatternCheckFailed(RuntimeError):
    """Raised when the pattern check call or its output is unusable."""


def keyword_prefilter(behaviors: Sequence[Behavior], transcripts: list[list[dict]]) -> bool:
    """True if any trigger term appears in user messages of >= 2 different transcripts."""
    if len(transcripts) < 2:
        return False
    terms = {t.lower() for b in behaviors for t in b.trigger_terms if t}
    per_run: list[set[str]] = []
    for transcript in transcripts:
        user_text = [m["content"].lower() for m in transcript if m.get("role") == "user"]
        per_run.append({t for t in terms if any(t in text for text in user_text)})
    return any(sum(1 for hits in per_run if t in hits) >= 2 for t in terms)


def _parse(raw: str) -> PatternResult:
    try:
        data = json.loads(extract_json(raw))
    except (json.JSONDecodeError, TypeError) as exc:
        raise PatternCheckFailed("pattern output was not valid JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("found"), bool):
        raise PatternCheckFailed("pattern output is missing a boolean 'found'")
    summary = data.get("pattern_summary", "")
    if summary is None:
        summary = ""
    if not isinstance(summary, str):
        raise PatternCheckFailed("pattern_summary must be a string")
    summary = summary.strip()
    if data["found"] and not summary:
        raise PatternCheckFailed("pattern found but summary is empty")
    return PatternResult(found=data["found"], pattern_summary=summary)


def detect_pattern(config: AgentConfig, transcripts: list[list[dict]]) -> PatternResult:
    """Ask the model whether the transcripts show a repeated testing pattern."""
    if not transcripts:
        raise ValueError("transcripts must not be empty.")
    for i, transcript in enumerate(transcripts, 1):
        if len(transcript) < MIN_TURNS_PER_TRANSCRIPT:
            raise TranscriptTooShort(
                f"Transcript {i} is too short; have a longer conversation first "
                f"(minimum {MIN_TURNS_PER_TRANSCRIPT} turns)."
            )

    system, messages = build_pattern_messages(config, transcripts)
    try:
        raw = groq_chat(system, messages, model=MODEL_PATTERN)
    except Exception as exc:
        raise PatternCheckFailed(f"pattern check call failed: {exc}") from exc
    return _parse(raw)
