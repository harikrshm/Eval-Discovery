"""Alert rules: pure thresholds over a snapshot of current and previous-period numbers."""

import dataclasses

import pytest

from backend.core.assist.alerts import THRESHOLDS, Alert, compute_alerts

QUIET = {
    "includes_demo": False,
    "calls": 100,
    "error_rate": 0.0,
    "chat_calls": 100,
    "p95_ms": 1000,
    "questions": 100,
    "reask_rate": 0.10,
    "prev_questions": 100,
    "prev_reask_rate": 0.10,
    "open_flagged": 0,
    "judge_waiting": 0,
}
UI_TABS = {"overview", "adoption", "quality", "evolution", "operations"}
UI_ANCHORS = {"ops-latency", "ops-errors", "quality-judge", "evolution-loop", "adoption-reasks"}


def ids(**changes):
    return [alert.id for alert in compute_alerts({**QUIET, **changes})]


def test_thresholds_are_one_dict_with_the_agreed_values():
    assert THRESHOLDS == {
        "error_rate": 0.05,
        "min_calls": 20,
        "p95_latency_ms": 8000,
        "reask_rate_rise_pts": 5,
        "min_questions": 20,
        "flagged_backlog": 5,
        "judge_backlog": 10,
    }


def test_a_quiet_snapshot_has_no_alerts():
    assert compute_alerts(QUIET) == []


@pytest.mark.parametrize(
    "changes, expected",
    [
        # error rate: at the threshold fires, just under does not
        ({"error_rate": 0.05}, ["error_rate"]),
        ({"error_rate": 0.0499}, []),
        # ...and never on too few calls
        ({"error_rate": 1.0, "calls": 19}, []),
        ({"error_rate": 1.0, "calls": 20}, ["error_rate"]),
        # p95 latency
        ({"p95_ms": 8000}, ["p95_latency"]),
        ({"p95_ms": 7999}, []),
        ({"p95_ms": 60000, "chat_calls": 19}, []),
        # re-ask rise in percentage points, needs enough questions in both periods
        ({"reask_rate": 0.15}, ["reask_rise"]),
        ({"reask_rate": 0.149}, []),
        ({"reask_rate": 0.9, "questions": 19}, []),
        ({"reask_rate": 0.9, "prev_questions": 19}, []),
        ({"reask_rate": 0.9, "prev_questions": None, "prev_reask_rate": None}, []),
        # a fall is never an alert
        ({"reask_rate": 0.0, "prev_reask_rate": 0.9}, []),
        # backlogs
        ({"open_flagged": 5}, ["flagged_backlog"]),
        ({"open_flagged": 4}, []),
        ({"judge_waiting": 10}, ["judge_backlog"]),
        ({"judge_waiting": 9}, []),
        # several at once come out in a fixed order
        ({"error_rate": 0.5, "p95_ms": 9000, "open_flagged": 9}, ["error_rate", "p95_latency", "flagged_backlog"]),
    ],
)
def test_alert_rules_fire_and_stay_quiet(changes, expected):
    assert ids(**changes) == expected


@pytest.mark.parametrize(
    "changes",
    [
        {"error_rate": None, "calls": 0},
        {"error_rate": None},
        {"p95_ms": None},
        {"p95_ms": None, "chat_calls": 0},
        {"reask_rate": None, "questions": 0},
        {"prev_reask_rate": None},
        {"open_flagged": None},
        {"judge_waiting": None},
        {"calls": None, "chat_calls": None, "questions": None},
    ],
)
def test_none_inputs_never_alert(changes):
    assert ids(**changes) == []


def test_empty_snapshot_never_alerts():
    assert compute_alerts({}) == []


def test_reask_boundary_is_not_lost_to_float_noise():
    assert ids(reask_rate=0.30, prev_reask_rate=0.25) == ["reask_rise"]  # exactly 5 points


def test_thresholds_can_be_overridden():
    custom = {**THRESHOLDS, "flagged_backlog": 2}
    assert [a.id for a in compute_alerts({**QUIET, "open_flagged": 2}, custom)] == ["flagged_backlog"]


def test_severity_is_critical_at_twice_the_threshold():
    by_id = {a.id: a for a in compute_alerts({**QUIET, "error_rate": 0.10, "p95_ms": 8000, "open_flagged": 10})}
    assert by_id["error_rate"].severity == "critical"
    assert by_id["p95_latency"].severity == "warning"
    assert by_id["flagged_backlog"].severity == "critical"


def test_alerts_point_at_real_ui_anchors_and_carry_fixed_text():
    snapshot = {
        **QUIET, "error_rate": 0.5, "p95_ms": 9000, "reask_rate": 0.5, "open_flagged": 9, "judge_waiting": 11,
    }
    alerts = compute_alerts(snapshot)
    assert [a.id for a in alerts] == ["error_rate", "p95_latency", "reask_rise", "flagged_backlog", "judge_backlog"]
    for alert in alerts:
        assert isinstance(alert, Alert)
        assert alert.tab in UI_TABS and alert.anchor in UI_ANCHORS
        assert alert.severity in ("warning", "critical")
        assert alert.title and alert.detail
    assert alerts[0].title == "Error rate 50.0%"
    with pytest.raises(dataclasses.FrozenInstanceError):
        alerts[0].title = "x"  # type: ignore[misc]


def test_alert_from_demo_rows_is_labelled_demo():
    live = compute_alerts({**QUIET, "error_rate": 0.5})
    demo = compute_alerts({**QUIET, "error_rate": 0.5, "includes_demo": True})
    assert [a.demo for a in live] == [False]
    assert [a.demo for a in demo] == [True]


def test_judge_backlog_is_live_data_and_never_labelled_demo():
    alerts = compute_alerts({**QUIET, "includes_demo": True, "judge_waiting": 12, "open_flagged": 6})
    assert {a.id: a.demo for a in alerts} == {"flagged_backlog": True, "judge_backlog": False}
