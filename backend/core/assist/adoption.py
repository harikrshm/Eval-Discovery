"""Adoption metrics for the Assist console. Pure: no I/O, no database, no clock.

The metrics describe how Google Patents is used and where users struggle, not how the widget is used
(widget opens and click-through are deliberately not measured). Page views and questions come in as
the plain tuples `PV` and `Q`.

Every ratio whose denominator is zero is None (never an exception, never NaN, never a 0 that pretends
to be a rate). Shares and rates are rounded to 4 decimals. Rows with source "imported" are demo data:
they count in the adoption numbers (so the console can show a demo dataset) but never in the
version comparison.
"""

from collections import defaultdict
from dataclasses import dataclass

from backend.core.assist.intent import SECTION_NAMES, Intent

IMPORTED_SOURCE = "imported"
COVERAGE_MIN_VISIBLE_MS = 3000  # a section counts as used when it was visible this long
HELP_DEMAND_PER_VIEWS = 100
VERSION_MIN_QUESTIONS = 10  # real questions a version needs before it can be compared
MIN_VERSIONS = 2
LEGAL_OPINION_INTENT = Intent.legal_opinion_request.value
NAVIGATION_INTENT = Intent.navigation_help.value

NOT_ENOUGH_VERSIONS = (
    "Fewer than two assistant versions have at least 10 real questions, so versions cannot be compared yet."
)
ALL_IMPORTED = "All questions are imported demo data, so versions cannot be compared."
REAL_ONLY_NOTE = "Real (non-imported) questions only."


@dataclass(frozen=True)
class PV:
    """One page view: per-section visible milliseconds plus the bounce flag and its provenance."""

    session_id: str
    sections: dict
    dwell_ms: int
    bounce: bool
    source: str
    assist_version: int | None


@dataclass(frozen=True)
class Q:
    """One question and the signals around it (no question or answer text)."""

    session_id: str
    intent: str
    section_focus: str | None
    rating: str | None
    reasked: bool
    citation_ok: bool
    source: str
    assist_version: int | None


def _ratio(numerator: int, denominator: int, scale: int = 1) -> float | None:
    """numerator / denominator * scale rounded to 4 places; None when the denominator is zero."""
    if denominator <= 0:
        return None
    return round(numerator * scale / denominator, 4)


def _visible_ms(view: PV, section: str) -> float:
    value = (view.sections or {}).get(section, 0)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _intent_keys(questions: list[Q]) -> list[str]:
    keys = [i.value for i in Intent]
    keys += sorted({q.intent for q in questions} - set(keys))
    return keys


def _source_counts(rows) -> dict:
    imported = sum(1 for r in rows if r.source == IMPORTED_SOURCE)
    return {"live": len(rows) - imported, "imported": imported}


def compute_adoption(page_views: list[PV], questions: list[Q]) -> dict:
    page_views = list(page_views)
    questions = list(questions)
    engaged = [v for v in page_views if not v.bounce]

    coverage: dict = {}
    views_by_section: dict = {}
    for section in SECTION_NAMES:
        viewed = sum(1 for v in engaged if _visible_ms(v, section) > 0)
        used = sum(1 for v in engaged if _visible_ms(v, section) >= COVERAGE_MIN_VISIBLE_MS)
        views_by_section[section] = viewed
        coverage[section] = _ratio(used, len(engaged))

    focus_counts = {s: sum(1 for q in questions if q.section_focus == s) for s in SECTION_NAMES}
    help_demand = {
        s: _ratio(focus_counts[s], views_by_section[s], HELP_DEMAND_PER_VIEWS) for s in SECTION_NAMES
    }
    without_views = {s: n for s, n in focus_counts.items() if n and not views_by_section[s]}

    intents = _intent_keys(questions)
    by_intent: dict = defaultdict(list)
    for q in questions:
        by_intent[q.intent].append(q)
    total_q = len(questions)

    task_mix = {
        i: {"count": len(by_intent[i]), "share": _ratio(len(by_intent[i]), total_q)} for i in intents
    }
    struggle = {
        "navigation_help_count": len(by_intent[NAVIGATION_INTENT]),
        "reask_rate_by_intent": {
            i: _ratio(sum(1 for q in by_intent[i] if q.reasked), len(by_intent[i])) for i in intents
        },
        "refusal_count": len(by_intent[LEGAL_OPINION_INTENT]),
        "unverified_citation_rate": _ratio(sum(1 for q in questions if not q.citation_ok), total_q),
        "thumbs_down_rate_by_intent": {
            i: _ratio(sum(1 for q in by_intent[i] if q.rating == "down"), len(by_intent[i])) for i in intents
        },
    }
    return {
        "totals": {"page_views": len(page_views), "questions": total_q, "non_bounce_views": len(engaged)},
        "sources": {"page_views": _source_counts(page_views), "questions": _source_counts(questions)},
        "bounce_rate": _ratio(len(page_views) - len(engaged), len(page_views)),
        "feature_coverage": coverage,
        "views_by_section": views_by_section,
        "help_demand": help_demand,
        "questions_without_views": without_views,
        "task_mix": task_mix,
        "struggle": struggle,
    }


def _rates(rows: list[Q]) -> dict:
    return {
        "questions": len(rows),
        "reask_rate": _ratio(sum(1 for q in rows if q.reasked), len(rows)),
        "thumbs_down_rate": _ratio(sum(1 for q in rows if q.rating == "down"), len(rows)),
    }


def compute_version_comparison(questions: list[Q]) -> dict:
    """Re-ask and thumbs-down rates per assistant version and intent, from real questions only.

    Imported (demo) questions are ignored. The comparison is available only when at least two versions
    each have VERSION_MIN_QUESTIONS real questions; otherwise {"available": False, "reason": ...}.
    """
    questions = list(questions)
    real = [q for q in questions if q.source != IMPORTED_SOURCE and q.assist_version is not None]
    by_version: dict = defaultdict(list)
    for q in real:
        by_version[q.assist_version].append(q)

    enough = [v for v, rows in by_version.items() if len(rows) >= VERSION_MIN_QUESTIONS]
    if len(enough) < MIN_VERSIONS:
        any_imported = any(q.source == IMPORTED_SOURCE for q in questions)
        return {"available": False, "reason": ALL_IMPORTED if any_imported and not real else NOT_ENOUGH_VERSIONS}

    versions = {}
    for version in sorted(by_version):
        rows = by_version[version]
        per_intent: dict = defaultdict(list)
        for q in rows:
            per_intent[q.intent].append(q)
        versions[str(version)] = {
            **_rates(rows),
            "enough_data": len(rows) >= VERSION_MIN_QUESTIONS,
            "intents": {i: _rates(per_intent[i]) for i in sorted(per_intent)},
        }
    return {"available": True, "versions": versions, "note": REAL_ONLY_NOTE}
