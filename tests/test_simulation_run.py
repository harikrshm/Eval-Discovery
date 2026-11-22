"""The simulation run and report CLIs. No network: mock mode uses the fake model, real mode patches the arms."""

import json
from pathlib import Path

import pytest

import backend.groq_client as groq_client
from simulation import arms, report, run
from simulation.arms import TrialResult
from simulation.fakeclient import FakeGroq
from simulation.scenarios import SCENARIOS

REPO = Path(__file__).resolve().parent.parent
KEY = "gsk_UNIQUE-KEY-MARKER-91c4e"
BANNER = "MOCK MODE: harness test only, the numbers are meaningless."
DISCLAIMER = (
    "These are simulated figures and are not measurements of real builders; "
    "they are not compared with the strategy document's illustrative targets."
)


def _trial(scenario="support-refund", arm="baseline", trial=1, converged=True, previews=3, path=None, **kw):
    if path is None:
        path = "fallback" if arm == "baseline" and converged else "none"
    return TrialResult(
        scenario=scenario,
        arm=arm,
        trial=trial,
        converged=converged,
        previews=previews,
        path=path,
        final_prompt_changed=converged,
        model_calls=kw.get("model_calls", {"m": 10}),
        model_tokens=kw.get("model_tokens", {"m": 1000}),
        wall_seconds=kw.get("wall_seconds", 5.0),
        notes=kw.get("notes", []),
    )


def _write(root: Path, mode: str, results, meta=None):
    folder = root / mode
    folder.mkdir(parents=True, exist_ok=True)
    for r in results:
        (folder / f"{r.scenario}-{r.arm}-{r.trial}.json").write_text(json.dumps(r.to_dict()), encoding="utf-8")
    meta = meta or {
        "mode": mode,
        "date": "2026-11-22T10:00:00+00:00",
        "trials": 4,
        "scenarios": ["support-refund"],
        "models": {"chat": groq_client.MODEL_CHAT, "judge": groq_client.MODEL_JUDGE},
        "max_previews": 12,
        "git_commit": "abc1234",
        "pacing_tpm_limit": None,
    }
    (folder / "run-meta.json").write_text(json.dumps(meta), encoding="utf-8")


@pytest.fixture
def crafted(tmp_path):
    results = [
        # baseline: previews 2, 3, 6, 9 -> median 4.5, mean 5.0
        _trial(arm="baseline", trial=1, previews=2),
        _trial(arm="baseline", trial=2, previews=3),
        _trial(arm="baseline", trial=3, previews=6),
        _trial(arm="baseline", trial=4, previews=9),
        # flywheel: 1 (no fix needed), 4 (loop), 6 (fallback), non-converged recorded as 7 -> counted at cap 12
        _trial(arm="flywheel", trial=1, previews=1, path="none"),
        _trial(arm="flywheel", trial=2, previews=4, path="loop"),
        _trial(arm="flywheel", trial=3, previews=6, path="fallback"),
        _trial(arm="flywheel", trial=4, previews=7, converged=False, path="none", notes=["The app found no loop."]),
    ]
    _write(tmp_path, "real", results)
    return tmp_path


@pytest.fixture(autouse=True)
def _isolate_client(monkeypatch):
    """Real mode may rebuild groq_client.client and set pacing; undo both after every test."""
    monkeypatch.setattr(groq_client, "client", groq_client.client)
    yield
    groq_client.configure_pacing(None)


class CountingFake(FakeGroq):
    created: list = []

    def __init__(self, behavior):
        super().__init__(behavior)
        self.total_calls = 0
        CountingFake.created.append(self)

    def _create(self, **kwargs):
        self.total_calls += 1
        return super()._create(**kwargs)


@pytest.fixture
def counting(monkeypatch):
    CountingFake.created = []
    monkeypatch.setattr(run, "make_fake_client", lambda scenario: CountingFake(run.fake_behavior(scenario)))
    return CountingFake


# --- run ---------------------------------------------------------------------------------------


def test_mock_run_writes_every_trial_and_meta(tmp_path, capsys):
    code = run.main(["--mode", "mock", "--trials", "2", "--out", str(tmp_path)])
    assert code == 0
    files = sorted(p.name for p in (tmp_path / "mock").glob("*.json"))
    expected = sorted(
        [f"{s}-{a}-{t}.json" for s in SCENARIOS for a in ("baseline", "flywheel") for t in (1, 2)] + ["run-meta.json"]
    )
    assert files == expected
    meta = json.loads((tmp_path / "mock" / "run-meta.json").read_text(encoding="utf-8"))
    assert meta["mode"] == "mock"
    assert meta["trials"] == 2
    assert meta["scenarios"] == list(SCENARIOS)
    assert meta["max_previews"] == 12
    assert meta["models"]["chat"] == groq_client.MODEL_CHAT
    assert meta["models"]["judge"] == groq_client.MODEL_JUDGE
    assert meta["pacing_tpm_limit"] is None
    assert "date" in meta and "git_commit" in meta
    one = TrialResult.from_dict(json.loads((tmp_path / "mock" / "support-refund-flywheel-1.json").read_text("utf-8")))
    assert one.scenario == "support-refund" and one.arm == "flywheel" and one.trial == 1
    assert BANNER in capsys.readouterr().out


def test_trials_are_interleaved_scenario_trial_arm(tmp_path, monkeypatch):
    order = []

    def fake_arm(arm):
        def go(scenario, *, trial, max_previews=12):
            order.append((scenario.name, trial, arm))
            return _trial(scenario=scenario.name, arm=arm, trial=trial)

        return go

    monkeypatch.setattr(arms, "run_baseline", fake_arm("baseline"))
    monkeypatch.setattr(arms, "run_flywheel", fake_arm("flywheel"))
    assert run.main(["--mode", "mock", "--trials", "2", "--scenarios", "support-refund", "--out", str(tmp_path)]) == 0
    assert order == [
        ("support-refund", 1, "baseline"),
        ("support-refund", 1, "flywheel"),
        ("support-refund", 2, "baseline"),
        ("support-refund", 2, "flywheel"),
    ]


def test_resume_skips_existing_trials(tmp_path, counting):
    args = ["--mode", "mock", "--trials", "1", "--scenarios", "support-refund", "--out", str(tmp_path)]
    assert run.main(args) == 0
    assert len(counting.created) == 2  # one fake per trial: baseline and flywheel
    counting.created.clear()

    assert run.main([*args, "--resume"]) == 0
    assert counting.created == []  # nothing re-ran, so no model call was made

    (tmp_path / "mock" / "support-refund-flywheel-1.json").unlink()
    assert run.main([*args, "--resume"]) == 0
    assert len(counting.created) == 1  # only the missing trial ran
    assert counting.created[0].total_calls > 0
    assert (tmp_path / "mock" / "support-refund-flywheel-1.json").exists()


def test_without_resume_trials_run_again(tmp_path, counting):
    args = ["--mode", "mock", "--trials", "1", "--scenarios", "support-refund", "--out", str(tmp_path)]
    run.main(args)
    run.main(args)
    assert len(counting.created) == 4


def test_unknown_scenario_exits_2(tmp_path, capsys):
    assert run.main(["--mode", "mock", "--scenarios", "support-refund,nope", "--out", str(tmp_path)]) == 2
    assert "nope" in capsys.readouterr().err
    assert not (tmp_path / "mock").exists()


def test_crashing_trial_is_skipped_and_run_continues(tmp_path, monkeypatch, capsys):
    def boom(scenario, *, trial, max_previews=12):
        raise RuntimeError(f"secret detail {KEY}")

    monkeypatch.setattr(arms, "run_baseline", boom)
    monkeypatch.setattr(arms, "run_flywheel", lambda s, *, trial, max_previews=12: _trial(s.name, "flywheel", trial))
    run.main(["--mode", "mock", "--trials", "1", "--scenarios", "support-refund", "--out", str(tmp_path)])
    out = capsys.readouterr()
    assert "RuntimeError" in out.out + out.err
    assert KEY not in out.out + out.err and "secret detail" not in out.out + out.err
    names = {p.name for p in (tmp_path / "mock").glob("*.json")}
    assert "support-refund-flywheel-1.json" in names
    assert "support-refund-baseline-1.json" not in names


@pytest.mark.parametrize("value", [None, "", "sk-test-placeholder", "your_groq_api_key_here"])
def test_real_mode_without_a_usable_key_exits_3(tmp_path, monkeypatch, capsys, value):
    if value is None:
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
    else:
        monkeypatch.setenv("GROQ_API_KEY", value)
    assert run.main(["--mode", "real", "--out", str(tmp_path)]) == 3
    out = capsys.readouterr()
    assert "GROQ_API_KEY is not set." in out.err
    assert not (tmp_path / "real").exists()


def test_real_mode_prints_estimate_and_never_the_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    monkeypatch.setenv("GROQ_TPM_LIMIT", "4321")
    limits = []
    monkeypatch.setattr(run, "configure_pacing", lambda limit: limits.append(limit))
    monkeypatch.setattr(arms, "run_baseline", lambda s, *, trial, max_previews=12: _trial(s.name, "baseline", trial))
    monkeypatch.setattr(arms, "run_flywheel", lambda s, *, trial, max_previews=12: _trial(s.name, "flywheel", trial))
    code = run.main(["--mode", "real", "--trials", "2", "--scenarios", "support-refund", "--out", str(tmp_path)])
    out = capsys.readouterr()
    assert code == 0
    assert "ESTIMATE" in out.out
    assert "model calls" in out.out and "minutes" in out.out
    assert KEY not in out.out + out.err
    assert limits[0] == 4321 and limits[-1] is None  # enabled for the run, disabled afterwards
    meta = json.loads((tmp_path / "real" / "run-meta.json").read_text(encoding="utf-8"))
    assert meta["mode"] == "real" and meta["pacing_tpm_limit"] == 4321
    assert KEY not in json.dumps(meta)
    assert BANNER not in out.out


def test_real_mode_default_pacing_is_7000(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    monkeypatch.delenv("GROQ_TPM_LIMIT", raising=False)
    limits = []
    monkeypatch.setattr(run, "configure_pacing", lambda limit: limits.append(limit))
    monkeypatch.setattr(arms, "run_baseline", lambda s, *, trial, max_previews=12: _trial(s.name, "baseline", trial))
    monkeypatch.setattr(arms, "run_flywheel", lambda s, *, trial, max_previews=12: _trial(s.name, "flywheel", trial))
    assert run.main(["--mode", "real", "--trials", "1", "--scenarios", "support-refund", "--out", str(tmp_path)]) == 0
    assert limits[0] == 7000


def test_estimate_formula_scales_with_trials_scenarios_and_arms():
    one = run.estimate(trials=1, scenarios=1, max_previews=12, tpm_limit=7000)
    four = run.estimate(trials=2, scenarios=2, max_previews=12, tpm_limit=7000)
    assert four.calls == 4 * one.calls
    assert one.minutes > 0
    assert run.estimate(trials=1, scenarios=1, max_previews=12, tpm_limit=3500).minutes > one.minutes


def test_load_dotenv_is_explicit_only(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(run, "_load_dotenv", lambda: called.append(1))
    monkeypatch.setattr(arms, "run_baseline", lambda s, *, trial, max_previews=12: _trial(s.name, "baseline", trial))
    monkeypatch.setattr(arms, "run_flywheel", lambda s, *, trial, max_previews=12: _trial(s.name, "flywheel", trial))
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    monkeypatch.setattr(run, "configure_pacing", lambda limit: None)
    base = ["--mode", "real", "--trials", "1", "--scenarios", "support-refund", "--out", str(tmp_path)]
    run.main(base)
    assert called == []
    run.main([*base, "--load-dotenv"])
    assert called == [1]


# --- report ------------------------------------------------------------------------------------


def test_report_has_required_sections_and_honesty_text(crafted):
    text = report.build_report(crafted, "real")
    assert not text.startswith(BANNER)
    for heading in ("## Results", "## Path counts", "## Trials that did not converge", "## Limitations"):
        assert heading in text
    assert "simulated" in text.lower()
    assert "N = 4" in text
    assert groq_client.MODEL_CHAT in text and groq_client.MODEL_JUDGE in text
    assert "2026-11-22" in text
    assert DISCLAIMER in text
    for phrase in (
        "tiny N",
        "LLM-played builder",
        "same model family",
        "reference judge shares the model family",
        "full goal criterion",
        "free-tier pacing",
        "roughly two previews behind",
        "30 jobs per hour",
    ):
        assert phrase.lower() in text.lower(), phrase


def test_report_medians_means_and_cap(crafted):
    text = report.build_report(crafted, "real")
    # baseline [2, 3, 6, 9]: median 4.5 (average of the two middle values), mean 5.00
    assert "| support-refund | baseline | 4 | 4 | 4.5 | 5.00 | 2 | 9 |" in text
    # flywheel [1, 4, 6, 12 (non-converged, counted at the cap)]: median 5.0, mean 5.75
    assert "| support-refund | flywheel | 4 | 3 | 5.0 | 5.75 | 1 | 12 |" in text
    # totals: 4 trials x 10 calls, 4 x 1000 tokens, 4 x 5.0 s
    assert "| 40 | 4000 | 20.0 s |" in text


def test_median_helper_handles_odd_even_and_single():
    assert report.median([5]) == 5
    assert report.median([1, 9, 3]) == 3
    assert report.median([4, 1, 2, 3]) == 2.5


def test_report_lists_non_converged_trials_separately(crafted):
    text = report.build_report(crafted, "real")
    section = text.split("## Trials that did not converge")[1].split("## ")[0]
    assert "support-refund" in section and "flywheel" in section and "trial 4" in section
    assert "The app found no loop." in section
    assert "counted at the cap of 12" in text


def test_report_without_non_converged_says_none(tmp_path):
    _write(tmp_path, "real", [_trial(trial=1), _trial(trial=2)])
    section = report.build_report(tmp_path, "real").split("## Trials that did not converge")[1].split("## ")[0]
    assert "None" in section


def test_path_counts_only_for_flywheel_and_no_fix_needed_is_not_a_failure(crafted):
    text = report.build_report(crafted, "real")
    section = text.split("## Path counts")[1].split("\n## ")[0]
    assert "n/a (manual rewrites)" in section
    assert "converged with no fix needed: 1" in section
    assert "loop: 1" in section and "fallback: 1" in section
    # the non-converged flywheel trial (path "none") is not counted under any path
    assert "did not converge: 1" in section
    assert "failure" not in section.lower().replace("not a failure", "")


def test_mock_report_carries_banner_first(tmp_path):
    _write(tmp_path, "mock", [_trial(trial=1)], meta={"mode": "mock", "date": "2026-11-22", "trials": 1, "max_previews": 12})
    text = report.build_report(tmp_path, "mock")
    assert text.startswith(BANNER)
    assert "simulated" in text.lower()


def test_report_cli_refuses_mock_results_in_docs_page(tmp_path, capsys, monkeypatch):
    _write(tmp_path, "mock", [_trial(trial=1)])
    target = REPO / "docs" / "simulation-results.md"
    before = target.exists()
    for out in (str(target), "docs/simulation-results.md", "docs/../docs/simulation-results.md"):
        monkeypatch.chdir(REPO)
        assert report.main(["--results", str(tmp_path), "--mode", "mock", "--output", out]) == 2
    assert target.exists() == before  # nothing was written
    assert "refus" in capsys.readouterr().err.lower()


def test_report_cli_writes_real_report_to_given_file(crafted, tmp_path):
    out = tmp_path / "page.md"
    assert report.main(["--results", str(crafted), "--mode", "real", "--output", str(out)]) == 0
    assert DISCLAIMER in out.read_text(encoding="utf-8")


def test_report_cli_mock_prints_to_stdout_by_default(tmp_path, capsys):
    _write(tmp_path, "mock", [_trial(trial=1)])
    assert report.main(["--results", str(tmp_path), "--mode", "mock"]) == 0
    assert capsys.readouterr().out.startswith(BANNER)


def test_report_cli_missing_results_exits_2(tmp_path, capsys):
    assert report.main(["--results", str(tmp_path / "nothing"), "--mode", "real", "--output", str(tmp_path / "x.md")]) == 2


def test_default_real_output_is_the_docs_page():
    assert report.DEFAULT_REAL_OUTPUT == REPO / "docs" / "simulation-results.md"


def test_end_to_end_mock_run_then_report(tmp_path):
    assert run.main(["--mode", "mock", "--trials", "2", "--out", str(tmp_path)]) == 0
    text = report.build_report(tmp_path, "mock")
    assert text.startswith(BANNER)
    assert "| support-refund | baseline | 2 |" in text
    assert "| booking-confirm | flywheel | 2 |" in text
