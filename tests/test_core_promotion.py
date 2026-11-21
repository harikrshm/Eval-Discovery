import copy
import json

import pytest

from backend.core.promotion import (
    Decision,
    RequiredTest,
    build_outcome_rows,
    decide_promotion,
    quote_turn,
    strength_for,
)

FIX = RequiredTest("fix1", "fix", "fail")
HELD = RequiredTest("held1", "heldout", "fail")
REG1 = RequiredTest("reg1", "regression", "pass")
REG2 = RequiredTest("reg2", "regression", "pass")

SUCCESS = "All required tests passed."
FIX_FAIL = "The fix test did not pass."
HELD_FAIL = "The held-out test did not pass."
REG_FAIL = "A previously passing test now fails."


def test_all_pass_with_heldout_promotes():
    d = decide_promotion(
        [FIX, HELD, REG1],
        {"fix1": "pass", "held1": "pass", "reg1": "pass"},
    )
    assert d == Decision(True, SUCCESS, ())


def test_all_pass_without_heldout_promotes():
    d = decide_promotion([FIX, REG1], {"fix1": "pass", "reg1": "pass"})
    assert d == Decision(True, SUCCESS, ())


def test_fix_only_promotes():
    assert decide_promotion([FIX], {"fix1": "pass"}).promote is True


@pytest.mark.parametrize("verdict", ["fail", "not_applicable"])
def test_fix_not_passing_blocks(verdict):
    d = decide_promotion([FIX, HELD, REG1], {"fix1": verdict, "held1": "pass", "reg1": "pass"})
    assert d == Decision(False, FIX_FAIL, ("fix1",))


@pytest.mark.parametrize("verdict", ["fail", "not_applicable"])
def test_heldout_not_passing_blocks(verdict):
    d = decide_promotion([FIX, HELD, REG1], {"fix1": "pass", "held1": verdict, "reg1": "pass"})
    assert d == Decision(False, HELD_FAIL, ("held1",))


def test_regression_fail_blocks_and_names_every_failing_test():
    d = decide_promotion(
        [FIX, HELD, REG1, REG2],
        {"fix1": "pass", "held1": "pass", "reg1": "fail", "reg2": "fail"},
    )
    assert d == Decision(False, REG_FAIL, ("reg1", "reg2"))


def test_regression_fail_without_heldout_blocks():
    d = decide_promotion([FIX, REG1], {"fix1": "pass", "reg1": "fail"})
    assert d == Decision(False, REG_FAIL, ("reg1",))


def test_regression_not_applicable_is_ignored():
    d = decide_promotion(
        [FIX, HELD, REG1, REG2],
        {"fix1": "pass", "held1": "pass", "reg1": "not_applicable", "reg2": "pass"},
    )
    assert d == Decision(True, SUCCESS, ())


def test_only_the_failing_regression_is_named():
    d = decide_promotion(
        [FIX, REG1, REG2], {"fix1": "pass", "reg1": "not_applicable", "reg2": "fail"}
    )
    assert d.failing_test_ids == ("reg2",)


def test_precedence_fix_before_heldout_before_regression():
    everything_bad = {"fix1": "fail", "held1": "fail", "reg1": "fail"}
    assert decide_promotion([FIX, HELD, REG1], everything_bad).reason == FIX_FAIL
    heldout_and_reg = {"fix1": "pass", "held1": "fail", "reg1": "fail"}
    assert decide_promotion([FIX, HELD, REG1], heldout_and_reg).reason == HELD_FAIL
    only_reg = {"fix1": "pass", "held1": "pass", "reg1": "fail"}
    assert decide_promotion([FIX, HELD, REG1], only_reg).reason == REG_FAIL


def test_required_order_does_not_matter():
    verdicts = {"fix1": "fail", "held1": "fail", "reg1": "fail"}
    assert decide_promotion([REG1, HELD, FIX], verdicts).reason == FIX_FAIL


def test_extra_verdicts_for_unrequired_tests_are_ignored():
    d = decide_promotion([FIX], {"fix1": "pass", "other": "fail", "weird": "bogus"})
    assert d.promote is True


@pytest.mark.parametrize("missing", ["fix1", "held1", "reg1"])
def test_missing_verdict_raises(missing):
    verdicts = {"fix1": "pass", "held1": "pass", "reg1": "pass"}
    del verdicts[missing]
    with pytest.raises(ValueError):
        decide_promotion([FIX, HELD, REG1], verdicts)


def test_missing_verdict_raises_even_when_fix_already_failed():
    with pytest.raises(ValueError):
        decide_promotion([FIX, REG1], {"fix1": "fail"})


@pytest.mark.parametrize("bad", ["PASS", "passed", "", "error", "skipped", None, 1, True])
def test_unknown_verdict_raises(bad):
    with pytest.raises(ValueError):
        decide_promotion([FIX, REG1], {"fix1": "pass", "reg1": bad})


def test_unknown_verdict_raises_even_when_fix_already_failed():
    with pytest.raises(ValueError):
        decide_promotion([FIX, REG1], {"fix1": "fail", "reg1": "bogus"})


def test_no_fix_role_raises():
    with pytest.raises(ValueError):
        decide_promotion([REG1], {"reg1": "pass"})
    with pytest.raises(ValueError):
        decide_promotion([], {})


def test_more_than_one_fix_raises():
    other = RequiredTest("fix2", "fix", "fail")
    with pytest.raises(ValueError):
        decide_promotion([FIX, other], {"fix1": "pass", "fix2": "pass"})


def test_more_than_one_heldout_raises():
    other = RequiredTest("held2", "heldout", "fail")
    with pytest.raises(ValueError):
        decide_promotion(
            [FIX, HELD, other], {"fix1": "pass", "held1": "pass", "held2": "pass"}
        )


@pytest.mark.parametrize("role", ["Fix", "held-out", "", "other", None])
def test_unknown_role_raises(role):
    odd = RequiredTest("x", role, "pass")
    with pytest.raises(ValueError):
        decide_promotion([FIX, odd], {"fix1": "pass", "x": "pass"})


def test_duplicate_test_id_raises():
    with pytest.raises(ValueError):
        decide_promotion([FIX, REG1, REG1], {"fix1": "pass", "reg1": "pass"})


def test_decide_does_not_mutate_inputs():
    required = [FIX, HELD, REG1]
    verdicts = {"fix1": "pass", "held1": "pass", "reg1": "fail"}
    before = (list(required), dict(verdicts))
    decide_promotion(required, verdicts)
    assert (required, verdicts) == before


def test_decision_and_required_are_frozen():
    with pytest.raises(Exception):
        FIX.role = "heldout"
    with pytest.raises(Exception):
        Decision(True, "x", ()).promote = False


def test_strength_full_with_heldout_weaker_without():
    assert strength_for([FIX, HELD, REG1]) == "full"
    assert strength_for([FIX, REG1]) == "weaker"
    assert strength_for([FIX]) == "weaker"
    assert strength_for([]) == "weaker"


# --- quote_turn ---


def test_quote_turn_in_range():
    transcript = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    assert quote_turn(transcript, 0) == "hi"
    assert quote_turn(transcript, 1) == "hello"


@pytest.mark.parametrize("index", [2, 99, -1])
def test_quote_turn_out_of_range_is_none(index):
    assert quote_turn([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "x"}], index) is None


def test_quote_turn_empty_or_missing_transcript_is_none():
    assert quote_turn([], 0) is None
    assert quote_turn(None, 0) is None


def test_quote_turn_malformed_message_is_none():
    assert quote_turn([{"role": "user"}], 0) is None
    assert quote_turn(["text"], 0) is None
    assert quote_turn([{"role": "user", "content": 5}], 0) is None


def test_quote_turn_non_int_index_is_none():
    assert quote_turn([{"role": "user", "content": "hi"}], "0") is None
    assert quote_turn([{"role": "user", "content": "hi"}], True) is None


def test_quote_turn_truncates_long_text_to_500():
    long = "a" * 900
    quoted = quote_turn([{"role": "assistant", "content": long}], 0)
    assert len(quoted) == 500
    assert quoted.endswith("…")
    assert quoted[:-1] == "a" * 499


def test_quote_turn_exactly_500_is_untouched():
    text = "b" * 500
    assert quote_turn([{"role": "assistant", "content": text}], 0) == text


# --- build_outcome_rows ---


def _rows(required=None, **over):
    required = required or [FIX, HELD, REG1]
    args = dict(
        names={"fix1": "Tone", "held1": "Tone", "reg1": "Safety"},
        after={"fix1": "pass", "held1": "pass", "reg1": "fail"},
        rationales={"fix1": "ok", "held1": "fine", "reg1": "bad"},
        transcripts={
            "fix1": [{"role": "user", "content": "hi"}],
            "held1": [{"role": "user", "content": "yo"}],
            "reg1": [{"role": "user", "content": "hey"}],
        },
    )
    args.update(over)
    return build_outcome_rows(required, **args)


def test_rows_shape_and_order_follow_required():
    rows = _rows([REG1, FIX, HELD])
    assert [r["test_id"] for r in rows] == ["reg1", "fix1", "held1"]
    assert rows[1] == {
        "test_id": "fix1",
        "role": "fix",
        "criterion_name": "Tone",
        "before": "fail",
        "after": "pass",
        "rationale": "ok",
        "transcript": [{"role": "user", "content": "hi"}],
    }
    assert set(rows[0]) == {
        "test_id", "role", "criterion_name", "before", "after", "rationale", "transcript",
    }


def test_rows_before_comes_from_required_after_from_dict():
    rows = _rows([RequiredTest("fix1", "fix", None), REG1], after={"reg1": "not_applicable"})
    assert rows[0]["before"] is None
    assert rows[0]["after"] is None  # missing -> None
    assert rows[1]["before"] == "pass"
    assert rows[1]["after"] == "not_applicable"


def test_rows_missing_text_inputs_become_safe_defaults():
    rows = _rows([FIX], names={}, rationales={}, transcripts={})
    assert rows[0]["criterion_name"] == ""
    assert rows[0]["rationale"] == ""
    assert rows[0]["transcript"] == []


def test_rows_none_transcript_is_empty_list():
    rows = _rows([FIX], transcripts={"fix1": None})
    assert rows[0]["transcript"] == []


def test_rows_none_rationale_is_empty_string():
    rows = _rows([FIX], rationales={"fix1": None})
    assert rows[0]["rationale"] == ""


def test_rows_rationale_capped_at_600():
    rows = _rows([FIX], rationales={"fix1": "r" * 5000})
    assert len(rows[0]["rationale"]) == 600
    assert rows[0]["rationale"].endswith("…")


def test_rows_rationale_of_exactly_600_untouched():
    rows = _rows([FIX], rationales={"fix1": "r" * 600})
    assert rows[0]["rationale"] == "r" * 600


def _json_len(value):
    return len(json.dumps(value, ensure_ascii=False))


def test_rows_short_transcript_kept_verbatim():
    transcript = [{"role": "user", "content": "héllo ✓"}, {"role": "assistant", "content": "x"}]
    rows = _rows([FIX], transcripts={"fix1": transcript})
    assert rows[0]["transcript"] == transcript


def test_rows_transcript_drops_trailing_messages_first():
    transcript = [{"role": "user", "content": "m%d" % i + "x" * 1000} for i in range(10)]
    rows = _rows([FIX], transcripts={"fix1": transcript})
    kept = rows[0]["transcript"]
    assert _json_len(kept) <= 6000
    assert 0 < len(kept) < 10
    # whole messages from the front, unmodified except possibly the last
    assert kept[:-1] == transcript[: len(kept) - 1]
    # as many messages as fit were kept: one more whole message would not fit
    assert _json_len(transcript[: len(kept) + 1]) > 6000


def test_rows_transcript_truncates_single_oversized_message_with_ellipsis():
    transcript = [{"role": "user", "content": "z" * 20000}, {"role": "assistant", "content": "later"}]
    rows = _rows([FIX], transcripts={"fix1": transcript})
    kept = rows[0]["transcript"]
    assert len(kept) == 1
    assert kept[0]["role"] == "user"
    assert kept[0]["content"].endswith("…")
    assert _json_len(kept) <= 6000
    # tight: the budget is used, not wasted
    assert _json_len(kept) >= 5990


def test_rows_transcript_budget_counts_json_escapes_and_unicode_as_one_char():
    quotes = [{"role": "user", "content": '"' * 20000}]  # each quote escapes to 2 chars
    kept = _rows([FIX], transcripts={"fix1": quotes})[0]["transcript"]
    assert _json_len(kept) <= 6000
    wide = [{"role": "user", "content": "é" * 20000}]  # ensure_ascii=False: 1 char each
    kept = _rows([FIX], transcripts={"fix1": wide})[0]["transcript"]
    assert 5990 <= _json_len(kept) <= 6000


def test_rows_transcript_ignores_extra_message_keys_and_stays_role_content():
    transcript = [{"role": "user", "content": "hi", "extra": 1}]
    kept = _rows([FIX], transcripts={"fix1": transcript})[0]["transcript"]
    assert kept == [{"role": "user", "content": "hi"}]


def test_build_rows_does_not_mutate_inputs():
    transcripts = {"fix1": [{"role": "user", "content": "q" * 9000}]}
    rationales = {"fix1": "r" * 900}
    names = {"fix1": "Tone"}
    after = {"fix1": "pass"}
    required = [FIX]
    snapshot = copy.deepcopy((required, names, after, rationales, transcripts))
    build_outcome_rows(required, names, after, rationales, transcripts)
    assert (required, names, after, rationales, transcripts) == snapshot
