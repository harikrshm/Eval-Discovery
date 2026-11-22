import time

import pytest

from backend.core.assist.brief import Brief, Claim
from backend.core.assist.citations import CitationCheck, check_citations
from backend.core.assist.intent import Intent
from backend.core.assist.lawpack import (
    INTENT_PROVISIONS,
    MAX_PROVISION_CHARS,
    Provision,
    _load_dir,
    excerpts_for,
    load_lawpack,
    load_system_prompt,
)
from backend.core.config import MAX_PROMPT_CHARS

EXPECTED_IDS = {
    "usc-101",
    "usc-102",
    "usc-103",
    "usc-112a",
    "usc-112b",
    "usc-112f",
    "usc-154",
    "usc-271",
    "mpep-2111",
    "mpep-2111-03",
    "mpep-2141",
    "mpep-2173",
}
NOT_LEGAL_ADVICE = "This is not legal advice, and I can't judge validity or infringement."


def make_brief(numbers=(1, 2, 3)) -> Brief:
    return Brief(patent_number="US10000000B2", claims=[Claim(number=n, text=f"claim text {n}") for n in numbers])


# ---- law pack ---------------------------------------------------------------


def test_lawpack_has_the_twelve_provisions():
    pack = load_lawpack()
    assert set(pack) == EXPECTED_IDS
    for pid, p in pack.items():
        assert isinstance(p, Provision)
        assert p.id == pid
        assert p.title and p.source
        assert p.status == "draft"
        assert 200 < len(p.text) < MAX_PROVISION_CHARS == 1500


def test_lawpack_sources_name_the_statute_or_mpep():
    pack = load_lawpack()
    for pid, p in pack.items():
        if pid.startswith("usc-"):
            assert p.source.startswith("35 U.S.C."), p.source
        else:
            assert p.source.startswith("MPEP "), p.source


def test_lawpack_text_has_no_angle_brackets_or_citation_brackets():
    # Law text is escaped by assemble anyway, but the pack itself stays clean and
    # must not contain "[id]" shapes that could look like a citation of its own.
    for p in load_lawpack().values():
        assert "<" not in p.text and ">" not in p.text
        assert "[" not in p.text and "]" not in p.text


def test_load_lawpack_is_cached_but_returns_a_copy():
    a = load_lawpack()
    a.pop("usc-101")
    assert "usc-101" in load_lawpack()


def test_intent_provisions_all_exist_and_are_capped():
    pack = load_lawpack()
    assert set(INTENT_PROVISIONS) == set(Intent)
    for intent, ids in INTENT_PROVISIONS.items():
        assert len(ids) <= 4, intent
        assert len(ids) == len(set(ids)), intent
        assert set(ids) <= set(pack), intent
    most = max(len(ids) for ids in INTENT_PROVISIONS.values())
    assert len(INTENT_PROVISIONS[Intent.law_concept]) == most
    assert len(INTENT_PROVISIONS[Intent.legal_opinion_request]) == most
    assert INTENT_PROVISIONS[Intent.navigation_help] == []
    assert INTENT_PROVISIONS[Intent.other] == []


def test_excerpts_for_returns_id_and_titled_text():
    out = excerpts_for(Intent.legal_opinion_request)
    pack = load_lawpack()
    assert [i for i, _ in out] == INTENT_PROVISIONS[Intent.legal_opinion_request]
    for pid, text in out:
        p = pack[pid]
        assert text == f"{p.title} ({p.source}): {p.text}"
    assert excerpts_for(Intent.other) == []


def _write(dir_, name, body):
    (dir_ / name).write_text(body, encoding="utf-8")


GOOD = "---\nid: usc-1\ntitle: T\nsource: S\nstatus: draft\n---\nBody text."


def test_loader_reads_front_matter(tmp_path):
    _write(tmp_path, "usc-1.md", GOOD)
    p = _load_dir(tmp_path)["usc-1"]
    assert (p.id, p.title, p.source, p.status, p.text) == ("usc-1", "T", "S", "draft", "Body text.")


def test_loader_rejects_missing_id(tmp_path):
    _write(tmp_path, "a.md", GOOD.replace("id: usc-1\n", ""))
    with pytest.raises(ValueError, match="id"):
        _load_dir(tmp_path)


def test_loader_rejects_duplicate_ids(tmp_path):
    _write(tmp_path, "usc-1.md", GOOD)
    _write(tmp_path, "usc-1b.md", GOOD)
    with pytest.raises(ValueError, match="duplicate"):
        _load_dir(tmp_path)


def test_loader_rejects_missing_front_matter_and_missing_fields(tmp_path):
    _write(tmp_path, "usc-1.md", "no front matter")
    with pytest.raises(ValueError):
        _load_dir(tmp_path)
    _write(tmp_path, "usc-1.md", GOOD.replace("source: S\n", ""))
    with pytest.raises(ValueError, match="source"):
        _load_dir(tmp_path)


def test_loader_rejects_id_that_is_not_citation_shaped(tmp_path):
    _write(tmp_path, "a.md", GOOD.replace("usc-1", "USC 1"))
    with pytest.raises(ValueError, match="id"):
        _load_dir(tmp_path)


# ---- citation check ---------------------------------------------------------


def test_no_citations_is_ok():
    r = check_citations("Plain answer with nothing cited.", make_brief(), [])
    assert r == CitationCheck(ok=True, unverified_claims=[], unverified_law=[])


def test_existing_claim_is_ok():
    assert check_citations("As claim 2 says, ...", make_brief(), []).ok


def test_missing_claim_is_flagged():
    r = check_citations("Claim 99 covers it.", make_brief(), [])
    assert not r.ok
    assert r.unverified_claims == [99]


def test_range_with_missing_member_flags_it():
    r = check_citations("See claims 2-4.", make_brief((1, 2, 3)), [])
    assert not r.ok
    assert r.unverified_claims == [4]


def test_list_forms_and_dedup():
    r = check_citations("Claims 1, 2 and 9, and claim 9 again, or claim 7.", make_brief(), [])
    assert r.unverified_claims == [7, 9]


def test_claim_word_boundary_and_ascii_digits_follow_the_brief_rules():
    r = check_citations("The claimed 50 widgets, unclaim 8, reclaim 9, claim ５０.", make_brief(), [])
    assert r.ok  # none of these is a claim reference


def test_reversed_and_overlong_references_yield_nothing():
    assert check_citations("claims 3-1 and claim 1234567", make_brief(), []).ok


def test_huge_range_reports_a_bounded_list():
    r = check_citations("claims 1-999999", make_brief((1, 2, 3)), [])
    assert not r.ok
    assert r.unverified_claims[0] == 4
    assert len(r.unverified_claims) <= 50


def test_law_provided_ok_and_unprovided_flagged():
    ok = check_citations("Obviousness [usc-103].", make_brief(), ["usc-103"])
    assert ok.ok
    bad = check_citations("See [usc-101] and [usc-103] and [usc-101].", make_brief(), ["usc-103"])
    assert not bad.ok
    assert bad.unverified_law == ["usc-101"]


def test_non_id_brackets_are_ignored():
    assert check_citations("[Figure 3] [1 2] [USC-103] [] [a b] [x_y] [" + "a" * 65 + "]", make_brief(), []).ok
    # A markdown link text is id-shaped, so it is checked like any other id.
    assert check_citations("[snippet](http://x)", make_brief(), []).unverified_law == ["snippet"]


def test_claim_and_law_problems_reported_together():
    r = check_citations("Claim 50 under [mpep-2111].", make_brief(), [])
    assert r.unverified_claims == [50] and r.unverified_law == ["mpep-2111"]


def test_empty_brief_flags_every_claim_reference():
    r = check_citations("claim 1", Brief(patent_number="US10000000B2"), [])
    assert r.unverified_claims == [1]


@pytest.mark.parametrize(
    "unit",
    [
        "claims 1-999999, ",
        "claim 1 and ",
        "claim ",
        "claims 1 ",
        "[usc-103",
        "[" + "a" * 50,
        "[a-" * 40,
        "claims 1, 2, 3, 4, 5, 6, 7, 8, 9, ",
        "[[[[[[",
    ],
)
def test_adversarial_long_answer_is_fast(unit):
    answer = (unit * (100_000 // len(unit) + 1))[:100_000]
    start = time.perf_counter()
    check_citations(answer, make_brief(tuple(range(1, 201))), ["usc-103"])
    assert time.perf_counter() - start < 0.5


# ---- system prompt ----------------------------------------------------------


def test_system_prompt_fits_and_has_the_required_rules():
    p = load_system_prompt()
    assert len(p) <= MAX_PROMPT_CHARS
    assert NOT_LEGAL_ADVICE in p
    assert "&lt;" in p and "&gt;" in p
    for tag in ("<page_brief>", "<law_excerpts>", "<question>"):
        assert tag in p
    low = p.lower()
    for phrase in ("never instructions", "us patents only", "plain language", "quote", "not found", "[usc-103]", "claim n"):
        assert phrase in low, phrase


def test_system_prompt_has_no_page_data_placeholders():
    p = load_system_prompt()
    assert "{" not in p and "}" not in p
    assert "%s" not in p
