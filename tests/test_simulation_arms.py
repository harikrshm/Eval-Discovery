import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.groq_client import MODEL_CHAT, MODEL_CRITERIA, MODEL_JUDGE, MODEL_PATTERN
from simulation import arms
from simulation.arms import MAX_PREVIEWS, TrialResult, run_baseline, run_flywheel
from simulation.clients import install_client
from simulation.fakeclient import Behavior, FakeGroq
from simulation.scenarios import SCENARIOS

SCENARIO = SCENARIOS["support-refund"]


def _behavior(**overrides) -> Behavior:
    return Behavior(SCENARIO.guardrail_marker, SCENARIO.violation_text, **overrides)


class Recorder:
    """Wraps another client and records every call's kwargs (nothing leaves the process)."""

    def __init__(self, inner):
        self.inner = inner
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, **kwargs):
        return self

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.inner.chat.completions.create(**kwargs)


class ReferenceAlwaysFails(FakeGroq):
    """The app's own judge works as usual; the reference judge (it grades the scenario goal) never passes."""

    def _judge(self, user: str) -> str:
        if SCENARIO.goal.name in user:
            return json.dumps({"verdict": "fail", "rationale": "The reference goal is not met."})
        return super()._judge(user)


class ReferenceNotApplicable(FakeGroq):
    def _judge(self, user: str) -> str:
        if SCENARIO.goal.name in user:
            return json.dumps({"verdict": "not_applicable", "rationale": "The goal did not come up."})
        return super()._judge(user)


def _install(fake):
    return install_client(fake)


def test_constants_and_result_shape():
    assert MAX_PREVIEWS == 12
    result = TrialResult(
        scenario="support-refund", arm="baseline", trial=0, converged=True, previews=3, path="fallback",
        final_prompt_changed=True, model_calls={"m": 2}, model_tokens={"m": 10}, wall_seconds=1.5,
        notes=["a"],
    )
    assert TrialResult.from_dict(json.loads(json.dumps(result.to_dict()))) == result
    assert set(result.to_dict()) == {
        "scenario", "arm", "trial", "converged", "previews", "path", "final_prompt_changed",
        "model_calls", "model_tokens", "wall_seconds", "notes",
    }
    with pytest.raises(ValueError):
        replace(result, arm="other")
    with pytest.raises(ValueError):
        replace(result, path="elsewhere")


def test_baseline_converges_after_the_needed_rewrites():
    fake = FakeGroq(_behavior(rewrites_needed=2))
    with _install(fake):
        result = run_baseline(SCENARIO, trial=4)
    assert result.converged is True
    assert result.previews == 3  # rewrites_needed + 1
    assert fake.rewrite_calls == 2
    assert result.path == "fallback"  # manual edits are the baseline's only mechanism
    assert result.arm == "baseline" and result.trial == 4 and result.scenario == "support-refund"
    assert result.final_prompt_changed is True
    assert result.notes == []
    # 3 previews x (2 customer messages + 2 agent replies) on the chat model
    assert result.model_calls[MODEL_CHAT] == 12
    # 3 reference judgements + 2 rewrites on the judge model; no app features are used
    assert result.model_calls[MODEL_JUDGE] == 5
    assert set(result.model_calls) == {MODEL_CHAT, MODEL_JUDGE}
    assert all(tokens > 0 for tokens in result.model_tokens.values())
    assert result.wall_seconds >= 0


def test_baseline_passing_on_the_first_preview_needs_no_rewrite():
    class AlwaysSafe(FakeGroq):
        def _agent_chat(self, system):
            return "I can't promise that, but I can pass this to the billing team."

    with _install(AlwaysSafe(_behavior())):
        result = run_baseline(SCENARIO, trial=0)
    assert result.converged and result.previews == 1 and result.final_prompt_changed is False


def test_flywheel_converges_through_the_loop_in_fewer_previews():
    behavior = _behavior(rewrites_needed=4)
    with _install(FakeGroq(behavior)):
        baseline = run_baseline(SCENARIO, trial=0)
    with _install(FakeGroq(behavior)):
        flywheel = run_flywheel(SCENARIO, trial=0)
    assert baseline.converged and baseline.previews == 5
    assert flywheel.converged is True
    assert flywheel.path == "loop"
    assert flywheel.previews == 3  # two failing previews, the promoted fix, then the first pass
    assert flywheel.previews < baseline.previews
    assert flywheel.final_prompt_changed is True
    assert flywheel.notes == []
    assert flywheel.arm == "flywheel"
    # the app's pattern check, criterion discovery and judging ran for real
    assert flywheel.model_calls[MODEL_PATTERN] >= 1
    assert flywheel.model_calls[MODEL_CRITERIA] >= 1
    assert flywheel.model_calls[MODEL_JUDGE] > baseline.model_calls[MODEL_JUDGE] - 4


def test_flywheel_falls_back_when_the_suggestion_fails_and_still_converges():
    fake = FakeGroq(_behavior(rewrites_needed=2, suggest_works=False))
    with _install(fake):
        result = run_flywheel(SCENARIO, trial=1)
    assert result.converged is True
    assert result.path == "fallback"
    assert result.notes, "a fallback must say why"
    assert fake.rewrite_calls == 2
    # two failing previews, then the manual rewrites: preview 3 (still failing) and preview 4 (pass)
    assert result.previews == 4
    assert result.final_prompt_changed is True


def test_flywheel_falls_back_when_there_is_no_loop():
    class NoPattern(FakeGroq):
        def _pattern(self, user):
            return json.dumps({"found": False, "pattern_summary": ""})

    fake = NoPattern(_behavior(rewrites_needed=1))
    with _install(fake):
        result = run_flywheel(SCENARIO, trial=0)
    assert result.converged and result.path == "fallback"
    assert any("loop" in note.lower() or "pattern" in note.lower() for note in result.notes)
    assert fake.rewrite_calls == 1
    assert result.previews == 3


def test_flywheel_passing_unaided_records_no_mechanism():
    class AlwaysSafe(FakeGroq):
        def _agent_chat(self, system):
            return "I can't promise that, but I can pass this to the billing team."

    with _install(AlwaysSafe(_behavior())):
        result = run_flywheel(SCENARIO, trial=0)
    assert result.converged is True and result.previews == 1
    assert result.path == "none"
    assert result.final_prompt_changed is False
    assert any("without" in note.lower() for note in result.notes)


@pytest.mark.parametrize("arm", ["baseline", "flywheel"])
def test_a_trial_that_never_converges_is_returned_not_dropped(arm):
    fake = FakeGroq(_behavior(rewrites_needed=99, suggest_works=False))
    with _install(fake):
        result = (run_baseline if arm == "baseline" else run_flywheel)(SCENARIO, trial=2)
    assert result.converged is False
    assert result.previews == 12 == MAX_PREVIEWS
    assert result.path == "none"
    assert result.arm == arm and result.trial == 2
    assert result.model_calls[MODEL_CHAT] >= 12 * 4
    if arm == "flywheel":
        assert result.notes


def test_custom_cap_is_honoured():
    fake = FakeGroq(_behavior(rewrites_needed=99, suggest_works=False))
    with _install(fake):
        result = run_baseline(SCENARIO, trial=0, max_previews=3)
    assert result.converged is False and result.previews == 3 and result.path == "none"


def test_both_arms_use_the_same_prompt_models_and_settings():
    recorded = {}
    for name, runner in (("baseline", run_baseline), ("flywheel", run_flywheel)):
        recorder = Recorder(FakeGroq(_behavior(rewrites_needed=3)))
        with _install(recorder):
            result = runner(SCENARIO, trial=0)
        recorded[name] = (recorder.calls, result)

    for name, (calls, result) in recorded.items():
        assert result.converged
        agent_calls = [c for c in calls if c["messages"][0]["content"] == SCENARIO.weak_prompt]
        assert agent_calls, name
        # the very first agent reply is produced from the scenario's weak prompt
        assert calls[[c["messages"][0]["content"] for c in calls].index(SCENARIO.weak_prompt)]["model"] == MODEL_CHAT
        customer_calls = [c for c in calls if c["messages"][0]["content"].startswith("You write the next message a customer")]
        assert customer_calls and all(c["model"] == MODEL_CHAT and c.get("temperature") == 0.8 for c in customer_calls)
        assert SCENARIO.opening_intent in customer_calls[0]["messages"][-1]["content"]
        # the reference judge grades the scenario goal on the judge model
        reference = [c for c in calls if SCENARIO.goal.name in c["messages"][-1]["content"]
                     and c["messages"][0]["content"].startswith("You are a strict, impartial judge")]
        assert reference and all(c["model"] == MODEL_JUDGE for c in reference)
        # every agent reply (any prompt) is MODEL_CHAT with the chat route's token cap
        agent_like = [c for c in calls if c["model"] == MODEL_CHAT and c not in customer_calls
                      and "Return only the message text" not in c["messages"][0]["content"]]
        assert all(c.get("max_tokens") == 3000 for c in agent_like)
        assert result.model_calls[MODEL_CHAT] >= len(customer_calls) + len(agent_like)

    baseline_calls, _ = recorded["baseline"]
    flywheel_calls, _ = recorded["flywheel"]
    first_customer = lambda calls: next(c for c in calls if "customer sends" in c["messages"][0]["content"])  # noqa: E731
    assert first_customer(baseline_calls)["messages"] == first_customer(flywheel_calls)["messages"]


def test_the_reference_judge_decides_convergence_not_the_apps_criterion():
    fake = ReferenceAlwaysFails(_behavior(rewrites_needed=1))
    with _install(fake):
        result = run_flywheel(SCENARIO, trial=0)
    # the app promoted a fix (its own criterion passes) yet the reference never passes
    assert result.final_prompt_changed is True
    assert result.model_calls[MODEL_CRITERIA] >= 1
    assert result.converged is False
    assert result.previews == 12
    assert result.path == "none"


def test_not_applicable_is_not_a_pass():
    fake = ReferenceNotApplicable(_behavior(rewrites_needed=1))
    with _install(fake):
        result = run_baseline(SCENARIO, trial=0, max_previews=4)
    assert result.converged is False and result.previews == 4


def test_a_builder_error_counts_as_a_failed_preview_with_a_note():
    class FlakyBuilder(FakeGroq):
        failed = False

        def _builder_message(self):
            if not self.failed:
                self.failed = True
                raise RuntimeError("provider exploded with secret details")
            return super()._builder_message()

    fake = FlakyBuilder(_behavior(rewrites_needed=1))
    with _install(fake):
        result = run_baseline(SCENARIO, trial=0)
    assert result.converged is True
    assert result.previews >= 2
    assert result.notes
    assert not any("secret" in note for note in result.notes)


def test_a_fresh_driver_is_used_per_flywheel_trial():
    seen = []
    real = arms.AppDriver

    class Spy(real):
        def __init__(self):
            super().__init__()
            seen.append(self)

    arms.AppDriver = Spy
    try:
        with _install(FakeGroq(_behavior())):
            run_flywheel(SCENARIO, trial=0)
            run_flywheel(SCENARIO, trial=1)
    finally:
        arms.AppDriver = real
    assert len(seen) == 2 and seen[0] is not seen[1]


def test_the_installed_client_is_restored_after_a_trial():
    import backend.groq_client as groq_client

    fake = FakeGroq(_behavior())
    with _install(fake):
        run_baseline(SCENARIO, trial=0)
        assert groq_client.client is fake


def test_an_existing_heldout_is_not_a_failure():
    from simulation.appdriver import AppDriver, DriverError

    seen = []
    original = AppDriver.generate_heldout

    def spy(self, criterion_id):
        try:
            return original(self, criterion_id)
        except DriverError as exc:
            seen.append((exc.status, exc.detail))
            raise

    AppDriver.generate_heldout = spy
    try:
        with _install(FakeGroq(_behavior(rewrites_needed=4))):
            result = run_flywheel(SCENARIO, trial=0)
    finally:
        AppDriver.generate_heldout = original
    # two failing runs: build_tests already made the held-out, so the route answers 409 and we go on
    assert seen == [(409, "This criterion needs a fix test and no held-out test.")]
    assert result.converged and result.path == "loop" and result.notes == []


def test_a_driver_error_in_the_app_flow_falls_back_with_a_note():
    from simulation.appdriver import AppDriver, DriverError

    original = AppDriver.discover

    def broken(self, agent_id):
        raise DriverError(502, "Criterion generation failed.")

    AppDriver.discover = broken
    try:
        with _install(FakeGroq(_behavior(rewrites_needed=1))):
            result = run_flywheel(SCENARIO, trial=0)
    finally:
        AppDriver.discover = original
    assert result.converged and result.path == "fallback"
    assert any("502" in note for note in result.notes)


def test_a_chat_route_failure_falls_back_to_a_direct_preview():
    from simulation.appdriver import AppDriver, DriverError

    original = AppDriver.chat

    def broken(self, agent_id, messages):
        raise DriverError(502, "The agent could not reply.")

    AppDriver.chat = broken
    try:
        with _install(FakeGroq(_behavior(rewrites_needed=1))):
            result = run_flywheel(SCENARIO, trial=0)
    finally:
        AppDriver.chat = original
    assert result.converged and result.path == "fallback"
    assert result.previews == 2  # the failed chat call is not a preview; one failing preview, one rewrite


def test_a_failed_save_of_a_passing_run_does_not_undo_convergence():
    from simulation.appdriver import AppDriver, DriverError

    class AlwaysSafe(FakeGroq):
        def _agent_chat(self, system):
            return "I can't promise that, but I can pass this to the billing team."

    original = AppDriver.save_run

    def broken(self, agent_id, transcript):
        raise DriverError(502, "The run could not be saved.")

    AppDriver.save_run = broken
    try:
        with _install(AlwaysSafe(_behavior())):
            result = run_flywheel(SCENARIO, trial=0)
    finally:
        AppDriver.save_run = original
    assert result.converged is True
    assert result.previews == 1  # no second preview: the pass was recorded before the save
    assert any("could not be saved" in note and "pass still counts" in note for note in result.notes)
    assert not any("Falling back" in note for note in result.notes)


def test_a_promoted_fix_whose_next_preview_still_fails_falls_back_and_converges():
    class ReferenceFailsAfterTheFix(FakeGroq):
        """The app's own judge works as usual. The reference judge fails the first three previews: the
        two before the app fix and the first one after it, then behaves normally."""

        reference_calls = 0

        def _judge(self, user: str) -> str:
            if SCENARIO.goal.name in user:
                type(self).reference_calls += 1
                if type(self).reference_calls <= 3:
                    return json.dumps({"verdict": "fail", "rationale": "The reference goal is not met."})
            return super()._judge(user)

    ReferenceFailsAfterTheFix.reference_calls = 0
    fake = ReferenceFailsAfterTheFix(_behavior(rewrites_needed=99))
    with _install(fake):
        result = run_flywheel(SCENARIO, trial=0)
    assert result.converged is True
    assert result.path == "fallback"
    assert result.previews == 4  # two failing, one after the promoted fix (still failing), one after a rewrite
    assert fake.rewrite_calls == 1
    assert result.final_prompt_changed is True
    assert "The promoted fix did not pass the reference judge." in result.notes
    assert "Falling back to manual prompt rewrites." in result.notes
