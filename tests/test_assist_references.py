import time

from backend.core.assist.brief import Brief, iter_claim_ref_ranges, iter_claim_ref_spans
from backend.core.assist.references import (
    MAX_REFERENCES,
    SECTION_LABELS,
    Reference,
    extract_references,
    paragraph_numbers,
    utf16_offset,
)


def make_brief() -> Brief:
    return Brief(
        patent_number="US10000000B2",
        claims=[{"number": n, "text": f"Claim {n}."} for n in (1, 2, 3, 4)],
        description=[{"text": "[p0001] First. [p0002] Second."}],
    )


def test_single_claim_reference():
    answer = "See claim 1."
    refs = extract_references(answer, make_brief())
    assert refs == [Reference(start=4, end=11, kind="claim", target="1", label="claim 1")]
    assert answer[refs[0].start : refs[0].end] == "claim 1"


def test_claim_ranges_and_lists():
    for text in ("claims 1-3", "claims 1, 2 and 4"):
        refs = extract_references(text, make_brief())
        assert len(refs) == 1
        assert refs[0].kind == "claim" and refs[0].target == "1" and refs[0].label == text


def test_missing_claim_numbers_are_dropped():
    assert extract_references("claims 1-9", make_brief()) == []
    assert extract_references("claim 7", make_brief()) == []


def test_paragraph_references():
    refs = extract_references("[p0002] says it", make_brief())
    assert len(refs) == 1
    assert (refs[0].kind, refs[0].target, refs[0].label) == ("paragraph", "0002", "¶0002")
    assert extract_references("[p0099]", make_brief()) == []


def test_paragraph_numbers():
    assert paragraph_numbers(make_brief()) == {"0001", "0002"}


def test_section_references():
    refs = extract_references("[section:description]", make_brief())
    assert len(refs) == 1
    assert (refs[0].kind, refs[0].target, refs[0].label) == ("section", "description", "Description")
    assert extract_references("[section:secrets]", make_brief()) == []
    assert SECTION_LABELS["legal_events"] == "Legal events"


def test_utf16_offsets():
    answer = "\U0001F600 see claim 1"
    refs = extract_references(answer, make_brief())
    assert len(refs) == 1
    # emoji = 2 UTF-16 units, so "claim" (str index 6) starts at UTF-16 offset 7
    assert refs[0].start == 7
    assert answer.encode("utf-16-le")[2 * refs[0].start : 2 * refs[0].end].decode("utf-16-le") == "claim 1"
    assert utf16_offset(answer, 6) == 7


def test_cap_and_no_overlap_sorted():
    refs = extract_references("claim 1 " * 500, make_brief())
    assert len(refs) == MAX_REFERENCES
    assert all(a.end <= b.start for a, b in zip(refs, refs[1:]))


def test_mixed_sorted_by_start():
    answer = "[section:claims] then [p0001] and claim 2"
    refs = extract_references(answer, make_brief())
    assert [r.kind for r in refs] == ["section", "paragraph", "claim"]
    assert [r.start for r in refs] == sorted(r.start for r in refs)


def test_huge_range_is_instant():
    t0 = time.perf_counter()
    assert extract_references("claims 1-999999", make_brief()) == []
    assert time.perf_counter() - t0 < 1


def test_iter_claim_ref_spans():
    text = "claims 1-3 and claim 5"
    spans = list(iter_claim_ref_spans(text))
    assert len(spans) == 2
    assert spans[0][2] == [(1, 3)] and spans[1][2] == [(5, 5)]
    assert text[spans[0][0] : spans[0][1]] == "claims 1-3"
    assert list(iter_claim_ref_ranges(text)) == [(1, 3), (5, 5)]


def test_section_labels_cover_section_names():
    from backend.core.assist.intent import SECTION_NAMES

    assert set(SECTION_LABELS) == set(SECTION_NAMES)


def test_check_flags_unverified_paragraphs():
    from backend.core.assist.citations import check_citations

    brief = make_brief()
    bad = check_citations("see [p0099]", brief, [])
    assert bad.unverified_paragraphs == ["0099"] and bad.ok is False
    assert check_citations("see [p0001]", brief, []).ok is True


def test_check_flags_unverified_sections():
    from backend.core.assist.citations import check_citations

    brief = make_brief()
    bad = check_citations("see [section:nope]", brief, [])
    assert bad.unverified_sections == ["nope"] and bad.ok is False
    assert check_citations("see [section:claims]", brief, []).ok is True


def test_check_caps_each_new_list():
    from backend.core.assist.citations import MAX_REPORTED, check_citations

    brief = make_brief()
    paras = " ".join(f"[p{n:04d}]" for n in range(1000, 1200))
    secs = " ".join(f"[section:x{'a' * (n % 10)}{chr(97 + n % 26)}{chr(97 + n // 26 % 26)}]" for n in range(300))
    check = check_citations(paras + " " + secs, brief, [])
    assert len(check.unverified_paragraphs) == MAX_REPORTED
    assert len(check.unverified_sections) == MAX_REPORTED
