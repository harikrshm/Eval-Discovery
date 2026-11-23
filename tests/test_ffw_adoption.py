"""Final fix wave M4: adoption edge cases, shared flag reasons, and the flagged-run filters."""

from backend.api import assist_console
from backend.core.assist.adoption import PV, Q, compute_adoption, flag_reasons
from backend.db.assist import _is_flagged, add_question
from tests.test_api_assist_console import add_q, add_view, flag, make_workspace


def pv(sections, bounce=False, session="s"):
    return PV(session, sections, 20000, bounce, "live", 1)


def q(intent="other", focus=None, rating=None, reasked=False, ok=True, session="s"):
    return Q(session, intent, focus, rating, reasked, ok, "live", 1)


# ---- pure metrics ----------------------------------------------------------------------------------


def test_an_unknown_intent_gets_its_own_rows_after_the_known_ones():
    result = compute_adoption([pv({"claims": 5000})], [q("brand_new_intent", "claims", rating="down"), q("claim_explain")])
    assert result["task_mix"]["brand_new_intent"] == {"count": 1, "share": 0.5}
    assert result["struggle"]["thumbs_down_rate_by_intent"]["brand_new_intent"] == 1.0
    assert result["struggle"]["reask_rate_by_intent"]["brand_new_intent"] == 0.0
    assert list(result["task_mix"])[-1] == "brand_new_intent"


def test_coverage_counts_3000_ms_but_not_2999_ms():
    result = compute_adoption([pv({"claims": 3000}), pv({"claims": 2999})], [])
    assert result["feature_coverage"]["claims"] == 0.5  # only the 3000 ms view counts as used
    assert result["views_by_section"]["claims"] == 2  # both were visible, so both count as viewed


def test_thumbs_down_counts_down_but_not_no_answer():
    questions = [q("claim_explain", rating="down"), q("claim_explain", rating="no_answer"), q("claim_explain", rating="up"), q("claim_explain")]
    result = compute_adoption([], questions)
    assert result["struggle"]["thumbs_down_rate_by_intent"]["claim_explain"] == 0.25


# ---- one definition of "flagged" ---------------------------------------------------------------------


def test_flag_reasons_are_ordered_and_empty_for_a_clean_question():
    assert flag_reasons("down", True, False) == ["rated_down", "reasked", "citation_unverified"]
    assert flag_reasons("no_answer", False, True) == ["no_answer"]
    assert flag_reasons("up", False, True) == []
    assert flag_reasons(None, False, True) == []


def test_the_db_flag_test_and_the_console_reasons_agree(db_session):
    workspace, _, _ = make_workspace(db_session)
    cases = [(None, False, True), ("down", False, True), ("no_answer", False, True), ("up", True, True), (None, False, False)]
    for i, (rating, reasked, ok) in enumerate(cases):
        row = add_question(
            db_session, workspace.id, f"session-agree-{i}", "US10000000B2", "q", "other", None, "a", ok, "live", 1,
            rating=rating, reasked=reasked,
        )
        assert _is_flagged(row) == bool(assist_console._reasons([row])) == bool(flag_reasons(rating, reasked, ok))


# ---- the flagged filter prefers flagged questions, and loads only the runs' sessions -----------------


def test_filter_matches_flagged_questions_first_and_falls_back_to_any(client, db_session):
    workspace, agent, headers = make_workspace(db_session)
    add_view(db_session, workspace, "sess-mixed", {"claims": 5000})
    add_q(db_session, workspace, "sess-mixed", "law_concept", "description")  # not flagged
    add_q(db_session, workspace, "sess-mixed", "claim_explain", "claims", rating="down")  # flagged
    add_q(db_session, workspace, "sess-clean", "navigation_help", "family")  # a run whose questions are all unflagged
    flag(db_session, agent, "sess-mixed")
    flag(db_session, agent, "sess-clean", source="imported")

    def sessions(query):
        return [r["session_id"] for r in client.get(f"/assist/console/flagged{query}", headers=headers).json()]

    assert sessions("?intent=claim_explain") == ["sess-mixed"]
    assert sessions("?intent=law_concept") == []  # only the unflagged question has it, and the session has a flagged one
    assert sessions("?section=description") == []
    assert sessions("?section=claims") == ["sess-mixed"]
    assert sessions("?intent=navigation_help") == ["sess-clean"]  # no flagged question there: any question matches
    assert sessions("?section=family") == ["sess-clean"]


def test_questions_are_loaded_only_for_the_given_sessions(db_session):
    workspace, _, _ = make_workspace(db_session)
    add_q(db_session, workspace, "sess-one", "other")
    add_q(db_session, workspace, "sess-two", "other")
    rows = assist_console._load_questions(db_session, workspace.id, ["sess-one"])
    assert {r.session_id for r in rows} == {"sess-one"}
    assert assist_console._load_questions(db_session, workspace.id, []) == []
    assert {r.session_id for r in assist_console._load_questions(db_session, workspace.id)} == {"sess-one", "sess-two"}
