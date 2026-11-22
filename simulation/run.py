"""Resumable runner: ``python -m simulation.run [--mode mock|real] [--trials N] [--scenarios a,b] ...``.

Import order matters. ``GROQ_SKIP_DOTENV`` is set before anything can import ``backend.groq_client``,
so a ``.env`` file is never read unless ``--load-dotenv`` asks for it.

Trials run interleaved (scenario, trial, arm) so a partial run stays balanced, and each finished trial
is written at once to ``DIR/<mode>/<scenario>-<arm>-<trial>.json`` next to ``run-meta.json``, so a run
that is stopped can be continued with ``--resume``. Real mode needs ``GROQ_API_KEY`` in the
environment; the key is never printed (every message here is fixed text, a count or an exception
class name).
"""

import os

if not os.environ.get("GROQ_SKIP_DOTENV"):  # an empty value counts as unset, as in backend.groq_client
    os.environ["GROQ_SKIP_DOTENV"] = "1"

import argparse  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from collections.abc import Mapping  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

import backend.groq_client as groq_client  # noqa: E402
from backend.groq_client import configure_pacing  # noqa: E402
from evalgate.cli import KEY_NAME, _sync_process_key, _valid_key  # noqa: E402
from simulation import arms  # noqa: E402
from simulation.arms import MAX_PREVIEWS, TrialResult  # noqa: E402
from simulation.clients import install_client  # noqa: E402
from simulation.fakeclient import Behavior, FakeGroq  # noqa: E402
from simulation.scenarios import SCENARIOS, Scenario  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = Path("simulation") / "results"
DEFAULT_TPM_LIMIT = 7000
ARM_NAMES = ("baseline", "flywheel")

EXIT_OK = 0
EXIT_SOME_TRIALS_CRASHED = 1
EXIT_USAGE = 2
EXIT_COULD_NOT_RUN = 3

MOCK_BANNER = "MOCK MODE: harness test only, the numbers are meaningless."

# Rough estimate, documented here and printed with the estimate. Per preview: 2 customer messages, 2
# agent replies and 1 reference judgement (5 calls), and every failing preview adds 1 prompt rewrite.
# Assume a trial needs about ASSUMED_PREVIEWS previews. The flywheel arm adds the app flow (run
# judging, pattern check, criterion and test generation, replays, held-out, suggestion, verification),
# about FLYWHEEL_APP_CALLS calls. Minutes are calls x ASSUMED_TOKENS_PER_CALL / the tokens-per-minute limit.
ASSUMED_PREVIEWS = 5
CALLS_PER_PREVIEW = 5
FLYWHEEL_APP_CALLS = 30
ASSUMED_TOKENS_PER_CALL = 1200


@dataclass(frozen=True)
class Estimate:
    calls: int
    minutes: float


def estimate(*, trials: int, scenarios: int, max_previews: int, tpm_limit: int) -> Estimate:
    previews = min(ASSUMED_PREVIEWS, max_previews)
    per_arm = previews * CALLS_PER_PREVIEW + (previews - 1)
    per_pair = 2 * per_arm + FLYWHEEL_APP_CALLS
    calls = trials * scenarios * per_pair
    return Estimate(calls, calls * ASSUMED_TOKENS_PER_CALL / tpm_limit)


def fake_behavior(scenario: Scenario) -> Behavior:
    return Behavior(scenario.guardrail_marker, scenario.violation_text, rewrites_needed=2, suggest_works=True)


def make_fake_client(scenario: Scenario) -> FakeGroq:
    return FakeGroq(fake_behavior(scenario))


def _load_dotenv() -> None:
    from dotenv import load_dotenv

    load_dotenv()


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number of 1 or more") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be a whole number of 1 or more")
    return value


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m simulation.run",
        description="Run the baseline and flywheel arms and save every trial as it finishes.",
    )
    p.add_argument("--mode", choices=("mock", "real"), default="mock", help="mock (offline fake model, the default) or real (Groq)")
    p.add_argument("--trials", type=_positive_int, default=3, help="trials per scenario and arm (default 3)")
    p.add_argument("--scenarios", default=None, help="comma-separated scenario names (default: all)")
    p.add_argument("--max-previews", type=_positive_int, default=MAX_PREVIEWS, help=f"preview cap per trial (default {MAX_PREVIEWS})")
    p.add_argument("--out", default=str(DEFAULT_OUT), help="results directory (default simulation/results)")
    p.add_argument("--resume", action="store_true", help="skip trials whose result file already exists")
    p.add_argument("--load-dotenv", action="store_true", help="read .env before checking GROQ_API_KEY: load_dotenv() searches upward from the simulation package, so it finds the repo-root .env (off by default)")
    return p


def _git_commit() -> str | None:
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True, timeout=10, check=False
        )
    except Exception:
        return None
    sha = done.stdout.strip()
    return sha if done.returncode == 0 and sha else None


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _meta(mode: str, trials: int, scenarios: list[str], max_previews: int, pacing: int | None) -> dict:
    return {
        "mode": mode,
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trials": trials,
        "scenarios": scenarios,
        "models": {
            "chat": groq_client.MODEL_CHAT,
            "judge": groq_client.MODEL_JUDGE,
            "pattern": groq_client.MODEL_PATTERN,
            "criteria": groq_client.MODEL_CRITERIA,
        },
        "max_previews": max_previews,
        "git_commit": _git_commit(),
        "pacing_tpm_limit": pacing,
    }


def _run_one(mode: str, scenario: Scenario, arm: str, trial: int, max_previews: int) -> TrialResult:
    runner = arms.run_baseline if arm == "baseline" else arms.run_flywheel
    if mode == "mock":
        with install_client(make_fake_client(scenario)):
            return runner(scenario, trial=trial, max_previews=max_previews)
    return runner(scenario, trial=trial, max_previews=max_previews)


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    args = _parser().parse_args(argv)
    env = os.environ if env is None else env

    names = list(SCENARIOS) if args.scenarios is None else [n.strip() for n in args.scenarios.split(",") if n.strip()]
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown or not names:
        print(f"Unknown scenario(s): {', '.join(unknown) or '(none given)'}. Known: {', '.join(SCENARIOS)}.", file=sys.stderr)
        return EXIT_USAGE

    pacing: int | None = None
    if args.mode == "real":
        if args.load_dotenv:
            _load_dotenv()
        key = _valid_key(env)
        if key is None:
            print(f"{KEY_NAME} is not set.", file=sys.stderr)
            return EXIT_COULD_NOT_RUN
        _sync_process_key(key)
        try:
            pacing = int(env.get("GROQ_TPM_LIMIT") or DEFAULT_TPM_LIMIT)
        except ValueError:
            print("GROQ_TPM_LIMIT must be a whole number.", file=sys.stderr)
            return EXIT_USAGE
        if pacing < 1:
            print("GROQ_TPM_LIMIT must be a whole number of 1 or more.", file=sys.stderr)
            return EXIT_USAGE
        est = estimate(trials=args.trials, scenarios=len(names), max_previews=args.max_previews, tpm_limit=pacing)
        print(
            f"ESTIMATE (rough, assumes about {ASSUMED_PREVIEWS} previews per trial): about {est.calls} model calls "
            f"and about {est.minutes:.0f} minutes at {pacing} tokens per minute "
            f"({args.trials} trials x {len(names)} scenarios x 2 arms)."
        )
        configure_pacing(pacing)
    else:
        print(MOCK_BANNER)
        configure_pacing(None)

    folder = Path(args.out) / args.mode
    folder.mkdir(parents=True, exist_ok=True)
    _write_json(folder / "run-meta.json", _meta(args.mode, args.trials, names, args.max_previews, pacing))

    total = len(names) * args.trials * len(ARM_NAMES)
    done = crashed = 0
    try:
        for name in names:
            scenario = SCENARIOS[name]
            for trial in range(1, args.trials + 1):
                for arm in ARM_NAMES:
                    done += 1
                    label = f"[{done}/{total}] {name} {arm} trial {trial}"
                    target = folder / f"{name}-{arm}-{trial}.json"
                    if args.resume and target.exists():
                        print(f"{label}: skipped (already saved)")
                        continue
                    try:
                        result = _run_one(args.mode, scenario, arm, trial, args.max_previews)
                        _write_json(target, result.to_dict())
                    except Exception as exc:  # the arms never raise; guard anyway and report the class only
                        crashed += 1
                        print(f"{label}: crashed ({type(exc).__name__}), not recorded", file=sys.stderr)
                        continue
                    state = "converged" if result.converged else "did not converge"
                    print(f"{label}: {state} after {result.previews} previews, path {result.path}, {result.wall_seconds:.1f}s")
    finally:
        if args.mode == "real":
            configure_pacing(None)
    if crashed:
        print(f"{crashed} trial(s) crashed and were not recorded; re-run with --resume to retry them.", file=sys.stderr)
        return EXIT_SOME_TRIALS_CRASHED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
