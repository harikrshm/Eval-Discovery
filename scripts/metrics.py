"""Read-only product metrics derived from the `event` table.

Usage: python scripts/metrics.py [--database-url URL] [--format text|json]

The URL defaults to $DATABASE_URL. The database password is never printed.
Rates with a zero denominator are reported as `n/a` (null in JSON).
"""

import argparse
import json
import os
import re
import statistics
import sys
from collections import defaultdict

from sqlalchemy import column, create_engine, select, table
from sqlalchemy.engine import make_url

COUNTED_EVENTS = (
    "agent_created",
    "run_saved",
    "loop_detected",
    "criterion_created",
    "test_created",
    "replay_passed",
    "replay_failed",
    "fix_suggested",
    "fix_approved",
    "fix_rejected",
    "fix_verified",
    "fix_verification_failed",
    "fix_promoted",
)

TEST_CONVERSION_DEFINITION = (
    "test conversion = workspaces that have both a criterion and a test / "
    "workspaces with a criterion; only computed for the total"
)

DEFINITIONS = {
    "acceptance_rate": "criterion_created / loop_detected",
    "test_conversion": TEST_CONVERSION_DEFINITION,
    "fix_approval_rate": "fix_approved / fix_suggested",
    "verification_pass_rate": "fix_verified / fix_approved",
    "runs_until_first_pass": (
        "run_saved events (in created_at order) strictly before the first replay_passed, "
        "per workspace; null when the workspace has no replay_passed; the total is the "
        "median over workspaces with a value"
    ),
    "zero_denominator": "rates with a zero denominator are null (n/a in text)",
}

_SECRET_QUERY_KEY = re.compile(r"pass|secret|token|key", re.IGNORECASE)

# A lightweight view of the event table: no app (or fastapi) import needed.
_EVENT = table("event", column("id"), column("workspace_id"), column("name"), column("created_at"))


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _workspace_rates(counts: dict[str, int]) -> dict[str, float | None]:
    return {
        "acceptance_rate": _ratio(counts["criterion_created"], counts["loop_detected"]),
        "fix_approval_rate": _ratio(counts["fix_approved"], counts["fix_suggested"]),
        "verification_pass_rate": _ratio(counts["fix_verified"], counts["fix_approved"]),
    }


def compute_metrics(rows) -> dict:
    """rows: iterable of (workspace_id, name, created_at), tied timestamps keep input order."""
    ordered = sorted(rows, key=lambda r: r[2])  # stable: ties keep the caller's (id) order
    counts: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(COUNTED_EVENTS, 0))
    runs_before_pass: dict[str, int] = {}
    runs_seen: dict[str, int] = defaultdict(int)
    for workspace_id, name, _created_at in ordered:
        if name not in COUNTED_EVENTS:
            continue
        counts[workspace_id][name] += 1
        if name == "run_saved":
            runs_seen[workspace_id] += 1
        elif name == "replay_passed" and workspace_id not in runs_before_pass:
            runs_before_pass[workspace_id] = runs_seen[workspace_id]

    workspaces = {}
    for workspace_id in sorted(counts):
        workspaces[workspace_id] = {
            "counts": counts[workspace_id],
            "rates": _workspace_rates(counts[workspace_id]),
            "runs_until_first_pass": runs_before_pass.get(workspace_id),
        }

    totals = dict.fromkeys(COUNTED_EVENTS, 0)
    for per_workspace in counts.values():
        for name, n in per_workspace.items():
            totals[name] += n
    with_criterion = sum(1 for c in counts.values() if c["criterion_created"])
    with_test_and_criterion = sum(
        1 for c in counts.values() if c["criterion_created"] and c["test_created"]
    )
    total_rates = _workspace_rates(totals)
    total_rates["test_conversion"] = _ratio(with_test_and_criterion, with_criterion)
    values = list(runs_before_pass.values())
    return {
        "totals": {
            "counts": totals,
            "rates": {
                "acceptance_rate": total_rates["acceptance_rate"],
                "test_conversion": total_rates["test_conversion"],
                "fix_approval_rate": total_rates["fix_approval_rate"],
                "verification_pass_rate": total_rates["verification_pass_rate"],
            },
            "runs_until_first_pass": {
                "median": statistics.median(values) if values else None,
                "workspaces_with_value": len(values),
            },
        },
        "workspaces": workspaces,
    }


def mask_url(url: str) -> str:
    """Render a database URL with its password hidden."""
    try:
        parsed = make_url(url)
        query = {
            key: ("***" if _SECRET_QUERY_KEY.search(key) else value)
            for key, value in parsed.query.items()
        }
        return parsed.set(query=query).render_as_string(hide_password=True)
    except Exception:
        return "<unparseable database url>"


def _fmt_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _fmt_num(value) -> str:
    if value is None:
        return "n/a"
    return f"{value:g}" if isinstance(value, float) else str(value)


def format_text(metrics: dict, database: str | None = None) -> str:
    lines = []
    if database:
        lines.append(f"Database: {database}")
        lines.append("")
    ids = list(metrics["workspaces"])
    columns = ["total"] + ids
    cells = {"total": metrics["totals"]}
    cells.update(metrics["workspaces"])
    name_width = max(len("runs until first pass"), *(len(n) for n in COUNTED_EVENTS))
    widths = [max(len(c), 8) for c in columns]

    def row(label: str, values: list[str]) -> str:
        parts = [label.ljust(name_width)] + [v.rjust(w) for v, w in zip(values, widths)]
        return "  ".join(parts)

    lines.append(row("event", columns))
    for name in COUNTED_EVENTS:
        lines.append(row(name, [str(cells[c]["counts"][name]) for c in columns]))
    lines.append("")
    rate_labels = [
        ("acceptance_rate", "acceptance rate"),
        ("test_conversion", "test conversion"),
        ("fix_approval_rate", "fix approval rate"),
        ("verification_pass_rate", "verification pass rate"),
    ]
    name_width = max(name_width, *(len(label) for _, label in rate_labels))
    for key, label in rate_labels:
        lines.append(row(label, [_fmt_rate(cells[c]["rates"].get(key)) for c in columns]))
    lines.append("")
    runs = metrics["totals"]["runs_until_first_pass"]
    values = [_fmt_num(runs["median"])] + [
        _fmt_num(metrics["workspaces"][i]["runs_until_first_pass"]) for i in ids
    ]
    lines.append(row("runs until first pass", values))
    lines.append(
        f"(total column = median over {runs['workspaces_with_value']} workspace(s) with a value)"
    )
    lines.append(f"({TEST_CONVERSION_DEFINITION})")
    return "\n".join(lines)


def fetch_rows(engine) -> list[tuple]:
    query = select(_EVENT.c.workspace_id, _EVENT.c.name, _EVENT.c.created_at).order_by(
        _EVENT.c.created_at, _EVENT.c.id
    )
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(query)]


def main(argv=None, env=None, engine_factory=create_engine) -> int:
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(
        description="Product metrics from the event table (read-only).",
        epilog="Definitions: " + TEST_CONVERSION_DEFINITION + ".",
    )
    parser.add_argument("--database-url", default=None, help="defaults to $DATABASE_URL")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)

    url = args.database_url or env.get("DATABASE_URL")
    if not url:
        print("error: no database URL; pass --database-url or set DATABASE_URL", file=sys.stderr)
        return 2
    masked = mask_url(url)
    engine = None
    try:
        engine = engine_factory(url)
        rows = fetch_rows(engine)
    except Exception as exc:  # never echo str(exc): it can carry connection details
        print(f"error: could not read events from {masked} ({type(exc).__name__})", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()

    metrics = compute_metrics(rows)
    if args.format == "json":
        print(json.dumps({**metrics, "definitions": DEFINITIONS}, indent=2, sort_keys=True))
    else:
        print(format_text(metrics, masked))
    return 0


if __name__ == "__main__":
    sys.exit(main())
