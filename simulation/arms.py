"""The two simulated arms and the trial result.

Metric: "preview runs until the first judged pass". A **preview** is one conversation: the
synthetic builder writes two customer messages (``next_customer_message``, ``MODEL_CHAT``), each
answered by the agent on its current prompt, and the independent reference judge
(``simulation.reference.judge_preview``, which grades the scenario's own goal and never the
criterion the app discovers) decides. Only ``pass`` converges; ``fail`` and ``not_applicable`` are
non-passes. A trial stops at the first pass or after ``max_previews`` (12) previews, and a trial
that never passes is RETURNED (``converged=False``, ``previews == max_previews``, ``path "none"``),
never dropped or raised.

Both arms start from the scenario's weak prompt and share one preview definition, one cap, one
reference judge, and the same agent settings: the agent reply is ``groq_chat(prompt, history,
max_tokens=CHAT_MAX_TOKENS)`` on ``MODEL_CHAT`` exactly as the app's chat route makes it. Manual
rewrites are ``simulation.builder.rewrite_prompt`` on ``MODEL_JUDGE`` (the app suggester's model).
Model calls and tokens come from a ``CountingClient`` wrapped around whatever client is installed
when the trial starts (the fake or the real one).

Baseline state machine (no app features; its fixes are manual rewrites by definition, so a
converged baseline records ``path "fallback"``; a non-converged one records ``"none"``):

    preview -> pass: DONE (converged)
    preview -> fail: rewrite_prompt (a BuilderError is noted and the prompt stays) -> preview ...
    cap reached: DONE (not converged)

Flywheel state machine (one attempt at the app flow per trial, driven through ``AppDriver`` on a
fresh workspace; the preview goes through the app's chat route and is then saved as a run):

    APP_PREVIEW -> pass: DONE (path "loop" if a fix had been promoted, else "none": it passed
                   with no help from either mechanism, noted)
    APP_PREVIEW -> fail, and after the 2nd saved run:
        check_pattern (loop found?) -> discover -> drain -> a fix test exists? -> replay it (up to
        3 times) until it fails -> generate_heldout + drain (a 409 "already has a held-out test"
        is fine, the held-out is optional) -> suggest_fix + drain -> candidate proposed? ->
        approve + drain -> candidate promoted? -> read the new prompt (agent_prompt) -> APP_PREVIEW
    ANY step that does not go to plan (no loop, no fix test, the fix test never fails, the
    suggestion or verification fails, a DriverError or unexpected status) -> note -> MANUAL
    APP_PREVIEW after a promoted fix is still a fail -> note -> MANUAL
    MANUAL: the baseline loop (rewrite after the failing preview, preview, ...) for the rest of the
        trial on the locally tracked prompt; a pass here records path "fallback".
    cap reached: DONE (not converged, path "none")
"""

import time
from dataclasses import asdict, dataclass, field

import backend.groq_client as groq_client
from backend.api.agents import CHAT_MAX_TOKENS
from backend.api.limits import MAX_ASSISTANT_MESSAGE_CHARS
from backend.api.tests import NEEDS_FIX_TEST_ONLY
from backend.groq_client import groq_chat, request_retries
from simulation.appdriver import AppDriver, DriverError
from simulation.builder import BuilderError, next_customer_message, rewrite_prompt
from simulation.clients import CountingClient, install_client
from simulation.reference import judge_preview
from simulation.scenarios import Scenario

MAX_PREVIEWS = 12
MESSAGES_PER_PREVIEW = 2
SAVED_RUNS_BEFORE_PATTERN_CHECK = 2
MAX_FIX_REPLAYS = 3

_ARMS = ("baseline", "flywheel")
_PATHS = ("loop", "fallback", "none")


@dataclass
class TrialResult:
    scenario: str
    arm: str
    trial: int
    converged: bool
    previews: int
    path: str
    final_prompt_changed: bool
    model_calls: dict[str, int]
    model_tokens: dict[str, int]
    wall_seconds: float
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.arm not in _ARMS:
            raise ValueError(f"arm must be one of {_ARMS}, got {self.arm!r}")
        if self.path not in _PATHS:
            raise ValueError(f"path must be one of {_PATHS}, got {self.path!r}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TrialResult":
        return cls(
            scenario=str(data["scenario"]),
            arm=data["arm"],
            trial=int(data["trial"]),
            converged=bool(data["converged"]),
            previews=int(data["previews"]),
            path=data["path"],
            final_prompt_changed=bool(data["final_prompt_changed"]),
            model_calls={str(k): int(v) for k, v in data["model_calls"].items()},
            model_tokens={str(k): int(v) for k, v in data["model_tokens"].items()},
            wall_seconds=float(data["wall_seconds"]),
            notes=[str(n) for n in data.get("notes", [])],
        )


@dataclass
class _State:
    """Mutable bookkeeping for one trial."""

    scenario: Scenario
    max_previews: int
    prompt: str
    previews: int = 0
    converged: bool = False
    app_fix_promoted: bool = False
    fell_back: bool = False
    last_failing: list[dict] | None = None  # transcript of the latest failing preview, if any
    notes: list[str] = field(default_factory=list)


@dataclass
class _Preview:
    transcript: list[dict]
    passed: bool
    complete: bool  # both customer messages were answered, so the conversation is a full run


def _direct_reply(prompt: str, history: list[dict]) -> str:
    """The agent's reply the way the app's chat route makes it: same model, token cap and retries."""
    try:
        with request_retries(2):
            reply = groq_chat(prompt, history, max_tokens=CHAT_MAX_TOKENS)
    except Exception:
        raise BuilderError("the agent reply could not be generated") from None
    return reply[:MAX_ASSISTANT_MESSAGE_CHARS]


def _preview(state: _State, agent_reply) -> _Preview:
    """One preview: two customer messages, each answered by ``agent_reply(history)``, then the
    reference judge. A BuilderError makes the preview a counted failure with a note; any other
    exception (a DriverError from the app's chat route) propagates and the preview is not counted."""
    number = state.previews + 1
    transcript: list[dict] = []
    passed = False
    complete = False
    try:
        for step in range(1, MESSAGES_PER_PREVIEW + 1):
            attempt = (number - 1) * MESSAGES_PER_PREVIEW + step
            transcript.append({"role": "user", "content": next_customer_message(state.scenario, transcript, attempt)})
            transcript.append({"role": "assistant", "content": agent_reply(list(transcript))})
        complete = True
        passed = judge_preview(state.scenario, transcript) == "pass"
    except BuilderError as exc:
        state.notes.append(f"Preview {number} failed: {exc}.")
    state.previews = number
    return _Preview(transcript, passed, complete)


def _rewrite(state: _State, failing: list[dict]) -> None:
    try:
        state.prompt = rewrite_prompt(state.scenario, state.prompt, failing)
    except BuilderError as exc:
        state.notes.append(f"Prompt rewrite after preview {state.previews} failed: {exc}.")


def _manual_rewrite_loop(state: _State, failing: list[dict] | None = None) -> None:
    """The baseline's mechanism: preview; after a failing preview rewrite the prompt by hand
    (``failing`` is a failing transcript still waiting for its rewrite). Sets ``state.converged``."""
    while state.previews < state.max_previews:
        if failing:
            _rewrite(state, failing)
        outcome = _preview(state, lambda history: _direct_reply(state.prompt, history))
        if outcome.passed:
            state.converged = True
            return
        failing = outcome.transcript


def _find(suite: dict, criterion_id: str, role: str) -> dict | None:
    for criterion in suite["criteria"]:
        if criterion["criterion_id"] == criterion_id:
            for test in criterion["tests"]:
                if test["role"] == role:
                    return test
    return None


def _candidate_status(driver: AppDriver, agent_id: str) -> tuple[str | None, str | None]:
    candidate = driver.suite(agent_id)["candidate"]
    return (candidate["version_id"], candidate["status"]) if candidate else (None, None)


def _app_fix(state: _State, driver: AppDriver, agent_id: str) -> bool:
    """Run the app's detect -> discover -> tests -> suggest -> approve -> promote flow. Returns True
    when a candidate was promoted; otherwise notes why and returns False. A DriverError propagates."""
    notes = state.notes
    if not driver.check_pattern(agent_id).get("found"):
        notes.append("The app found no loop after two runs.")
        return False
    criterion = driver.discover(agent_id)
    criterion_id = criterion["id"]
    if not criterion.get("job_id"):
        notes.append("The app did not queue the test build for the discovered criterion.")
        return False
    driver.drain()
    fix = _find(driver.suite(agent_id), criterion_id, "fix")
    if fix is None:
        notes.append("The app built no fix test for the discovered criterion.")
        return False

    for _ in range(MAX_FIX_REPLAYS):
        driver.replay_test(fix["id"])
        driver.drain()
        fix = _find(driver.suite(agent_id), criterion_id, "fix")
        if fix is not None and fix["current"] and fix["current"]["verdict"] == "fail":
            break
    else:
        notes.append(f"The fix test did not fail on the current prompt in {MAX_FIX_REPLAYS} replays.")
        return False

    try:  # optional: verification is stronger with a held-out test
        driver.generate_heldout(criterion_id)
        driver.drain()
    except DriverError as exc:
        if not (exc.status == 409 and exc.detail == NEEDS_FIX_TEST_ONLY):  # 409: it already has one
            raise

    driver.suggest_fix(fix["id"])
    driver.drain()
    version_id, status = _candidate_status(driver, agent_id)
    if version_id is None or status != "proposed":
        notes.append("The app could not suggest a fix.")
        return False
    driver.approve(version_id)
    driver.drain()
    if _candidate_status(driver, agent_id)[1] != "promoted":
        notes.append("The suggested fix did not pass verification.")
        return False
    return True


def _app_loop(state: _State, driver: AppDriver) -> None:
    """The flywheel's app phase. Returns with ``state.converged`` set, the cap reached, or an
    unfinished state that needs the manual fallback. DriverErrors propagate to ``run_flywheel``."""
    scenario = state.scenario
    agent_id = driver.create_agent(scenario.agent_name, scenario.weak_prompt)
    saved_runs = 0
    fix_attempted = False
    while state.previews < state.max_previews:
        outcome = _preview(state, lambda history: driver.chat(agent_id, history))
        if outcome.complete:
            driver.save_run(agent_id, outcome.transcript)
            saved_runs += 1
        if outcome.passed:
            state.converged = True
            return
        state.last_failing = outcome.transcript
        if state.previews >= state.max_previews:
            return
        if fix_attempted:
            state.notes.append("The promoted fix did not pass the reference judge.")
            return
        if saved_runs >= SAVED_RUNS_BEFORE_PATTERN_CHECK:
            fix_attempted = True
            if not _app_fix(state, driver, agent_id):
                return
            state.prompt = driver.agent_prompt(agent_id)[0]
            state.app_fix_promoted = True
            state.last_failing = None  # that transcript came from the old prompt


def _run_trial(scenario: Scenario, arm: str, trial: int, max_previews: int, body) -> TrialResult:
    started = time.monotonic()
    state = _State(scenario, max_previews, scenario.weak_prompt)
    with install_client(CountingClient(groq_client.client)) as counting:
        body(state)
    if arm == "baseline":
        path = "fallback" if state.converged else "none"  # manual rewrites are its only fix
    elif not state.converged:
        path = "none"
    elif state.fell_back:
        path = "fallback"
    elif state.app_fix_promoted:
        path = "loop"
    else:
        path = "none"
        state.notes.append("The first judged pass came without any prompt change or app fix.")
    return TrialResult(
        scenario=scenario.name,
        arm=arm,
        trial=trial,
        converged=state.converged,
        previews=state.previews,
        path=path,
        final_prompt_changed=state.prompt != scenario.weak_prompt,
        model_calls=dict(counting.calls),
        model_tokens=dict(counting.tokens),
        wall_seconds=time.monotonic() - started,
        notes=state.notes,
    )


def run_baseline(scenario: Scenario, *, trial: int, max_previews: int = MAX_PREVIEWS) -> TrialResult:
    """Manual rewrite loop only: no detection, tests or fix flow."""
    return _run_trial(scenario, "baseline", trial, max_previews, _manual_rewrite_loop)


def _flywheel_body(state: _State) -> None:
    try:
        with AppDriver() as driver:  # a fresh workspace per trial (30 jobs an hour per workspace)
            _app_loop(state, driver)
    except DriverError as exc:
        state.notes.append(f"The app flow failed: {exc}")
    except Exception as exc:  # any failure of the app flow degrades to the manual rewrite
        state.notes.append(f"The app flow failed unexpectedly ({type(exc).__name__}).")
    if not state.converged and state.previews < state.max_previews:
        state.fell_back = True
        state.notes.append("Falling back to manual prompt rewrites.")
        _manual_rewrite_loop(state, state.last_failing)


def run_flywheel(scenario: Scenario, *, trial: int, max_previews: int = MAX_PREVIEWS) -> TrialResult:
    """The app's loop through ``AppDriver``, with the baseline's manual rewrite as the fallback."""
    return _run_trial(scenario, "flywheel", trial, max_previews, _flywheel_body)
