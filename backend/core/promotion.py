"""Pure promotion rule and report helpers for a candidate prompt version.

The rule is code, never an LLM: promote iff the fix test passes, the held-out
test passes when present, and no regression test has verdict "fail". A
"not_applicable" fix or held-out verdict counts as not passing; a
"not_applicable" regression verdict is ignored.

Pure core module: no I/O, no LLM calls.
"""

import json
from dataclasses import dataclass

ROLES = ("fix", "heldout", "regression")
VERDICTS = ("pass", "fail", "not_applicable")

SUCCESS_REASON = "All required tests passed."
FIX_FAILED_REASON = "The fix test did not pass."
HELDOUT_FAILED_REASON = "The held-out test did not pass."
REGRESSION_FAILED_REASON = "A previously passing test now fails."

QUOTE_MAX_CHARS = 500
RATIONALE_MAX_CHARS = 600
TRANSCRIPT_MAX_JSON_CHARS = 6000
ELLIPSIS = "…"


@dataclass(frozen=True)
class RequiredTest:
    test_id: str
    role: str  # "fix" | "heldout" | "regression"
    before: str | None  # verdict recorded at approval time


@dataclass(frozen=True)
class Decision:
    promote: bool
    reason: str
    failing_test_ids: tuple[str, ...]


def _validate(required: list[RequiredTest], verdicts: dict[str, str]) -> None:
    seen: set[str] = set()
    counts = {role: 0 for role in ROLES}
    for test in required:
        if test.role not in ROLES:
            raise ValueError(f"Unknown test role: {test.role!r}")
        if test.test_id in seen:
            raise ValueError(f"Duplicate required test: {test.test_id!r}")
        seen.add(test.test_id)
        counts[test.role] += 1
        if test.test_id not in verdicts:
            raise ValueError(f"No verdict for required test {test.test_id!r}")
        if verdicts[test.test_id] not in VERDICTS:
            raise ValueError(f"Unknown verdict for test {test.test_id!r}: {verdicts[test.test_id]!r}")
    if counts["fix"] != 1:
        raise ValueError("Exactly one fix test is required.")
    if counts["heldout"] > 1:
        raise ValueError("At most one held-out test is allowed.")


def decide_promotion(required: list[RequiredTest], verdicts: dict[str, str]) -> Decision:
    """Apply the promotion rule to the replay verdicts of the required tests.

    Raises ValueError for malformed input (no or several fix tests, several
    held-out tests, unknown role, duplicate test, missing or unknown verdict).
    Reasons are reported in the order fix, held-out, regression.
    """
    _validate(required, verdicts)

    fix = next(t for t in required if t.role == "fix")
    if verdicts[fix.test_id] != "pass":
        return Decision(False, FIX_FAILED_REASON, (fix.test_id,))

    heldout = next((t for t in required if t.role == "heldout"), None)
    if heldout is not None and verdicts[heldout.test_id] != "pass":
        return Decision(False, HELDOUT_FAILED_REASON, (heldout.test_id,))

    regressions = tuple(
        t.test_id for t in required if t.role == "regression" and verdicts[t.test_id] == "fail"
    )
    if regressions:
        return Decision(False, REGRESSION_FAILED_REASON, regressions)

    return Decision(True, SUCCESS_REASON, ())


def strength_for(required: list[RequiredTest]) -> str:
    """"full" when a held-out test is required, otherwise "weaker"."""
    return "full" if any(t.role == "heldout" for t in required) else "weaker"


def _clip(text: str, limit: int) -> str:
    """At most `limit` characters; a clipped text ends with an ellipsis."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + ELLIPSIS


def quote_turn(transcript: list[dict] | None, index: int) -> str | None:
    """The content of the message at `index` (max 500 characters), else None."""
    if not isinstance(transcript, list):
        return None
    if isinstance(index, bool) or not isinstance(index, int):
        return None
    if index < 0 or index >= len(transcript):
        return None
    message = transcript[index]
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        return None
    return _clip(message["content"], QUOTE_MAX_CHARS)


def _json_len(value) -> int:
    return len(json.dumps(value, ensure_ascii=False))


def _clean_messages(transcript) -> list[dict]:
    if not isinstance(transcript, list):
        return []
    cleaned = []
    for message in transcript:
        if not isinstance(message, dict):
            continue
        cleaned.append({"role": str(message.get("role", "")), "content": str(message.get("content", ""))})
    return cleaned


def _truncate_transcript(transcript, limit: int = TRANSCRIPT_MAX_JSON_CHARS) -> list[dict]:
    """Copy of the messages whose JSON text is at most `limit` characters.

    Trailing messages are dropped first; if even the first message is too
    long, its content is cut (with an ellipsis) to fit.
    """
    messages = _clean_messages(transcript)
    while messages and _json_len(messages) > limit:
        if len(messages) > 1:
            messages.pop()
            continue
        only = messages[0]
        content = only["content"]
        low, high = 0, len(content)  # largest cut that fits (JSON length grows with the cut)
        while low < high:
            mid = (low + high + 1) // 2
            if _json_len([{"role": only["role"], "content": content[: mid - 1] + ELLIPSIS}]) <= limit:
                low = mid
            else:
                high = mid - 1
        if low == 0:
            return []
        return [{"role": only["role"], "content": content[: low - 1] + ELLIPSIS}]
    return messages


def build_outcome_rows(
    required: list[RequiredTest],
    names: dict,
    after: dict,
    rationales: dict,
    transcripts: dict,
) -> list[dict]:
    """The before -> after table, one row per required test, in `required` order."""
    rows = []
    for test in required:
        rationale = rationales.get(test.test_id)
        rows.append(
            {
                "test_id": test.test_id,
                "role": test.role,
                "criterion_name": names.get(test.test_id) or "",
                "before": test.before,
                "after": after.get(test.test_id),
                "rationale": _clip(rationale, RATIONALE_MAX_CHARS) if isinstance(rationale, str) else "",
                "transcript": _truncate_transcript(transcripts.get(test.test_id)),
            }
        )
    return rows
