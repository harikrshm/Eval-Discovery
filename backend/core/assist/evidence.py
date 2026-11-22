"""Evidence summaries for the flywheel: what went wrong around a flagged assistant answer, without the text.

Pure and deterministic. A summary lists the intent, the section focus, which brief sections were in the
context slice for that intent (from CONTEXT_POLICY, so code constants only), the sections the page
reported as missing, the citation-check result and the signal reasons. It never contains brief text,
the question or the answer: the pattern, rubric and suggester stages get the conversation itself from
the transcript and only need these facts to tell a retrieval or law-pack problem from a wording problem.

Everything that comes from the request (intent, section focus, completeness keys) is reduced to a
short, plain-character token first, so no angle bracket or free text can pass through.
"""

import re

from backend.core.assist.assemble import CONTEXT_POLICY
from backend.core.assist.intent import Intent

MAX_EVIDENCE_CHARS = 1500

_TOKEN_CHARS = 40
_MAX_MISSING_NAMES = 20
_RATINGS = ("up", "down", "no_answer")
_UNSAFE = re.compile(r"[^A-Za-z0-9 _.-]")


def _token(value: object) -> str:
    """A short plain-character version of request data (letters, digits, space, underscore, dot, dash)."""
    return " ".join(_UNSAFE.sub(" ", str(value)).split())[:_TOKEN_CHARS]


def _intent(meta_intent: object) -> Intent:
    try:
        return Intent(meta_intent)
    except ValueError:
        return Intent.other


def _missing(completeness: object) -> tuple[list[str], int] | None:
    """(sorted missing section names, number of sections reported) or None when nothing was reported."""
    if not isinstance(completeness, dict) or not completeness:
        return None
    names = sorted({_token(k) for k, ok in completeness.items() if not ok} - {""})
    return names, len(completeness)


def evidence_summary(run_messages: list[dict], brief_snapshot: dict | None, meta: dict) -> str:
    """One compact block (at most MAX_EVIDENCE_CHARS characters) about one flagged assistant answer.

    ``meta`` keys: intent, section_focus, rating, reasked, citation_ok, assist_version and optionally
    completeness (the brief's completeness map; read from ``brief_snapshot`` when absent).
    ``run_messages`` only contributes the turn count.
    """
    intent = _intent(meta.get("intent"))
    slice_sections = CONTEXT_POLICY[intent].sections
    completeness = meta["completeness"] if "completeness" in meta else (brief_snapshot or {}).get("completeness")
    missing = _missing(completeness)

    focus = meta.get("section_focus")
    focus_text = (_token(focus) if focus else "") or "none"

    lines = [
        f"Intent: {intent.value}",
        f"Section focus: {focus_text}",
        f"Brief sections in the context slice: {', '.join(slice_sections)}",
    ]
    if missing is None:
        lines.append("Brief completeness: not reported by the extension")
    else:
        names, total = missing
        if names:
            shown = names[:_MAX_MISSING_NAMES]
            more = f" (and {len(names) - len(shown)} more)" if len(names) > len(shown) else ""
            lines.append(f"Brief completeness: missing or incomplete: {', '.join(shown)}{more} ({total} reported)")
            needed = [n for n in slice_sections if n in names]
            if needed:
                lines.append(f"Missing sections the context slice needed: {', '.join(needed)}")
        else:
            lines.append(f"Brief completeness: all {total} reported sections captured")

    citation_ok = meta.get("citation_ok")
    if citation_ok is False:
        lines.append("Citation check: failed (the answer cited a claim number or law id that was not in its context)")
    elif citation_ok is True:
        lines.append("Citation check: passed")

    reasons = []
    rating = meta.get("rating")
    if rating in ("down", "no_answer"):
        reasons.append(f"rating {rating}")
    if meta.get("reasked"):
        reasons.append("re-asked")
    if citation_ok is False:
        reasons.append("citation failure")
    lines.append(f"Signals: {'; '.join(reasons) if reasons else 'none'}")
    if rating in _RATINGS:
        lines.append(f"Rating: {rating}")

    version = meta.get("assist_version")
    if isinstance(version, int) and not isinstance(version, bool):
        lines.append(f"Assistant prompt version: {version}")
    lines.append(f"Turns in the saved conversation: {len(run_messages or [])}")
    return "\n".join(lines)[:MAX_EVIDENCE_CHARS]
