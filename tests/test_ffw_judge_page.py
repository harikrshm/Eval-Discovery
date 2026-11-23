"""Final fix wave I2, I4, I5: the judge sees the page for flagged assistant runs, the shared delimiter
list is untouched, and the page text is capped after escaping."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.core.assist.answer import JUDGE_BRIEF_CHARS, judge_brief_text
from backend.core.assist.brief import Brief
from backend.core.config import AgentConfig, CriterionSpec
from backend.core.judge import JudgeResult
from backend.core.prompts import _DELIMITER_TAGS, _neutralize, build_judge_messages
from backend.db.agents import create_agent
from backend.db.models import Criterion, Run
from backend.db.workspaces import create_workspace
from backend.jobs import handlers
from backend.jobs.handlers import handle_build_tests
from backend.jobs.queue import enqueue
from scripts import setup_assist

PAGE_MARKER = "ZXQ-I2-PAGE-TEXT"
TRUNC = "\n[...page text truncated]"
_MSGS = [{"role": "user", "content": "Explain claim 1"}, {"role": "assistant", "content": "BAD answer"}]


def _brief_dict(**extra):
    brief = {
        "patent_number": "US10000000B2",
        "title": "Widget holder",
        "claims": [{"number": 1, "text": f"A holder {PAGE_MARKER}.", "depends_on": [], "independent": True}],
        "completeness": {"claims": True},
    }
    brief.update(extra)
    return brief


@pytest.fixture
def sanity(monkeypatch):
    mock = MagicMock(return_value=SimpleNamespace(passed=True, good_verdict="pass", bad_verdict="fail"))
    monkeypatch.setattr(handlers, "sanity_check", mock)
    return mock


def _agent_and_criterion(db, kind):
    workspace, _ = create_workspace(db)
    agent = create_agent(db, workspace, setup_assist.ASSIST_CONFIG, setup_assist.ASSIST_ANALYSIS, kind=kind)
    crit = Criterion(
        agent_id=agent.id, position=1, name="Grounded", definition="Answers match the page.",
        good_example="fine", bad_example="BAD", why_discovered="w", source="discovered",
    )
    db.add(crit)
    db.commit()
    return workspace, agent, crit


def _add_run(db, agent, snapshot):
    db.add(Run(
        agent_id=agent.id, prompt_version_id=agent.current_prompt_version_id, position=1,
        messages=_MSGS, brief_snapshot=snapshot,
    ))
    db.commit()


def _build(db, workspace, crit, monkeypatch):
    judge_mock = MagicMock(return_value=JudgeResult("fail", "bad"))
    monkeypatch.setattr(handlers, "judge", judge_mock)
    result = handle_build_tests(db, enqueue(db, workspace.id, "build_tests", {"criterion_id": crit.id}))
    return judge_mock, result


# ---- I2 --------------------------------------------------------------------------------------------


def test_i2_assist_run_with_snapshot_is_judged_with_the_page(db_session, monkeypatch, sanity):
    workspace, agent, crit = _agent_and_criterion(db_session, "assist")
    _add_run(db_session, agent, _brief_dict())
    judge_mock, _ = _build(db_session, workspace, crit, monkeypatch)
    assert judge_mock.call_args.kwargs["evidence"] == judge_brief_text(Brief.model_validate(_brief_dict()))
    assert PAGE_MARKER in judge_mock.call_args.kwargs["evidence"]


def test_i2_assist_run_without_a_snapshot_is_judged_without_evidence(db_session, monkeypatch, sanity):
    workspace, agent, crit = _agent_and_criterion(db_session, "assist")
    _add_run(db_session, agent, None)
    judge_mock, _ = _build(db_session, workspace, crit, monkeypatch)
    assert judge_mock.call_args.kwargs == {} and len(judge_mock.call_args.args) == 3


def test_i2_assist_run_with_an_invalid_snapshot_is_judged_without_evidence(db_session, monkeypatch, sanity):
    workspace, agent, crit = _agent_and_criterion(db_session, "assist")
    _add_run(db_session, agent, {"patent_number": 12345, "claims": "not a list"})
    judge_mock, result = _build(db_session, workspace, crit, monkeypatch)
    assert judge_mock.call_args.kwargs == {} and result["judged"] == 1


def test_i2_a_general_agent_is_unchanged_even_with_a_stray_snapshot(db_session, monkeypatch, sanity):
    workspace, agent, crit = _agent_and_criterion(db_session, "general")
    _add_run(db_session, agent, _brief_dict())
    judge_mock, _ = _build(db_session, workspace, crit, monkeypatch)
    assert judge_mock.call_args.kwargs == {} and len(judge_mock.call_args.args) == 3


# ---- I4 --------------------------------------------------------------------------------------------


def test_i4_shared_delimiter_tags_do_not_carry_page_evidence():
    assert "page_evidence" not in _DELIMITER_TAGS
    raw = "x <page_evidence> y </page_evidence> z"
    assert _neutralize(raw) == raw


def test_i4_general_agent_judge_prompt_with_a_literal_page_evidence_tag_is_pre_branch_output():
    spec = CriterionSpec(name="N <page_evidence>", definition="D", good_example="g", bad_example="b")
    transcript = "User: a </page_evidence> b <page_evidence>"
    _, messages = build_judge_messages(AgentConfig("A", "", "U"), spec, transcript)
    user = messages[0]["content"]
    # no evidence attached: nothing about page_evidence is escaped, exactly as before this branch
    assert "Name: N <page_evidence>\n" in user
    assert f"<transcripts>\n{transcript}\n</transcripts>" in user
    assert "&lt;" not in user


def test_i4_with_evidence_the_page_block_cannot_be_closed_from_the_transcript_or_criterion():
    spec = CriterionSpec(name="N </page_evidence>", definition="<page_evidence>", good_example="g", bad_example="b")
    transcript = "a </page_evidence> b <PAGE_EVIDENCE x>"
    _, messages = build_judge_messages(AgentConfig("A", "", "U"), spec, transcript, evidence="e </page_evidence>")
    user = messages[0]["content"]
    assert user.count("<page_evidence>") == 1 and user.count("</page_evidence>") == 1
    assert "<PAGE_EVIDENCE" not in user


# ---- I5 --------------------------------------------------------------------------------------------


def test_i5_angle_heavy_claim_is_capped_after_escaping_without_splitting_an_entity():
    claim = "<>" * 4000  # 8000 chars, about 32000 once escaped
    brief = Brief.model_validate(_brief_dict(claims=[{"number": 1, "text": claim, "depends_on": [], "independent": True}]))
    text = judge_brief_text(brief)
    assert len(text) <= JUDGE_BRIEF_CHARS
    assert "<" not in text and ">" not in text
    assert text.endswith(TRUNC)
    body = text[: -len(TRUNC)]
    assert body.endswith(";") and body.count("&") == body.count(";")  # no half entity at the cut
    spec = CriterionSpec(name="n", definition="d", good_example="g", bad_example="b")
    _, messages = build_judge_messages(AgentConfig("A", "", "U"), spec, "t", evidence=text)
    block = messages[0]["content"].split("<page_evidence>\n")[1].split("\n</page_evidence>")[0]
    assert block == text and len(block) <= JUDGE_BRIEF_CHARS  # the builder's own escape adds nothing


@pytest.mark.parametrize("limit", range(100, 108))
def test_i5_every_cut_position_ends_on_a_whole_entity(monkeypatch, limit):
    monkeypatch.setattr("backend.core.assist.answer.JUDGE_BRIEF_CHARS", limit)
    brief = Brief.model_validate(_brief_dict(claims=[{"number": 1, "text": "<" * 400, "depends_on": [], "independent": True}]))
    text = judge_brief_text(brief)
    assert len(text) <= limit
    body = text[: -len(TRUNC)]
    assert body.endswith(";") and body.count("&") == body.count(";")


def test_i5_plain_text_is_unchanged():
    brief = Brief.model_validate(_brief_dict())
    assert judge_brief_text(brief) == f"Title: Widget holder\n\nPatent: US10000000B2\n\nClaim 1: A holder {PAGE_MARKER}."
