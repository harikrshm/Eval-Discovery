import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.core.config import CriterionSpec
from backend.db.models import Agent, EvalResult, EvalTest
from evalgate import suite as suite_mod
from evalgate.suite import (
    FORMAT,
    MAX_MESSAGE_CHARS,
    MAX_MESSAGES_PER_TEST,
    MAX_PROMPT_CHARS,
    MAX_SUITE_BYTES,
    MAX_TESTS,
    SUPPORTED_VERSION,
    Suite,
    SuiteError,
    SuiteTest,
    load_prompt,
    load_suite,
    parse_suite,
    select_tests,
)


def _test(tid="t1", role="fix", verdict="pass", messages=("hi", "there")):
    return {
        "id": tid,
        "role": role,
        "origin": "run",
        "scripted_user_messages": list(messages),
        "current_verdict": verdict,
    }


def _doc():
    return {
        "format": "eval-discovery.suite",
        "version": 1,
        "agent": {"name": "Alex", "user_role_label": "Prospect"},
        "prompt": {"text": "You are Alex.", "version_number": 3},
        "criteria": [
            {
                "id": "c1",
                "name": "Answers pricing",
                "definition": "def one",
                "good_example": "good one",
                "bad_example": "bad one",
                "tests": [_test("t1", "fix", "pass"), _test("t2", "heldout", "fail"), _test("t3", "regression", None)],
            },
            {
                "id": "c2",
                "name": "Stays polite",
                "definition": "def two",
                "good_example": "good two",
                "bad_example": "bad two",
                "tests": [],
            },
        ],
    }


def _bad(mutate, match):
    doc = _doc()
    mutate(doc)
    with pytest.raises(SuiteError, match=match):
        parse_suite(doc)


def test_constants():
    assert FORMAT == "eval-discovery.suite"
    assert SUPPORTED_VERSION == 1
    assert (MAX_TESTS, MAX_MESSAGES_PER_TEST, MAX_MESSAGE_CHARS) == (60, 20, 2000)
    assert (MAX_PROMPT_CHARS, MAX_SUITE_BYTES) == (4000, 2_000_000)


def test_valid_export_parses():
    suite = parse_suite(_doc())
    assert isinstance(suite, Suite)
    assert (suite.agent_name, suite.user_role_label) == ("Alex", "Prospect")
    assert (suite.prompt_text, suite.version_number) == ("You are Alex.", 3)
    assert [t.id for t in suite.tests] == ["t1", "t2", "t3"]
    first = suite.tests[0]
    assert isinstance(first, SuiteTest)
    assert first.role == "fix" and first.origin == "run"
    assert first.criterion_name == "Answers pricing"
    assert first.criterion == CriterionSpec("Answers pricing", "def one", "good one", "bad one")
    assert first.messages == ("hi", "there")
    assert first.current_verdict == "pass"
    assert suite.tests[2].current_verdict is None


def test_criterion_with_no_tests_is_fine():
    doc = _doc()
    doc["criteria"] = [doc["criteria"][1]]
    assert parse_suite(doc).tests == ()


def test_real_export_route_output_parses(client, auth_headers, db_session):
    agent_id = client.post("/agents", json={"preset_id": "alex-sales"}, headers=auth_headers).json()["id"]
    body = {"name": "Answers pricing", "definition": "d", "good_example": "g", "bad_example": "b"}
    cid = client.post(f"/agents/{agent_id}/criteria", json=body, headers=auth_headers).json()["id"]
    test = EvalTest(criterion_id=cid, role="fix", origin="run", source_run_id="r", scripted_user_messages=["a", "b"])
    db_session.add(test)
    db_session.commit()
    version_id = db_session.get(Agent, agent_id).current_prompt_version_id
    db_session.add(
        EvalResult(
            test_id=test.id,
            prompt_version_id=version_id,
            kind="replay",
            verdict="pass",
            rationale="r",
            transcript=[],
            stale=False,
        )
    )
    db_session.commit()

    exported = client.get(f"/agents/{agent_id}/export", headers=auth_headers).json()
    suite = parse_suite(exported)

    assert len(suite.tests) == 1
    assert suite.tests[0].messages == ("a", "b")
    assert suite.tests[0].current_verdict == "pass"
    assert suite.tests[0].criterion.name == "Answers pricing"


def test_not_a_mapping():
    with pytest.raises(SuiteError, match="suite"):
        parse_suite([1, 2])


def test_wrong_format():
    _bad(lambda d: d.update(format="other"), "format")


def test_unsupported_version():
    _bad(lambda d: d.update(version=2), "version")


@pytest.mark.parametrize("bad", [True, 1.0, "1", None])
def test_version_must_be_exact_int(bad):
    _bad(lambda d: d.update(version=bad), "version")


@pytest.mark.parametrize("key", ["format", "version", "agent", "prompt", "criteria"])
def test_missing_top_key(key):
    _bad(lambda d: d.pop(key), key)


def test_missing_nested_keys():
    _bad(lambda d: d["agent"].pop("name"), "agent.name")
    _bad(lambda d: d["prompt"].pop("text"), "prompt.text")
    _bad(lambda d: d["criteria"][0].pop("definition"), r"criteria\[0\].definition")
    _bad(lambda d: d["criteria"][0]["tests"][1].pop("origin"), r"criteria\[0\].tests\[1\].origin")
    _bad(lambda d: d["criteria"][0]["tests"][1].pop("current_verdict"), r"tests\[1\].current_verdict")


def test_wrong_types():
    _bad(lambda d: d.update(agent="x"), "agent")
    _bad(lambda d: d["agent"].update(name=5), "agent.name")
    _bad(lambda d: d["prompt"].update(version_number=True), "prompt.version_number")
    _bad(lambda d: d["prompt"].update(version_number="3"), "prompt.version_number")
    _bad(lambda d: d["prompt"].update(text=None), "prompt.text")
    _bad(lambda d: d.update(criteria={}), "criteria")
    _bad(lambda d: d["criteria"].__setitem__(0, "x"), r"criteria\[0\]")
    _bad(lambda d: d["criteria"][0].update(name=1), r"criteria\[0\].name")
    _bad(lambda d: d["criteria"][0].update(tests="x"), r"criteria\[0\].tests")
    _bad(lambda d: d["criteria"][0]["tests"][0].update(id=7), r"tests\[0\].id")
    _bad(lambda d: d["criteria"][0]["tests"][0].update(scripted_user_messages="hi"), "scripted_user_messages")
    _bad(lambda d: d["criteria"][0]["tests"][0].update(scripted_user_messages=[1]), r"scripted_user_messages\[0\]")


def test_unknown_role_and_verdict():
    _bad(lambda d: d["criteria"][0]["tests"][0].update(role="boss"), r"criteria\[0\].tests\[0\].role")
    _bad(lambda d: d["criteria"][0]["tests"][0].update(current_verdict="maybe"), "current_verdict")
    _bad(lambda d: d["criteria"][0]["tests"][0].update(current_verdict=True), "current_verdict")


def test_too_many_tests():
    doc = _doc()
    doc["criteria"][1]["tests"] = [_test(f"x{i}") for i in range(MAX_TESTS - 3)]
    assert len(parse_suite(doc).tests) == MAX_TESTS
    doc["criteria"][1]["tests"].append(_test("extra"))
    with pytest.raises(SuiteError, match="at most 60"):
        parse_suite(doc)


def test_message_count_cap():
    ok = _doc()
    ok["criteria"][0]["tests"][0]["scripted_user_messages"] = ["m"] * MAX_MESSAGES_PER_TEST
    parse_suite(ok)
    _bad(
        lambda d: d["criteria"][0]["tests"][0].update(scripted_user_messages=["m"] * 21),
        r"criteria\[0\].tests\[0\].scripted_user_messages",
    )
    _bad(lambda d: d["criteria"][0]["tests"][0].update(scripted_user_messages=[]), "scripted_user_messages")


def test_message_size_and_blank():
    ok = _doc()
    ok["criteria"][0]["tests"][0]["scripted_user_messages"] = ["x" * MAX_MESSAGE_CHARS]
    parse_suite(ok)
    big = "x" * (MAX_MESSAGE_CHARS + 1)
    doc = _doc()
    doc["criteria"][0]["tests"][0]["scripted_user_messages"] = ["ok", big]
    with pytest.raises(SuiteError, match=r"scripted_user_messages\[1\]") as excinfo:
        parse_suite(doc)
    assert big not in str(excinfo.value)
    _bad(lambda d: d["criteria"][0]["tests"][0].update(scripted_user_messages=["  "]), r"scripted_user_messages\[0\]")


def test_errors_never_echo_huge_input():
    doc = _doc()
    huge = "Z" * 50_000
    doc["format"] = huge
    with pytest.raises(SuiteError) as excinfo:
        parse_suite(doc)
    assert huge not in str(excinfo.value)
    assert len(str(excinfo.value)) < 500


def test_select_tests_default_and_include_all():
    suite = parse_suite(_doc())
    selected, skipped = select_tests(suite, include_all=False)
    assert [t.id for t in selected] == ["t1"]
    assert skipped == 2
    everything, skipped_all = select_tests(suite, include_all=True)
    assert [t.id for t in everything] == ["t1", "t2", "t3"]
    assert skipped_all == 0


def test_load_suite_roundtrip(tmp_path):
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(_doc()), encoding="utf-8")
    assert len(load_suite(path).tests) == 3
    assert len(load_suite(str(path)).tests) == 3


def test_load_suite_invalid_json(tmp_path):
    path = tmp_path / "suite.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SuiteError, match="JSON"):
        load_suite(path)


def test_load_suite_missing_file(tmp_path):
    with pytest.raises(SuiteError, match="read"):
        load_suite(tmp_path / "nope.json")


def test_load_suite_directory(tmp_path):
    with pytest.raises(SuiteError):
        load_suite(tmp_path)


def test_load_suite_non_utf8(tmp_path):
    path = tmp_path / "suite.json"
    path.write_bytes(b'{"format": "\xff\xfe"}')
    with pytest.raises(SuiteError, match="UTF-8"):
        load_suite(path)


def test_load_suite_oversize_checked_before_parsing(tmp_path, monkeypatch):
    path = tmp_path / "suite.json"
    path.write_bytes(b" " * (MAX_SUITE_BYTES + 1))

    def boom(*args, **kwargs):
        raise AssertionError("parsed an oversize file")

    monkeypatch.setattr(suite_mod.json, "loads", boom)
    with pytest.raises(SuiteError, match="too large"):
        load_suite(path)


def test_load_prompt(tmp_path):
    path = tmp_path / "prompt.txt"
    path.write_text("  \n Be kind.\n\n", encoding="utf-8")
    assert load_prompt(path) == "Be kind."
    path.write_text("x" * MAX_PROMPT_CHARS, encoding="utf-8")
    assert len(load_prompt(path)) == MAX_PROMPT_CHARS


@pytest.mark.parametrize("content", ["", "   \n\t "])
def test_load_prompt_blank(tmp_path, content):
    path = tmp_path / "prompt.txt"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(SuiteError, match="empty"):
        load_prompt(path)


def test_load_prompt_too_long(tmp_path):
    path = tmp_path / "prompt.txt"
    path.write_text("x" * (MAX_PROMPT_CHARS + 1), encoding="utf-8")
    with pytest.raises(SuiteError, match="4000"):
        load_prompt(path)


def test_load_prompt_huge_file_rejected(tmp_path):
    path = tmp_path / "prompt.txt"
    path.write_bytes(b"x" * (MAX_SUITE_BYTES + 1))
    with pytest.raises(SuiteError):
        load_prompt(path)


def test_load_prompt_non_utf8_and_missing(tmp_path):
    path = tmp_path / "prompt.txt"
    path.write_bytes(b"\xff\xfe\xfa")
    with pytest.raises(SuiteError, match="UTF-8"):
        load_prompt(path)
    with pytest.raises(SuiteError, match="read"):
        load_prompt(tmp_path / "missing.txt")


def test_module_does_not_import_fastapi_or_sqlalchemy():
    code = (
        "import sys; import evalgate.suite; "
        "bad = [m for m in sys.modules if m.split('.')[0] in ('fastapi', 'sqlalchemy')]; "
        "print(bad); sys.exit(1 if bad else 0)"
    )
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_parse_does_not_mutate_input():
    doc = _doc()
    snapshot = copy.deepcopy(doc)
    parse_suite(doc)
    assert doc == snapshot
