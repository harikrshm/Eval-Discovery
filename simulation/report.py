"""Results page generator: ``python -m simulation.report --results DIR --mode real|mock [--output FILE]``.

Reads the trial files and ``run-meta.json`` that ``simulation.run`` saved under ``DIR/<mode>`` and
renders a markdown page. Every table is labelled simulated and names N, the models and the date.
Non-converged trials are counted at the preview cap in the statistics and listed separately. Path
counts are shown only for the flywheel arm and only among its converged trials. Mock results carry
a banner and can never be written to ``docs/simulation-results.md``.
"""

import argparse
import json
import sys
from pathlib import Path

from simulation.arms import MAX_PREVIEWS, TrialResult

REPO = Path(__file__).resolve().parent.parent
DEFAULT_REAL_OUTPUT = REPO / "docs" / "simulation-results.md"
MOCK_BANNER = "MOCK MODE: harness test only, the numbers are meaningless."
DISCLAIMER = (
    "These are simulated figures and are not measurements of real builders; "
    "they are not compared with the strategy document's illustrative targets."
)
ARM_ORDER = ("baseline", "flywheel")

LIMITATIONS = (
    "Tiny N: a handful of trials per scenario and arm, so medians move a lot between runs and no difference here is statistically established.",
    "An LLM-played builder, not a human: the customer messages and the baseline's prompt rewrites come from a model, so they are more patient and more literal than a real builder.",
    "The same model family plays the builder and powers the agent, which can make the conversations easier or more predictable than real ones.",
    "The reference judge shares the model family with the app's own judge (both judge calls use the same judge model), so the two are not independent in kind; in mock mode independence is structural only.",
    "The baseline rewriter and the customer-message builder (in both arms) are shown the full goal criterion, as are the flywheel's fallback rewrites; only the app-driven fix steps never see it. This favours the baseline.",
    "Free-tier pacing: calls are paced to the tokens-per-minute limit, so wall times describe the pacing as much as the work.",
    "The flywheel arm gets one attempt at the app flow and then falls back to manual rewrites, so a fallback trial starts roughly two previews behind the baseline.",
    "The app limits each workspace to 30 jobs per hour; every trial uses a fresh workspace so the limit does not bind inside a trial.",
)


def median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        raise ValueError("median of no values")
    mid = n // 2
    return float(ordered[mid]) if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _load(folder: Path, mode: str) -> tuple[list[TrialResult], dict]:
    if not folder.is_dir():
        raise FileNotFoundError(f"no results directory at {folder}")
    meta: dict = {}
    meta_path = folder / "run-meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            raise ValueError("run-meta.json is not a JSON object")
        if meta.get("mode") and meta["mode"] != mode:
            raise ValueError(f"run-meta.json says mode {meta['mode']!r} but the report was asked for mode {mode!r}")
    elif mode == "real":
        raise ValueError("run-meta.json is missing, so a real-mode report cannot state its models and date honestly")
    trials = [
        TrialResult.from_dict(json.loads(p.read_text(encoding="utf-8")))
        for p in sorted(folder.glob("*.json"))
        if p.name != "run-meta.json"
    ]
    if not trials:
        raise FileNotFoundError(f"no trial results in {folder}")
    return trials, meta


def _effective_previews(trial: TrialResult, cap: int) -> int:
    return trial.previews if trial.converged else cap


def _seconds(total: float) -> str:
    return f"{total:.1f} s"


def _models(meta: dict) -> str:
    models = meta.get("models") or {}
    if not models:
        return "not recorded"
    return ", ".join(f"{role}: {name}" for role, name in models.items())


def _scenario_order(trials: list[TrialResult], meta: dict) -> list[str]:
    seen = [str(x) for x in (meta.get("scenarios") or [])]
    for t in trials:
        if t.scenario not in seen:
            seen.append(t.scenario)
    return seen  # includes scenarios with no files, so they show as missing rather than vanish


def _expected_trials(trials: list[TrialResult], meta: dict) -> list[int]:
    requested = meta.get("trials")
    count = requested if isinstance(requested, int) and not isinstance(requested, bool) else max(t.trial for t in trials)
    return list(range(1, count + 1))


def _incomplete_section(trials: list[TrialResult], scenarios: list[str], meta: dict) -> list[str]:
    """Empty when every scenario and arm has exactly the requested trials; otherwise a visible warning."""
    expected = _expected_trials(trials, meta)
    rows = []
    counts = set()
    for scenario in scenarios:
        for arm in ARM_ORDER:
            on_file = {t.trial for t in trials if t.scenario == scenario and t.arm == arm}
            counts.add(len(on_file))
            missing = [n for n in expected if n not in on_file]
            if missing:
                rows.append(
                    f"- {scenario}, {arm}: {len(on_file)} of {len(expected)} trials on file; "
                    f"missing trial(s) {', '.join(str(n) for n in missing)}."
                )
    if not rows and len(counts) <= 1:
        return []
    lines = [
        "## Incomplete data",
        "",
        "**This run is incomplete or unbalanced: do not read the table as a full comparison.** "
        "A run that was stopped, or had trials that crashed, leaves trials missing. "
        "Each count below is trials on file against trials requested.",
        "",
        *rows,
    ]
    if not rows:
        lines.append(f"- The number of trials on file differs between arms or scenarios ({sorted(counts)}).")
    lines.append("")
    return lines


def _results_table(trials: list[TrialResult], scenarios: list[str], cap: int) -> list[str]:
    lines = [
        "| Scenario | Arm | N | Converged | Median previews (simulated) | Mean previews | Min | Max | Model calls | Tokens | Wall time |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for scenario in scenarios:
        for arm in ARM_ORDER:
            group = [t for t in trials if t.scenario == scenario and t.arm == arm]
            if not group:
                lines.append(f"| {scenario} | {arm} | 0 | 0 | no trials on file | - | - | - | - | - | - |")
                continue
            values = [_effective_previews(t, cap) for t in group]
            calls = sum(sum(t.model_calls.values()) for t in group)
            tokens = sum(sum(t.model_tokens.values()) for t in group)
            wall = sum(t.wall_seconds for t in group)
            lines.append(
                f"| {scenario} | {arm} | {len(group)} | {sum(t.converged for t in group)} "
                f"| {median(values):.1f} | {sum(values) / len(values):.2f} | {min(values)} | {max(values)} "
                f"| {calls} | {tokens} | {_seconds(wall)} |"
            )
    return lines


def _path_section(trials: list[TrialResult], scenarios: list[str]) -> list[str]:
    lines = [
        "## Path counts",
        "",
        "Shown for the flywheel arm only, among its converged trials. The baseline's only mechanism is manual rewrites.",
        "",
    ]
    for scenario in scenarios:
        lines.append(f"### {scenario} (simulated)")
        lines.append("")
        lines.append("- baseline: n/a (manual rewrites)")
        fly = [t for t in trials if t.scenario == scenario and t.arm == "flywheel"]
        if not fly:
            lines.append("- flywheel: no trials on file")
        else:
            converged = [t for t in fly if t.converged]
            lines.append(f"- flywheel, loop: {sum(t.path == 'loop' for t in converged)}")
            lines.append(f"- flywheel, fallback: {sum(t.path == 'fallback' for t in converged)}")
            lines.append(
                f"- flywheel, converged with no fix needed: {sum(t.path == 'none' for t in converged)} "
                "(the first preview(s) passed with no prompt change and no app fix)"
            )
            lines.append(f"- flywheel, did not converge: {len(fly) - len(converged)} (listed below, not counted under any path)")
        lines.append("")
    return lines


def _non_converged_section(trials: list[TrialResult]) -> list[str]:
    lines = ["## Trials that did not converge", ""]
    missed = [t for t in trials if not t.converged]
    if not missed:
        lines.append("None: every trial reached a judged pass within the cap.")
        lines.append("")
        return lines
    for t in sorted(missed, key=lambda t: (t.scenario, t.arm, t.trial)):
        note = f" Notes: {' '.join(t.notes)}" if t.notes else ""
        lines.append(f"- {t.scenario}, {t.arm}, trial {t.trial}: no judged pass in {t.previews} previews.{note}")
    lines.append("")
    return lines


def build_report(results_dir, mode: str) -> str:
    trials, meta = _load(Path(results_dir) / mode, mode)
    cap = int(meta.get("max_previews") or MAX_PREVIEWS)
    scenarios = _scenario_order(trials, meta)
    n = meta.get("trials", "not recorded")
    date = str(meta.get("date", "not recorded"))[:10]
    commit = meta.get("git_commit")

    lines: list[str] = []
    if mode == "mock":
        lines += [MOCK_BANNER, ""]
    lines += [
        "# Simulation results",
        "",
        f"Simulated results. N requested = {n} per scenario and arm; the N column is the number of trials on file. "
        f"Models: {_models(meta)}. Date: {date}."
        + (f" Commit: {commit}." if commit else ""),
        "",
        DISCLAIMER,
        "",
        *_incomplete_section(trials, scenarios, meta),
        "## Results",
        "",
        f"Preview runs until the first judged pass, simulated, lower is better. Trials that never passed are counted at the cap of {cap}.",
        "",
        *_results_table(trials, scenarios, cap),
        "",
        *_path_section(trials, scenarios),
        *_non_converged_section(trials),
        "## Limitations",
        "",
        *(f"- {item}" for item in LIMITATIONS),
        "",
        DISCLAIMER,
        "",
    ]
    return "\n".join(lines)


def _is_docs_page(path: Path) -> bool:
    if path.resolve() == DEFAULT_REAL_OUTPUT.resolve():
        return True
    parts = Path(str(path).replace("\\", "/")).as_posix().split("/")
    return parts[-2:] == ["docs", "simulation-results.md"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m simulation.report", description="Render the simulation results page.")
    p.add_argument("--results", required=True, help="the results directory given to simulation.run --out")
    p.add_argument("--mode", choices=("real", "mock"), default="real")
    p.add_argument("--output", default=None, help="markdown file to write (real default: docs/simulation-results.md; mock default: stdout)")
    args = p.parse_args(argv)

    output = Path(args.output) if args.output else (DEFAULT_REAL_OUTPUT if args.mode == "real" else None)
    if args.mode == "mock" and output is not None and _is_docs_page(output):
        print("Refusing to write mock results to docs/simulation-results.md: mock numbers are meaningless.", file=sys.stderr)
        return 2
    try:
        text = build_report(args.results, args.mode)
    except (FileNotFoundError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Could not build the report ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2
    if output is None:
        sys.stdout.write(text)
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
