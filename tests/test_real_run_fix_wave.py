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
