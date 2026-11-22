"""The GitHub Action and demo workflow: structure and security properties, parsed from the YAML (no GitHub)."""

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
ACTION = REPO / ".github" / "actions" / "eval-gate" / "action.yml"
WORKFLOW = REPO / ".github" / "workflows" / "eval-gate-demo.yml"


def load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def triggers(workflow):
    # PyYAML (YAML 1.1) reads the bare key `on` as the boolean True.
    return workflow["on"] if "on" in workflow else workflow[True]


def steps_of(action):
    return action["runs"]["steps"]


def test_every_yaml_file_parses():
    for path in (ACTION, WORKFLOW):
        assert isinstance(load(path), dict), path


def test_action_is_composite_with_documented_inputs():
    action = load(ACTION)
    assert action["runs"]["using"] == "composite"
    inputs = action["inputs"]
    assert set(inputs) == {"suite", "prompt", "threshold", "groq-api-key", "repeat", "python-version", "extra-args"}
    for name in ("suite", "prompt", "groq-api-key"):
        assert inputs[name]["required"] is True
        assert "default" not in inputs[name]
    assert inputs["threshold"]["default"] == "1.0"
    assert inputs["repeat"]["default"] == "1"
    assert inputs["python-version"]["default"] == "3.13"
    assert inputs["extra-args"]["default"] == ""


def test_no_input_is_interpolated_inside_a_run_script():
    scripts = [s["run"] for s in steps_of(load(ACTION)) if "run" in s]
    assert scripts
    for script in scripts:
        assert "${{ inputs." not in script
        assert not re.search(r"\$\{\{\s*inputs\.", script)
        assert not re.search(r"\$\{\{\s*(secrets|github\.event)", script)


def test_every_run_step_declares_its_shell():
    for step in steps_of(load(ACTION)):
        if "run" in step:
            assert step["shell"] == "bash"


def test_api_key_reaches_only_the_gate_step_through_env():
    steps = steps_of(load(ACTION))
    holders = [s for s in steps if "groq-api-key" in yaml.safe_dump(s)]
    assert len(holders) == 1
    gate = holders[0]
    assert gate["env"]["GROQ_API_KEY"] == "${{ inputs.groq-api-key }}"
    assert gate["env"]["GROQ_SKIP_DOTENV"] == "1"
    assert "groq-api-key" not in gate["run"]


def test_gate_step_runs_the_module_with_env_passed_inputs():
    gate = [s for s in steps_of(load(ACTION)) if "evalgate" in s.get("run", "")][0]
    run = gate["run"]
    assert "python -m evalgate" in run
    assert "--format markdown" in run
    for flag in ("--suite", "--prompt", "--threshold", "--repeat"):
        assert flag in run
    env = gate["env"]
    assert env["SUITE"] == "${{ inputs.suite }}"
    assert env["PROMPT"] == "${{ inputs.prompt }}"
    assert env["THRESHOLD"] == "${{ inputs.threshold }}"
    assert env["REPEAT"] == "${{ inputs.repeat }}"
    assert env["EXTRA"] == "${{ inputs.extra-args }}"
    assert env["PYTHONPATH"].replace(" ", "") == "${{github.action_path}}/../../.."


def test_install_step_uses_gate_requirements_and_python_input():
    steps = steps_of(load(ACTION))
    setup = [s for s in steps if str(s.get("uses", "")).startswith("actions/setup-python@")][0]
    assert setup["with"]["python-version"] == "${{ inputs.python-version }}"
    install = [s for s in steps if "pip install" in s.get("run", "")][0]
    assert "requirements-gate.txt" in install["run"]


def test_actions_are_pinned_to_a_major_version():
    uses = []
    for doc in (load(ACTION), load(WORKFLOW)):
        if "runs" in doc:
            steps = steps_of(doc)
        else:
            steps = [s for job in doc["jobs"].values() for s in job["steps"]]
        uses += [s["uses"] for s in steps if "uses" in s]
    assert uses
    for ref in uses:
        if ref.startswith("actions/"):
            assert re.fullmatch(r"actions/[\w.-]+@v\d+", ref), ref


def test_demo_workflow_is_least_privilege_and_path_filtered():
    workflow = load(WORKFLOW)
    assert workflow["permissions"] == {"contents": "read"}
    on = triggers(workflow)
    assert on["pull_request"]["paths"] == ["examples/eval-gate-demo/prompt.md"]
    assert "workflow_dispatch" in on
    assert "pull_request_target" not in on


def test_demo_workflow_uses_the_local_action():
    workflow = load(WORKFLOW)
    assert len(workflow["jobs"]) == 1
    steps = next(iter(workflow["jobs"].values()))["steps"]
    assert steps[0]["uses"] == "actions/checkout@v4"
    gate = steps[1]
    assert gate["uses"] == "./.github/actions/eval-gate"
    assert gate["with"]["suite"] == "examples/eval-gate-demo/suite.json"
    assert gate["with"]["prompt"] == "examples/eval-gate-demo/prompt.md"
    assert gate["with"]["groq-api-key"] == "${{ secrets.GROQ_API_KEY }}"


def _pins(path):
    lines = (REPO / path).read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")}


def test_requirements_gate_pins_match_requirements():
    gate = _pins("requirements-gate.txt")
    assert {p.split("==")[0] for p in gate} == {"groq", "python-dotenv"}
    assert gate <= _pins("requirements.txt")


def test_pyyaml_is_a_pinned_dev_requirement():
    assert "PyYAML==6.0.2" in _pins("requirements.txt")
