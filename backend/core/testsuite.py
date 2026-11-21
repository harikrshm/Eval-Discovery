"""Pure test-suite rules: which saved runs become tests, and criterion status.

Pure core module: no I/O, no LLM calls.
"""


def pick_test_runs(verdicts: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """From (run_id, verdict) pairs in position order, pick up to two failing runs.

    The first failing run becomes the "fix" test, the next the "heldout" test.
    """
    kinds = ("fix", "heldout")
    picked: list[tuple[str, str]] = []
    for run_id, verdict in verdicts:
        if verdict != "fail":
            continue
        picked.append((run_id, kinds[len(picked)]))
        if len(picked) == len(kinds):
            break
    return picked


def criterion_status(current_verdicts: list[str | None]) -> str:
    """Status from each test's current replay verdict (None = no current replay).

    Any fail -> failing; else any test without a current replay -> untested;
    else at least one pass -> passing; else untested.
    """
    if "fail" in current_verdicts:
        return "failing"
    if any(v is None for v in current_verdicts):
        return "untested"
    if "pass" in current_verdicts:
        return "passing"
    return "untested"
