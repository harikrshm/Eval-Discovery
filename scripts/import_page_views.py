"""Import hand-written page-view records (JSON Lines) into the Patent Assist demo console.

Usage: python scripts/import_page_views.py FILE [--dry-run] [--database-url URL]

FILE holds one JSON object per line (format: docs/assist-import-format.md; example:
examples/assist/page_views.example.jsonl). The script validates EVERY line first and prints all problems with
their line numbers; if any line is invalid nothing is imported. Otherwise it writes, in ONE transaction, into the
workspace of the configured assist agent (ASSIST_AGENT_ID): page views, questions and the flagged conversations
(as runs), all with source "imported". The console labels such data "demo dataset" and it never counts as
measured effect. The script never calls a model.

Re-importing the same session ids updates them: the page view merges with the live rules (the larger duration
wins), that session's imported questions are replaced by the file's, and its flagged run is updated in place.
A session id that already exists as live data is a conflict and aborts the whole import.

--dry-run validates and prints the counts only; it needs no database. The database is --database-url, else
$DATABASE_URL, else the local SQLite file. The password is masked in all output and no driver error text is ever
echoed (it can carry the connection URL).

Exit codes: 0 ok, 2 invalid input (bad lines, a conflict, the run cap, an unreadable file), 3 database or
configuration error (no assist agent, schema out of date, connection failure).
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `python scripts/import_page_views.py` finds `backend`

from sqlalchemy import delete, func, inspect, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend.core.assist.bounce import is_bounce  # noqa: E402
from backend.core.assist.importing import (  # noqa: E402
    IMPORTED_SOURCE,
    FlaggedConversation,
    PageViewRow,
    QuestionRow,
    expand,
    parse_lines,
)
from backend.db.assist import MAX_ASSIST_RUNS, get_assist_agent  # noqa: E402
from backend.db.models import Agent, AssistPageView, AssistQuestion, Run  # noqa: E402
from backend.db.session import DEFAULT_DATABASE_URL, make_engine  # noqa: E402
from scripts.metrics import mask_url  # noqa: E402

EXIT_OK, EXIT_INVALID, EXIT_DATABASE = 0, 2, 3
CHUNK = 400  # session ids per IN (...) query, under SQLite's variable limit
REQUIRED_TABLES = ("agent", "assist_page_view", "assist_question", "run")
NO_AGENT = (
    "the assist agent is not configured; run scripts/setup_assist.py, then set ASSIST_AGENT_ID "
    "to the agent id it prints"
)

Expanded = tuple[PageViewRow, list[QuestionRow], list[FlaggedConversation]]


def _error(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)


def _chunks(items: list[str]):
    for start in range(0, len(items), CHUNK):
        yield items[start : start + CHUNK]


# ---- writing --------------------------------------------------------------------------------------------


def _insert_page_view(db: Session, workspace_id: str, row: PageViewRow) -> None:
    db.add(
        AssistPageView(
            session_id=row.session_id,
            workspace_id=workspace_id,
            patent_number=row.patent_number,
            started_at=row.started_at,
            updated_at=row.updated_at,
            dwell_ms=row.dwell_ms,
            sections=dict(row.sections),
            bounce=row.bounce,
            source=IMPORTED_SOURCE,
            assist_version=row.assist_version,
        )
    )


def _merge_page_view(view: AssistPageView, row: PageViewRow) -> None:
    """The live merge rules (see upsert_page_view): keep `started_at` and `assist_version`, raise the durations."""
    merged_dwell = max(view.dwell_ms or 0, row.dwell_ms)
    merged = dict(view.sections or {})
    for name, ms in row.sections.items():
        merged[name] = max(merged.get(name, 0), ms)
    view.dwell_ms = merged_dwell
    view.sections = merged  # a new dict, so the JSON column registers the change
    view.bounce = is_bounce(merged_dwell, merged)
    view.updated_at = max(view.updated_at or row.updated_at, row.updated_at)
    view.source = IMPORTED_SOURCE


def _insert_question(db: Session, workspace_id: str, row: QuestionRow) -> None:
    db.add(
        AssistQuestion(
            workspace_id=workspace_id,
            session_id=row.session_id,
            patent_number=row.patent_number,
            text=row.text,
            intent=row.intent,
            section_focus=row.section_focus,
            answer=row.answer,
            rating=row.rating,
            rating_reason=row.rating_reason,
            reasked=row.reasked,
            citation_ok=row.citation_ok,
            source=IMPORTED_SOURCE,
            assist_version=row.assist_version,
            created_at=row.created_at,
        )
    )


def _insert_flagged_run(db: Session, agent: Agent, conversation: FlaggedConversation, position: int) -> None:
    """Same column semantics as save_flagged_run for a new run, with the session's start as `created_at`."""
    db.add(
        Run(
            agent_id=agent.id,
            prompt_version_id=agent.current_prompt_version_id,
            position=position,
            messages=[{"role": m["role"], "content": m["content"]} for m in conversation.messages],
            source=IMPORTED_SOURCE,
            session_id=conversation.session_id,
            brief_snapshot=dict(conversation.brief),
            created_at=conversation.started_at,
        )
    )


def _update_flagged_run(run: Run, conversation: FlaggedConversation) -> None:
    """A second save for the session updates messages, brief and source; position and version stay."""
    run.messages = [{"role": m["role"], "content": m["content"]} for m in conversation.messages]
    run.brief_snapshot = dict(conversation.brief)
    run.source = IMPORTED_SOURCE


def _plan(db: Session, agent: Agent, workspace_id: str, records: list[tuple[int, Expanded]]):
    """Read what exists for the file's sessions; return (views, runs, conflicts). Writes nothing."""
    ids = [expanded[0].session_id for _, expanded in records]
    views: dict[str, AssistPageView] = {}
    runs: dict[str, Run] = {}
    live_questions: dict[str, str] = {}
    for chunk in _chunks(ids):
        for view in db.scalars(select(AssistPageView).where(AssistPageView.session_id.in_(chunk)).with_for_update()):
            views[view.session_id] = view
        for run in db.scalars(select(Run).where(Run.agent_id == agent.id, Run.session_id.in_(chunk))):
            runs[run.session_id] = run
        for session_id, source in db.execute(
            select(AssistQuestion.session_id, AssistQuestion.source)
            .where(
                AssistQuestion.workspace_id == workspace_id,
                AssistQuestion.session_id.in_(chunk),
                AssistQuestion.source != IMPORTED_SOURCE,
            )
            .distinct()
        ):
            live_questions[session_id] = source

    conflicts = []
    for line_no, (row, _, _) in records:
        sid = row.session_id
        view = views.get(sid)
        reason = None
        if view is not None and view.workspace_id != workspace_id:
            reason = "belongs to another workspace"
        elif view is not None and view.source != IMPORTED_SOURCE:
            reason = f"already exists as {view.source!r} data"
        elif view is not None and view.patent_number.strip().upper() != row.patent_number:
            reason = f"already exists for patent {view.patent_number}, not {row.patent_number}"
        elif sid in live_questions:
            reason = f"already has {live_questions[sid]!r} questions"
        elif sid in runs and runs[sid].source != IMPORTED_SOURCE:
            reason = f"already has a {runs[sid].source!r} flagged run"
        if reason:
            conflicts.append(f"line {line_no}: session_id: {sid!r} {reason}; the importer never changes live data")
    return views, runs, conflicts


def _apply(db: Session, agent: Agent, workspace_id: str, records: list[tuple[int, Expanded]], views, runs) -> int:
    """Insert or update everything (flushed, not committed). Returns how many page views were updates."""
    updated = 0
    ids = [expanded[0].session_id for _, expanded in records]
    for chunk in _chunks(ids):  # a session's old imported questions are replaced, never added to
        db.execute(
            delete(AssistQuestion)
            .where(
                AssistQuestion.workspace_id == workspace_id,
                AssistQuestion.session_id.in_(chunk),
                AssistQuestion.source == IMPORTED_SOURCE,
            )
            .execution_options(synchronize_session=False)
        )
    next_position = (db.scalar(select(func.max(Run.position)).where(Run.agent_id == agent.id)) or 0) + 1
    for _, (view_row, question_rows, conversations) in records:
        existing = views.get(view_row.session_id)
        if existing is None:
            _insert_page_view(db, workspace_id, view_row)
        else:
            _merge_page_view(existing, view_row)
            updated += 1
        for question_row in question_rows:
            _insert_question(db, workspace_id, question_row)
        for conversation in conversations:
            run = runs.get(conversation.session_id)
            if run is None:
                _insert_flagged_run(db, agent, conversation, next_position)
                next_position += 1
            else:
                _update_flagged_run(run, conversation)
    db.flush()
    return updated


# ---- the command ----------------------------------------------------------------------------------------


def _summary(prefix: str, records: list[tuple[int, Expanded]]) -> str:
    views = len(records)
    questions = sum(len(q) for _, (_, q, _) in records)
    flagged = sum(len(f) for _, (_, _, f) in records)
    return f"{prefix}{views} page views, {questions} questions, {flagged} flagged conversations"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import hand-written page-view records (JSON Lines) as demo data.")
    parser.add_argument("file", help="the JSON Lines file (one page view per line)")
    parser.add_argument("--dry-run", action="store_true", help="validate and print counts; write nothing")
    parser.add_argument("--database-url", default=None, help="defaults to $DATABASE_URL, else the local SQLite file")
    args = parser.parse_args(argv)

    try:
        text = Path(args.file).read_text(encoding="utf-8-sig")  # a BOM is tolerated
    except (OSError, UnicodeDecodeError) as exc:
        _error(f"could not read {args.file} as UTF-8 text ({type(exc).__name__}); nothing was imported")
        return EXIT_INVALID

    parsed = parse_lines(line.rstrip("\r") for line in text.split("\n"))
    if parsed.errors:
        for message in parsed.errors:
            print(message, file=sys.stderr)
        _error(f"{len(parsed.errors)} problem(s) in {args.file}; nothing was imported")
        return EXIT_INVALID
    if not parsed.records:
        _error(f"no records in {args.file}; nothing was imported")
        return EXIT_INVALID

    records = [(line_no, expand(record)) for line_no, record in parsed.records]
    flagged_total = sum(len(f) for _, (_, _, f) in records)
    if flagged_total > MAX_ASSIST_RUNS:
        _error(
            f"{flagged_total} flagged conversations exceed the cap of {MAX_ASSIST_RUNS} flagged runs per "
            "workspace; nothing was imported"
        )
        return EXIT_INVALID

    if args.dry_run:
        print(_summary("Dry run: ", records) + ". Nothing was written.")
        return EXIT_OK

    url = args.database_url or os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL
    masked = mask_url(url)
    engine = None
    try:
        # Never echo a driver error: it can carry the connection URL.
        try:
            engine = make_engine(url)
            insp = inspect(engine)
            migrated = all(insp.has_table(name) for name in REQUIRED_TABLES)
        except Exception as exc:
            _error(f"could not read the database {masked} ({type(exc).__name__}); check DATABASE_URL")
            return EXIT_DATABASE
        if not migrated:
            _error("the database schema is out of date; run `alembic upgrade head` first")
            return EXIT_DATABASE

        with Session(engine, expire_on_commit=False) as db:
            try:
                agent, workspace = get_assist_agent(db)
            except LookupError:
                _error(NO_AGENT)
                return EXIT_DATABASE
            except Exception as exc:
                _error(f"could not read the database {masked} ({type(exc).__name__})")
                return EXIT_DATABASE
            try:
                views, runs, conflicts = _plan(db, agent, workspace.id, records)
                if conflicts:
                    for message in conflicts:
                        print(message, file=sys.stderr)
                    _error(f"{len(conflicts)} conflict(s) with existing data; nothing was imported")
                    db.rollback()
                    return EXIT_INVALID
                new_runs = sum(1 for _, (_, _, convs) in records for c in convs if c.session_id not in runs)
                existing_runs = db.scalar(select(func.count()).select_from(Run).where(Run.agent_id == agent.id))
                if existing_runs + new_runs > MAX_ASSIST_RUNS:
                    _error(
                        f"the cap of {MAX_ASSIST_RUNS} flagged runs per workspace would be exceeded "
                        f"({existing_runs} exist, {new_runs} new); nothing was imported"
                    )
                    db.rollback()
                    return EXIT_INVALID
                updated = _apply(db, agent, workspace.id, records, views, runs)
                db.commit()
            except Exception as exc:
                db.rollback()
                _error(f"the import failed and was rolled back ({type(exc).__name__}); nothing was imported")
                return EXIT_DATABASE
    finally:
        if engine is not None:
            engine.dispose()

    print(_summary("Imported ", records) + f" into {masked} (demo dataset).")
    if updated:
        print(f"{updated} of the page views already existed as imported data and were updated.")
    print("Imported data shows as demo dataset in the console and never counts as measured effect.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
