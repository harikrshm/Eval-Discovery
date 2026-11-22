"""Task 13: the validated importer for hand-written page-view JSON Lines files (never calls a model)."""

import copy
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.api.schemas import AssistPageViewRequest
from backend.core.assist.brief import Brief
from backend.core.assist.importing import (
    ImportValidationError,
    expand,
    parse_lines,
    validate_record,
)
from backend.core.assist.intent import Intent, classify_intent, primary_section
from backend.db.agents import create_agent
from backend.db.assist import add_question, run_evidence, upsert_page_view
from backend.db.models import Agent, AssistPageView, AssistQuestion, Run
from backend.db.session import Base
from backend.db.workspaces import create_workspace
from scripts import import_page_views, setup_assist

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "assist" / "page_views.example.jsonl"

EXAMPLE_PAGE_VIEWS = 12
EXAMPLE_QUESTIONS = 17
EXAMPLE_FLAGGED = 5
EXAMPLE_BOUNCES = 3
FLAGGED_SESSIONS = {"demo-a-0003", "demo-a-0004", "demo-b-0003", "demo-b-0004", "demo-c-0004"}

BRIEF = {"patent_number": "US12401720B1", "title": "T", "claims": [{"number": 1, "text": "A device."}]}


def rec(**overrides):
    base = {
        "session_id": "demo-test-0001",
        "patent_number": "US12401720B1",
        "started_at": "2026-11-02T09:14:03Z",
        "dwell_ms": 60000,
        "sections": {"claims": 30000},
        "questions": [{"text": "Explain claim 1", "answer": "Claim 1 is a device.", "assist_version": 1}],
    }
    base.update(overrides)
    return base


def q(**overrides):
    base = {"text": "Explain claim 1", "answer": "Claim 1 is a device.", "assist_version": 1}
    base.update(overrides)
    return base


def jsonl(*records) -> str:
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n"


def error_for(obj, line_no=7) -> list[str]:
    with pytest.raises(ImportValidationError) as exc:
        validate_record(obj, line_no)
    return exc.value.errors


# --- the example file ----------------------------------------------------------------------------


def test_example_file_validates_and_expands_with_exact_counts():
    parsed = parse_lines(EXAMPLE.read_text(encoding="utf-8").splitlines())
    assert parsed.errors == []
    expanded = [expand(record) for _, record in parsed.records]
    assert len(expanded) == EXAMPLE_PAGE_VIEWS
    assert sum(len(questions) for _, questions, _ in expanded) == EXAMPLE_QUESTIONS
    flagged = [conv for _, _, convs in expanded for conv in convs]
    assert len(flagged) == EXAMPLE_FLAGGED
    assert {conv.session_id for conv in flagged} == FLAGGED_SESSIONS
    assert sum(1 for view, _, _ in expanded if view.bounce) == EXAMPLE_BOUNCES


def test_example_file_is_clearly_demo_data_over_three_us_patents_with_matching_briefs():
    parsed = parse_lines(EXAMPLE.read_text(encoding="utf-8").splitlines())
    records = [record for _, record in parsed.records]
    assert all(r.session_id.startswith("demo-") for r in records)
    assert {r.patent_number for r in records} == {"US12401720B1", "US6756997B1", "US20250119367A1"}
    assert {q.assist_version for r in records for q in r.questions} == {1, 2}
    ratings = {q.rating for r in records for q in r.questions}
    assert {"up", "down", "no_answer", None} <= ratings
    for r in records:
        if r.brief is not None:
            Brief.model_validate(r.brief.model_dump())
            assert r.brief.patent_number.upper() == r.patent_number
    assert max(len(line) for line in EXAMPLE.read_text(encoding="utf-8").splitlines()) < 6000  # small briefs


def test_example_file_mixes_intents():
    parsed = parse_lines(EXAMPLE.read_text(encoding="utf-8").splitlines())
    intents = {row.intent for _, record in parsed.records for row in expand(record)[1]}
    assert len(intents) >= 5


# --- validation rules: every failure names its line and field ------------------------------------


@pytest.mark.parametrize(
    "mutation, field",
    [
        ({"session_id": "short"}, "session_id"),
        ({"session_id": "has_underscore_here"}, "session_id"),
        ({"session_id": "x" * 65}, "session_id"),
        ({"session_id": 12345678}, "session_id"),
        ({"patent_number": "EP4517554A1"}, "patent_number"),
        ({"patent_number": "US 12,401,720 B1"}, "patent_number"),
        ({"started_at": "yesterday"}, "started_at"),
        ({"started_at": 1762000000}, "started_at"),
        ({"dwell_ms": -1}, "dwell_ms"),
        ({"dwell_ms": 86_400_001}, "dwell_ms"),
        ({"dwell_ms": "5000"}, "dwell_ms"),
        ({"dwell_ms": 1.5}, "dwell_ms"),
        ({"dwell_ms": True}, "dwell_ms"),
        ({"sections": {"footer": 10}}, "sections"),
        ({"sections": {"claims": -5}}, "sections.claims"),
        ({"sections": {"claims": 86_400_001}}, "sections.claims"),
        ({"sections": []}, "sections"),
        ({"questions": [q()] * 41}, "questions"),
        ({"questions": "none"}, "questions"),
        ({"questions": [q(text="x" * 2001)]}, "questions.0.text"),
        ({"questions": [q(text="   ")]}, "questions.0.text"),
        ({"questions": [q(answer="x" * 16001)]}, "questions.0.answer"),
        ({"questions": [q(answer="")]}, "questions.0.answer"),
        ({"questions": [q(rating="meh")]}, "questions.0.rating"),
        ({"questions": [q(rating="up", rating_reason="x" * 501)]}, "questions.0.rating_reason"),
        ({"questions": [q(rating_reason="no rating given")]}, "questions.0.rating_reason"),
        ({"questions": [q(reasked="yes")]}, "questions.0.reasked"),
        ({"questions": [q(citation_ok=0)]}, "questions.0.citation_ok"),
        ({"questions": [q(intent="gossip")]}, "questions.0.intent"),
        ({"questions": [q(section_focus="footer")]}, "questions.0.section_focus"),
        ({"questions": [q(asked_at="soon")]}, "questions.0.asked_at"),
        ({"questions": [q(assist_version=0)]}, "questions.0.assist_version"),
        ({"questions": [q(assist_version="2")]}, "questions.0.assist_version"),
        ({"questions": [q(), q(text="")]}, "questions.1.text"),
    ],
)
def test_each_validation_rule_fails_with_line_number_and_field(mutation, field):
    errors = error_for(rec(**mutation), line_no=7)
    assert errors and all(e.startswith("line 7: ") for e in errors)
    assert any(f"line 7: {field}" in e for e in errors), errors


def test_missing_required_fields_are_named():
    obj = rec()
    for key in ("session_id", "patent_number", "started_at", "dwell_ms"):
        broken = {k: v for k, v in obj.items() if k != key}
        assert any(f"line 3: {key}" in e for e in error_for(broken, 3))
    broken = copy.deepcopy(obj)
    del broken["questions"][0]["assist_version"]
    assert any("questions.0.assist_version" in e for e in error_for(broken, 3))


def test_a_record_that_is_not_an_object_is_rejected_with_its_line():
    errors = error_for([1, 2, 3], 9)
    assert errors == ["line 9: the record must be a JSON object"]


def test_unknown_keys_are_rejected_at_every_level():
    assert any("line 4: bounce" in e and "unknown" in e for e in error_for(rec(bounce=True), 4))
    assert any("line 4: questions.0.score" in e and "unknown" in e for e in error_for(rec(questions=[q(score=5)]), 4))
    assert any("line 4: brief.colour" in e for e in error_for(rec(brief={**BRIEF, "colour": "red"}), 4))
    assert any(
        "line 4: brief.claims.0.bold" in e
        for e in error_for(rec(brief={**BRIEF, "claims": [{"number": 1, "text": "A.", "bold": 1}]}), 4)
    )


def test_session_id_pattern_matches_the_live_api():
    for value in ("abcdefgh", "a-b-c-d-e-f", "A" * 64, "short", "x" * 65, "under_score", "cafécafe1", "ab cdefgh"):
        live = True
        try:
            AssistPageViewRequest(session_id=value, patent_number="US1234567B2", dwell_ms=0)
        except ValueError:
            live = False
        try:
            validate_record(rec(session_id=value, questions=[]), 1)
            ours = True
        except ImportValidationError:
            ours = False
        assert ours == live, value


def test_sections_and_durations_use_the_live_caps_at_their_boundaries():
    ok = validate_record(rec(dwell_ms=86_400_000, sections={"claims": 0, "landscapes": 86_400_000}, questions=[]), 1)
    assert ok.dwell_ms == 86_400_000
    names = ("claims", "description", "classifications", "citations", "family", "legal_events", "landscapes")
    assert validate_record(rec(sections={n: 1 for n in names}, questions=[]), 1)
    assert validate_record(rec(questions=[q()] * 40, brief=None), 1)
    assert validate_record(rec(questions=[q(text="x" * 2000, answer="y" * 16000, rating="up", rating_reason="z" * 500)]), 1)


def test_flagged_questions_require_the_brief():
    for flagged in (q(rating="down"), q(rating="no_answer"), q(reasked=True), q(citation_ok=False)):
        errors = error_for(rec(questions=[q(), flagged]), 5)
        assert any(e.startswith("line 5: brief") and "question 2" in e for e in errors), errors
        assert validate_record(rec(questions=[q(), flagged], brief=BRIEF), 5)  # with a brief it passes
    for fine in (q(rating="up"), q(), q(reasked=False, citation_ok=True)):
        assert validate_record(rec(questions=[fine]), 5)  # not flagged: no brief needed


def test_brief_is_validated_with_the_backend_model_and_must_match_the_patent():
    errors = error_for(rec(brief={**BRIEF, "claims": [{"number": 0, "text": "x"}]}), 6)
    assert any(e.startswith("line 6: brief.claims.0.number") for e in errors)
    errors = error_for(rec(brief={**BRIEF, "patent_number": "US6756997B1"}), 6)
    assert any(e.startswith("line 6: brief.patent_number") for e in errors)
    errors = error_for(rec(brief={"title": "no number"}), 6)
    assert any(e.startswith("line 6: brief.patent_number") for e in errors)
    # strip + upper before comparing
    assert validate_record(rec(brief={**BRIEF, "patent_number": " us12401720b1 "}), 6)
    assert validate_record(rec(patent_number=" us12401720b1", brief=BRIEF), 6)


def test_a_flagged_conversation_obeys_the_live_conversation_caps():
    flagged_many = [q(rating="down")] + [q() for _ in range(21)]  # 22 questions = 44 turns > 42
    errors = error_for(rec(questions=flagged_many, brief=BRIEF), 8)
    assert any(e.startswith("line 8: questions") for e in errors)
    assert validate_record(rec(questions=flagged_many[:21], brief=BRIEF), 8)
    # unflagged records can have up to 40 questions
    assert validate_record(rec(questions=[q() for _ in range(40)]), 8)
    huge = [q(rating="down", answer="a" * 16000) for _ in range(9)]  # 144k chars > 120k
    assert any("line 8: questions" in e for e in error_for(rec(questions=huge, brief=BRIEF), 8))


# --- the file level: every line is checked, all errors are collected -----------------------------


def test_parse_lines_collects_every_error_with_its_line_number_and_skips_blank_lines():
    lines = [
        json.dumps(rec(session_id="demo-ok-0001")),
        "",
        "   ",
        "{not json",
        json.dumps(rec(session_id="demo-ok-0002", dwell_ms=-3)),
        json.dumps(rec(session_id="demo-ok-0003")),
    ]
    parsed = parse_lines(lines)
    assert [n for n, _ in parsed.records] == [1, 6]
    assert len(parsed.errors) == 2
    assert parsed.errors[0].startswith("line 4: invalid JSON")
    assert parsed.errors[1].startswith("line 5: dwell_ms")


def test_parse_lines_rejects_duplicate_session_ids_in_one_file():
    parsed = parse_lines([json.dumps(rec()), json.dumps(rec(dwell_ms=5))])
    assert len(parsed.errors) == 1
    assert parsed.errors[0].startswith("line 2: session_id") and "line 1" in parsed.errors[0]


# --- expansion ------------------------------------------------------------------------------------


def test_expand_computes_missing_intent_and_section_focus_and_keeps_explicit_values():
    record = validate_record(
        rec(
            questions=[
                q(text="Explain claim 1"),
                q(text="What do these CPC codes mean?"),
                q(text="Is this still in force?"),
                q(text="Is claim 1 valid?"),
                q(text="hello there"),
                q(text="Explain claim 1", intent="classification", section_focus="citations"),
                q(text="Explain claim 1", section_focus=None),
            ]
        ),
        1,
    )
    _, rows, _ = expand(record)
    expected = [
        ("claim_explain", "claims"),
        ("classification", "classifications"),
        ("status_family", "legal_events"),
        ("legal_opinion_request", None),
        ("other", None),
        ("classification", "citations"),
        ("claim_explain", None),  # an explicit null keeps "no section"
    ]
    assert [(r.intent, r.section_focus) for r in rows] == expected
    for row in rows[:5]:
        intent = classify_intent(row.text)
        assert row.intent == intent.value and row.section_focus == primary_section(intent)
    assert isinstance(Intent(rows[0].intent), Intent)


@pytest.mark.parametrize(
    "dwell, sections, bounce",
    [
        (9999, {}, True),
        (9999, {"claims": 2999}, True),
        (9999, {"claims": 3000}, False),
        (10000, {}, False),
        (10000, {"claims": 2999}, False),
        (0, {}, True),
        (5000, {"claims": 100, "family": 2999}, True),
        (5000, {"claims": 100, "family": 3000}, False),
    ],
)
def test_bounce_follows_the_live_rule_at_its_boundaries(dwell, sections, bounce):
    view, _, _ = expand(validate_record(rec(dwell_ms=dwell, sections=sections, questions=[]), 1))
    assert view.bounce is bounce


def test_bounce_is_the_shared_live_function():
    from backend.core.assist import bounce as shared
    from backend.core.assist import importing
    from backend.db import assist as db_assist

    assert importing.is_bounce is shared.is_bounce is db_assist.is_bounce


def test_expand_page_view_row_and_question_defaults():
    record = validate_record(
        rec(
            started_at="2026-11-10T22:30:05+05:30",
            patent_number=" us12401720b1 ",
            questions=[q(assist_version=2), q(assist_version=1, asked_at="2026-11-10T20:00:00Z"), q()],
        ),
        1,
    )
    view, rows, flagged = expand(record)
    assert flagged == []
    assert view.patent_number == "US12401720B1" and view.source == "imported"
    assert view.started_at == datetime(2026, 11, 10, 17, 0, 5) == view.updated_at  # aware -> naive UTC
    assert view.assist_version == 2  # the version first seen
    assert [r.created_at for r in rows] == [
        datetime(2026, 11, 10, 17, 0, 6),  # started_at + 1 s
        datetime(2026, 11, 10, 20, 0, 0),  # asked_at
        datetime(2026, 11, 10, 17, 0, 8),  # started_at + 3 s
    ]
    assert all(r.source == "imported" and r.citation_ok is True and r.reasked is False for r in rows)
    assert all(r.patent_number == "US12401720B1" and r.session_id == "demo-test-0001" for r in rows)


def test_naive_timestamps_are_read_as_utc():
    view, _, _ = expand(validate_record(rec(started_at="2026-11-04T08:21:30", questions=[]), 1))
    assert view.started_at == datetime(2026, 11, 4, 8, 21, 30)
    view, _, _ = expand(validate_record(rec(started_at="2026-11-04T08:21:30Z", questions=[]), 1))
    assert view.started_at == datetime(2026, 11, 4, 8, 21, 30)


def test_flagged_conversation_is_created_only_for_flagged_sessions_and_carries_the_brief():
    plain = validate_record(rec(questions=[q(rating="up"), q()], brief=BRIEF), 1)
    assert expand(plain)[2] == []

    questions = [q(text="Is this valid?", answer="No view.", rating="down"), q(text="Which claims?", answer="1.")]
    flagged_record = validate_record(rec(questions=questions, brief=BRIEF), 1)
    _, _, flagged = expand(flagged_record)
    assert len(flagged) == 1
    conv = flagged[0]
    assert conv.session_id == "demo-test-0001" and conv.source == "imported"
    assert conv.messages == [
        {"role": "user", "content": "Is this valid?"},
        {"role": "assistant", "content": "No view."},
        {"role": "user", "content": "Which claims?"},
        {"role": "assistant", "content": "1."},
    ]
    assert conv.brief == Brief.model_validate(BRIEF).model_dump(mode="json")


def test_expand_does_not_mutate_or_share_state():
    obj = rec(questions=[q(rating="down")], brief=BRIEF)
    snapshot = copy.deepcopy(obj)
    record = validate_record(obj, 1)
    a = expand(record)
    b = expand(record)
    assert a == b and obj == snapshot
    a[0].sections["claims"] = 1
    assert expand(record)[0].sections["claims"] == 30000


# --- the script against a database ----------------------------------------------------------------


@pytest.fixture
def demo_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'demo.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    workspace, _ = create_workspace(session)
    agent = create_agent(session, workspace, setup_assist.ASSIST_CONFIG, setup_assist.ASSIST_ANALYSIS, kind="assist")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ASSIST_AGENT_ID", agent.id)
    session.agent, session.workspace, session.url = agent, workspace, url
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def write(tmp_path, *records, name="in.jsonl") -> Path:
    path = tmp_path / name
    path.write_text(jsonl(*records), encoding="utf-8")
    return path


def counts(db):
    db.expire_all()
    return (
        db.scalar(select(func.count()).select_from(AssistPageView)),
        db.scalar(select(func.count()).select_from(AssistQuestion)),
        db.scalar(select(func.count()).select_from(Run)),
    )


def test_importing_the_example_file_creates_exact_rows_all_imported(demo_db, capsys):
    assert import_page_views.main([str(EXAMPLE)]) == 0
    out = capsys.readouterr().out
    assert counts(demo_db) == (EXAMPLE_PAGE_VIEWS, EXAMPLE_QUESTIONS, EXAMPLE_FLAGGED)
    assert f"{EXAMPLE_PAGE_VIEWS} page views" in out and f"{EXAMPLE_QUESTIONS} questions" in out
    assert f"{EXAMPLE_FLAGGED} flagged" in out and "demo dataset" in out
    workspace_id = demo_db.workspace.id
    views = demo_db.scalars(select(AssistPageView)).all()
    assert {v.source for v in views} == {"imported"} and {v.workspace_id for v in views} == {workspace_id}
    questions = demo_db.scalars(select(AssistQuestion)).all()
    assert {q.source for q in questions} == {"imported"} and {q.workspace_id for q in questions} == {workspace_id}
    runs = demo_db.scalars(select(Run)).all()
    assert {r.source for r in runs} == {"imported"} and {r.session_id for r in runs} == FLAGGED_SESSIONS
    assert sorted(r.position for r in runs) == [1, 2, 3, 4, 5]
    assert all(r.brief_snapshot and r.messages for r in runs)
    assert {r.agent_id for r in runs} == {demo_db.agent.id}
    assert {r.prompt_version_id for r in runs} == {demo_db.agent.current_prompt_version_id}


def test_timestamps_are_preserved_on_every_row(demo_db, tmp_path):
    path = write(
        tmp_path,
        rec(
            session_id="demo-time-0001", started_at="2026-03-04T05:06:07+02:00", brief=BRIEF,
            questions=[q(rating="down", asked_at="2026-03-04T03:10:00Z"), q(text="Is it in force?")],
        ),
    )
    assert import_page_views.main([str(path)]) == 0
    view = demo_db.get(AssistPageView, "demo-time-0001")
    assert view.started_at == datetime(2026, 3, 4, 3, 6, 7) == view.updated_at
    rows = demo_db.scalars(select(AssistQuestion).order_by(AssistQuestion.created_at)).all()
    assert [r.created_at for r in rows] == [datetime(2026, 3, 4, 3, 6, 9), datetime(2026, 3, 4, 3, 10, 0)]
    assert demo_db.scalar(select(Run.created_at)) == datetime(2026, 3, 4, 3, 6, 7)


def test_an_invalid_line_aborts_everything_even_earlier_valid_lines(demo_db, tmp_path, capsys):
    path = write(
        tmp_path,
        rec(session_id="demo-good-0001"),
        rec(session_id="demo-good-0002"),
        rec(session_id="demo-bad-0003", dwell_ms=-1),
        rec(session_id="demo-bad-0004", patent_number="EP1"),
        rec(session_id="demo-good-0005"),
    )
    assert import_page_views.main([str(path)]) == 2
    captured = capsys.readouterr()
    assert counts(demo_db) == (0, 0, 0)
    assert "line 3: dwell_ms" in captured.err and "line 4: patent_number" in captured.err
    assert "nothing was imported" in captured.err.lower()


def test_reimport_is_idempotent_and_replaces_the_questions(demo_db, tmp_path):
    assert import_page_views.main([str(EXAMPLE)]) == 0
    first = {r.session_id: (r.dwell_ms, r.sections, r.bounce, r.started_at) for r in demo_db.scalars(select(AssistPageView))}
    assert import_page_views.main([str(EXAMPLE)]) == 0
    assert counts(demo_db) == (EXAMPLE_PAGE_VIEWS, EXAMPLE_QUESTIONS, EXAMPLE_FLAGGED)
    demo_db.expire_all()
    second = {r.session_id: (r.dwell_ms, r.sections, r.bounce, r.started_at) for r in demo_db.scalars(select(AssistPageView))}
    assert first == second
    positions = sorted(r.position for r in demo_db.scalars(select(Run)))
    assert positions == [1, 2, 3, 4, 5]

    # a changed file replaces that session's questions (not a union)
    path = write(
        tmp_path,
        rec(session_id="demo-a-0001", questions=[q(text="Is this still in force?", answer="Active.")]),
    )
    assert import_page_views.main([str(path)]) == 0
    texts = demo_db.scalars(select(AssistQuestion.text).where(AssistQuestion.session_id == "demo-a-0001")).all()
    assert texts == ["Is this still in force?"]
    assert counts(demo_db) == (EXAMPLE_PAGE_VIEWS, EXAMPLE_QUESTIONS - 3 + 1, EXAMPLE_FLAGGED)


def test_reimport_merges_the_page_view_with_the_live_rules(demo_db, tmp_path):
    first = write(tmp_path, rec(session_id="demo-merge-01", dwell_ms=5000, sections={"claims": 4000, "family": 100}, questions=[]))
    assert import_page_views.main([str(first)]) == 0
    view = demo_db.get(AssistPageView, "demo-merge-01")
    assert view.bounce is False and view.assist_version is None
    second = write(
        tmp_path, rec(session_id="demo-merge-01", dwell_ms=3000, sections={"claims": 1000, "legal_events": 7}, questions=[]),
        name="second.jsonl",
    )
    assert import_page_views.main([str(second)]) == 0
    demo_db.expire_all()
    view = demo_db.get(AssistPageView, "demo-merge-01")
    assert view.dwell_ms == 5000
    assert view.sections == {"claims": 4000, "family": 100, "legal_events": 7}
    assert view.bounce is False and view.source == "imported"


def test_reimport_updates_the_one_flagged_run_in_place(demo_db, tmp_path):
    flagged = write(tmp_path, rec(session_id="demo-flag-0001", brief=BRIEF, questions=[q(rating="down")]))
    assert import_page_views.main([str(flagged)]) == 0
    changed = write(
        tmp_path,
        rec(session_id="demo-flag-0001", brief=BRIEF, questions=[q(rating="down", text="Another one", answer="A2")]),
        name="changed.jsonl",
    )
    assert import_page_views.main([str(changed)]) == 0
    run = demo_db.scalar(select(Run))
    demo_db.refresh(run)
    assert counts(demo_db)[2] == 1
    assert [m["content"] for m in run.messages] == ["Another one", "A2"]


def test_a_session_that_already_exists_as_live_is_a_conflict_and_nothing_is_imported(demo_db, tmp_path, capsys):
    upsert_page_view(demo_db, demo_db.workspace.id, "demo-live-0001", "US12401720B1", 1000, {}, None, 1, "live")
    path = write(tmp_path, rec(session_id="demo-other-001"), rec(session_id="demo-live-0001"), rec(session_id="demo-other-002"))
    assert import_page_views.main([str(path)]) == 2
    err = capsys.readouterr().err
    assert "demo-live-0001" in err and "live" in err and "line 2" in err
    assert counts(demo_db) == (1, 0, 0)
    view = demo_db.get(AssistPageView, "demo-live-0001")
    assert view.source == "live" and view.dwell_ms == 1000


def test_live_questions_are_never_touched_and_conflict_even_without_a_page_view(demo_db, tmp_path):
    add_question(demo_db, demo_db.workspace.id, "demo-live-0002", "US12401720B1", "live q", "other", None, "a", True, "live", 1)
    path = write(tmp_path, rec(session_id="demo-live-0002"))
    assert import_page_views.main([str(path)]) == 2
    assert counts(demo_db) == (0, 1, 0)
    assert demo_db.scalar(select(AssistQuestion.source)) == "live"


def test_a_session_pinned_to_another_patent_is_a_conflict(demo_db, tmp_path):
    assert import_page_views.main([str(write(tmp_path, rec(session_id="demo-pin-0001")))]) == 0
    other = write(tmp_path, rec(session_id="demo-pin-0001", patent_number="US6756997B1"), name="other.jsonl")
    assert import_page_views.main([str(other)]) == 2


def test_other_sessions_live_rows_survive_a_reimport(demo_db, tmp_path):
    upsert_page_view(demo_db, demo_db.workspace.id, "live-session-1", "US12401720B1", 9000, {}, None, 1, "live")
    add_question(demo_db, demo_db.workspace.id, "live-session-1", "US12401720B1", "live q", "other", None, "a", True, "live", 1)
    assert import_page_views.main([str(EXAMPLE)]) == 0
    assert import_page_views.main([str(EXAMPLE)]) == 0
    assert demo_db.scalar(select(func.count()).select_from(AssistQuestion).where(AssistQuestion.source == "live")) == 1
    assert counts(demo_db) == (EXAMPLE_PAGE_VIEWS + 1, EXAMPLE_QUESTIONS + 1, EXAMPLE_FLAGGED)


def test_imported_flagged_runs_feed_the_flywheel_evidence(demo_db):
    assert import_page_views.main([str(EXAMPLE)]) == 0
    run = demo_db.scalar(select(Run).where(Run.session_id == "demo-a-0003"))
    evidence = run_evidence(demo_db, run)
    assert evidence and "down" in evidence


def test_the_flagged_run_cap_rejects_the_file_with_a_clear_error(demo_db, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(import_page_views, "MAX_ASSIST_RUNS", 2)
    flagged = [rec(session_id=f"demo-cap-{i:04d}", brief=BRIEF, questions=[q(rating="down")]) for i in range(3)]
    assert import_page_views.main([str(write(tmp_path, *flagged))]) == 2
    err = capsys.readouterr().err
    assert "2" in err and "flagged" in err.lower() and "cap" in err.lower()
    assert counts(demo_db) == (0, 0, 0)

    ok = write(tmp_path, *flagged[:2], name="ok.jsonl")
    assert import_page_views.main([str(ok)]) == 0
    # re-importing the same sessions does not count twice; a third new one would
    assert import_page_views.main([str(ok)]) == 0
    assert import_page_views.main([str(write(tmp_path, flagged[2], name="third.jsonl"))]) == 2
    assert counts(demo_db)[2] == 2


def test_the_live_cap_value_is_used():
    from backend.api.assist import MAX_ASSIST_RUNS

    assert import_page_views.MAX_ASSIST_RUNS == MAX_ASSIST_RUNS == 1000


def test_dry_run_prints_counts_and_writes_nothing(demo_db, capsys):
    assert import_page_views.main([str(EXAMPLE), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert counts(demo_db) == (0, 0, 0)
    assert f"{EXAMPLE_PAGE_VIEWS} page views" in out and f"{EXAMPLE_QUESTIONS} questions" in out
    assert f"{EXAMPLE_FLAGGED} flagged" in out and "dry run" in out.lower()


def test_dry_run_still_rejects_invalid_files_and_needs_no_database(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ASSIST_AGENT_ID", raising=False)
    assert import_page_views.main([str(EXAMPLE), "--dry-run", "--database-url", "sqlite:///" + str(tmp_path / "x.db")]) == 0
    bad = write(tmp_path, rec(dwell_ms=-1), name="bad.jsonl")
    assert import_page_views.main([str(bad), "--dry-run"]) == 2
    assert "line 1: dwell_ms" in capsys.readouterr().err
    assert not (tmp_path / "x.db").exists()


def test_the_password_never_appears_in_output(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ASSIST_AGENT_ID", raising=False)
    url = "postgresql://demo_user:hunter2-SECRET@127.0.0.1:1/nodb?password=hunter2-SECRET&sslmode=disable"
    code = import_page_views.main([str(EXAMPLE), "--database-url", url])
    captured = capsys.readouterr()
    assert code == 3
    assert "hunter2" not in captured.out + captured.err
    assert "demo_user" in captured.out + captured.err or "127.0.0.1" in captured.out + captured.err  # masked, not hidden


def test_the_environment_url_is_masked_too(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:topsecretpw@127.0.0.1:1/db")
    monkeypatch.delenv("ASSIST_AGENT_ID", raising=False)
    assert import_page_views.main([str(EXAMPLE)]) == 3
    captured = capsys.readouterr()
    assert "topsecretpw" not in captured.out + captured.err


def test_no_assist_agent_is_a_database_config_error_pointing_to_setup(demo_db, monkeypatch, capsys):
    monkeypatch.delenv("ASSIST_AGENT_ID", raising=False)
    assert import_page_views.main([str(EXAMPLE)]) == 3
    err = capsys.readouterr().err
    assert "scripts/setup_assist.py" in err
    assert counts(demo_db) == (0, 0, 0)
    monkeypatch.setenv("ASSIST_AGENT_ID", "does-not-exist")
    assert import_page_views.main([str(EXAMPLE)]) == 3
    assert "scripts/setup_assist.py" in capsys.readouterr().err


def test_an_unmigrated_database_is_a_config_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'empty.db'}")
    assert import_page_views.main([str(EXAMPLE)]) == 3
    err = capsys.readouterr().err
    assert "alembic upgrade head" in err and str(tmp_path) not in err


def test_a_missing_or_undecodable_file_is_invalid_input(demo_db, tmp_path, capsys):
    assert import_page_views.main([str(tmp_path / "nope.jsonl")]) == 2
    assert "nope.jsonl" in capsys.readouterr().err
    binary = tmp_path / "bin.jsonl"
    binary.write_bytes(b"\xff\xfe\x00bad")
    assert import_page_views.main([str(binary)]) == 2
    assert counts(demo_db) == (0, 0, 0)


def test_an_empty_file_imports_nothing_and_says_so(demo_db, tmp_path, capsys):
    path = tmp_path / "empty.jsonl"
    path.write_text("\n\n", encoding="utf-8")
    assert import_page_views.main([str(path)]) == 2
    assert "no records" in capsys.readouterr().err.lower()


def test_unicode_text_round_trips(demo_db, tmp_path):
    text = "¿Qué cubre la reivindicación 1? 请解释权利要求 1 \U0001f9ea — “quotes” § 112"
    answer = "Cubre un dispositivo étanche; 权利要求 1 描述一个设备 \U0001f4a1."
    brief = {**BRIEF, "title": "Dispositivo über 装置"}
    path = write(tmp_path, rec(session_id="demo-uni-0001", brief=brief, questions=[q(text=text, answer=answer, rating="down", rating_reason="résumé")]))
    assert import_page_views.main([str(path)]) == 0
    row = demo_db.scalar(select(AssistQuestion))
    assert (row.text, row.answer, row.rating_reason) == (text, answer, "résumé")
    run = demo_db.scalar(select(Run))
    assert [m["content"] for m in run.messages] == [text, answer]
    assert run.brief_snapshot["title"] == "Dispositivo über 装置"


def test_a_utf8_bom_at_the_start_of_the_file_is_accepted(demo_db, tmp_path):
    path = tmp_path / "bom.jsonl"
    path.write_bytes(b"\xef\xbb\xbf" + jsonl(rec(session_id="demo-bom-0001")).encode("utf-8"))
    assert import_page_views.main([str(path)]) == 0


def test_a_thousand_line_file_imports_quickly(demo_db, tmp_path):
    records = []
    for i in range(1000):
        flagged = i % 10 == 0
        record = rec(
            session_id=f"demo-bulk-{i:05d}", dwell_ms=1000 + i, sections={"claims": i * 10},
            questions=[q(rating="down" if flagged else "up"), q(text="Is this still in force?")],
        )
        if flagged:
            record["brief"] = BRIEF
        records.append(record)
    path = write(tmp_path, *records)
    start = time.perf_counter()
    assert import_page_views.main([str(path)]) == 0
    first = time.perf_counter() - start
    assert counts(demo_db) == (1000, 2000, 100)
    start = time.perf_counter()
    assert import_page_views.main([str(path)]) == 0  # the update path is also fast
    second = time.perf_counter() - start
    assert counts(demo_db) == (1000, 2000, 100)
    assert first < 10 and second < 10, (first, second)


def test_a_failure_midway_rolls_everything_back(demo_db, tmp_path, monkeypatch, capsys):
    real = import_page_views._insert_flagged_run
    calls = []

    def boom(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("disk on fire password=hunter2")
        return real(*args, **kwargs)

    monkeypatch.setattr(import_page_views, "_insert_flagged_run", boom)
    assert import_page_views.main([str(EXAMPLE)]) == 3
    captured = capsys.readouterr()
    assert counts(demo_db) == (0, 0, 0)
    assert "hunter2" not in captured.out + captured.err  # an exception text is never echoed


def test_cli_runs_as_a_script_from_another_directory(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["GROQ_SKIP_DOTENV"] = "1"
    env["DATABASE_URL"] = f"sqlite:///{tmp_path / 'cli.db'}"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "import_page_views.py"), str(EXAMPLE), "--dry-run"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert f"{EXAMPLE_PAGE_VIEWS} page views" in result.stdout
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"session_id": "x"}\n', encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "import_page_views.py"), str(bad)],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 2 and "line 1:" in result.stderr


def test_docs_describe_the_format_and_the_labelling_rule():
    text = (ROOT / "docs" / "assist-import-format.md").read_text(encoding="utf-8")
    for needle in ("demo dataset", "never counts as measured effect", "--dry-run", "exit", "session_id", "assist_version", "86,400,000"):
        assert needle.lower() in text.lower(), needle
