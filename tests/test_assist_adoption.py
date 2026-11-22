"""Adoption metrics and version comparison: pure functions over hand-built rows with exact numbers."""

import json

from backend.core.assist.adoption import PV, Q, compute_adoption, compute_version_comparison
from backend.core.assist.intent import SECTION_NAMES, Intent


def pv(sections, bounce=False, source="live", dwell_ms=20000, session="s", version=1):
    return PV(session, sections, dwell_ms, bounce, source, version)


def q(intent="other", focus=None, rating=None, reasked=False, ok=True, source="live", version=1, session="s"):
    return Q(session, intent, focus, rating, reasked, ok, source, version)


# Page views: 4 engaged (1, 2, 3, 6), 2 bounces (4, 5). View 5 is flagged bounce although a section
# reached 3 s, so it proves bounces are excluded from every coverage numerator and denominator.
VIEWS = [
    pv({"claims": 5000, "description": 1000}),
    pv({"claims": 3000, "citations": 4000}),
    pv({"claims": 2000, "description": 8000}),
    pv({"claims": 500}, bounce=True, dwell_ms=900),
    pv({"claims": 4000}, bounce=True, source="imported"),
    pv({"description": 3000, "family": 6000}, source="imported"),
]

QUESTIONS = [
    q("claim_explain", "claims", rating="down"),
    q("claim_explain", "claims", reasked=True),
    q("claim_scope", "claims"),
    q("navigation_help"),
    q("legal_opinion_request", "claims", rating="down", source="imported"),
    q("citations_prior_art", "citations", ok=False),
    q("classification", "classifications", rating="up"),
    q("claim_explain", "description", rating="down", reasked=True, ok=False),
    q("other"),
    q("navigation_help", "description", source="imported"),
]


def test_totals_sources_and_bounce_rate():
    result = compute_adoption(VIEWS, QUESTIONS)
    assert result["totals"] == {"page_views": 6, "questions": 10, "non_bounce_views": 4}
    assert result["sources"] == {
        "page_views": {"live": 4, "imported": 2},
        "questions": {"live": 8, "imported": 2},
    }
    assert result["bounce_rate"] == 0.3333


def test_feature_coverage_uses_non_bounce_views_only():
    coverage = compute_adoption(VIEWS, QUESTIONS)["feature_coverage"]
    assert list(coverage) == list(SECTION_NAMES)
    assert coverage == {
        "claims": 0.5,  # views 1 and 2 reach 3 s; view 3 (2 s) does not; the 4 s bounce is ignored
        "description": 0.5,  # views 3 and 6
        "classifications": 0.0,
        "citations": 0.25,
        "family": 0.25,
        "legal_events": 0.0,
        "landscapes": 0.0,
    }


def test_views_by_section_counts_non_bounce_views_where_the_section_was_seen():
    assert compute_adoption(VIEWS, QUESTIONS)["views_by_section"] == {
        "claims": 3,
        "description": 3,
        "classifications": 0,
        "citations": 1,
        "family": 1,
        "legal_events": 0,
        "landscapes": 0,
    }


def test_help_demand_per_100_views_and_none_without_views():
    result = compute_adoption(VIEWS, QUESTIONS)
    assert result["help_demand"] == {
        "claims": 133.3333,  # 4 questions / 3 views
        "description": 66.6667,  # 2 / 3
        "classifications": None,  # a question, but nobody viewed the section
        "citations": 100.0,
        "family": 0.0,  # viewed, never asked about: a real zero
        "legal_events": None,  # no views and no questions
        "landscapes": None,
    }
    assert result["questions_without_views"] == {"classifications": 1}


def test_task_mix_counts_and_shares():
    mix = compute_adoption(VIEWS, QUESTIONS)["task_mix"]
    assert set(mix) == {i.value for i in Intent}
    assert mix["claim_explain"] == {"count": 3, "share": 0.3}
    assert mix["navigation_help"] == {"count": 2, "share": 0.2}
    assert mix["claim_scope"] == {"count": 1, "share": 0.1}
    assert mix["law_concept"] == {"count": 0, "share": 0.0}


def test_struggle_metrics():
    struggle = compute_adoption(VIEWS, QUESTIONS)["struggle"]
    assert struggle["navigation_help_count"] == 2
    assert struggle["refusal_count"] == 1
    assert struggle["unverified_citation_rate"] == 0.2
    reask = struggle["reask_rate_by_intent"]
    assert reask["claim_explain"] == 0.6667
    assert reask["claim_scope"] == 0.0
    assert reask["law_concept"] is None  # no questions of that intent
    down = struggle["thumbs_down_rate_by_intent"]
    assert down["claim_explain"] == 0.6667
    assert down["legal_opinion_request"] == 1.0
    assert down["classification"] == 0.0
    assert down["status_family"] is None


def test_zero_rows_give_none_everywhere_and_no_exception():
    result = compute_adoption([], [])
    assert result["totals"] == {"page_views": 0, "questions": 0, "non_bounce_views": 0}
    assert result["bounce_rate"] is None
    for name in ("feature_coverage", "help_demand"):
        assert list(result[name]) == list(SECTION_NAMES)
        assert all(v is None for v in result[name].values())
    assert result["views_by_section"] == dict.fromkeys(SECTION_NAMES, 0)
    assert all(v == {"count": 0, "share": None} for v in result["task_mix"].values())
    assert result["struggle"]["unverified_citation_rate"] is None
    assert all(v is None for v in result["struggle"]["reask_rate_by_intent"].values())
    assert all(v is None for v in result["struggle"]["thumbs_down_rate_by_intent"].values())
    assert result["struggle"]["navigation_help_count"] == 0
    assert result["struggle"]["refusal_count"] == 0
    json.dumps(result, allow_nan=False)  # no NaN anywhere


def test_only_bounces_leave_coverage_undefined():
    result = compute_adoption([pv({"claims": 100}, bounce=True)], [])
    assert result["bounce_rate"] == 1.0
    assert all(v is None for v in result["feature_coverage"].values())


def test_questions_without_page_views_do_not_break_help_demand():
    result = compute_adoption([], [q("claim_explain", "claims")])
    assert result["help_demand"]["claims"] is None
    assert result["questions_without_views"] == {"claims": 1}


def test_unknown_section_keys_and_bad_values_are_ignored():
    view = pv({"claims": 4000, "sidebar": 9000, "family": "lots"})
    result = compute_adoption([view], [])
    assert result["feature_coverage"]["claims"] == 1.0
    assert result["feature_coverage"]["family"] == 0.0
    assert "sidebar" not in result["feature_coverage"]


# ---- version comparison ---------------------------------------------------------


def v1_questions():
    # 8 claim_explain (2 reasked, 4 down) + 4 claim_scope (1 reasked, 0 down) = 12
    rows = [q("claim_explain", reasked=i < 2, rating="down" if i < 4 else None, version=1) for i in range(8)]
    rows += [q("claim_scope", reasked=i == 0, version=1) for i in range(4)]
    return rows


def v2_questions():
    # 10 claim_explain, 1 reasked, 0 down
    return [q("claim_explain", reasked=i == 0, version=2) for i in range(10)]


def test_version_comparison_exact_numbers_and_imported_rows_ignored():
    imported = [q("claim_explain", reasked=True, rating="down", source="imported", version=3) for _ in range(50)]
    result = compute_version_comparison(v1_questions() + v2_questions() + imported)
    assert result["available"] is True
    assert result["note"] == "Real (non-imported) questions only."
    assert set(result["versions"]) == {"1", "2"}
    one, two = result["versions"]["1"], result["versions"]["2"]
    assert (one["questions"], one["reask_rate"], one["thumbs_down_rate"]) == (12, 0.25, 0.3333)
    assert one["intents"]["claim_explain"] == {"questions": 8, "reask_rate": 0.25, "thumbs_down_rate": 0.5}
    assert one["intents"]["claim_scope"] == {"questions": 4, "reask_rate": 0.25, "thumbs_down_rate": 0.0}
    assert (two["questions"], two["reask_rate"], two["thumbs_down_rate"]) == (10, 0.1, 0.0)
    assert two["intents"] == {"claim_explain": {"questions": 10, "reask_rate": 0.1, "thumbs_down_rate": 0.0}}


def test_sparse_data_is_not_available():
    rows = v1_questions() + v2_questions()[:9]  # version 2 has only 9
    result = compute_version_comparison(rows)
    assert result["available"] is False
    assert "two" in result["reason"]
    assert set(result) == {"available", "reason"}


def test_single_version_is_not_available():
    assert compute_version_comparison(v1_questions())["available"] is False


def test_no_questions_is_not_available():
    assert compute_version_comparison([])["available"] is False


def test_imported_questions_do_not_make_a_second_version():
    rows = v1_questions() + [q("claim_explain", source="imported", version=2) for _ in range(40)]
    assert compute_version_comparison(rows)["available"] is False


def test_entirely_imported_data_is_never_available():
    rows = [q("claim_explain", source="imported", version=v) for v in (1, 2) for _ in range(30)]
    result = compute_version_comparison(rows)
    assert result["available"] is False
    assert "imported" in result["reason"]
