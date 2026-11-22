"""Command line for the CI gate: `python -m evalgate --suite suite.json --prompt prompt.txt`.

Import order matters. `GROQ_SKIP_DOTENV` is set before anything can import `backend.groq_client`, so a
`.env` file is never read unless `--load-dotenv` asks for it. The runner (and with it the Groq client)
is imported only after the API key has been validated and made consistent with `os.environ`.

The key is read only from the environment mapping, and no output path ever formats it: every message
this module prints is fixed text, a count, or a value that came from the suite file or the runner's
fixed error strings.
"""

import os

if not os.environ.get("GROQ_SKIP_DOTENV"):  # an empty value counts as unset, as in backend.groq_client
    os.environ["GROQ_SKIP_DOTENV"] = "1"

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import sys  # noqa: E402
from collections.abc import Mapping  # noqa: E402

from evalgate.suite import SuiteError, load_prompt, load_suite, select_tests  # noqa: E402

KEY_NAME = "GROQ_API_KEY"
# backend/groq_client.py falls back to this when no key is set; .env.example ships the second one.
PLACEHOLDER_KEYS = frozenset({"sk-test-placeholder", "your_groq_api_key_here"})

EXIT_PASS = 0
EXIT_BELOW_THRESHOLD = 1
EXIT_USAGE = 2
EXIT_COULD_NOT_RUN = 3

EPILOG = """\
exit codes:
  0  every graded test passed at or above the threshold
  1  the pass rate is below the threshold
  2  bad usage: invalid arguments, or a missing or invalid suite or prompt file
  3  the gate could not run: GROQ_API_KEY not set, a test errored, no runnable tests,
     or no test was applicable (the gate never reports success when it could not run)

The key is read only from the GROQ_API_KEY environment variable and is never printed.
A .env file is not read unless --load-dotenv is given.
"""

_LABELS = {"pass": "PASS", "fail": "FAIL", "not_applicable": "N/A", "error": "ERROR"}
_STATUS = {EXIT_PASS: "PASSED", EXIT_BELOW_THRESHOLD: "FAILED", EXIT_COULD_NOT_RUN: "COULD NOT RUN"}


def _threshold(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number between 0 and 1") from None
    if not 0.0 <= value <= 1.0:  # also rejects nan
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return value


def _repeat(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer from 1 to 9") from None
    if not 1 <= value <= 9:
        raise argparse.ArgumentTypeError("must be an integer from 1 to 9")
    return value


def _pause(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number of seconds, 0 or more") from None
    if not (math.isfinite(value) and value >= 0):
        raise argparse.ArgumentTypeError("must be a number of seconds, 0 or more")
    return value


def _max_tests(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer of 1 or more") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be an integer of 1 or more")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evalgate",
        description="Replay an exported Eval Discovery suite against a prompt and gate on the pass rate.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--suite", required=True, metavar="FILE", help="exported eval-discovery.suite JSON file")
    parser.add_argument("--prompt", required=True, metavar="FILE", help="the agent prompt to test, as a text file")
    parser.add_argument("--threshold", type=_threshold, default=1.0, metavar="FLOAT",
                        help="minimum pass rate from 0 to 1 (default: 1.0)")
    parser.add_argument("--format", choices=("text", "json", "markdown"), default="text",
                        help="output format on stdout (default: text)")
    parser.add_argument("--repeat", type=_repeat, default=1, metavar="K",
                        help="runs per test, 1 to 9; a test passes on a strict majority (default: 1)")
    parser.add_argument("--pause", type=_pause, default=0.0, metavar="SECONDS",
                        help="seconds to wait between tests (default: 0)")
    parser.add_argument("--max-tests", type=_max_tests, default=None, metavar="N",
                        help="run at most the first N selected tests")
    parser.add_argument("--all", action="store_true", dest="include_all",
                        help="also run tests whose verdict at export was not pass")
    parser.add_argument("--load-dotenv", action="store_true",
                        help="load the first .env found by searching upward from the working directory, before the key check (off by default)")
    return parser


def _clean(text: object) -> str:
    """One printable line: control characters and line breaks become spaces."""
    return " ".join("".join(c if c.isprintable() else " " for c in str(text)).split())


def _cell(text: object) -> str:
    """A markdown table cell that cannot break out of its row."""
    cleaned = _clean(text).replace("\\", "\\\\")
    for char in "|[]`":
        cleaned = cleaned.replace(char, "\\" + char)
    return cleaned.replace("<", "&lt;").replace(">", "&gt;")


def _counts(result) -> dict[str, int]:
    return {
        "pass": result.passes,
        "fail": result.fails,
        "not_applicable": result.not_applicable,
        "error": result.errors,
    }


def _summary_line(result, threshold: float, code: int) -> str:
    graded = result.passes + result.fails
    return f"pass rate {result.passes} of {graded} (threshold {threshold:g}): {_STATUS[code]}"


def format_text(result, threshold: float, code: int) -> str:
    lines = []
    for o in result.outcomes:
        line = f"{_LABELS[o.verdict]}  {_clean(o.role)}  {_clean(o.criterion_name)}"
        if o.error:
            line += f" ({_clean(o.error)})"
        lines.append(line)
    lines.append(_summary_line(result, threshold, code))
    return "\n".join(lines)


def format_json(result, threshold: float, code: int) -> str:
    tests = [
        {
            "id": o.test_id,
            "criterion": o.criterion_name,
            "role": o.role,
            "verdict": o.verdict,
            "runs": list(o.runs),
            "rationale": o.rationale,
            "error": o.error,
        }
        for o in result.outcomes
    ]
    document = {
        "pass_rate": result.pass_rate,
        "threshold": threshold,
        "exit_code": code,
        "counts": _counts(result),
        "skipped": result.skipped,
        "tests": tests,
    }
    return json.dumps(document, indent=2)


def format_markdown(result, threshold: float, code: int) -> str:
    counts = _counts(result)
    lines = [
        "# Eval gate",
        "",
        f"**{_summary_line(result, threshold, code)}**",
        "",
        f"{counts['pass']} passed, {counts['fail']} failed, {counts['not_applicable']} not applicable, "
        f"{counts['error']} errored, {result.skipped} skipped.",
        "",
        "| Verdict | Role | Criterion | Rationale |",
        "| --- | --- | --- | --- |",
    ]
    for o in result.outcomes:
        detail = o.error if o.error else o.rationale
        lines.append(f"| {_LABELS[o.verdict]} | {_cell(o.role)} | {_cell(o.criterion_name)} | {_cell(detail)} |")
    return "\n".join(lines)


_FORMATTERS = {"text": format_text, "json": format_json, "markdown": format_markdown}


def _emit(fmt: str, result, threshold: float, code: int, env: Mapping[str, str]) -> None:
    """Write the step summary, then print. Each is best effort and neither can change the exit code."""
    summary_path = env.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write("\n" + format_markdown(result, threshold, code) + "\n")
        except Exception:
            _err("Could not write to GITHUB_STEP_SUMMARY.")
    try:
        print(_FORMATTERS[fmt](result, threshold, code))
        sys.stdout.flush()
    except Exception:
        _err("Could not write the result to stdout.")


def _err(message: str) -> None:
    try:
        print(message, file=sys.stderr)
    except Exception:  # a broken or unencodable stderr must not change the exit code
        pass


def _lenient_streams() -> None:
    """Make stdout and stderr replace what the console cannot encode instead of raising.

    A Windows console or a pipe on cp1252 cannot encode model text such as arrows or curly quotes.
    Streams that cannot be reconfigured (some capture objects) are left as they are.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


def _valid_key(env: Mapping[str, str]) -> str | None:
    key = str(env.get(KEY_NAME) or "").strip()
    return key if key and key not in PLACEHOLDER_KEYS else None


def _sync_process_key(key: str) -> None:
    """Make os.environ and an already-built Groq client agree with the key that was validated.

    backend.groq_client reads os.environ at import time. When `env` was an explicit mapping the process
    environment may differ, so set it before the first import, and rebuild the client if the module was
    already imported (as happens in-process, e.g. under test) with another key.
    """
    if os.environ.get(KEY_NAME) != key:
        os.environ[KEY_NAME] = key
    groq_client = sys.modules.get("backend.groq_client")
    if groq_client is not None and getattr(groq_client.client, "api_key", None) != key:
        groq_client.client = groq_client._make_client(key)


def _skip_message(skipped: int) -> str:
    return (
        f"{skipped} skipped: their verdict at export was not pass "
        "(they were failing or never graded). Use --all to run them too."
    )


def _run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    try:
        suite = load_suite(args.suite)
        prompt_text = load_prompt(args.prompt)
    except SuiteError as exc:
        _err(f"evalgate: {exc}")
        return EXIT_USAGE

    selected, skipped = select_tests(suite, args.include_all)
    if skipped:
        _err(_skip_message(skipped))
    if not selected:
        _err(f"No runnable tests: {skipped} skipped, nothing left to run. Use --all to include them.")
        return EXIT_COULD_NOT_RUN

    if args.load_dotenv:
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))

    key = _valid_key(env)
    if key is None:
        _err("GROQ_API_KEY is not set.")
        return EXIT_COULD_NOT_RUN
    _sync_process_key(key)

    from evalgate import runner  # after the key is in place: this import builds the Groq client

    result = runner.run_suite(
        selected,
        agent_name=suite.agent_name,
        user_role_label=suite.user_role_label,
        prompt_text=prompt_text,
        repeat=args.repeat,
        pause=args.pause,
        max_tests=args.max_tests,
        skipped=skipped,
    )
    code = runner.exit_code(result, args.threshold)

    not_run = len(selected) - len(result.outcomes)
    if not_run > 0:
        _err(f"Warning: --max-tests {args.max_tests} limited the run; {not_run} tests were not run.")
    if code == EXIT_COULD_NOT_RUN:
        if result.errors:
            _err(f"{result.errors} test(s) errored, so the gate could not run to completion.")
        else:
            _err("No test was applicable, so there is no pass rate.")

    _emit(args.format, result, args.threshold, code, env)
    return code


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    _lenient_streams()
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse: 0 for --help, 2 for a usage error
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    try:
        return _run(args, env)
    except Exception:  # never a traceback (it could carry secrets), never exit 0 or 1
        _err("evalgate: unexpected error; the gate could not run.")
        return EXIT_COULD_NOT_RUN
