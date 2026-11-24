"""Task 4: the demo dataset seed script (never calls a model, never touches live rows or prompt versions)."""

import hashlib
import math
import os
import statistics
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.core.assist.brief import Brief
from backend.core.assist.convo_judge import FLAG_GRADE_AT_OR_BELOW, REASONS
from backend.core.assist.intent import classify_intent
from backend.db.agents import create_agent
from backend.db.assist import add_question, upsert_page_view
from backend.db.models import (
    AssistCall,
    AssistJudgement,
    AssistPageView,
    AssistQuestion,
    PromptVersion,
    Run,
)
from backend.db.session import Base
from backend.db.workspaces import create_workspace
from scripts import import_page_views, seed_demo_console, setup_assist

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "assist" / "page_views.example.jsonl"
END = date(2026, 11, 24)
FIRST = END - timedelta(days=29)
ARGS = ["--confirm", "--end", END.isoformat()]
TABLES = (AssistPageView, AssistQuestion, AssistCall, AssistJudgement, Run)


def make_db(tmp_path, name):
    url = f"sqlite:///{tmp_path / name}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    workspace, _ = create_workspace(session)
    agent = create_agent(session, workspace, setup_assist.ASSIST_CONFIG, setup_assist.ASSIST_ANALYSIS, kind="assist")
    session.agent, session.workspace, session.url, session.engine = agent, workspace, url, engine
    return session


@pytest.fixture
def demo_db(tmp_path, monkeypatch):
    session = make_db(tmp_path, "demo.db")
    monkeypatch.setenv("DATABASE_URL", session.url)
    monkeypatch.setenv("ASSIST_AGENT_ID", session.agent.id)
    try:
        yield session
    finally:
        session.close()
        session.engine.dispose()


def live_question(db, session_id):
    return add_question(
        db, db.workspace.id, session_id, "US12401720B1", "Explain claim 1", "claim_explain", "claims",
        "Claim 1 is a device.", True, "extension", 1,
    )


def counts(db):
    db.expire_all()
    return tuple(db.scalar(select(func.count()).select_from(model)) for model in TABLES)


def day_of(moment) -> int:
    return (moment.date() - FIRST).days + 1


def percentile(values, share):
    """Nearest-rank percentile."""
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(share * len(ordered)) - 1)]


def fingerprint(db) -> str:
    rows = db.execute(
        select(AssistQuestion.created_at, AssistQuestion.intent, AssistQuestion.rating, AssistQuestion.assist_version)
    ).all()
    return hashlib.sha256(repr(sorted(map(repr, rows))).encode()).hexdigest()


def snapshot(db, model, **where):
    """Every column of every matching row, as comparable tuples."""
    db.expire_all()
    columns = [c.name for c in model.__table__.columns]
    rows = db.execute(select(model).filter_by(**where)).scalars().all()
    return sorted(repr(tuple(getattr(r, c) for c in columns)) for r in rows)


# --- the command ------------------------------------------------------------------------------------------


def test_requires_confirm_and_writes_nothing_without_it(demo_db, capsys):
    assert seed_demo_console.main(["--end", END.isoformat()]) == 2
    out = capsys.readouterr().out
    assert "Would seed" in out and "Nothing was written" in out and "--confirm" in out
    assert counts(demo_db) == (0, 0, 0, 0, 0)
    assert seed_demo_console.main(["--remove"]) == 2
    assert counts(demo_db) == (0, 0, 0, 0, 0)


def test_default_run_prints_counts_and_writes_all_tables(demo_db, capsys):
    assert seed_demo_console.main(ARGS) == 0
    out = capsys.readouterr().out
    page_views, questions, calls, judgements, runs = counts(demo_db)
    for label, value in (
        ("page views", page_views),
        ("questions", questions),
        ("model calls", calls),
        ("judgements", judgements),
        ("flagged runs", runs),
    ):
        assert f"{label}: {value}" in out
        assert value > 0
    assert page_views + questions + calls + judgements + runs < 4000  # a few thousand rows at most
    assert "demo dataset" in out


def test_seed_is_deterministic(tmp_path, monkeypatch):
    results = []
    for name in ("a.db", "b.db"):
        db = make_db(tmp_path, name)
        monkeypatch.setenv("DATABASE_URL", db.url)
        monkeypatch.setenv("ASSIST_AGENT_ID", db.agent.id)
        assert seed_demo_console.main(ARGS + ["--seed", "7"]) == 0
        results.append((counts(db), fingerprint(db)))
        db.close()
        db.engine.dispose()
    assert results[0] == results[1]


def test_a_different_seed_gives_different_rows(tmp_path, monkeypatch):
    prints = []
    for name, seed in (("a.db", "1"), ("b.db", "2")):
        db = make_db(tmp_path, name)
        monkeypatch.setenv("DATABASE_URL", db.url)
        monkeypatch.setenv("ASSIST_AGENT_ID", db.agent.id)
        assert seed_demo_console.main(ARGS + ["--seed", seed]) == 0
        prints.append(fingerprint(db))
        db.close()
        db.engine.dispose()
    assert prints[0] != prints[1]


def test_seed_twice_is_idempotent(demo_db):
    assert seed_demo_console.main(ARGS) == 0
    first = counts(demo_db), fingerprint(demo_db)
    assert seed_demo_console.main(ARGS) == 0
    assert (counts(demo_db), fingerprint(demo_db)) == first


def test_seed_never_touches_live_or_example_rows(demo_db):
    db = demo_db
    upsert_page_view(db, db.workspace.id, "live-session-001", "US12401720B1", 40000, {"claims": 20000}, False, 1, "extension")
    live_question(db, "live-session-001")
    assert import_page_views.main([str(EXAMPLE)]) == 0
    db.expire_all()
    models = (AssistPageView, AssistQuestion, Run, PromptVersion)
    before = {m.__name__: snapshot(db, m) for m in models}
    current_before = db.agent.current_prompt_version_id
    assert seed_demo_console.main(ARGS) == 0
    db.expire_all()
    after_untouched = {
        "AssistPageView": [r for r in snapshot(db, AssistPageView) if "demo30-" not in r],
        "AssistQuestion": [r for r in snapshot(db, AssistQuestion) if "demo30-" not in r],
        "Run": [r for r in snapshot(db, Run) if "demo30-" not in r],
        "PromptVersion": snapshot(db, PromptVersion),
    }
    assert after_untouched == before
    db.refresh(db.agent)
    assert db.agent.current_prompt_version_id == current_before


def test_remove_restores_pre_seed_counts(demo_db, capsys):
    db = demo_db
    live_question(db, "live-session-001")
    assert import_page_views.main([str(EXAMPLE)]) == 0
    before = counts(db)
    assert seed_demo_console.main(ARGS) == 0
    assert counts(db) != before
    capsys.readouterr()
    assert seed_demo_console.main(["--remove", "--confirm"]) == 0
    assert counts(db) == before
    assert "Removed" in capsys.readouterr().out


def test_a_live_row_using_the_prefix_aborts_without_writing(demo_db, capsys):
    db = demo_db
    live_question(db, "demo30-live-0001")
    before = counts(db)
    assert seed_demo_console.main(ARGS) == 2
    assert counts(db) == before
    assert "never changes live data" in capsys.readouterr().err


def test_invalid_end_date_is_invalid_input(demo_db, capsys):
    assert seed_demo_console.main(["--confirm", "--end", "yesterday"]) == 2
    assert counts(demo_db) == (0, 0, 0, 0, 0)


def test_missing_agent_is_a_configuration_error(demo_db, monkeypatch, capsys):
    monkeypatch.delenv("ASSIST_AGENT_ID", raising=False)
    assert seed_demo_console.main(ARGS) == 3
    assert "ASSIST_AGENT_ID" in capsys.readouterr().err


def test_the_password_never_appears_in_output(monkeypatch, capsys):
    monkeypatch.delenv("ASSIST_AGENT_ID", raising=False)
    url = "postgresql://user:hunter2@127.0.0.1:1/none"
    assert seed_demo_console.main(ARGS + ["--database-url", url]) == 3
    captured = capsys.readouterr()
    assert "hunter2" not in captured.out + captured.err


def test_an_unmigrated_database_is_a_configuration_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'empty.db'}")
    assert seed_demo_console.main(ARGS) == 3
    assert "alembic upgrade head" in capsys.readouterr().err


def test_a_failure_while_saving_runs_leaves_nothing_behind(demo_db, monkeypatch, capsys):
    real = seed_demo_console.save_flagged_run
    calls = []

    def boom(*args, **kwargs):
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("disk on fire password=hunter2")
        return real(*args, **kwargs)

    monkeypatch.setattr(seed_demo_console, "save_flagged_run", boom)
    assert seed_demo_console.main(ARGS) == 3
    captured = capsys.readouterr()
    assert counts(demo_db) == (0, 0, 0, 0, 0)
    assert "hunter2" not in captured.out + captured.err


def test_cli_runs_as_a_script_from_another_directory(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["GROQ_SKIP_DOTENV"] = "1"
    env["DATABASE_URL"] = f"sqlite:///{tmp_path / 'cli.db'}"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "seed_demo_console.py"), "--end", "2026-11-24"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 2, result.stderr
    assert "Nothing was written" in result.stdout


# --- the data ---------------------------------------------------------------------------------------------


@pytest.fixture
def seeded(demo_db):
    assert seed_demo_console.main(ARGS) == 0
    return demo_db


def test_spans_about_thirty_days(seeded):
    views = seeded.scalars(select(AssistPageView.started_at)).all()
    days = {day_of(v) for v in views}
    assert min(days) == 1 and max(days) == 30
    assert 28 <= (max(views) - min(views)).days + 1 <= 30
    assert max(views).date() == END


def test_no_prompt_version_created(demo_db):
    before = snapshot(demo_db, PromptVersion)
    current = demo_db.agent.current_prompt_version_id
    assert seed_demo_console.main(ARGS) == 0
    assert snapshot(demo_db, PromptVersion) == before
    demo_db.refresh(demo_db.agent)
    assert demo_db.agent.current_prompt_version_id == current


def test_everything_is_labelled_imported_and_prefixed(seeded):
    for model in (AssistPageView, AssistQuestion, AssistJudgement, Run):
        rows = seeded.scalars(select(model)).all()
        assert rows and all(r.source == "imported" and r.session_id.startswith("demo30-") for r in rows)
    calls = seeded.scalars(select(AssistCall)).all()
    assert calls and all(c.source == "imported" for c in calls)


def test_judgements_never_reference_real_model_output(seeded):
    judgements = seeded.scalars(select(AssistJudgement)).all()
    assert judgements
    for j in judgements:
        assert j.model == "demo-seed" and j.rubric_version == "demo" and j.source == "imported"
        assert j.reason in REASONS and 1 <= j.grade <= 5
        assert j.flagged == (j.grade <= FLAG_GRADE_AT_OR_BELOW)
    assert len({(j.session_id, j.prompt_version) for j in judgements}) == len(judgements)
    assert {c.model for c in seeded.scalars(select(AssistCall)).all()} == {"demo-seed"}


def test_every_asking_session_is_judged_once_and_flags_match(seeded):
    asked = set(seeded.scalars(select(AssistQuestion.session_id)).all())
    judged = {j.session_id: j for j in seeded.scalars(select(AssistJudgement)).all()}
    assert asked == set(judged)
    flagged_questions = {
        q.id: q for q in seeded.scalars(select(AssistQuestion).where(AssistQuestion.judge_flagged.is_(True))).all()
    }
    low = {sid for sid, j in judged.items() if j.grade <= FLAG_GRADE_AT_OR_BELOW}
    assert {q.session_id for q in flagged_questions.values()} == low
    assert len(flagged_questions) == len(low)  # one flagged question per low-graded session
    for sid in low:
        assert judged[sid].worst_question_id in flagged_questions


def test_about_twenty_five_flagged_sessions_have_runs(seeded):
    runs = seeded.scalars(select(Run)).all()
    assert 20 <= len(runs) <= 30
    low = {j.session_id for j in seeded.scalars(select(AssistJudgement)).all() if j.grade <= FLAG_GRADE_AT_OR_BELOW}
    assert {r.session_id for r in runs} == low
    for run in runs:
        assert run.prompt_version_id == seeded.agent.current_prompt_version_id
        Brief.model_validate(run.brief_snapshot)
        assert [m["role"] for m in run.messages][:2] == ["user", "assistant"]
    assert sorted(r.position for r in runs) == list(range(1, len(runs) + 1))


def test_versions_move_one_two_three_on_fixed_days(seeded):
    questions = seeded.scalars(select(AssistQuestion)).all()
    first_day = {v: min(day_of(q.created_at) for q in questions if q.assist_version == v) for v in (1, 2, 3)}
    assert first_day[1] == 1
    assert first_day[2] == 20
    assert first_day[3] >= 27
    assert max(day_of(q.created_at) for q in questions if q.assist_version == 1) <= 20
    assert {q.assist_version for q in questions} == {1, 2, 3}
    views = seeded.scalars(select(AssistPageView)).all()
    assert {v.assist_version for v in views} <= {None, 1, 2, 3}


def test_storyline_is_visible_in_the_data(seeded):
    db = seeded
    # weekday / weekend rhythm
    per_day: dict[date, int] = {}
    for started in db.scalars(select(AssistPageView.started_at)).all():
        per_day[started.date()] = per_day.get(started.date(), 0) + 1
    weekday = [n for d, n in per_day.items() if d.weekday() < 5]
    weekend = [n for d, n in per_day.items() if d.weekday() >= 5]
    assert statistics.mean(weekday) > 1.8 * statistics.mean(weekend)

    # grades and thumbs-ups dip on days 14-20 and recover
    grade_by_day: dict[int, list[int]] = {}
    for j in db.scalars(select(AssistJudgement)).all():
        grade_by_day.setdefault(day_of(j.created_at), []).append(j.grade)

    def grades(lo, hi):
        return [g for d, gs in grade_by_day.items() if lo <= d <= hi for g in gs]

    dip, before, after = grades(14, 20), grades(1, 13), grades(21, 30)
    assert statistics.median(dip) < statistics.median(before)
    assert statistics.median(dip) < statistics.median(after)

    questions = db.scalars(select(AssistQuestion)).all()

    def share(lo, hi, test):
        window = [q for q in questions if lo <= day_of(q.created_at) <= hi]
        return sum(1 for q in window if test(q)) / len(window)

    def up(q):
        return q.rating == "up"

    def reask(q):
        return q.reasked

    assert share(14, 20, up) < share(1, 13, up) and share(14, 20, up) < share(21, 30, up)
    assert share(14, 20, reask) > share(1, 13, reask) and share(14, 20, reask) > share(21, 30, reask)

    # latency: p95 on days 8-10 against the median day's p95
    ok = [c for c in db.scalars(select(AssistCall)).all() if c.outcome == "ok"]
    by_day: dict[int, list[int]] = {}
    for c in ok:
        by_day.setdefault(day_of(c.created_at), []).append(c.latency_ms)
    median_day_p95 = statistics.median(percentile(v, 0.95) for v in by_day.values())
    bump = [ms for d in (8, 9, 10) for ms in by_day.get(d, [])]
    assert percentile(bump, 0.95) >= 1.5 * median_day_p95

    # the rate-limit burst on day 15
    limited = [
        c for c in db.scalars(select(AssistCall)).all()
        if c.outcome == "rate_limited" and c.kind == "chat" and day_of(c.created_at) == 15
    ]
    assert len(limited) >= 5
    assert all(c.total_tokens is None for c in limited)


def test_question_and_answer_text_is_canned(seeded):
    texts = {q.text for q in seeded.scalars(select(AssistQuestion)).all()}
    assert len(texts) < 100  # a small set of templates, not generated prose
    assert all(len(q.answer) < 400 for q in seeded.scalars(select(AssistQuestion)).all())


def test_templates_classify_as_their_intent():
    for intent, templates in seed_demo_console.QUESTIONS.items():
        for template in templates:
            assert classify_intent(template.format(n=3)) == intent, template
    assert set(seed_demo_console.QUESTIONS) == set(seed_demo_console.ANSWERS)


def test_every_brief_is_valid_and_patents_are_us_numbers():
    for number in seed_demo_console.PATENTS:
        title, abstract = seed_demo_console.BRIEFS[number]
        Brief.model_validate({"patent_number": number, "title": title, "abstract": abstract})
