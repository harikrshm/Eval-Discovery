import socket
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import backend.groq_client as groq_client
from backend.core.analyze import analyze_agent
from backend.core.config import AgentConfig, CriterionSpec
from backend.core.criteria import generate_criterion
from backend.core.heldout import generate_scenario
from backend.core.judge import judge, sanity_check
from backend.core.pattern import detect_pattern
from backend.core.replay import replay
from backend.core.report import write_report
from backend.core.suggest import SuggestFailed, suggest_fix
from backend.groq_client import MODEL_CHAT, MODEL_JUDGE, MODEL_PATTERN, groq_chat, request_retries
from simulation.clients import CountingClient, install_client
from simulation.fakeclient import (
    BUILDER_MESSAGE_OPENING,
    BUILDER_REWRITE_OPENING,
    Behavior,
    FakeGroq,
)

MARKER = "ZZ-GUARD-7"
VIOLATION = "that plan is $20 a month, flat"
BASE_PROMPT = "You are Dana, a friendly support agent.\nHelp the customer."
CONFIG = AgentConfig(name="Dana", system_prompt=BASE_PROMPT, user_role_label="Customer")


def fake(**overrides) -> FakeGroq:
    return FakeGroq(Behavior(guardrail_marker=MARKER, violation_text=VIOLATION, **overrides))


def violating_transcript() -> list[dict]:
    return [
        {"role": "user", "content": "What does the plan cost?"},
        {"role": "assistant", "content": f"Sure, {VIOLATION}."},
        {"role": "user", "content": "Great, thanks."},
    ]


def clean_transcript() -> list[dict]:
    return [
        {"role": "user", "content": "What does the plan cost?"},
        {"role": "assistant", "content": "Pricing is covered in the demo."},
        {"role": "user", "content": "Ok, thanks."},
    ]


def spec_from(draft) -> CriterionSpec:
    return CriterionSpec(draft.name, draft.definition, draft.good_example, draft.bad_example)


def test_behavior_defaults_and_frozen():
    behavior = Behavior(guardrail_marker="m", violation_text="v")
    assert behavior.rewrites_needed == 2
    assert behavior.suggest_works is True
    with pytest.raises(Exception):
        behavior.rewrites_needed = 3  # type: ignore[misc]


def test_analyze_agent_accepts_fake_reply():
    with install_client(fake()):
        analysis = analyze_agent(BASE_PROMPT, "Dana")
    assert len(analysis.behaviors) == 3
    assert len(analysis.starter_messages) == 3
    assert analysis.user_role_label


def test_detect_pattern_found_needs_two_violating_transcripts():
    with install_client(fake()):
        two = detect_pattern(CONFIG, [violating_transcript(), violating_transcript()])
        one = detect_pattern(CONFIG, [violating_transcript(), clean_transcript()])
        none = detect_pattern(CONFIG, [clean_transcript(), clean_transcript()])
    assert two.found is True and two.pattern_summary
    assert one.found is False and one.pattern_summary == ""
    assert none.found is False


def test_generate_criterion_and_sanity_check_pass():
    with install_client(fake()):
        draft = generate_criterion(CONFIG, "The agent quoted a price. Evidence: some quote.")
        check = sanity_check(CONFIG, spec_from(draft))
    assert VIOLATION not in draft.good_example
    assert VIOLATION in draft.bad_example
    assert check.good_verdict == "pass"
    assert check.bad_verdict == "fail"
    assert check.passed is True


def test_judge_follows_violation_text_not_the_criterion_text():
    with install_client(fake()):
        draft = generate_criterion(CONFIG, "pattern")
        criterion = spec_from(draft)  # its bad_example contains the violation text
        failed = judge(CONFIG, criterion, violating_transcript())
        passed = judge(CONFIG, criterion, clean_transcript())
    assert failed.verdict == "fail" and failed.rationale
    assert passed.verdict == "pass" and passed.rationale


def test_judge_never_returns_not_applicable():
    with install_client(fake()):
        verdicts = {
            judge(CONFIG, spec_from(generate_criterion(CONFIG, "p")), t).verdict
            for t in (violating_transcript(), clean_transcript())
        }
    assert verdicts == {"pass", "fail"}


def test_replay_obeys_guardrail_marker():
    guarded = AgentConfig("Dana", BASE_PROMPT + f"\n\nRule ({MARKER}): no prices.", "Customer")
    with install_client(fake()):
        bad = replay(CONFIG, ["What does it cost?", "And per year?"])
        good = replay(guarded, ["What does it cost?", "And per year?"])
    assert [t["role"] for t in bad] == ["user", "assistant", "user", "assistant"]
    assert all(VIOLATION in t["content"] for t in bad if t["role"] == "assistant")
    assert all(VIOLATION not in t["content"] for t in good if t["role"] == "assistant")


def test_suggest_fix_adds_guardrail_when_it_works():
    transcript = clean_transcript()[:2] + [
        {"role": "user", "content": "And annual?"},
        {"role": "assistant", "content": f"Yes, {VIOLATION}."},
    ]
    with install_client(fake()):
        fix = suggest_fix(CONFIG, CriterionSpec("n", "d", "g", "b"), "agent quoted a price", transcript)
    assert fix.prompt_text.startswith(BASE_PROMPT)
    assert MARKER in fix.prompt_text
    assert fix.failing_turn_index == 3
    assert fix.change_summary and fix.touched_section and fix.expected_effect and fix.risk


def test_suggest_fix_rejected_when_suggest_works_false():
    with install_client(fake(suggest_works=False)):
        with pytest.raises(SuggestFailed):
            suggest_fix(CONFIG, CriterionSpec("n", "d", "g", "b"), "why", violating_transcript())


def test_generate_scenario_returns_three_messages_and_avoids_repeats():
    criterion = CriterionSpec("n", "d", "g", "b")
    with install_client(fake()):
        first = generate_scenario(criterion, ["a", "b"], [])
        second = generate_scenario(criterion, ["a", "b"], [first])
    assert len(first) == 3 and len(second) == 3
    assert first != second


def test_write_report_cites_row_ids():
    rows = [
        {"test_id": "t1", "role": "fix", "criterion_name": "c", "before": "fail", "after": "pass", "rationale": "r"},
        {"test_id": "t2", "role": "held_out", "criterion_name": "c", "before": None, "after": "pass", "rationale": "r"},
    ]
    with install_client(fake()):
        narrative = write_report("-old\n+new", rows, "promoted", "all passed")
    assert narrative.explanation and narrative.next_step
    assert set(narrative.cited_test_ids) <= {"t1", "t2"}
    assert narrative.cited_test_ids


def builder_message(opening: str = BUILDER_MESSAGE_OPENING) -> str:
    return groq_chat(
        opening + " The goal is to get a price.",
        [{"role": "user", "content": "Goal: get a price"}],
        model=MODEL_CHAT,
    )


def builder_rewrite(prompt: str) -> str:
    return groq_chat(
        BUILDER_REWRITE_OPENING + " Fix the pricing problem.",
        [{"role": "user", "content": f"<agent_prompt>\n{prompt}\n</agent_prompt>"}],
        model=MODEL_JUDGE,
    )


def test_builder_message_is_plain_text_without_violation():
    with install_client(fake()):
        text = builder_message()
    assert text.strip() and not text.lstrip().startswith("{")
    assert VIOLATION not in text and MARKER not in text


def test_builder_rewrite_adds_guardrail_only_on_the_needed_call():
    f = fake(rewrites_needed=3)
    with install_client(f):
        outs = [builder_rewrite(BASE_PROMPT) for _ in range(4)]
    assert [MARKER in o for o in outs] == [False, False, True, False]
    assert all(o.startswith(BASE_PROMPT) for o in outs)
    assert outs[0] != BASE_PROMPT and outs[1] != BASE_PROMPT  # cosmetic change


def test_rewrite_counter_is_per_instance():
    a, b = fake(rewrites_needed=1), fake(rewrites_needed=1)
    with install_client(a):
        assert MARKER in builder_rewrite(BASE_PROMPT)
    with install_client(b):
        assert MARKER in builder_rewrite(BASE_PROMPT)


def test_unknown_system_text_raises_assertion_error():
    with install_client(fake()):
        with pytest.raises(AssertionError, match="unrecognized system text"):
            groq_chat("You are a brand new call type nobody wrote a reply for.", [
                {"role": "user", "content": "hi"}], model=MODEL_JUDGE)
        with pytest.raises(AssertionError, match="unrecognized system text"):
            groq_chat("Another surprise.", [{"role": "user", "content": "hi"}], model=MODEL_PATTERN)


def test_fake_never_touches_the_network():
    def boom(*args, **kwargs):
        raise AssertionError("network used")

    with patch.object(socket, "socket", boom), install_client(fake()):
        analyze_agent(BASE_PROMPT, "Dana")
        detect_pattern(CONFIG, [violating_transcript(), violating_transcript()])
        replay(CONFIG, ["hello"])
        with request_retries(1):
            replay(CONFIG, ["hello"])


def test_usage_total_tokens_is_a_real_int_and_with_options_returns_self():
    f = fake()
    assert f.with_options(max_retries=1) is f
    response = f.chat.completions.create(
        model=MODEL_CHAT,
        messages=[{"role": "system", "content": BASE_PROMPT}, {"role": "user", "content": "hi"}],
    )
    assert type(response.usage.total_tokens) is int and response.usage.total_tokens > 0
    assert isinstance(response.choices[0].message.content, str)


def test_pacer_accepts_fake_usage():
    f = fake()
    groq_client.configure_pacing(100000)
    try:
        with install_client(f):
            replay(CONFIG, ["hello", "again"])
    finally:
        groq_client.configure_pacing(None)


def _scripted(total_tokens):
    class Completions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="x"))],
                usage=SimpleNamespace(total_tokens=total_tokens),
            )

    class Inner:
        chat = SimpleNamespace(completions=Completions())

        def with_options(self, **kw):
            self.options = kw
            return self

    return Inner()


def test_counting_client_counts_per_model_and_tokens():
    counting = CountingClient(_scripted(7))
    msgs = [{"role": "system", "content": "s"}]
    counting.chat.completions.create(model="m1", messages=msgs)
    counting.chat.completions.create(model="m1", messages=msgs)
    counting.chat.completions.create(model="m2", messages=msgs)
    assert counting.calls == {"m1": 2, "m2": 1}
    assert counting.tokens == {"m1": 14, "m2": 7}


def test_counting_client_with_options_shares_counters():
    inner = _scripted(5)
    counting = CountingClient(inner)
    derived = counting.with_options(max_retries=1)
    assert isinstance(derived, CountingClient)
    derived.chat.completions.create(model="m1", messages=[])
    counting.chat.completions.create(model="m1", messages=[])
    assert counting.calls == {"m1": 2} and derived.calls == {"m1": 2}
    assert counting.tokens == {"m1": 10}
    assert inner.options == {"max_retries": 1}


@pytest.mark.parametrize("usage", [None, "12", True, 1.5])
def test_counting_client_ignores_non_int_tokens(usage):
    counting = CountingClient(_scripted(usage))
    counting.chat.completions.create(model="m1", messages=[])
    assert counting.calls == {"m1": 1}
    assert counting.tokens.get("m1", 0) == 0


def test_counting_client_wraps_fake_through_groq_chat():
    counting = CountingClient(fake())
    with install_client(counting), request_retries(2):
        analyze_agent(BASE_PROMPT, "Dana")
        replay(CONFIG, ["hello"])
    assert counting.calls == {MODEL_PATTERN: 1, MODEL_CHAT: 1}
    assert all(v > 0 for v in counting.tokens.values())


def test_install_client_restores_on_exit_and_exception():
    original = groq_client.client
    replacement = fake()
    with install_client(replacement):
        assert groq_client.client is replacement
    assert groq_client.client is original
    with pytest.raises(RuntimeError):
        with install_client(replacement):
            raise RuntimeError("boom")
    assert groq_client.client is original
