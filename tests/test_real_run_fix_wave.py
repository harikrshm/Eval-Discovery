"""Real-run fix wave (R1-R7): offline reproductions of the defects the real Groq run found.

Every test uses fakes or saved fixtures only; nothing here calls the network or a real model.
"""

import json
import re
from pathlib import Path

import pytest

from backend.core.assist.answer import build_answer_request
from backend.core.assist.assemble import CONTEXT_POLICY, MAX_CONTEXT_CHARS
from backend.core.assist.brief import Brief
from backend.core.assist.intent import Intent

FIXTURES = Path(__file__).resolve().parent.parent / "extension" / "fixtures"
SYSTEM = "You are Patent Assist."


def fixture_brief(name: str) -> Brief:
    return Brief.model_validate(json.loads((FIXTURES / f"{name}.expected.json").read_text(encoding="utf-8")))


def page_brief_of(brief: Brief, question: str) -> str:
    request = build_answer_request(brief, [], question, SYSTEM)
    text = request.messages[-1]["content"]
    return text[text.index("<page_brief>") + len("<page_brief>"):text.index("</page_brief>")]


# ---- R1: the slice fit keeps the facts the question needs ------------------------------------------


def test_r1_still_in_force_keeps_status_and_expiry_and_says_lists_were_cut():
    brief = fixture_brief("us6756997b1")
    assert brief.status == "Expired - Lifetime" and brief.expiry == "2019-09-05"
    body = page_brief_of(brief, "Is this patent still in force?")
    assert "Status: Expired - Lifetime" in body
    assert "Expiry: 2019-09-05" in body
    # the facts the question needs are whole: every legal event and family row is still there
    for event in brief.legal_events:
        assert f"{event.date} | {event.text}" in body
    assert "[list truncated" not in body  # nothing needed was cut, so no list carries the marker
    assert "claims" in body.strip().splitlines()[0]  # the note says the claims were cut to fit
    assert len(body) <= CONTEXT_POLICY[Intent.status_family].max_chars + 2


def test_r1_completeness_note_names_the_sections_trimmed_to_fit():
    body = page_brief_of(fixture_brief("us6756997b1"), "Is this patent still in force?")
    first = body.strip().splitlines()[0]
    assert "trimmed to fit" in first
    assert "legal_events" in first or "family" in first or "claims" in first
    # the extractor's own claim is still there
    assert "every section the extension tracks was captured" in first


def test_r1_cited_by_rows_are_whole_rows_and_the_marker_counts_them():
    brief = fixture_brief("us6756997b1")
    body = page_brief_of(brief, "What prior art does this patent cite?")
    for table, rows in (("Patent citations", brief.citations.patent), ("Cited by", brief.citations.cited_by)):
        want = [" | ".join((r.number or "", r.date or "", r.assignee or "", r.title or "")) for r in rows]
        start = body.index(table)
        end = body.find("\n\n", start)
        lines = body[start:end if end != -1 else None].splitlines()[1:]
        marker = [ln for ln in lines if ln.startswith("[list truncated")]
        shown = [ln for ln in lines if not ln.startswith("[")]
        assert all(ln in want for ln in shown), "a row was cut mid-title"
        assert shown == want[: len(shown)]
        if len(shown) < len(want):
            assert marker == [f"[list truncated: showing {len(shown)} of {len(want)} rows]"]
        else:
            assert not marker
    assert "Expiry: 2019-09-05" in body
    assert len(body) <= CONTEXT_POLICY[Intent.citations_prior_art].max_chars + 2


def test_r1_a_slice_that_fits_has_no_marker_and_no_trimmed_note():
    brief = Brief(
        patent_number="US10000000B2",
        claims=[{"number": 1, "text": "A widget.", "depends_on": [], "independent": True}],
        citations={"cited_by": [{"number": "US222", "title": "Newer widget"}]},
        completeness={"claims": True},
    )
    body = page_brief_of(brief, "Which patents cite this one?")
    assert "trimmed to fit" not in body
    assert "[list truncated" not in body and "[...truncated]" not in body


@pytest.mark.parametrize("name", ["us6756997b1", "us12401720b1", "us20250119367a1"])
@pytest.mark.parametrize("intent", list(Intent))
def test_r1_every_fixture_and_intent_stays_within_the_unchanged_caps(name, intent):
    question = {
        Intent.claim_explain: "Explain claim 1 in plain language",
        Intent.claim_scope: "What does claim 1 cover?",
        Intent.status_family: "Is this patent still in force?",
        Intent.citations_prior_art: "What prior art does this patent cite?",
        Intent.classification: "What do the CPC codes on this patent mean?",
        Intent.law_concept: "What is obviousness?",
        Intent.navigation_help: "What does this button do?",
        Intent.legal_opinion_request: "Is this patent valid?",
        Intent.other: "Tell me about this patent",
    }[intent]
    body = page_brief_of(fixture_brief(name), question)
    assert len(body) <= min(CONTEXT_POLICY[intent].max_chars, MAX_CONTEXT_CHARS) + 2
    brief = fixture_brief(name)
    assert f"Status: {brief.status}" in body
    if brief.expiry:
        assert f"Expiry: {brief.expiry}" in body


# ---- R2: look-alike characters cannot bypass the citation check or the disclaimer test --------------

from backend.core.assist.answer import CITATION_NOTE, finalize_answer  # noqa: E402
from backend.core.assist.citations import check_citations, normalize_for_check  # noqa: E402
from backend.core.assist.lawpack import NOT_LEGAL_ADVICE  # noqa: E402

SMALL = Brief(
    patent_number="US10000000B2",
    claims=[{"number": 1, "text": "A widget.", "depends_on": [], "independent": True}],
)
NB_HYPHEN = "\u2011"


def test_r2_fake_law_id_written_with_a_non_breaking_hyphen_is_caught():
    assert check_citations("see claim 1 [usc-999]", SMALL, ["usc-103"]).unverified_law == ["usc-999"]
    check = check_citations(f"see claim 1 [usc{NB_HYPHEN}999]", SMALL, ["usc-103"])
    assert check.unverified_law == ["usc-999"] and not check.ok


@pytest.mark.parametrize("dash", ["\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\u2212"])
def test_r2_every_dash_lookalike_is_normalised_for_the_check(dash):
    assert check_citations(f"[usc{dash}999]", SMALL, []).unverified_law == ["usc-999"]
    assert check_citations(f"[usc{dash}103]", SMALL, ["usc-103"]).ok


def test_r2_a_valid_id_written_with_a_non_breaking_hyphen_is_accepted():
    assert check_citations(f"Under [usc{NB_HYPHEN}103] and [mpep{NB_HYPHEN}2111], claim 1.", SMALL, ["usc-103", "mpep-2111"]).ok


def test_r2_finalize_flags_the_fake_id_and_leaves_the_text_as_written():
    raw = f"Obvious under [usc{NB_HYPHEN}999]."
    answer, check = finalize_answer(raw, Intent.law_concept, SMALL, ["usc-103"])
    assert not check.ok and check.unverified_law == ["usc-999"]
    assert answer.startswith(raw)  # the displayed text keeps the model's own characters
    assert answer.endswith(CITATION_NOTE)


def test_r2_curly_apostrophe_disclaimer_is_not_appended_twice():
    curly = NOT_LEGAL_ADVICE.replace("'", "\u2019")
    assert curly != NOT_LEGAL_ADVICE
    raw = f"The claim says X.\n{curly}"
    answer, _ = finalize_answer(raw, Intent.legal_opinion_request, SMALL, [])
    assert answer == raw
    assert NOT_LEGAL_ADVICE not in answer
    # still appended when really missing
    answer2, _ = finalize_answer("The claim says X.", Intent.legal_opinion_request, SMALL, [])
    assert answer2.endswith(NOT_LEGAL_ADVICE)


def test_r2_normalize_for_check_maps_quotes_and_compatibility_forms():
    assert normalize_for_check("it\u2019s \u201cx\u201d \u2018y\u2019") == "it's \"x\" 'y'"
    assert normalize_for_check("claim\u202f5 \uff55\uff53\uff43") == "claim 5 usc"
    assert normalize_for_check("a\u2011b\u2212c") == "a-b-c"


# ---- R3: internal tag names never reach the user -------------------------------------------------------


@pytest.mark.parametrize(
    "leak",
    ["\u3010<page_brief>\u3011", "\u3010page_brief\u3011", "<page_brief>", "</page_brief>", "[page_brief]",
     "&lt;page_brief&gt;", "\u3010<law_excerpts>\u3011", "<law_excerpts>", "\u3010<question>\u3011", "<question>", "</question>",
     "page_brief"],
)
def test_r3_internal_tag_names_are_removed_from_the_answer(leak):
    raw = f"The claim is narrow {leak} as shown {leak}."
    answer, _ = finalize_answer(raw, Intent.claim_explain, SMALL, [])
    for name in ("page_brief", "law_excerpts", "<question>", "</question>", "\u3010"):
        assert name not in answer
    assert answer.startswith("The claim is narrow")


def test_r3_five_leaks_in_one_answer_all_go_and_ordinary_words_stay():
    raw = " ".join("\u3010<page_brief>\u3011 says claim 1." for _ in range(5)) + " Which question did you ask?"
    answer, _ = finalize_answer(raw, Intent.claim_explain, SMALL, [])
    assert "page_brief" not in answer and "\u3010" not in answer
    assert answer.count("says claim 1.") == 5
    assert "Which question did you ask?" in answer


def test_r3_law_ids_in_brackets_are_untouched():
    answer, check = finalize_answer("See [usc-103] and claim 1.", Intent.law_concept, SMALL, ["usc-103"])
    assert answer == "See [usc-103] and claim 1." and check.ok


def test_r3_system_prompt_tells_the_model_not_to_name_its_tags():
    prompt = (Path(__file__).resolve().parent.parent / "backend" / "core" / "assist" / "system_prompt.md").read_text(
        encoding="utf-8"
    )
    assert "Never mention these block names" in prompt


# ---- R4: the judge sees the facts the answer was built from -----------------------------------------

from backend.core.assist.answer import JUDGE_BRIEF_CHARS, judge_brief_text  # noqa: E402


def _judge_brief(**extra) -> Brief:
    data = {
        "patent_number": "US20250119367A1",
        "title": "Unique id generation for sensors",
        "abstract": "Systems and methods.",
        "status": "Pending",
        "expiry": "2040-01-01",
        "assignees": ["Cisco Technology Inc"],
        "claims": [{"number": 1, "text": "A method comprising a hypervisor.", "depends_on": [], "independent": True}],
        "classifications": [
            {"code": "G06F9/45558", "title": "Hypervisor-specific management and integration aspects", "path": ["G", "G06F"]},
            {"code": "H04L9/32", "title": "Authentication"},
        ],
        "legal_events": [{"date": "2025-04-10", "text": "Publication of US20250119367A1"}],
        "family": [{"number": "US20250119367A1", "country": "US", "status": "Pending"}],
        "citations": {
            "patent": [{"number": "US111", "title": "Older hypervisor"}],
            "cited_by": [{"number": f"US9{n:06d}", "title": f"Newer {n}"} for n in range(5)],
        },
        "completeness": {"claims": True},
    }
    data.update(extra)
    return Brief.model_validate(data)


def test_r4_judge_text_carries_classifications_status_events_family_and_citations():
    text = judge_brief_text(_judge_brief())
    assert "G06F9/45558" in text and "Hypervisor-specific management and integration aspects" in text
    assert "Status: Pending" in text and "Expiry: 2040-01-01" in text
    assert "Publication of US20250119367A1" in text
    assert "US20250119367A1 | US" in text  # a family row
    assert "Cited by: 5" in text and "Patent citations: 1" in text  # counts
    assert "US9000000" in text and "Older hypervisor" in text  # the first rows


def test_r4_a_correct_cpc_answer_can_be_checked_against_the_judge_input():
    # the false failure of the real run: a long claim set used to leave no room for the classifications
    claims = [{"number": n, "text": f"C{n} " + "w" * 3000, "depends_on": [], "independent": True} for n in range(1, 8)]
    text = judge_brief_text(_judge_brief(claims=claims))
    assert "G06F9/45558" in text and "Hypervisor-specific management" in text
    assert "Claim 1:" in text  # claims still come first
    assert len(text) <= JUDGE_BRIEF_CHARS


def test_r4_hostile_input_stays_under_the_cap_with_no_angle_brackets_or_half_entities():
    evil = "<>" * 25
    big = lambda n: [{"number": f"US{n}{i}", "date": evil, "assignee": evil, "title": evil} for i in range(300)]  # noqa: E731
    brief = _judge_brief(
        title="<>" * 250, abstract=evil * 20,
        classifications=[{"code": f"G06F{i}", "title": evil, "path": [evil] * 5} for i in range(300)],
        legal_events=[{"date": evil, "text": evil} for _ in range(300)],
        family=[{"number": evil, "country": evil, "title": evil, "priority_date": evil, "status": evil} for _ in range(300)],
        citations={"patent": big(1), "cited_by": big(2)},
        claims=[{"number": n, "text": evil * 40, "depends_on": [], "independent": True} for n in range(1, 60)],
        description=[{"heading": evil, "text": evil * 100} for _ in range(10)],
    )
    text = judge_brief_text(brief)
    assert len(text) <= JUDGE_BRIEF_CHARS
    assert "<" not in text and ">" not in text
    # no entity is split anywhere: every "&" opens a whole &lt; or &gt;
    assert re.sub(r"&(?:lt|gt);", "", text).count("&") == 0
    assert "[truncated]" in text  # a cut section says so
    assert "Claim 1:" in text and "G06F0" in text


def test_r4_each_cut_fact_section_carries_its_own_marker():
    rows = [{"date": "2020-01-01", "text": "event " + "e" * 200} for _ in range(100)]
    text = judge_brief_text(_judge_brief(legal_events=rows))
    legal = text[text.index("Legal events"):]
    legal = legal.split("\n\n")[0]
    assert legal.rstrip().endswith("[truncated]")
    assert "Classifications" in text and "[truncated]" not in text.split("Legal events")[0]


# ---- R5: build_tests judges live flagged runs before imported ones --------------------------------------

from unittest.mock import MagicMock  # noqa: E402

from backend.core.judge import JudgeResult  # noqa: E402
from backend.db.agents import create_agent  # noqa: E402
from backend.db.models import Criterion, Run  # noqa: E402
from backend.db.workspaces import create_workspace  # noqa: E402
from backend.jobs import handlers  # noqa: E402
from backend.jobs.handlers import handle_build_tests  # noqa: E402
from backend.jobs.queue import enqueue  # noqa: E402
from scripts import setup_assist  # noqa: E402


def _assist_setup(db, kind="assist"):
    workspace, _ = create_workspace(db)
    agent = create_agent(db, workspace, setup_assist.ASSIST_CONFIG, setup_assist.ASSIST_ANALYSIS, kind=kind)
    crit = Criterion(
        agent_id=agent.id, position=1, name="Grounded", definition="d", good_example="g", bad_example="b",
        why_discovered="w", source="discovered",
    )
    db.add(crit)
    db.commit()
    return workspace, agent, crit


def _add_runs(db, agent, sources):
    """One run per entry, position 1..n; the user message names the source and position.

    Every run carries a saved page: build_tests skips assistant runs without one (they could not be replayed).
    """
    for position, source in enumerate(sources, start=1):
        db.add(Run(
            agent_id=agent.id, prompt_version_id=agent.current_prompt_version_id, position=position, source=source,
            messages=[{"role": "user", "content": f"{source}-{position}"}, {"role": "assistant", "content": "a"}],
            brief_snapshot={"patent_number": "US10000000B2"},
        ))
    db.commit()


def _run_build(db, workspace, crit, monkeypatch, verdicts):
    mock = MagicMock(side_effect=[JudgeResult(v, "r") for v in verdicts])
    monkeypatch.setattr(handlers, "judge", mock)
    monkeypatch.setattr(handlers, "sanity_check", MagicMock(return_value=MagicMock(passed=True, good_verdict="pass", bad_verdict="fail")))
    result = handle_build_tests(db, enqueue(db, workspace.id, "build_tests", {"criterion_id": crit.id}))
    return [c.args[2][0]["content"] for c in mock.call_args_list], result


def test_r5_live_runs_are_judged_first_and_imported_never_push_them_out_of_the_eight(db_session, monkeypatch):
    workspace, agent, crit = _assist_setup(db_session)
    # 3 live runs are OLDER than 7 imported ones: the newest 8 overall would hold only 1 live run.
    _add_runs(db_session, agent, ["imported", "assist", "assist", "assist"] + ["imported"] * 6)
    seen, result = _run_build(db_session, workspace, crit, monkeypatch, ["pass"] * 8)
    assert result["judged"] == 8
    assert seen[:3] == ["assist-4", "assist-3", "assist-2"]  # live first, newest first inside the group
    assert seen[3:] == [f"imported-{n}" for n in (10, 9, 8, 7, 6)]  # then imported, newest first, filling the 8


def test_r5_the_stop_rule_still_applies_so_two_live_failures_judge_no_imported_run(db_session, monkeypatch):
    workspace, agent, crit = _assist_setup(db_session)
    _add_runs(db_session, agent, ["imported"] * 3 + ["assist"] * 3 + ["imported"] * 2)
    seen, result = _run_build(db_session, workspace, crit, monkeypatch, ["fail", "fail", "pass"])
    assert seen == ["assist-6", "assist-5"]
    assert result["tests_created"] == 2
    # the tests come from the live runs
    sources = {r.source for r in db_session.query(Run).filter(Run.id.in_([t.source_run_id for t in db_session.query(handlers.EvalTest)]))}
    assert sources == {"assist"}


def test_r5_imported_runs_remain_eligible_when_there_are_few_live_runs(db_session, monkeypatch):
    workspace, agent, crit = _assist_setup(db_session)
    _add_runs(db_session, agent, ["assist"] + ["imported"] * 5)
    seen, _ = _run_build(db_session, workspace, crit, monkeypatch, ["pass"] * 6)
    assert seen == ["assist-1", "imported-6", "imported-5", "imported-4", "imported-3", "imported-2"]


def test_r5_with_no_live_runs_or_a_general_agent_the_order_is_unchanged(db_session, monkeypatch):
    workspace, agent, crit = _assist_setup(db_session)
    _add_runs(db_session, agent, ["imported"] * 4)
    seen, _ = _run_build(db_session, workspace, crit, monkeypatch, ["pass"] * 4)
    assert seen == ["imported-1", "imported-2", "imported-3", "imported-4"]  # oldest first, as before


def test_r5_more_than_eight_live_runs_judge_the_newest_eight_live_runs(db_session, monkeypatch):
    workspace, agent, crit = _assist_setup(db_session)
    _add_runs(db_session, agent, ["assist"] * 10 + ["imported"] * 2)
    seen, _ = _run_build(db_session, workspace, crit, monkeypatch, ["pass"] * 8)
    assert seen == [f"assist-{n}" for n in range(10, 2, -1)]


# ---- R6: the suggester's prompt hygiene ---------------------------------------------------------------

import json  # noqa: E402
from unittest.mock import patch  # noqa: E402

from backend.core.config import AgentConfig, CriterionSpec  # noqa: E402
from backend.core.suggest import SuggestFailed, suggest_fix  # noqa: E402

_SUGGEST_CONFIG = AgentConfig(name="Assist", system_prompt="Be kind.", user_role_label="Visitor")
_SUGGEST_CRITERION = CriterionSpec(name="n", definition="d", good_example="g", bad_example="b")
_TURNS = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]


def _suggest(prompt_text, **kwargs):
    reply = {
        "prompt_text": prompt_text, "failing_turn_index": 1, "change_summary": "c", "touched_section": "t",
        "expected_effect": "e", "risk": "r",
    }
    with patch("backend.core.suggest.groq_chat", return_value=json.dumps(reply)) as mock:
        fix = suggest_fix(_SUGGEST_CONFIG, _SUGGEST_CRITERION, "why", _TURNS, **kwargs)
    return fix, mock


def test_r6_a_wrapping_agent_prompt_pair_is_stripped_from_the_stored_prompt():
    fix, _ = _suggest("<agent_prompt>\nBe very kind.\nRule 7.\n</agent_prompt>")
    assert fix.prompt_text == "Be very kind.\nRule 7."


def test_r6_only_a_single_wrapping_pair_is_stripped():
    fix, _ = _suggest("  <agent_prompt>\nKeep <agent_prompt> as a word.\n</agent_prompt>\n")
    assert fix.prompt_text == "Keep <agent_prompt> as a word."
    fix2, _ = _suggest("Say <agent_prompt> only inside text.")
    assert fix2.prompt_text == "Say <agent_prompt> only inside text."
    fix3, _ = _suggest("<agent_prompt>\nopen only")
    assert fix3.prompt_text == "<agent_prompt>\nopen only"  # not a wrapping pair: left as written


def test_r6_a_wrapped_copy_of_the_current_prompt_counts_as_identical():
    with pytest.raises(SuggestFailed, match="identical"):
        _suggest("<agent_prompt>\nBe kind.\n</agent_prompt>")


def test_r6_the_assistant_message_names_only_the_blocks_that_exist():
    _, mock = _suggest("Be very kind.", evidence=["Intent: classification"])
    user = mock.call_args.args[1][0]["content"]
    assert "<page_brief>" in user and "<law_excerpts>" in user and "<question>" in user
    assert "no other tags" in user and "<classifications>" not in user
    assert "without wrapping it in <agent_prompt>" in user
    _, plain = _suggest("Be very kind.")
    assert "page_brief" not in plain.call_args.args[1][0]["content"]  # general agents: message unchanged


# ---- R7: insights grounding of percentages and a brief that was cut off ---------------------------------

from types import SimpleNamespace  # noqa: E402

from backend import groq_client  # noqa: E402
from backend.core.assist import insights  # noqa: E402
from tests.test_assist_insights import FakeClient, URL as INSIGHTS_URL, seeded  # noqa: E402,F401


def _bounce(value):
    return {"totals": {"page_views": 15, "questions": 31}, "bounce_rate": value}


@pytest.mark.parametrize("space", ["", " ", "\u202f", "\u00a0", "\u2009"])
def test_r7_a_percentage_with_any_space_before_the_sign_matches_a_stored_fraction(space):
    # the real run: the model wrote "20" + U+202F + "%"; the old scan read a bare 20 and flagged it
    grounded, warnings = insights.check_grounded(f"- 20{space}% of views bounced.", _bounce(0.2), {})
    assert grounded, warnings


@pytest.mark.parametrize("stored", [0.2, 20, 20.0])
def test_r7_a_percentage_matches_the_stored_figure_as_a_fraction_or_a_percent(stored):
    grounded, warnings = insights.check_grounded("- Bounce rate is 20% of views.", _bounce(stored), {})
    assert grounded, warnings
    assert insights.check_grounded("- Bounce rate is 20 percent.", _bounce(stored), {})[0]


def test_r7_a_percentage_that_matches_nothing_rate_like_is_still_flagged():
    # 20 is the page-view count here, not a rate: "20%" must not ride on it
    adoption = {"totals": {"page_views": 20, "questions": 31}, "bounce_rate": 0.5}
    grounded, warnings = insights.check_grounded("- 20% of views bounced.", adoption, {})
    assert not grounded and "20%" in warnings[0]
    assert not insights.check_grounded("- 21% bounced.", _bounce(20), {})[0]
    assert not insights.check_grounded("- 4821 users left.", _bounce(0.2), {})[0]


@pytest.fixture
def _swap_client(monkeypatch):
    def install(reply, finish_reason=None):
        class _Fake(FakeClient):
            def _create(self, **kwargs):
                response = super()._create(**kwargs)
                response.choices[0].finish_reason = finish_reason
                return response

        fake = _Fake(reply=reply)
        monkeypatch.setattr(groq_client, "client", fake)
        return fake

    return install


def test_r7_the_call_gets_a_bigger_budget_and_a_hard_length_in_the_prompt(client, seeded, _swap_client):
    fake = _swap_client("- 6 page views.")
    assert client.post(INSIGHTS_URL, headers=seeded["headers"]).status_code == 200
    assert fake.calls[0]["max_tokens"] == 1500
    assert "never more than 220 words" in insights.SYSTEM_PROMPT


def test_r7_finish_reason_length_marks_the_brief_as_cut_off(client, seeded, _swap_client):
    _swap_client("- 6 page views.", finish_reason="length")
    body = client.post(INSIGHTS_URL, headers=seeded["headers"]).json()
    assert body["grounded"] is False
    assert insights.TRUNCATED_WARNING in body["warnings"]
    assert body["brief"] == "- 6 page views."


@pytest.mark.parametrize("reply", ["- 6 page views.\n-", "- 6 page views.\n- Questions rose,", "- 6 page views.\n- Asked about (claims"])
def test_r7_an_unterminated_last_bullet_marks_the_brief_as_cut_off(client, seeded, _swap_client, reply):
    _swap_client(reply, finish_reason="stop")
    body = client.post(INSIGHTS_URL, headers=seeded["headers"]).json()
    assert body["grounded"] is False and insights.TRUNCATED_WARNING in body["warnings"]


def test_r7_a_finished_brief_is_not_marked(client, seeded, _swap_client):
    _swap_client("- 6 page views.\n- Next step: add more data.", finish_reason="stop")
    body = client.post(INSIGHTS_URL, headers=seeded["headers"]).json()
    assert insights.TRUNCATED_WARNING not in body["warnings"]
    assert body["grounded"] is True
