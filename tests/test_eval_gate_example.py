"""The runnable example in examples/eval-gate-demo: it passes with its prompt and fails with the weakened one.

`replay` and `judge` are faked at evalgate.runner so the verdict depends only on whether the prompt text
carries the guardrail. No network.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.core.judge import JudgeResult
from evalgate.cli import main
from evalgate.suite import MAX_MESSAGE_CHARS, parse_suite

DEMO = Path(__file__).resolve().parent.parent / "examples" / "eval-gate-demo"
GUARDRAIL = "never promise or guarantee a refund"
FAKE_KEY = "gsk_FAKE-EXAMPLE-KEY-0000"
PROMISE = "Yes, I guarantee you a full refund."
SAFE = "I cannot promise a refund. What is your order number? I will escalate this."


@pytest.fixture(autouse=True)
def _isolate_key_state(monkeypatch):
    from backend import groq_client

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(groq_client, "client", groq_client.client)


def fake_replay(config, messages):
    reply = SAFE if GUARDRAIL in config.system_prompt.lower() else PROMISE
    return [{"role": "user", "content": messages[0]}, {"role": "assistant", "content": reply}]


def fake_judge(config, criterion, transcript):
    promised = transcript[-1]["content"] == PROMISE
    return JudgeResult("fail" if promised else "pass", "promised a refund" if promised else "no promise")


def run(prompt_name, *extra):
    argv = ["--suite", str(DEMO / "suite.json"), "--prompt", str(DEMO / prompt_name), *extra]
    with patch("evalgate.runner.replay", side_effect=fake_replay) as rp, patch(
        "evalgate.runner.judge", side_effect=fake_judge
    ) as jd:
        code = main(argv, env={"GROQ_API_KEY": FAKE_KEY})
    return code, rp, jd


def load_doc():
    return json.loads((DEMO / "suite.json").read_text(encoding="utf-8"))


def test_suite_json_is_a_valid_export():
    suite = parse_suite(load_doc())
    roles = {t.role: t for t in suite.tests}
    assert {t.criterion_name for t in suite.tests} == {"Does not promise refunds"}
    assert {"fix", "heldout"} <= set(roles)
    assert roles["fix"].current_verdict == "pass" and roles["heldout"].current_verdict == "pass"
    assert [t.current_verdict for t in suite.tests].count("fail") == 1
    for t in suite.tests:
        assert all(len(m) < MAX_MESSAGE_CHARS for m in t.messages)


def test_prompts_differ_by_the_guardrail():
    good = (DEMO / "prompt.md").read_text(encoding="utf-8")
    weak = (DEMO / "weakened-prompt.md").read_text(encoding="utf-8")
    assert GUARDRAIL in good.lower()
    assert GUARDRAIL not in weak.lower()
    assert "order number" in good.lower() and "escalate" in good.lower()


def test_the_only_difference_is_the_removed_guardrail_paragraph():
    good = (DEMO / "prompt.md").read_text(encoding="utf-8")
    weak = (DEMO / "weakened-prompt.md").read_text(encoding="utf-8")
    paragraphs = [p for p in good.replace("\r\n", "\n").split("\n\n") if p.strip()]
    removed = [p for p in paragraphs if GUARDRAIL in p.lower()]
    assert len(removed) == 1 and removed[0].startswith("Refunds:")
    kept = [p for p in paragraphs if p is not removed[0]]

    def norm(text):
        return " ".join(text.split())

    assert norm(weak) == norm("\n\n".join(kept))


def test_gate_passes_with_the_real_prompt():
    code, replay_mock, judge_mock = run("prompt.md")
    assert code == 0
    assert replay_mock.call_count == judge_mock.call_count == 2


def test_gate_fails_with_the_weakened_prompt():
    code, _, _ = run("weakened-prompt.md")
    assert code == 1


def test_the_failing_test_is_skipped_by_default_and_run_with_all():
    _, replay_mock, _ = run("prompt.md")
    assert replay_mock.call_count == 2
    _, replay_mock, _ = run("prompt.md", "--all")
    assert replay_mock.call_count == 3
