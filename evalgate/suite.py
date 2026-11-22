"""Load and strictly validate an exported `eval-discovery.suite` v1 document.

Standard library plus `backend.core.config` only: the gate must run without the web stack installed.
Every error is a `SuiteError` whose message names the offending field and never echoes input values.
"""

import json
from dataclasses import dataclass
from os import PathLike
from pathlib import Path

from backend.core.config import MAX_PROMPT_CHARS, CriterionSpec

FORMAT = "eval-discovery.suite"
SUPPORTED_VERSION = 1
MAX_TESTS = 60
MAX_MESSAGES_PER_TEST = 20
MAX_MESSAGE_CHARS = 2000
MAX_SUITE_BYTES = 2_000_000

ROLES = frozenset({"fix", "heldout", "regression"})
VERDICTS = frozenset({"pass", "fail", "not_applicable"})

__all__ = [
    "FORMAT",
    "MAX_MESSAGES_PER_TEST",
    "MAX_MESSAGE_CHARS",
    "MAX_PROMPT_CHARS",
    "MAX_SUITE_BYTES",
    "MAX_TESTS",
    "SUPPORTED_VERSION",
    "Suite",
    "SuiteError",
    "SuiteTest",
    "load_prompt",
    "load_suite",
    "parse_suite",
    "select_tests",
]


class SuiteError(ValueError):
    """The suite or prompt input is unusable. The message is safe to show a user."""


@dataclass(frozen=True)
class SuiteTest:
    id: str
    role: str
    origin: str
    criterion_name: str
    criterion: CriterionSpec
    messages: tuple[str, ...]
    current_verdict: str | None


@dataclass(frozen=True)
class Suite:
    agent_name: str
    user_role_label: str
    prompt_text: str
    version_number: int
    tests: tuple[SuiteTest, ...]


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _object(value: object, path: str) -> dict:
    if not isinstance(value, dict):
        raise SuiteError(f"{path}: must be a JSON object.")
    return value


def _field(obj: dict, key: str, path: str) -> object:
    if key not in obj:
        raise SuiteError(f"{_join(path, key)}: is required.")
    return obj[key]


def _string(obj: dict, key: str, path: str) -> str:
    value = _field(obj, key, path)
    if not isinstance(value, str):
        raise SuiteError(f"{_join(path, key)}: must be a string.")
    return value


def _list(obj: dict, key: str, path: str) -> list:
    value = _field(obj, key, path)
    if not isinstance(value, list):
        raise SuiteError(f"{_join(path, key)}: must be a list.")
    return value


def _parse_messages(raw: object, path: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise SuiteError(f"{path}: must be a list of strings.")
    if not 1 <= len(raw) <= MAX_MESSAGES_PER_TEST:
        raise SuiteError(f"{path}: must have between 1 and {MAX_MESSAGES_PER_TEST} messages.")
    messages = []
    for i, message in enumerate(raw):
        if not isinstance(message, str):
            raise SuiteError(f"{path}[{i}]: must be a string.")
        if not message.strip():
            raise SuiteError(f"{path}[{i}]: must not be blank.")
        if len(message) > MAX_MESSAGE_CHARS:
            raise SuiteError(f"{path}[{i}]: must be at most {MAX_MESSAGE_CHARS} characters.")
        messages.append(message)
    return tuple(messages)


def _parse_test(raw: object, path: str, criterion: CriterionSpec) -> SuiteTest:
    obj = _object(raw, path)
    role = _string(obj, "role", path)
    if role not in ROLES:
        raise SuiteError(f"{path}.role: must be one of fix, heldout, regression.")
    verdict = _field(obj, "current_verdict", path)
    if verdict is not None and (not isinstance(verdict, str) or verdict not in VERDICTS):
        raise SuiteError(f"{path}.current_verdict: must be pass, fail, not_applicable or null.")
    return SuiteTest(
        id=_string(obj, "id", path),
        role=role,
        origin=_string(obj, "origin", path),
        criterion_name=criterion.name,
        criterion=criterion,
        messages=_parse_messages(_field(obj, "scripted_user_messages", path), f"{path}.scripted_user_messages"),
        current_verdict=verdict,
    )


def parse_suite(data: object) -> Suite:
    """Validate a decoded export document and return it as immutable dataclasses."""
    top = _object(data, "suite")
    if _field(top, "format", "") != FORMAT:
        raise SuiteError(f"format: must be {FORMAT!r}.")
    version = _field(top, "version", "")
    if type(version) is not int or version != SUPPORTED_VERSION:
        raise SuiteError(f"version: unsupported, this gate reads version {SUPPORTED_VERSION}.")

    agent = _object(_field(top, "agent", ""), "agent")
    prompt = _object(_field(top, "prompt", ""), "prompt")
    agent_name = _string(agent, "name", "agent")
    user_role_label = _string(agent, "user_role_label", "agent")
    prompt_text = _string(prompt, "text", "prompt")
    version_number = _field(prompt, "version_number", "prompt")
    if type(version_number) is not int:
        raise SuiteError("prompt.version_number: must be an integer.")

    tests: list[SuiteTest] = []
    for ci, raw_criterion in enumerate(_list(top, "criteria", "")):
        cpath = f"criteria[{ci}]"
        cobj = _object(raw_criterion, cpath)
        _string(cobj, "id", cpath)
        spec = CriterionSpec(
            name=_string(cobj, "name", cpath),
            definition=_string(cobj, "definition", cpath),
            good_example=_string(cobj, "good_example", cpath),
            bad_example=_string(cobj, "bad_example", cpath),
        )
        for ti, raw_test in enumerate(_list(cobj, "tests", cpath)):
            if len(tests) >= MAX_TESTS:
                raise SuiteError(f"{cpath}.tests[{ti}]: the suite may have at most {MAX_TESTS} tests.")
            tests.append(_parse_test(raw_test, f"{cpath}.tests[{ti}]", spec))

    return Suite(
        agent_name=agent_name,
        user_role_label=user_role_label,
        prompt_text=prompt_text,
        version_number=version_number,
        tests=tuple(tests),
    )


def _read_text(path: str | PathLike, label: str) -> str:
    """Read a UTF-8 file, refusing anything over MAX_SUITE_BYTES before its content is decoded or parsed."""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        raise SuiteError(f"{label}: could not read the file.") from None
    if size > MAX_SUITE_BYTES:
        raise SuiteError(f"{label}: file is too large (limit {MAX_SUITE_BYTES} bytes).")
    try:
        with p.open("rb") as handle:
            raw = handle.read(MAX_SUITE_BYTES + 1)  # bounded even if the file grew since stat
    except OSError:
        raise SuiteError(f"{label}: could not read the file.") from None
    if len(raw) > MAX_SUITE_BYTES:
        raise SuiteError(f"{label}: file is too large (limit {MAX_SUITE_BYTES} bytes).")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SuiteError(f"{label}: file is not valid UTF-8.") from None


def load_suite(path: str | PathLike) -> Suite:
    text = _read_text(path, "suite file")
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        raise SuiteError("suite file: not valid JSON.") from None
    return parse_suite(data)


def load_prompt(path: str | PathLike) -> str:
    text = _read_text(path, "prompt file").strip()
    if not text:
        raise SuiteError("prompt file: is empty.")
    if len(text) > MAX_PROMPT_CHARS:
        raise SuiteError(f"prompt file: must be at most {MAX_PROMPT_CHARS} characters.")
    return text


def select_tests(suite: Suite, include_all: bool) -> tuple[list[SuiteTest], int]:
    """Default to tests that currently pass (regressions to guard); return the tests and the skipped count."""
    if include_all:
        return list(suite.tests), 0
    chosen = [t for t in suite.tests if t.current_verdict == "pass"]
    return chosen, len(suite.tests) - len(chosen)
