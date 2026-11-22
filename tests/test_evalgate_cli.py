"""The evalgate command line: exit codes, output formats and the never-leak-the-key rule. No network."""

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.core.judge import JudgeFailed, JudgeResult
from evalgate.cli import main

REPO = Path(__file__).resolve().parent.parent
KEY = "gsk_UNIQUE-KEY-MARKER-7f3a9"
TRANSCRIPT = [{"role": "user", "content": "x"}]


def _test(tid, verdict="pass", role="regression"):
    return {
        "id": tid,
        "role": role,
        "origin": "run",
        "scripted_user_messages": ["hi", "there"],
        "current_verdict": verdict,
    }


def _doc(tests=None, criterion_name="Answers pricing"):
    tests = tests if tests is not None else [_test("t1"), _test("t2")]
    return {
        "format": "eval-discovery.suite",
        "version": 1,
        "agent": {"name": "Alex", "user_role_label": "Prospect"},
        "prompt": {"text": "You are Alex.", "version_number": 3},
        "criteria": [
            {
                "id": "c1",
                "name": criterion_name,
                "definition": "def",
                "good_example": "good",
                "bad_example": "bad",
                "tests": tests,
            }
        ],
    }


@pytest.fixture(autouse=True)
def _isolate_key_state(monkeypatch):
    """main() may set GROQ_API_KEY and rebuild the Groq client; restore both after each test."""
    from backend import groq_client

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(groq_client, "client", groq_client.client)


@pytest.fixture
def files(tmp_path):
    suite = tmp_path / "suite.json"
    prompt = tmp_path / "prompt.txt"
    suite.write_text(json.dumps(_doc()), encoding="utf-8")
    prompt.write_text("You are Alex, a careful agent.", encoding="utf-8")
    return suite, prompt


def write_suite(tmp_path, doc):
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def run_cli(suite, prompt, *extra, env=None, judge_results=None, replay_side_effect=None):
    """Run main() with replay/judge patched; returns (exit code, replay mock, judge mock)."""
    env = {"GROQ_API_KEY": KEY} if env is None else env
    results = judge_results if judge_results is not None else [JudgeResult("pass", "ok")] * 10
    argv = ["--suite", str(suite), "--prompt", str(prompt), *extra]
    with patch("evalgate.runner.replay", side_effect=replay_side_effect, return_value=TRANSCRIPT) as rp, patch(
        "evalgate.runner.judge", side_effect=results
    ) as jd:
        code = main(argv, env=env)
    return code, rp, jd


def test_all_pass_exits_zero_and_text_format(files, capsys):
    suite, prompt = files
    code, _, _ = run_cli(suite, prompt)
    out = capsys.readouterr().out
    assert code == 0
    lines = out.strip().splitlines()
    assert lines[0] == "PASS  regression  Answers pricing"
    assert lines[1] == "PASS  regression  Answers pricing"
    assert lines[-1] == "pass rate 2 of 2 (threshold 1): PASSED"


def test_below_threshold_exits_one(files, capsys):
    suite, prompt = files
    code, _, _ = run_cli(suite, prompt, judge_results=[JudgeResult("pass", "a"), JudgeResult("fail", "b")])
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL  regression  Answers pricing" in out
    assert out.strip().splitlines()[-1] == "pass rate 1 of 2 (threshold 1): FAILED"


def test_threshold_lets_a_partial_pass_through(files, capsys):
    suite, prompt = files
    code, _, _ = run_cli(
        suite, prompt, "--threshold", "0.5", judge_results=[JudgeResult("pass", "a"), JudgeResult("fail", "b")]
    )
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "pass rate 1 of 2 (threshold 0.5): PASSED"


@pytest.mark.parametrize("threshold", ["1.5", "-0.1", "nan", "abc"])
def test_bad_threshold_exits_two(files, capsys, threshold):
    suite, prompt = files
    code, rp, _ = run_cli(suite, prompt, "--threshold", threshold)
    assert code == 2
    assert rp.call_count == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("repeat", ["0", "10", "-1", "x"])
def test_bad_repeat_exits_two(files, repeat):
    suite, prompt = files
    code, rp, _ = run_cli(*files, "--repeat", repeat)
    assert code == 2
    assert rp.call_count == 0


def test_repeat_runs_each_test_that_many_times(files):
    code, rp, _ = run_cli(*files, "--repeat", "3")
    assert code == 0
    assert rp.call_count == 6


def test_missing_required_arguments_exit_two(capsys):
    assert main([], env={"GROQ_API_KEY": KEY}) == 2
    assert main(["--suite", "x"], env={"GROQ_API_KEY": KEY}) == 2


def test_missing_suite_file_exits_two(files, tmp_path, capsys):
    _, prompt = files
    code, _, _ = run_cli(tmp_path / "nope.json", prompt)
    assert code == 2
    assert "suite file" in capsys.readouterr().err


def test_missing_prompt_file_exits_two(files, tmp_path, capsys):
    suite, _ = files
    code, _, _ = run_cli(suite, tmp_path / "nope.txt")
    assert code == 2
    assert "prompt file" in capsys.readouterr().err


def test_invalid_suite_exits_two(files, tmp_path, capsys):
    _, prompt = files
    bad = tmp_path / "bad.json"
    bad.write_text('{"format": "other"}', encoding="utf-8")
    code, _, _ = run_cli(bad, prompt)
    assert code == 2
    assert "format" in capsys.readouterr().err


def test_empty_prompt_exits_two(files, tmp_path):
    suite, _ = files
    empty = tmp_path / "empty.txt"
    empty.write_text("   \n", encoding="utf-8")
    code, _, _ = run_cli(suite, empty)
    assert code == 2


@pytest.mark.parametrize(
    "env", [{}, {"GROQ_API_KEY": ""}, {"GROQ_API_KEY": "   "}, {"GROQ_API_KEY": "sk-test-placeholder"},
            {"GROQ_API_KEY": "your_groq_api_key_here"}]
)
def test_missing_blank_or_placeholder_key_exits_three(files, capsys, env):
    code, rp, _ = run_cli(*files, env=env)
    captured = capsys.readouterr()
    assert code == 3
    assert "GROQ_API_KEY is not set." in captured.err
    assert rp.call_count == 0


def test_key_is_read_only_from_the_given_env(files, monkeypatch, capsys):
    monkeypatch.setenv("GROQ_API_KEY", KEY)  # the process has a key, the explicit env does not
    code, rp, _ = run_cli(*files, env={})
    assert code == 3
    assert rp.call_count == 0


def test_default_env_is_os_environ(files, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    argv = ["--suite", str(files[0]), "--prompt", str(files[1])]
    with patch("evalgate.runner.replay", return_value=TRANSCRIPT), patch(
        "evalgate.runner.judge", return_value=JudgeResult("pass", "ok")
    ):
        assert main(argv) == 0


def test_errored_test_exits_three(files, capsys):
    code, _, _ = run_cli(*files, judge_results=[JudgeResult("pass", "a"), JudgeFailed("boom")])
    captured = capsys.readouterr()
    assert code == 3
    assert "ERROR  regression  Answers pricing" in captured.out
    assert captured.out.strip().splitlines()[-1].endswith("COULD NOT RUN")


def test_all_not_applicable_exits_three(files, capsys):
    code, _, _ = run_cli(*files, judge_results=[JudgeResult("not_applicable", "n")] * 2)
    out = capsys.readouterr().out
    assert code == 3
    assert "N/A  regression  Answers pricing" in out
    assert out.strip().splitlines()[-1].endswith("COULD NOT RUN")


def test_no_runnable_tests_after_skipping_exits_three(tmp_path, capsys):
    suite = write_suite(tmp_path, _doc([_test("t1", "fail"), _test("t2", None)]))
    prompt = tmp_path / "p.txt"
    prompt.write_text("prompt", encoding="utf-8")
    code, rp, _ = run_cli(suite, prompt)
    err = capsys.readouterr().err
    assert code == 3
    assert rp.call_count == 0
    assert "No runnable tests" in err
    assert "2 skipped" in err


def test_unexpected_exception_exits_three_without_traceback(files, capsys):
    with patch("evalgate.runner.run_suite", side_effect=RuntimeError(f"boom {KEY}")):
        code, _, _ = run_cli(*files)
    captured = capsys.readouterr()
    assert code == 3
    assert KEY not in captured.out + captured.err
    assert "Traceback" not in captured.err


def test_skipped_count_and_reason_are_printed(tmp_path, capsys):
    suite = write_suite(tmp_path, _doc([_test("t1"), _test("t2", "fail"), _test("t3", None)]))
    prompt = tmp_path / "p.txt"
    prompt.write_text("prompt", encoding="utf-8")
    code, rp, _ = run_cli(suite, prompt)
    err = capsys.readouterr().err
    assert code == 0
    assert rp.call_count == 1
    assert "2 skipped" in err
    assert "--all" in err


def test_all_includes_tests_that_failed_at_export(tmp_path, capsys):
    suite = write_suite(tmp_path, _doc([_test("t1"), _test("t2", "fail"), _test("t3", None)]))
    prompt = tmp_path / "p.txt"
    prompt.write_text("prompt", encoding="utf-8")
    code, rp, _ = run_cli(suite, prompt, "--all")
    captured = capsys.readouterr()
    assert code == 0
    assert rp.call_count == 3
    assert "skipped" not in captured.err


def test_max_tests_truncation_warns_with_count(tmp_path, capsys):
    suite = write_suite(tmp_path, _doc([_test(f"t{i}") for i in range(5)]))
    prompt = tmp_path / "p.txt"
    prompt.write_text("prompt", encoding="utf-8")
    code, rp, _ = run_cli(suite, prompt, "--max-tests", "2")
    err = capsys.readouterr().err
    assert code == 0
    assert rp.call_count == 2
    assert "3 tests were not run" in err


def test_max_tests_must_be_positive(files):
    assert run_cli(*files, "--max-tests", "0")[0] == 2


def test_pause_is_passed_to_the_runner(files):
    from evalgate import runner

    with patch("evalgate.runner.run_suite", wraps=runner.run_suite) as spy:
        code, _, _ = run_cli(*files, "--pause", "0.001")
    assert code == 0
    assert spy.call_args.kwargs["pause"] == 0.001


def test_json_format(tmp_path, capsys):
    suite = write_suite(tmp_path, _doc([_test("t1"), _test("t2"), _test("t3", "fail")]))
    prompt = tmp_path / "p.txt"
    prompt.write_text("prompt", encoding="utf-8")
    code, _, _ = run_cli(
        suite, prompt, "--format", "json", "--threshold", "0.5",
        judge_results=[JudgeResult("pass", "good"), JudgeResult("fail", "bad")],
    )
    data = json.loads(capsys.readouterr().out)
    assert code == 0
    assert data["pass_rate"] == 0.5
    assert data["threshold"] == 0.5
    assert data["exit_code"] == 0
    assert data["skipped"] == 1
    assert data["counts"] == {"pass": 1, "fail": 1, "not_applicable": 0, "error": 0}
    assert [t["id"] for t in data["tests"]] == ["t1", "t2"]
    assert data["tests"][1] == {
        "id": "t2",
        "criterion": "Answers pricing",
        "role": "regression",
        "verdict": "fail",
        "runs": ["fail"],
        "rationale": "bad",
        "error": None,
    }


def test_json_format_for_a_run_that_could_not_run(files, capsys):
    code, _, _ = run_cli(*files, "--format", "json", judge_results=[JudgeFailed("x"), JudgeFailed("x")])
    data = json.loads(capsys.readouterr().out)
    assert code == 3
    assert data["exit_code"] == 3
    assert data["pass_rate"] is None
    assert data["counts"]["error"] == 2
    assert data["tests"][0]["error"] == "The judge was unavailable."


def test_markdown_format(files, capsys):
    code, _, _ = run_cli(
        *files, "--format", "markdown", judge_results=[JudgeResult("pass", "fine"), JudgeResult("fail", "a | b")]
    )
    out = capsys.readouterr().out
    assert code == 1
    lines = out.strip().splitlines()
    assert lines[0].startswith("# ")
    assert "| Verdict | Role | Criterion | Rationale |" in out
    rows = [ln for ln in lines if ln.startswith("| PASS") or ln.startswith("| FAIL")]
    assert len(rows) == 2
    assert rows[0].startswith("| PASS | regression | Answers pricing |")
    assert "a \\| b" in rows[1]
    assert "pass rate 1 of 2 (threshold 1): FAILED" in out


def test_markdown_cells_cannot_break_the_table_or_inject_commands(tmp_path, capsys):
    suite = write_suite(tmp_path, _doc([_test("t1")], criterion_name="evil\n::error::pwned | x"))
    prompt = tmp_path / "p.txt"
    prompt.write_text("prompt", encoding="utf-8")
    code, _, _ = run_cli(suite, prompt, "--format", "markdown")
    out = capsys.readouterr().out
    assert code == 0
    assert "\n::error::" not in out
    run_cli(suite, prompt)
    text = capsys.readouterr().out
    assert "\n::error::" not in text
    assert all(len(ln.splitlines()) == 1 for ln in text.splitlines())


def test_step_summary_is_appended_with_markdown(files, tmp_path, capsys):
    summary = tmp_path / "summary.md"
    summary.write_text("EXISTING CONTENT\n", encoding="utf-8")
    code, _, _ = run_cli(*files, env={"GROQ_API_KEY": KEY, "GITHUB_STEP_SUMMARY": str(summary)})
    content = summary.read_text(encoding="utf-8")
    assert code == 0
    assert content.startswith("EXISTING CONTENT\n")
    assert "| Verdict | Role | Criterion | Rationale |" in content
    assert "| PASS | regression | Answers pricing |" in content
    assert "PASS  regression" in capsys.readouterr().out  # stdout still has the chosen (text) format


def test_unwritable_step_summary_does_not_change_the_exit_code(files, tmp_path, capsys):
    code, _, _ = run_cli(*files, env={"GROQ_API_KEY": KEY, "GITHUB_STEP_SUMMARY": str(tmp_path / "no" / "dir" / "s.md")})
    assert code == 0
    assert "GITHUB_STEP_SUMMARY" in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra, judge_results",
    [
        ((), None),
        (("--format", "json"), None),
        (("--format", "markdown"), None),
        ((), [JudgeFailed(f"provider said {KEY}")] * 2),
        (("--format", "json"), [RuntimeError(f"leak {KEY}")] * 2),
        (("--format", "markdown"), [JudgeResult("fail", "x")] * 2),
        (("--threshold", "9"), None),
    ],
)
def test_key_never_appears_anywhere(files, tmp_path, capsys, extra, judge_results):
    summary = tmp_path / "summary.md"
    code, _, _ = run_cli(
        *files, *extra, env={"GROQ_API_KEY": KEY, "GITHUB_STEP_SUMMARY": str(summary)}, judge_results=judge_results,
        replay_side_effect=None,
    )
    captured = capsys.readouterr()
    assert KEY not in captured.out
    assert KEY not in captured.err
    if summary.exists():
        assert KEY not in summary.read_text(encoding="utf-8")
    assert code in (0, 1, 2, 3)


def test_key_never_appears_when_the_run_crashes(files, tmp_path, capsys):
    summary = tmp_path / "summary.md"
    with patch("evalgate.cli._emit", side_effect=RuntimeError(KEY)):
        code, _, _ = run_cli(*files, env={"GROQ_API_KEY": KEY, "GITHUB_STEP_SUMMARY": str(summary)})
    captured = capsys.readouterr()
    assert code == 3
    assert KEY not in captured.out + captured.err


def test_key_never_appears_for_a_missing_suite(files, tmp_path, capsys):
    code, _, _ = run_cli(tmp_path / "missing.json", files[1])
    captured = capsys.readouterr()
    assert code == 2
    assert KEY not in captured.out + captured.err


def test_validated_key_is_made_consistent_with_the_groq_client(files, monkeypatch):
    from backend import groq_client

    assert os.environ.get("GROQ_API_KEY") is None
    code, _, _ = run_cli(*files)
    assert code == 0
    assert os.environ["GROQ_API_KEY"] == KEY
    assert groq_client.client.api_key == KEY


def test_dotenv_is_not_read_by_default(files, tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text("EVALGATE_DOTENV_PROBE=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(work)
    monkeypatch.delenv("EVALGATE_DOTENV_PROBE", raising=False)
    code, _, _ = run_cli(*files)
    assert code == 0
    assert "EVALGATE_DOTENV_PROBE" not in os.environ


def test_load_dotenv_flag_reads_the_working_directory_env(files, tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text("EVALGATE_DOTENV_PROBE=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(work)
    monkeypatch.delenv("EVALGATE_DOTENV_PROBE", raising=False)
    code, _, _ = run_cli(*files, "--load-dotenv")
    assert code == 0
    assert os.environ.get("EVALGATE_DOTENV_PROBE") == "from-dotenv"
    monkeypatch.delenv("EVALGATE_DOTENV_PROBE", raising=False)


def _subprocess(code_or_args, cwd=REPO, env_extra=None, module=False):
    env = {k: v for k, v in os.environ.items() if k not in ("GROQ_API_KEY", "GROQ_SKIP_DOTENV")}
    env.update(env_extra or {})
    cmd = [sys.executable, "-m", "evalgate", *code_or_args] if module else [sys.executable, "-c", code_or_args]
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=60)


def test_importing_the_cli_needs_no_web_stack_key_or_network():
    code = (
        "import sys, evalgate.cli, evalgate.runner\n"
        "bad = [m for m in ('fastapi', 'sqlalchemy') if m in sys.modules]\n"
        "print('BAD', bad) if bad else print('OK')\n"
    )
    result = _subprocess(code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"


def test_cli_sets_skip_dotenv_before_the_client_is_imported():
    code = "import os, sys, evalgate.cli\nprint(os.environ.get('GROQ_SKIP_DOTENV'), 'backend.groq_client' in sys.modules)\n"
    result = _subprocess(code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["1", "False"]


def test_python_dash_m_help_lists_the_exit_codes():
    result = _subprocess(["--help"], module=True)
    assert result.returncode == 0, result.stderr
    for fragment in ("exit codes", "0", "1", "2", "3", "--suite", "--prompt", "--threshold", "--load-dotenv"):
        assert fragment in result.stdout


def test_python_dash_m_without_a_key_exits_three(files):
    result = _subprocess(["--suite", str(files[0]), "--prompt", str(files[1])], module=True)
    assert result.returncode == 3
    assert "GROQ_API_KEY is not set." in result.stderr


def test_python_dash_m_does_not_read_a_working_directory_env(files, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text("GROQ_API_KEY=gsk_from_dotenv_file\n", encoding="utf-8")
    env = {"PYTHONPATH": str(REPO)}
    result = _subprocess(["--suite", str(files[0]), "--prompt", str(files[1])], cwd=work, env_extra=env, module=True)
    assert result.returncode == 3
    assert "gsk_from_dotenv_file" not in result.stdout + result.stderr


# --- consoles that cannot encode model text (cp1252 strict) must not turn a pass into exit 3 ---

FANCY = "Agent said \u2192 \u2265 \u2019 and \u201cquotes\u201d \u2713"


def _cp1252_stdout():
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict", newline="")


@pytest.mark.parametrize("fmt", ["text", "json", "markdown"])
def test_unencodable_model_text_keeps_the_true_exit_code(files, tmp_path, monkeypatch, fmt):
    summary = tmp_path / "summary.md"
    stdout = _cp1252_stdout()
    monkeypatch.setattr(sys, "stdout", stdout)
    code, _, _ = run_cli(
        *files,
        "--format", fmt,
        env={"GROQ_API_KEY": KEY, "GITHUB_STEP_SUMMARY": str(summary)},
        judge_results=[JudgeResult("pass", FANCY)] * 2,
    )
    assert code == 0
    assert FANCY in summary.read_text(encoding="utf-8")
    stdout.flush()
    assert b"PASSED" in stdout.buffer.getvalue() or b"pass_rate" in stdout.buffer.getvalue()


def test_a_dead_stdout_cannot_lose_the_summary_or_change_the_exit_code(files, tmp_path, monkeypatch):
    class Dead:
        def write(self, _):
            raise OSError("closed")

        def flush(self):
            raise OSError("closed")

    summary = tmp_path / "summary.md"
    monkeypatch.setattr(sys, "stdout", Dead())
    code, _, _ = run_cli(*files, env={"GROQ_API_KEY": KEY, "GITHUB_STEP_SUMMARY": str(summary)})
    assert code == 0
    assert "| PASS |" in summary.read_text(encoding="utf-8")


def test_the_summary_is_written_before_stdout_is_printed(files, tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    seen = []

    class Spy:
        def write(self, text):
            seen.append(summary.exists() and summary.read_text(encoding="utf-8"))
            return len(text)

        def flush(self):
            pass

    monkeypatch.setattr(sys, "stdout", Spy())
    run_cli(*files, env={"GROQ_API_KEY": KEY, "GITHUB_STEP_SUMMARY": str(summary)})
    assert seen and all(seen)


def test_markdown_cells_escape_links_and_code_spans(tmp_path, capsys):
    suite = write_suite(tmp_path, _doc([_test("t1")], criterion_name="[click](http://x) `code`"))
    prompt = tmp_path / "p.txt"
    prompt.write_text("prompt", encoding="utf-8")
    code, _, _ = run_cli(suite, prompt, "--format", "markdown")
    out = capsys.readouterr().out
    assert code == 0
    assert "\[click\]" in out
    assert "\`code\`" in out
    assert "[click]" not in out.replace("\[click\]", "")
    assert "`code`" not in out.replace("\`code\`", "")


def test_step_summary_starts_on_a_fresh_line(files, tmp_path):
    summary = tmp_path / "summary.md"
    summary.write_text("EXISTING WITHOUT NEWLINE", encoding="utf-8")
    code, _, _ = run_cli(*files, env={"GROQ_API_KEY": KEY, "GITHUB_STEP_SUMMARY": str(summary)})
    content = summary.read_text(encoding="utf-8")
    assert code == 0
    assert content.startswith("EXISTING WITHOUT NEWLINE\n")
    assert "EXISTING WITHOUT NEWLINE# Eval gate" not in content
    assert "\n# Eval gate" in content


@pytest.mark.parametrize("module", ["evalgate.cli", "simulation.run", "simulation.appdriver"])
def test_an_empty_skip_dotenv_value_is_replaced_with_one(tmp_path, module):
    """GROQ_SKIP_DOTENV="" counts as unset for backend.groq_client, so the entry points must overwrite it."""
    work = tmp_path / "work"
    work.mkdir()  # no .env here, and none is created in the repo
    code = f"import os, {module}\nprint(os.environ.get('GROQ_SKIP_DOTENV'))\n"
    result = _subprocess(code, cwd=work, env_extra={"PYTHONPATH": str(REPO), "GROQ_SKIP_DOTENV": ""})
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["1"]
