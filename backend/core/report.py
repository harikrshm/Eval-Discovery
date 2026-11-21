"""Change-report writer: explain the stored results of a prompt change in plain language.

Pure core module: takes plain arguments and never touches the API or database layers.
The writer may see the prompt diff (only the verdict judge is kept blind to the agent
prompt). It receives the diff, the table rows and the stored outcome as delimited data and
returns only a narrative: it never returns, accepts or overrides a status or a verdict.
"""

import json
from dataclasses import dataclass

from backend.core.prompts import build_report_messages
from backend.groq_client import MODEL_JUDGE, groq_chat
from backend.utils import extract_json

MAX_TEXT_CHARS = 2000


@dataclass(frozen=True)
class ReportNarrative:
    explanation: str
    next_step: str
    cited_test_ids: tuple[str, ...]


class ReportFailed(RuntimeError):
    """Raised when the report call or its output is unusable. Messages are fixed strings
    and never include provider or model text."""


def _text(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ReportFailed(f"report output is missing {key}")
    return value.strip()[:MAX_TEXT_CHARS]


def _citations(value: object, allowed: set[str]) -> tuple[str, ...]:
    """Keep only ids that are real row ids, in first-seen order. Never raises."""
    if not isinstance(value, list):
        return ()
    kept: list[str] = []
    for item in value:
        if isinstance(item, str) and item in allowed and item not in kept:
            kept.append(item)
    return tuple(kept)


def _parse(raw: str, allowed: set[str]) -> ReportNarrative:
    try:
        data = json.loads(extract_json(raw))
    except (json.JSONDecodeError, TypeError) as exc:
        raise ReportFailed("report output was not valid JSON") from exc
    if not isinstance(data, dict):
        raise ReportFailed("report output must be a JSON object")
    return ReportNarrative(
        explanation=_text(data, "explanation"),
        next_step=_text(data, "next_step"),
        cited_test_ids=_citations(data.get("cited_test_ids"), allowed),
    )


def write_report(diff_text: str, rows: list[dict], status: str, reason: str) -> ReportNarrative:
    """Return the narrative for a finished candidate, or raise ``ReportFailed``.

    ``status`` and ``reason`` are the code-decided outcome, shown to the model as data only.
    Empty text is rejected; text over 2000 characters is truncated; citations that are not a
    row's ``test_id`` are dropped."""
    system, messages = build_report_messages(diff_text, rows, status, reason)
    try:
        raw = groq_chat(system, messages, model=MODEL_JUDGE, temperature=0)
    except Exception as exc:
        raise ReportFailed("report call failed") from exc
    allowed = {row["test_id"] for row in rows if isinstance(row.get("test_id"), str)}
    return _parse(raw, allowed)
