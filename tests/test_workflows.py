"""The GitHub Action and demo workflow: structure and security properties, parsed from the YAML (no GitHub)."""

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
ACTION = REPO / ".github" / "actions" / "eval-gate" / "action.yml"
WORKFLOW = REPO / ".github" / "workflows" / "eval-gate-demo.yml"
CI = REPO / ".github" / "workflows" / "ci.yml"
TEST_DB_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/eval_test"


def load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def triggers(workflow):
    # PyYAML (YAML 1.1) reads the bare key `on` as the boolean True.
    return workflow["on"] if "on" in workflow else workflow[True]


def steps_of(action):
    return action["runs"]["steps"]


def test_every_yaml_file_parses():
    for path in (ACTION, WORKFLOW, CI):
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
    for doc in (load(ACTION), load(WORKFLOW), load(CI)):
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


# ---- CI workflow (.github/workflows/ci.yml) ----


def _env_maps(job):
    """Every env mapping a job declares: the job's own, then each step's."""
    maps = [job.get("env", {})]
    maps += [s.get("env", {}) for s in job["steps"]]
    return maps


def _runs(job):
    return [s["run"] for s in job["steps"] if "run" in s]


def test_ci_triggers_on_push_and_pull_request():
    assert set(triggers(load(CI))) == {"push", "pull_request"}


def test_ci_is_read_only_at_the_top_level_and_in_every_job():
    workflow = load(CI)
    assert workflow["permissions"] == {"contents": "read"}
    for name, job in workflow["jobs"].items():
        assert job.get("permissions", {"contents": "read"}) == {"contents": "read"}, name


def test_ci_has_exactly_the_three_jobs():
    assert set(load(CI)["jobs"]) == {"backend", "postgres", "frontend"}


def test_postgres_service_uses_postgres_16_with_a_health_check():
    service = load(CI)["jobs"]["postgres"]["services"]["postgres"]
    assert service["image"] == "postgres:16"
    assert service["env"]["POSTGRES_PASSWORD"] == "postgres"
    assert service["env"]["POSTGRES_DB"] == "eval_test"
    assert "5432:5432" in service["ports"]
    options = service["options"]
    assert "--health-cmd pg_isready" in options
    for option in ("--health-interval", "--health-timeout", "--health-retries"):
        assert option in options


def test_test_database_url_is_set_only_in_the_postgres_job():
    workflow = load(CI)
    assert "TEST_DATABASE_URL" not in workflow.get("env", {})
    holders = [name for name, job in workflow["jobs"].items() if any("TEST_DATABASE_URL" in m for m in _env_maps(job))]
    assert holders == ["postgres"]
    assert workflow["jobs"]["postgres"]["env"]["TEST_DATABASE_URL"] == TEST_DB_URL


def test_ci_never_references_secrets():
    assert "secrets" not in CI.read_text(encoding="utf-8").lower()


def test_backend_job_skips_dotenv_and_runs_pytest_after_install():
    backend = load(CI)["jobs"]["backend"]
    assert backend["env"]["GROQ_SKIP_DOTENV"] == "1"
    setup = [s for s in backend["steps"] if str(s.get("uses", "")).startswith("actions/setup-python@")]
    assert str(setup[0]["with"]["python-version"]) == "3.13"
    runs = _runs(backend)
    install = next(i for i, run in enumerate(runs) if "pip install -r requirements.txt" in run)
    pytest_run = next(i for i, run in enumerate(runs) if run.strip() == "python -m pytest -q")
    assert install < pytest_run


def test_postgres_job_installs_migrates_then_runs_the_pg_tests():
    job = load(CI)["jobs"]["postgres"]
    runs = _runs(job)
    install = next(i for i, run in enumerate(runs) if "pip install -r requirements.txt" in run)
    migrate = next(i for i, run in enumerate(runs) if "alembic upgrade head" in run)
    pg_tests = next(i for i, run in enumerate(runs) if run.strip() == "python -m pytest tests/pg -q")
    assert install < migrate < pg_tests


def test_alembic_smoke_step_sets_database_url_to_the_test_database():
    # alembic reads DATABASE_URL, not TEST_DATABASE_URL, so the smoke step must set it too.
    steps = load(CI)["jobs"]["postgres"]["steps"]
    smoke = [s for s in steps if "alembic upgrade head" in s.get("run", "")]
    assert len(smoke) == 1
    assert smoke[0]["env"]["DATABASE_URL"] == TEST_DB_URL
    assert "test" in TEST_DB_URL.rsplit("/", 1)[-1]


def test_frontend_job_runs_lint_test_and_build_in_order_in_the_frontend_directory():
    job = load(CI)["jobs"]["frontend"]
    setup = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/setup-node@")]
    assert str(setup[0]["with"]["node-version"]) == "22"
    npm_steps = [s for s in job["steps"] if s.get("run", "").startswith("npm ")]
    assert [s["run"] for s in npm_steps] == ["npm ci", "npm run lint", "npm test", "npm run build"]
    assert all(s["working-directory"] == "frontend" for s in npm_steps)
