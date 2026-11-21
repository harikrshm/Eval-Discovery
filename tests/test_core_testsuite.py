import pytest

from backend.core.testsuite import criterion_status, pick_test_runs


def test_pick_first_fail_is_fix_next_is_heldout():
    verdicts = [("a", "pass"), ("b", "fail"), ("c", "not_applicable"), ("d", "fail"), ("e", "fail")]
    assert pick_test_runs(verdicts) == [("b", "fix"), ("d", "heldout")]


def test_pick_single_fail_only_fix():
    assert pick_test_runs([("a", "pass"), ("b", "fail")]) == [("b", "fix")]


def test_pick_none():
    assert pick_test_runs([("a", "pass"), ("b", "not_applicable")]) == []
    assert pick_test_runs([]) == []


@pytest.mark.parametrize(
    "verdicts,expected",
    [
        (["fail", "pass"], "failing"),
        (["pass", None], "untested"),
        (["pass", "not_applicable"], "passing"),
        (["not_applicable"], "untested"),
        ([], "untested"),
        ([None], "untested"),
        (["fail", None], "failing"),
        (["pass", "pass"], "passing"),
    ],
)
def test_criterion_status(verdicts, expected):
    assert criterion_status(verdicts) == expected
