import json
from pathlib import Path

import pytest

from backend.core.assist import ASSIST_MODEL
from backend.core.assist.brief import (
    MAX_BRIEF_JSON_CHARS,
    MAX_ABSTRACT_CHARS,
    MAX_CLAIM_CHARS,
    MAX_CLAIMS,
    MAX_DESCRIPTION_SECTIONS,
    MAX_LIST_ITEMS,
    MAX_SECTION_CHARS,
    MAX_SHORT_CHARS,
    Brief,
    build_claim_tree,
    is_us_patent,
    parse_claim_refs,
)
from backend.groq_client import MODEL_CHAT


def full_brief() -> dict:
    return {
        "patent_number": "US10000000B2",
        "title": "Widget system",
        "abstract": "A widget.",
        "status": "Active",
        "expiry": "2035-01-01",
        "dates": {"priority": "2015-01-01", "filing": "2015-02-02", "grant": "2018-06-19"},
        "inventors": ["A. Inventor"],
        "assignees": ["Acme Corp"],
        "classifications": [{"code": "G06F", "title": "Computing", "path": ["G", "G06", "G06F"]}],
        "landscapes": ["Widgets"],
        "claims": [
            {"number": 1, "text": "A method comprising a step.", "depends_on": [], "independent": True},
            {"number": 2, "text": "The method of claim 1, wherein x.", "depends_on": [1], "independent": False},
        ],
        "description": [{"heading": "Background", "text": "Some background."}],
        "citations": {
            "patent": [{"number": "US1", "date": "2001-01-01", "assignee": "B", "title": "T"}],
            "cited_by": [{"number": None, "date": None, "assignee": None, "title": None}],
        },
        "family": [
            {"number": "US2", "country": "US", "title": "T", "priority_date": "2015-01-01", "status": "Active"}
        ],
        "legal_events": [{"date": "2018-06-19", "text": "Granted"}],
        "completeness": {"claims": True, "description": False},
    }


def test_assist_model_is_chat_model():
    assert ASSIST_MODEL == MODEL_CHAT


def test_brief_round_trips():
    brief = Brief.model_validate(full_brief())
    dumped = brief.model_dump()
    assert Brief.model_validate(dumped) == brief
    assert dumped["claims"][1]["depends_on"] == [1]
    assert dumped["citations"]["cited_by"][0]["number"] is None


def test_minimal_brief_defaults():
    brief = Brief.model_validate({"patent_number": "US10000000B2"})
    assert brief.claims == [] and brief.title is None and brief.dates == {}


def test_unknown_top_level_key_rejected():
    data = full_brief()
    data["evil"] = "x"
    with pytest.raises(ValueError, match="evil"):
        Brief.model_validate(data)


def test_unknown_nested_key_rejected():
    data = full_brief()
    data["claims"][0]["extra"] = 1
    with pytest.raises(ValueError, match="extra"):
        Brief.model_validate(data)


@pytest.mark.parametrize("bad", [0, -3])
def test_claim_number_below_one_rejected(bad):
    data = full_brief()
    data["claims"][0]["number"] = bad
    with pytest.raises(ValueError, match="number"):
        Brief.model_validate(data)


def test_depends_on_below_one_rejected():
    data = full_brief()
    data["claims"][1]["depends_on"] = [0]
    with pytest.raises(ValueError, match="depends_on"):
        Brief.model_validate(data)


def test_too_many_claims_rejected():
    data = full_brief()
    data["claims"] = [{"number": i, "text": "A method."} for i in range(1, MAX_CLAIMS + 2)]
    with pytest.raises(ValueError, match="claims"):
        Brief.model_validate(data)


def test_claims_at_cap_accepted():
    data = full_brief()
    data["claims"] = [{"number": i, "text": "A method."} for i in range(1, MAX_CLAIMS + 1)]
    assert len(Brief.model_validate(data).claims) == MAX_CLAIMS


def test_claim_text_over_cap_rejected_not_truncated():
    data = full_brief()
    data["claims"][0]["text"] = "x" * (MAX_CLAIM_CHARS + 1)
    with pytest.raises(ValueError, match="claims"):
        Brief.model_validate(data)
    data["claims"][0]["text"] = "x" * MAX_CLAIM_CHARS
    assert len(Brief.model_validate(data).claims[0].text) == MAX_CLAIM_CHARS


def test_too_many_description_sections_rejected():
    data = full_brief()
    data["description"] = [{"heading": "h", "text": "t"}] * (MAX_DESCRIPTION_SECTIONS + 1)
    with pytest.raises(ValueError, match="description"):
        Brief.model_validate(data)


def test_section_text_over_cap_rejected():
    data = full_brief()
    data["description"][0]["text"] = "x" * (MAX_SECTION_CHARS + 1)
    with pytest.raises(ValueError, match="description"):
        Brief.model_validate(data)


@pytest.mark.parametrize("field", ["inventors", "assignees", "landscapes"])
def test_string_list_over_cap_rejected(field):
    data = full_brief()
    data[field] = ["x"] * (MAX_LIST_ITEMS + 1)
    with pytest.raises(ValueError, match=field):
        Brief.model_validate(data)


def test_citation_list_over_cap_rejected():
    data = full_brief()
    data["citations"]["cited_by"] = [{}] * (MAX_LIST_ITEMS + 1)
    with pytest.raises(ValueError, match="cited_by"):
        Brief.model_validate(data)


def test_family_and_legal_events_over_cap_rejected():
    data = full_brief()
    data["family"] = [{}] * (MAX_LIST_ITEMS + 1)
    with pytest.raises(ValueError, match="family"):
        Brief.model_validate(data)
    data = full_brief()
    data["legal_events"] = [{"date": "d", "text": "t"}] * (MAX_LIST_ITEMS + 1)
    with pytest.raises(ValueError, match="legal_events"):
        Brief.model_validate(data)


def test_total_size_over_cap_rejected():
    data = full_brief()
    # each section is under its own cap; together they exceed the total cap
    n = MAX_BRIEF_JSON_CHARS // MAX_SECTION_CHARS + 2
    assert n <= MAX_DESCRIPTION_SECTIONS
    data["description"] = [{"heading": "h", "text": "x" * MAX_SECTION_CHARS} for _ in range(n)]
    with pytest.raises(ValueError, match="brief"):
        Brief.model_validate(data)


def test_unknown_date_key_rejected():
    data = full_brief()
    data["dates"]["bogus"] = "2020-01-01"
    with pytest.raises(ValueError, match="dates"):
        Brief.model_validate(data)


# ---- build_claim_tree ----------------------------------------------------


def tree(*pairs):
    return {c.number: c for c in build_claim_tree(list(pairs))}


def test_independent_claim():
    c = tree((1, "A method comprising a step."))[1]
    assert c.depends_on == [] and c.independent is True


def test_single_reference():
    c = tree((1, "A method."), (2, "The method of claim 1, wherein x."))[2]
    assert c.depends_on == [1] and c.independent is False


def test_or_reference():
    c = tree((1, "A."), (2, "B."), (3, "C."), (4, "D."), (5, "The system of claim 3 or 4"))[5]
    assert c.depends_on == [3, 4]


def test_and_and_comma_references():
    t = tree((1, "A."), (2, "B."), (3, "C."), (4, "The device of claims 1, 2 and 3."))
    assert t[4].depends_on == [1, 2, 3]


def test_range_reference():
    c = tree((1, "A."), (2, "B."), (3, "C."), (4, "The method of any one of claims 1-3, wherein y."))[4]
    assert c.depends_on == [1, 2, 3]


def test_range_with_en_dash_and_to():
    t = tree((1, "A."), (2, "B."), (3, "C."), (4, "Of claims 1–3."), (5, "Of claims 1 to 3."))
    assert t[4].depends_on == [1, 2, 3]
    assert t[5].depends_on == [1, 2, 3]


def test_any_of_claims_list():
    t = tree((1, "A."), (2, "B."), (3, "C."), (4, "The method of any of claims 1, 3, wherein z."))
    assert t[4].depends_on == [1, 3]


def test_case_insensitive():
    c = tree((1, "A."), (2, "The method of Claim 1."))[2]
    assert c.depends_on == [1]


def test_forward_reference_ignored():
    t = tree((1, "A."), (2, "The method of claim 5, wherein x."), (5, "E."))
    assert t[2].depends_on == [] and t[2].independent is True


def test_self_reference_ignored():
    t = tree((1, "A."), (2, "As in claim 2."))
    assert t[2].depends_on == []


def test_partial_forward_range_clamped():
    t = tree((1, "A."), (2, "B."), (3, "C."), (4, "Of claims 1-9."))
    assert t[4].depends_on == [1, 2, 3]


def test_huge_range_does_not_blow_up():
    t = tree((1, "A."), (2, "Of claims 1-999999999999."))
    assert t[2].depends_on == [1]


def test_trailing_number_not_swallowed():
    t = tree((1, "A."), (2, "The method of claim 1, wherein 5 widgets are used."))
    assert t[2].depends_on == [1]


def test_multiple_separate_references_merged_sorted():
    t = tree((1, "A."), (2, "B."), (3, "The method of claim 2 combined with claim 1."))
    assert t[3].depends_on == [1, 2]


def test_text_preserved_and_order_preserved():
    out = build_claim_tree([(1, "A."), (2, "B of claim 1.")])
    assert [c.number for c in out] == [1, 2]
    assert out[1].text == "B of claim 1."


def test_no_claim_word_means_independent():
    t = tree((1, "A."), (2, "A system comprising the method as claimed."))
    assert t[2].independent is True


# ---- is_us_patent --------------------------------------------------------


@pytest.mark.parametrize("n", ["US10000000B2", "US20160266243A1", "us7629993b2", "US7629993", "US5123456A"])
def test_is_us_patent_true(n):
    assert is_us_patent(n) is True


@pytest.mark.parametrize("n", ["EP1234567A1", "WO2020123456A1", "CN123456A", "", "US", "USABC", "10000000"])
def test_is_us_patent_false(n):
    assert is_us_patent(n) is False


# ---- shared claim-reference cases (the JS port reuses this table) ---------

FW3 = chr(0xFF13)  # fullwidth digit three
EN, EM = chr(0x2013), chr(0x2014)

# (own claim number, claim text, expected depends_on); the table lives in a JSON
# file shared with the extension's JavaScript tests (extension/src/claimrefs.test.js).
with open(Path(__file__).parent / "fixtures" / "claim_ref_cases.json", encoding="utf-8") as _f:
    REF_CASES = [(c["number"], c["text"], c["expected"]) for c in json.load(_f)]


@pytest.mark.parametrize("number,text,expected", REF_CASES)
def test_claim_ref_cases(number, text, expected):
    assert parse_claim_refs(number, text) == expected


def test_six_digit_number_is_a_number():
    assert parse_claim_refs(1_000_000, "of claim 123456") == [123456]


def test_fullwidth_digit_in_claim_reference_gives_no_reference():
    t = tree((1, "A."), (2, "B."), (3, "C."), (4, "The method of claim " + FW3 + "."))
    assert t[4].depends_on == [] and t[4].independent is True


def test_long_digit_run_does_not_raise():
    t = tree((1, "A."), (2, "The method of claim " + "9" * 5000 + "."))
    assert t[2].depends_on == []


# ---- ASCII-only US numbers -------------------------------------------------


def test_fullwidth_digits_rejected_in_us_number():
    fw = "".join(chr(0xFF10 + int(d)) for d in "123456")
    assert is_us_patent("US" + fw) is False
    assert is_us_patent("US10000000B2" + chr(0xFF11)) is False


@pytest.mark.parametrize("n", ["US 10,000,000 B2", "US10,000,000", "US 7629993"])
def test_non_compact_forms_rejected(n):
    assert is_us_patent(n) is False


# ---- per-field caps --------------------------------------------------------


def _set(data, path, value):
    cur = data
    for key in path[:-1]:
        cur = cur[key]
    cur[path[-1]] = value


FIELD_CAPS = [
    (["title"], "title", MAX_SHORT_CHARS),
    (["status"], "status", MAX_SHORT_CHARS),
    (["expiry"], "expiry", MAX_SHORT_CHARS),
    (["abstract"], "abstract", MAX_ABSTRACT_CHARS),
    (["inventors", 0], "inventors", MAX_SHORT_CHARS),
    (["assignees", 0], "assignees", MAX_SHORT_CHARS),
    (["landscapes", 0], "landscapes", MAX_SHORT_CHARS),
    (["dates", "grant"], "dates", MAX_SHORT_CHARS),
    (["classifications", 0, "code"], "classifications", MAX_SHORT_CHARS),
    (["classifications", 0, "title"], "classifications", MAX_SHORT_CHARS),
    (["classifications", 0, "path", 0], "classifications", MAX_SHORT_CHARS),
    (["citations", "patent", 0, "title"], "citations", MAX_SHORT_CHARS),
    (["family", 0, "status"], "family", MAX_SHORT_CHARS),
    (["legal_events", 0, "text"], "legal_events", MAX_SHORT_CHARS),
    (["description", 0, "heading"], "description", MAX_SHORT_CHARS),
]


@pytest.mark.parametrize("path,field,cap", FIELD_CAPS)
def test_field_cap_boundary(path, field, cap):
    data = full_brief()
    _set(data, path, "x" * cap)
    Brief.model_validate(data)  # at the cap: accepted, untouched
    _set(data, path, "x" * (cap + 1))
    with pytest.raises(ValueError, match=field):
        Brief.model_validate(data)


def test_completeness_key_over_cap_rejected():
    data = full_brief()
    data["completeness"] = {"k" * MAX_SHORT_CHARS: True}
    Brief.model_validate(data)
    data["completeness"] = {"k" * (MAX_SHORT_CHARS + 1): True}
    with pytest.raises(ValueError, match="completeness"):
        Brief.model_validate(data)


def test_short_field_not_truncated():
    data = full_brief()
    data["title"] = "t" * MAX_SHORT_CHARS
    assert Brief.model_validate(data).title == "t" * MAX_SHORT_CHARS
