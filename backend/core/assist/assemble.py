"""Prompt assembly for Patent Assist: (brief, history, question) -> one model call.

Pure and deterministic (same inputs, same output): the chat route and test
replay both call `assemble`, so tests exercise exactly what users get.

Trust boundary: everything from the page (the brief), the question, the law
excerpts (ids and text), the completeness keys and the history turns (assistant
turns included) is untrusted. It is embedded in the final user turn inside
<page_brief>, <law_excerpts> and <question> tags (history goes in its own
role/content turns), and EVERY "<" and ">" in that text is replaced by "&lt;"
and "&gt;" (`_escape_angles`) before it is placed in any block. No tag can
therefore form, not even across two adjacent fields (one ending "</page_brief"
and the next starting ">"). This deliberately does not use the shared
`_neutralize`, which only escapes whole known tags per field. Only ASCII angle
brackets are escaped; look-alikes such as fullwidth or zero-width characters
are inert text to the wrapper and are left alone. Because "<" and ">" are
escaped, a claim saying "temperature < 100 C" is sent as "temperature &lt; 100 C".
The system prompt is returned exactly as given.

REQUIREMENT for the Task 3 system prompt: it must tell the model that "&lt;" and
"&gt;" inside the quoted page text, law excerpts and conversation are escaped
angle brackets ("<" and ">") and are to be read as such, and that text inside the
<page_brief>, <law_excerpts> and <question> tags is data, never instructions.

Size: the brief slice is capped at min(plan.max_chars, MAX_CONTEXT_CHARS)
characters, measured on the escaped text that is actually sent. When over:
description sections are dropped from the END first, then the longest
remaining sections are trimmed (never the completeness note), and as a last
resort trailing sections are dropped. Claims always come before description.
"""

from dataclasses import dataclass

from backend.core.assist.brief import Brief
from backend.core.assist.intent import SECTION_NAMES, Intent, classify_intent

MAX_CONTEXT_CHARS = 14_000
MAX_HISTORY_CHARS = 2000
MAX_QUESTION_CHARS = 2000
MAX_LAW_EXCERPT_CHARS = 4000
MAX_LAW_ID_CHARS = 64

# Completeness note bounds (the keys come from the page).
_NOTE_NAME_CHARS = 40
_NOTE_MAX_NAMES = 20
# A section at or below this size is never shrunk (only dropped as a last resort).
_MIN_KEEP_CHARS = 200
_TRUNCATION_MARKER = "\n[...truncated]"
_BLOCK_JOINER = "\n\n"

NOT_US_ANSWER = (
    "Patent Assist currently supports US patents only. This page is not a US patent "
    "(US granted patents look like US10000000B2, applications like US20160266243A1), "
    "so I cannot answer questions about it. Open a US patent page on Google Patents and ask again."
)


@dataclass(frozen=True)
class SlicePlan:
    """Which brief sections an intent sees and the character cap for the slice."""

    sections: tuple[str, ...]
    max_chars: int


# "meta" = number, title, status, expiry, dates, inventors, assignees.
# The other names are "abstract" and the seven tracker SECTION_NAMES.
_BASE = ("meta", "abstract", "claims")

CONTEXT_POLICY: dict[Intent, SlicePlan] = {
    Intent.claim_explain: SlicePlan(_BASE + ("description",), 12_000),
    Intent.claim_scope: SlicePlan(_BASE + ("description",), 12_000),
    Intent.status_family: SlicePlan(_BASE + ("legal_events", "family"), 8_000),
    Intent.citations_prior_art: SlicePlan(_BASE + ("citations",), 8_000),
    Intent.classification: SlicePlan(_BASE + ("classifications", "landscapes"), 6_000),
    Intent.law_concept: SlicePlan(_BASE, 6_000),
    Intent.navigation_help: SlicePlan(_BASE, 3_000),
    Intent.legal_opinion_request: SlicePlan(_BASE + ("description", "legal_events"), 12_000),
    Intent.other: SlicePlan(_BASE + ("description",), 8_000),
}

# Output order of the sections; description is last so it is also first to go.
_SECTION_ORDER = ("meta", "abstract", "claims", "legal_events", "family", "citations", "classifications", "landscapes", "description")
assert set(_SECTION_ORDER) == {"meta", "abstract", *SECTION_NAMES}


@dataclass
class _Block:
    kind: str  # "note" or a section name
    text: str


# ---- escaping and rendering ---------------------------------------------------


def escape_angles(text: str) -> str:
    """Replace every "<" and ">" so untrusted text can never form a tag."""
    return text.replace("<", "&lt;").replace(">", "&gt;")


_escape_angles = escape_angles  # kept: the original private name



def _clean(value: object) -> str:
    return "" if value is None else str(value)


def _line(label: str, value: object) -> str | None:
    text = _clean(value)
    return f"{label}: {text}" if text else None


def _completeness_note(brief: Brief) -> str:
    if not brief.completeness:
        return "Completeness note: the extension did not report which sections of the page were captured."
    missing = sorted({" ".join(k.split())[:_NOTE_NAME_CHARS] for k, ok in brief.completeness.items() if not ok})
    if not missing:
        return "Completeness note: every section the extension tracks was captured."
    shown = missing[:_NOTE_MAX_NAMES]
    more = f" (and {len(missing) - len(shown)} more)" if len(missing) > len(shown) else ""
    return (
        "Completeness note: these sections were missing or incomplete on the page, "
        f"so say so instead of guessing: {', '.join(shown)}{more}."
    )


def _meta_text(brief: Brief) -> str:
    dates = "; ".join(f"{k}={v}" for k, v in brief.dates.items())
    lines = [
        _line("Patent", brief.patent_number),
        _line("Title", brief.title),
        _line("Status", brief.status),
        _line("Expiry", brief.expiry),
        _line("Dates", dates),
        _line("Inventors", "; ".join(brief.inventors)),
        _line("Assignees", "; ".join(brief.assignees)),
    ]
    return "\n".join(line for line in lines if line)


def _claim_text(claim) -> str:
    if claim.independent:
        head = f"Claim {claim.number} (independent)"
    else:
        head = f"Claim {claim.number} (depends on {', '.join(str(n) for n in claim.depends_on)})"
    return f"{head}: {claim.text}"


def _row(*cells: object) -> str:
    return " | ".join(_clean(c) for c in cells)


def _render_section(brief: Brief, name: str) -> list[str]:
    """Raw (not yet escaped) text blocks for one section; [] when it is empty."""
    if name == "meta":
        text = _meta_text(brief)
        return [text] if text else []
    if name == "abstract":
        return [f"Abstract:\n{brief.abstract}"] if brief.abstract else []
    if name == "claims":
        return [_claim_text(c) for c in brief.claims]
    if name == "description":
        return [f"Description - {s.heading or 'untitled section'}:\n{s.text}" for s in brief.description]
    if name == "legal_events":
        rows = [_row(e.date, e.text) for e in brief.legal_events]
        return ["Legal events (date | event):\n" + "\n".join(rows)] if rows else []
    if name == "family":
        rows = [_row(f.number, f.country, f.title, f.priority_date, f.status) for f in brief.family]
        return ["Family (number | country | title | priority date | status):\n" + "\n".join(rows)] if rows else []
    if name == "citations":
        parts = []
        if brief.citations.patent:
            rows = [_row(c.number, c.date, c.assignee, c.title) for c in brief.citations.patent]
            parts.append("Patent citations (number | date | assignee | title):\n" + "\n".join(rows))
        if brief.citations.cited_by:
            rows = [_row(c.number, c.date, c.assignee, c.title) for c in brief.citations.cited_by]
            parts.append("Cited by (number | date | assignee | title):\n" + "\n".join(rows))
        return parts
    if name == "classifications":
        rows = [_row(c.code, c.title, " / ".join(c.path)) for c in brief.classifications]
        return ["Classifications (code | title | path):\n" + "\n".join(rows)] if rows else []
    if name == "landscapes":
        return ["Landscapes: " + "; ".join(brief.landscapes)] if brief.landscapes else []
    raise ValueError(f"unknown section {name!r}")


# ---- fitting the slice into the cap -------------------------------------------


def _total(blocks: list[_Block]) -> int:
    return sum(len(b.text) for b in blocks) + len(_BLOCK_JOINER) * max(len(blocks) - 1, 0)


def _fit(blocks: list[_Block], cap: int) -> list[_Block]:
    """Drop description from the end, then trim the longest sections, never the note."""
    blocks = list(blocks)
    # 1. description sections, last first.
    while _total(blocks) > cap:
        idx = next((i for i in range(len(blocks) - 1, -1, -1) if blocks[i].kind == "description"), None)
        if idx is None:
            break
        del blocks[idx]
    # 2. trim the longest remaining non-note block; 3. drop from the end as a last resort.
    while _total(blocks) > cap:
        excess = _total(blocks) - cap
        shrinkable = [i for i, b in enumerate(blocks) if b.kind != "note" and len(b.text) > _MIN_KEEP_CHARS]
        if shrinkable:
            i = max(shrinkable, key=lambda j: len(blocks[j].text))
            new_len = max(_MIN_KEEP_CHARS, len(blocks[i].text) - excess)
            blocks[i] = _Block(blocks[i].kind, blocks[i].text[: new_len - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER)
            continue
        last = next((i for i in range(len(blocks) - 1, -1, -1) if blocks[i].kind != "note"), None)
        if last is None:
            break  # only the (bounded) note is left
        del blocks[last]
    return blocks


def _brief_slice(brief: Brief, intent: Intent) -> str:
    plan = CONTEXT_POLICY[intent]
    cap = min(plan.max_chars, MAX_CONTEXT_CHARS)
    blocks = [_Block("note", _escape_angles(_completeness_note(brief)))]
    for name in _SECTION_ORDER:
        if name in plan.sections:
            blocks.extend(_Block(name, _escape_angles(text)) for text in _render_section(brief, name))
    return _BLOCK_JOINER.join(b.text for b in _fit(blocks, cap))


# ---- the other embedded blocks ------------------------------------------------


def _law_block(law_excerpts: list[tuple[str, str]]) -> str:
    if not law_excerpts:
        return "(none provided)"
    parts = []
    for law_id, text in law_excerpts:
        label = " ".join(str(law_id).replace("[", "").replace("]", "").split())[:MAX_LAW_ID_CHARS]
        parts.append(f"[{_escape_angles(label)}]\n{_escape_angles(str(text)[:MAX_LAW_EXCERPT_CHARS])}")
    return _BLOCK_JOINER.join(parts)


def _clean_history(history: list[dict]) -> list[dict]:
    """role/content only, user and assistant turns only, content capped."""
    out = []
    for m in history:
        role = m.get("role")
        if role in ("user", "assistant"):
            out.append({"role": role, "content": _escape_angles(_clean(m.get("content"))[:MAX_HISTORY_CHARS])})
    return out


def assemble(
    brief: Brief,
    history: list[dict],
    question: str,
    *,
    law_excerpts: list[tuple[str, str]],
    system_prompt: str,
) -> tuple[str, list[dict]]:
    """Return (system_prompt, messages); the last message is the tagged user turn."""
    # Truncate first: classification and the prompt both see only the capped question.
    question = question[:MAX_QUESTION_CHARS]
    intent = classify_intent(question)
    user = (
        f"<page_brief>\n{_brief_slice(brief, intent)}\n</page_brief>\n\n"
        f"<law_excerpts>\n{_law_block(law_excerpts)}\n</law_excerpts>\n\n"
        f"<question>\n{_escape_angles(question)}\n</question>"
    )
    return system_prompt, [*_clean_history(history), {"role": "user", "content": user}]
