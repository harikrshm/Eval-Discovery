import copy

import pytest

from backend.core.promotion import (
    Decision,
    RequiredTest,
    decide_promotion,
    early_failure,
)

FIX = RequiredTest("fix1", "fix", "fail")
HELD = RequiredTest("held1", "heldout", "fail")
REG1 = RequiredTest("reg1", "regression", "pass")
REG2 = RequiredTest("reg2", "regression", "pass")

FIX_FAIL = "The fix test did not pass."
HELD_FAIL = "The held-out test did not pass."
REG_FAIL = "A previously passing test now fails."

ALL = [FIX, HELD, REG1, REG2]


@pytest.mark.parametrize(
    "verdicts",
    [
        {"fix1": "fail", "held1": "pass", "reg1": "pass", "reg2": "pass"},
        {"fix1": "not_applicable", "held1": "fail", "reg1": "fail", "reg2": "pass"},
        {"fix1": "pass", "held1": "fail", "reg1": "pass", "reg2": "pass"},
        {"fix1": "pass", "held1": "not_applicable", "reg1": "fail", "reg2": "fail"},
        {"fix1": "pass", "held1": "pass", "reg1": "fail", "reg2": "pass"},
        {"fix1": "pass", "held1": "pass", "reg1": "fail", "reg2": "fail"},
    ],
)
def test_complete_failing_verdicts_agree_with_decide_promotion(verdicts):
    expected = decide_promotion(ALL, verdicts)
    assert expected.promote is False
    assert early_failure(ALL, verdicts) == expected


@pytest.mark.parametrize(
    "required, verdicts, expected",
    [
        # fix missing, held-out failing
        ([FIX, HELD, REG1], {"held1": "fail"}, Decision(False, HELD_FAIL, ("held1",))),
        ([FIX, HELD, REG1], {"held1": "not_applicable"}, Decision(False, HELD_FAIL, ("held1",))),
        # only a regression failing, fix and held-out missing
        ([FIX, HELD, REG1, REG2], {"reg2": "fail"}, Decision(False, REG_FAIL, ("reg2",))),
        # all failing regressions, in required order
        (
            [FIX, HELD, REG1, REG2],
            {"reg2": "fail", "reg1": "fail"},
            Decision(False, REG_FAIL, ("reg1", "reg2")),
        ),
        # fix not applicable
        ([FIX, HELD, REG1], {"fix1": "not_applicable"}, Decision(False, FIX_FAIL, ("fix1",))),
        ([FIX], {"fix1": "fail"}, Decision(False, FIX_FAIL, ("fix1",))),
        # precedence: fix before held-out before regression
        (
            [FIX, HELD, REG1],
            {"fix1": "fail", "held1": "fail", "reg1": "fail"},
            Decision(False, FIX_FAIL, ("fix1",)),
        ),
        (
            [FIX, HELD, REG1],
            {"fix1": "pass", "held1": "fail", "reg1": "fail"},
            Decision(False, HELD_FAIL, ("held1",)),
        ),
        # a passing fix does not hide a failing regression while held-out is missing
        ([FIX, HELD, REG1], {"fix1": "pass", "reg1": "fail"}, Decision(False, REG_FAIL, ("reg1",))),
    ],
)
def test_partial_verdicts_that_decide_failure(required, verdicts, expected):
    assert early_failure(required, verdicts) == expected


@pytest.mark.parametrize(
    "required, verdicts",
    [
        ([FIX, HELD, REG1], {}),
        ([FIX, HELD, REG1], {"held1": "pass"}),  # missing fix with passing held-out
        ([FIX, HELD, REG1], {"fix1": "pass"}),
        ([FIX, HELD, REG1], {"reg1": "pass"}),
        ([FIX, HELD, REG1], {"fix1": "pass", "held1": "pass", "reg1": "pass"}),  # never promotes
        ([FIX, REG1], {"fix1": "pass", "reg1": "pass"}),
        ([FIX], {"fix1": "pass"}),
        ([FIX, HELD, REG1], {"fix1": "pass", "held1": "pass", "reg1": "not_applicable"}),
        ([FIX, HELD, REG1, REG2], {"reg1": "not_applicable", "reg2": "not_applicable"}),
        ([FIX, HELD, REG1], {"fix1": "pass", "held1": "pass"}),  # regression still missing
    ],
)
def test_undecided_returns_none(required, verdicts):
    assert early_failure(required, verdicts) is None


@pytest.mark.parametrize("bad", ["PASS", "Fail", "failed", "", "ok", "N/A"])
@pytest.mark.parametrize("test_id", ["fix1", "held1", "reg1"])
def test_unknown_verdict_raises(test_id, bad):
    with pytest.raises(ValueError):
        early_failure([FIX, HELD, REG1], {test_id: bad})


def test_unknown_verdict_raises_even_when_failure_is_already_decided():
    with pytest.raises(ValueError):
        early_failure([FIX, HELD, REG1], {"fix1": "fail", "reg1": "PASS"})


@pytest.mark.parametrize(
    "required",
    [
        [FIX, FIX],  # duplicate id
        [FIX, RequiredTest("fix1", "regression", None)],  # duplicate id across roles
        [FIX, RequiredTest("fix2", "fix", "fail")],  # two fixes
        [HELD, REG1],  # no fix
        [FIX, HELD, RequiredTest("held2", "heldout", "fail")],  # two held-outs
        [FIX, RequiredTest("x", "bogus", None)],  # unknown role
        [],
    ],
)
def test_malformed_required_raises(required):
    with pytest.raises(ValueError):
        early_failure(required, {"fix1": "fail"})


def test_inputs_are_not_mutated():
    required = [FIX, HELD, REG1, REG2]
    verdicts = {"reg2": "fail", "fix1": "pass"}
    required_before = copy.deepcopy(required)
    verdicts_before = copy.deepcopy(verdicts)
    early_failure(required, verdicts)
    assert required == required_before
    assert verdicts == verdicts_before
