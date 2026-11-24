"""Console alerts: pure rules over a snapshot of the current and previous period's numbers. No I/O, no clock.

Every threshold lives in `THRESHOLDS`. A rule fires when its value is AT OR ABOVE the threshold and any
minimum sample is met; a `None` (or missing) input never alerts. A rule at twice its threshold or more is
`critical`, otherwise `warning`. Titles and details are fixed strings built from numbers only.

The snapshot keys (all optional, `None` means unknown):
  includes_demo      the numbers were computed from rows that include demo (imported) data
  calls, error_rate  calls in the period and the share of them that failed
  chat_calls, p95_ms chat calls in the period and their nearest-rank 95th percentile latency
  questions, reask_rate, prev_questions, prev_reask_rate   questions and re-ask share, this and the previous period
  open_flagged       flagged conversations nobody has checked yet
  judge_waiting      conversations waiting for the automatic judge (live data only, never demo)
"""

from dataclasses import dataclass
from typing import Literal

THRESHOLDS: dict[str, float] = {
    "error_rate": 0.05,
    "min_calls": 20,
    "p95_latency_ms": 8000,
    "reask_rate_rise_pts": 5,
    "min_questions": 20,
    "flagged_backlog": 5,
    "judge_backlog": 10,
}
CRITICAL_FACTOR = 2


@dataclass(frozen=True)
class Alert:
    id: str
    severity: Literal["warning", "critical"]
    title: str
    detail: str
    tab: str  # overview | adoption | quality | evolution | operations
    anchor: str  # a real UI anchor on that tab
    demo: bool


def _severity(value: float, threshold: float) -> Literal["warning", "critical"]:
    return "critical" if value >= CRITICAL_FACTOR * threshold else "warning"


def _num(value: object) -> float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def compute_alerts(snapshot: dict, thresholds: dict = THRESHOLDS) -> list[Alert]:
    """The alerts that fire for `snapshot`, in a fixed order (errors, latency, re-asks, flagged, judge)."""
    demo = bool(snapshot.get("includes_demo"))
    found: list[Alert] = []

    calls, rate = _num(snapshot.get("calls")), _num(snapshot.get("error_rate"))
    if calls is not None and rate is not None and calls >= thresholds["min_calls"] and rate >= thresholds["error_rate"]:
        found.append(
            Alert(
                "error_rate", _severity(rate, thresholds["error_rate"]),
                f"Error rate {rate * 100:.1f}%",
                f"{rate * 100:.1f}% of {int(calls)} model calls failed; the alert starts at "
                f"{thresholds['error_rate'] * 100:g}%.",
                "operations", "ops-errors", demo,
            )
        )

    chat, p95 = _num(snapshot.get("chat_calls")), _num(snapshot.get("p95_ms"))
    if chat is not None and p95 is not None and chat >= thresholds["min_calls"] and p95 >= thresholds["p95_latency_ms"]:
        found.append(
            Alert(
                "p95_latency", _severity(p95, thresholds["p95_latency_ms"]),
                f"Slow answers: p95 {int(p95)} ms",
                f"The slowest 5% of {int(chat)} chat calls took {int(p95)} ms or more; the alert starts at "
                f"{int(thresholds['p95_latency_ms'])} ms.",
                "operations", "ops-latency", demo,
            )
        )

    questions, before = _num(snapshot.get("questions")), _num(snapshot.get("prev_questions"))
    now_rate, prev_rate = _num(snapshot.get("reask_rate")), _num(snapshot.get("prev_reask_rate"))
    if (
        None not in (questions, before, now_rate, prev_rate)
        and questions >= thresholds["min_questions"]
        and before >= thresholds["min_questions"]
    ):
        rise = round((now_rate - prev_rate) * 100, 6)  # rounding keeps an exact 5-point rise from missing by float noise
        if rise >= thresholds["reask_rate_rise_pts"]:
            found.append(
                Alert(
                    "reask_rise", _severity(rise, thresholds["reask_rate_rise_pts"]),
                    f"Re-asks up {rise:.1f} points",
                    f"{now_rate * 100:.1f}% of {int(questions)} questions were asked again, against "
                    f"{prev_rate * 100:.1f}% in the previous period.",
                    "adoption", "adoption-reasks", demo,
                )
            )

    flagged = _num(snapshot.get("open_flagged"))
    if flagged is not None and flagged >= thresholds["flagged_backlog"]:
        n = int(flagged)
        found.append(
            Alert(
                "flagged_backlog", _severity(flagged, thresholds["flagged_backlog"]),
                f"{n} flagged {_plural(n, 'conversation', 'conversations')} waiting",
                f"{n} flagged {_plural(n, 'conversation has', 'conversations have')} not been checked for a pattern yet.",
                "evolution", "evolution-loop", demo,
            )
        )

    waiting = _num(snapshot.get("judge_waiting"))
    if waiting is not None and waiting >= thresholds["judge_backlog"]:
        n = int(waiting)
        found.append(
            Alert(
                "judge_backlog", _severity(waiting, thresholds["judge_backlog"]),
                f"{n} conversations waiting to be judged",
                f"{n} conversations have no automatic judgement yet; the alert starts at "
                f"{int(thresholds['judge_backlog'])}.",
                "quality", "quality-judge", False,  # the judge only ever sees live conversations
            )
        )
    return found
