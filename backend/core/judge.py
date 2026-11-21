"""LLM judge: grade a transcript against a criterion, and sanity-check the judge itself.

Pure core module: takes plain arguments and never touches the API or database layers.
"""

import json
from dataclasses import dataclass

from backend.core.config import AgentConfig, CriterionSpec
from backend.core.prompts import build_judge_messages, format_transcripts
from backend.groq_client import MODEL_JUDGE, groq_chat
from backend.utils import extract_json

VERDICTS = ("pass", "fail", "not_applicable")


@dataclass(frozen=True)
class JudgeResult:
    verdict: str
    rationale: str


@dataclass(frozen=True)
class JudgeCheck:
    passed: bool
    good_verdict: str
    bad_verdict: str


class JudgeFailed(RuntimeError):
    """Raised when the judge call or its output is unusable."""


def _parse(raw: str) -> JudgeResult:
    try:
        data = json.loads(extract_json(raw))
    except (json.JSONDecodeError, TypeError) as exc:
        raise JudgeFailed("judge output was not valid JSON") from exc
    if not isinstance(data, dict):
        raise JudgeFailed("judge output must be a JSON object")

    verdict = data.get("verdict")
    if not isinstance(verdict, str) or verdict.strip().lower() not in VERDICTS:
        raise JudgeFailed("judge output has an unknown verdict")
    rationale = data.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise JudgeFailed("judge output is missing a rationale")
    return JudgeResult(verdict.strip().lower(), rationale.strip())


def _judge_text(config: AgentConfig, criterion: CriterionSpec, transcripts_text: str) -> JudgeResult:
    system, messages = build_judge_messages(config, criterion, transcripts_text)
    try:
        raw = groq_chat(system, messages, model=MODEL_JUDGE, temperature=0)
    except Exception as exc:
        raise JudgeFailed(f"judge call failed: {exc}") from exc
    return _parse(raw)


def judge(config: AgentConfig, criterion: CriterionSpec, transcript: list[dict]) -> JudgeResult:
    """Grade one transcript ({role, content} turns) against the criterion."""
    text = format_transcripts([transcript], config.name, config.user_role_label)
    return _judge_text(config, criterion, text)


def sanity_check(config: AgentConfig, criterion: CriterionSpec) -> JudgeCheck:
    """The judge must pass the criterion's good_example and fail its bad_example."""
    good = _judge_text(config, criterion, criterion.good_example)
    bad = _judge_text(config, criterion, criterion.bad_example)
    return JudgeCheck(
        passed=good.verdict == "pass" and bad.verdict == "fail",
        good_verdict=good.verdict,
        bad_verdict=bad.verdict,
    )
