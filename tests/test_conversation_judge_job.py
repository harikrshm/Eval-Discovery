"""Conversation judge job: handler, idle scan, "Evaluate now" endpoint and the worker's idle pass."""

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import groq
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend import groq_client
from backend.core.assist.convo_judge import JudgeOutputError, JudgeTruncated, Verdict
from backend.db.agents import create_agent
from backend.db.assist import add_question, save_flagged_run
from backend.db.models import AssistCall, AssistJudgement, AssistPageView, Job, Run, utcnow
from backend.db.workspaces import create_workspace
from backend.groq_client import MODEL_JUDGE
from backend.jobs import conversation_judge as cj
from backend.jobs import worker
from backend.jobs.conversation_judge import (
    CHAT_QUIET_SECONDS,
    JOB_TYPE,
    JUDGE_IDLE_MINUTES,
    JUDGE_MAX_PER_HOUR,
    evaluate_now,
    handle_judge_conversation,
    judge_queue_stats,
    scan_and_enqueue,
    unjudged_sessions,
)
from backend.jobs.handlers import HANDLERS, JobFailed, RetryableJobFailure
from backend.jobs.queue import claim_next, enqueue
from backend.jobs.worker import run_one
from scripts import setup_assist

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 6, 1, 12, 0, 0)
EVALUATE = "/assist/console/judge/evaluate"
_REQUEST = httpx.Request("POST", "http://x")


@pytest.fixture(autouse=True)
def _clean_usage():
    groq_client.last_usage.set(None)
    yield
    groq_client.last_usage.set(None)


@pytest.fixture(autouse=True)
def _fresh_scan_throttle():
    worker.reset_scan_throttle()
    yield
    worker.reset_scan_throttle()


@pytest.fixture
def world(db_session):
    workspace, token = create_workspace(db_session)
    agent = create_agent(db_session, workspace, setup_assist.ASSIST_CONFIG, setup_assist.ASSIST_ANALYSIS, kind="assist")
    return workspace, agent, {"X-Workspace-Token": token}


def ask(db, workspace, session, minutes_ago=10.0, version=1, source="live", n=1, now=NOW):
    return add_question(
        db, workspace.id, session, "US10000000B2", f"question {n}", "claim_explain", "claims", f"answer {n}",
        True, source, version, created_at=now - timedelta(minutes=minutes_ago),
    )


def mark_judged(db, workspace, session, version=1):
    db.add(
        AssistJudgement(
            workspace_id=workspace.id, session_id=session, prompt_version=version, rubric_version="1",
            model=MODEL_JUDGE, answered="yes", concise=True, citations_sensible=True, legal="not_applicable",
            grade=4, reason="no_issue", flagged=False,
        )
    )
    db.commit()


def verdict(grade=4, worst=None) -> Verdict:
    return Verdict("yes", True, True, "not_applicable", grade, "no_issue" if grade > 2 else "missed_question", worst)


def judge_job(db, workspace, session, attempts=1):
    enqueue(db, workspace.id, JOB_TYPE, {"session_id": session, "system": True})
    job = claim_next(db)
    job.attempts = attempts
    return job


@pytest.fixture
def fake_judge(monkeypatch):
    """Replace judge_conversation; `.calls` records (turns, page_summary), `.result` / `.error` steer it."""
    state = MagicMock()
    state.calls = []
    state.result = verdict()
    state.error = None

    def _judge(turns, page_summary):
        state.calls.append((turns, page_summary))
        if state.error is not None:
            raise state.error
        groq_client.last_usage.set({"prompt_tokens": 700, "completion_tokens": 50, "total_tokens": 750})
        return state.result

    monkeypatch.setattr(cj, "judge_conversation", _judge)
    return state


def judge_calls(db):
    return list(db.scalars(select(AssistCall).where(AssistCall.kind == "judge")))


# ---- handler ---------------------------------------------------------------------


def test_handler_judges_and_stores_verdict_and_telemetry(db_session, world, fake_judge):
    workspace, agent, _ = world
    first = ask(db_session, workspace, "s1", minutes_ago=20, version=1, n=1)
    second = ask(db_session, workspace, "s1", minutes_ago=10, version=2, n=2)
    fake_judge.result = verdict(grade=4, worst=second.id)
    job = judge_job(db_session, workspace, "s1", attempts=2)

    result = handle_judge_conversation(db_session, job)

    assert result == {"status": "judged", "grade": 4, "flagged": False}
    turns, summary = fake_judge.calls[0]
    assert [(t.question_id, t.question, t.answer) for t in turns] == [
        (first.id, "question 1", "answer 1"),
        (second.id, "question 2", "answer 2"),
    ]
    assert summary is None  # no stored run, so no page summary
    row = db_session.scalar(select(AssistJudgement))
    assert (row.session_id, row.prompt_version, row.model, row.grade) == ("s1", 2, MODEL_JUDGE, 4)
    assert row.worst_question_id == second.id and row.flagged is False
    (call,) = judge_calls(db_session)
    assert (call.model, call.outcome, call.attempts, call.source) == (MODEL_JUDGE, "ok", 2, "live")
    assert (call.prompt_tokens, call.completion_tokens, call.total_tokens) == (700, 50, 750)
    assert call.workspace_id == workspace.id


def test_handler_gives_the_judge_a_short_summary_of_the_stored_brief(db_session, world, fake_judge):
    workspace, agent, _ = world
    ask(db_session, workspace, "s1")
    brief = {"patent_number": "US10000000B2", "title": "T", "abstract": "A", "claims": [],
             "completeness": {"claims": True, "description": False}}
    save_flagged_run(db_session, agent, "s1", [{"role": "user", "content": "q"}], brief, "assist")

    handle_judge_conversation(db_session, judge_job(db_session, workspace, "s1"))

    summary = fake_judge.calls[0][1]
    assert "US10000000B2" in summary and "claims present" in summary and "description missing" in summary
    assert len(summary) < 200  # a short label line, never the brief's text


def test_handler_flags_a_low_grade_into_the_loop(db_session, world, fake_judge):
    workspace, agent, _ = world
    asked = ask(db_session, workspace, "s1")
    fake_judge.result = verdict(grade=1, worst=asked.id)

    result = handle_judge_conversation(db_session, judge_job(db_session, workspace, "s1"))

    assert result == {"status": "judged", "grade": 1, "flagged": True}
    run = db_session.scalar(select(Run).where(Run.session_id == "s1"))
    assert run.source == "assist"
    assert [m["content"] for m in run.messages] == ["question 1", "answer 1"]


def test_handler_skips_imported_session_without_model_call(db_session, world, fake_judge):
    workspace, _, _ = world
    ask(db_session, workspace, "s-imp", source="imported")
    ask(db_session, workspace, "s-view")
    db_session.add(
        AssistPageView(session_id="s-view", workspace_id=workspace.id, patent_number="US1", source="imported")
    )
    db_session.commit()

    for session in ("s-imp", "s-view"):
        result = handle_judge_conversation(db_session, judge_job(db_session, workspace, session))
        assert result == {"status": "skipped", "reason": "imported"}

    assert fake_judge.calls == []
    assert judge_calls(db_session) == []
    assert db_session.scalar(select(AssistJudgement)) is None


def test_handler_skips_a_session_without_questions(db_session, world, fake_judge):
    workspace, _, _ = world

    result = handle_judge_conversation(db_session, judge_job(db_session, workspace, "ghost"))

    assert result == {"status": "skipped", "reason": "no_questions"}
    assert fake_judge.calls == [] and judge_calls(db_session) == []


def test_handler_skips_already_judged_for_version(db_session, world, fake_judge):
    workspace, _, _ = world
    ask(db_session, workspace, "s1", version=3)
    mark_judged(db_session, workspace, "s1", version=3)

    result = handle_judge_conversation(db_session, judge_job(db_session, workspace, "s1"))

    assert result == {"status": "skipped", "reason": "already_judged"}
    assert fake_judge.calls == [] and judge_calls(db_session) == []


def test_handler_treats_a_lost_race_as_already_judged(db_session, world, fake_judge, monkeypatch):
    workspace, _, _ = world
    ask(db_session, workspace, "s1")

    def duplicate(*args, **kwargs):
        raise IntegrityError("insert", {}, Exception("duplicate"))

    monkeypatch.setattr(cj, "record_judgement", duplicate)

    result = handle_judge_conversation(db_session, judge_job(db_session, workspace, "s1"))

    assert result == {"status": "skipped", "reason": "already_judged"}
    assert [c.outcome for c in judge_calls(db_session)] == ["ok"]  # the model call was made and is recorded


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (groq.RateLimitError("slow", response=httpx.Response(429, request=_REQUEST), body=None), "rate_limited"),
        (groq.APITimeoutError(request=_REQUEST), "timeout"),
    ],
)
def test_handler_rate_limit_is_retryable(db_session, world, fake_judge, error, outcome):
    workspace, _, _ = world
    ask(db_session, workspace, "s1")
    fake_judge.error = error

    with pytest.raises(RetryableJobFailure):
        handle_judge_conversation(db_session, judge_job(db_session, workspace, "s1", attempts=2))

    (call,) = judge_calls(db_session)
    assert (call.outcome, call.attempts) == (outcome, 2)
    assert db_session.scalar(select(AssistJudgement)) is None


def test_handler_bad_output_fails_job_with_fixed_message(db_session, world, fake_judge):
    workspace, _, _ = world
    ask(db_session, workspace, "s1")
    fake_judge.error = JudgeOutputError("provider said: <script>alert(1)</script>")

    with pytest.raises(JobFailed) as caught:
        handle_judge_conversation(db_session, judge_job(db_session, workspace, "s1"))

    assert not isinstance(caught.value, RetryableJobFailure)
    assert str(caught.value) == "The judge could not read this conversation."
    assert [c.outcome for c in judge_calls(db_session)] == ["error"]


def test_handler_records_the_judged_sessions_prompt_version_on_the_telemetry_row(db_session, world, fake_judge):
    workspace, _, _ = world
    ask(db_session, workspace, "s1", version=3)

    handle_judge_conversation(db_session, judge_job(db_session, workspace, "s1"))

    (call,) = judge_calls(db_session)
    assert call.prompt_version == 3


def test_a_truncated_reply_is_retryable_not_unreadable_and_the_session_stays_eligible(db_session, world, fake_judge):
    workspace, _, _ = world
    ask(db_session, workspace, "s1", minutes_ago=30)
    fake_judge.error = JudgeTruncated("cut off")

    with pytest.raises(RetryableJobFailure) as caught:
        handle_judge_conversation(db_session, judge_job(db_session, workspace, "s1", attempts=2))

    assert str(caught.value) == "The judge was unavailable. Try again."
    (call,) = judge_calls(db_session)
    assert (call.outcome, call.attempts, call.prompt_version) == ("error", 2, 1)
    assert db_session.scalar(select(AssistJudgement)) is None


def test_a_session_whose_replies_keep_truncating_fails_as_unavailable_and_is_retried_later(db_session, world, fake_judge):
    workspace, _, _ = world
    ask(db_session, workspace, "s1", minutes_ago=30)
    fake_judge.error = JudgeTruncated("cut off")
    job = enqueue(db_session, workspace.id, JOB_TYPE, {"session_id": "s1", "system": True})

    for _ in range(3):  # MAX_JOB_ATTEMPTS: requeued twice, then failed
        assert run_one(db_session) is True

    job = db_session.get(Job, job.id)
    assert (job.status, job.error) == ("failed", UNAVAILABLE)
    stats = judge_queue_stats(db_session, workspace.id)
    assert (stats["waiting_sessions"], stats["unreadable_sessions"]) == (1, 0)
    assert evaluate_now(db_session, workspace.id)["queued"] == 1  # eligible again, never "unreadable"


def test_a_storage_failure_after_the_model_call_still_records_the_telemetry_row(db_session, world, fake_judge, monkeypatch):
    workspace, _, _ = world
    ask(db_session, workspace, "s1")

    def broken(*args, **kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(cj, "record_judgement", broken)

    with pytest.raises(RuntimeError):
        handle_judge_conversation(db_session, judge_job(db_session, workspace, "s1"))

    (call,) = judge_calls(db_session)  # the model call happened and used tokens: it is still counted
    assert (call.outcome, call.total_tokens, call.prompt_version) == ("error", 750, 1)


def test_a_failing_judge_never_blocks_the_queue_and_runs_through_the_worker(db_session, world, fake_judge):
    workspace, _, _ = world
    ask(db_session, workspace, "s1")
    job = enqueue(db_session, workspace.id, JOB_TYPE, {"session_id": "s1", "system": True})
    fake_judge.error = JudgeOutputError("garbage")

    assert run_one(db_session) is True

    job = db_session.get(Job, job.id)
    assert job.status == "failed" and job.error == "The judge could not read this conversation."

    fake_judge.error = None
    ask(db_session, workspace, "s2")
    ok = enqueue(db_session, workspace.id, JOB_TYPE, {"session_id": "s2", "system": True})
    assert run_one(db_session) is True
    assert db_session.get(Job, ok.id).status == "done"
    assert db_session.get(Job, ok.id).result["status"] == "judged"


def test_handler_is_registered():
    assert HANDLERS[JOB_TYPE] is handle_judge_conversation


@pytest.mark.parametrize("first", ["backend.jobs.conversation_judge", "backend.jobs.handlers", "backend.jobs.worker"])
def test_modules_import_in_any_order(first):
    code = f"import {first}; from backend.jobs.handlers import HANDLERS; assert 'judge_conversation' in HANDLERS"
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr


def test_constants():
    assert (JUDGE_IDLE_MINUTES, JUDGE_MAX_PER_HOUR, CHAT_QUIET_SECONDS, JOB_TYPE) == (3, 20, 60, "judge_conversation")


# ---- unjudged sessions -----------------------------------------------------------


def test_unjudged_sessions_are_live_idle_and_oldest_first(db_session, world):
    workspace, _, _ = world
    ask(db_session, workspace, "newer", minutes_ago=10)
    ask(db_session, workspace, "oldest", minutes_ago=30)
    ask(db_session, workspace, "busy", minutes_ago=1)
    ask(db_session, workspace, "imp", minutes_ago=40, source="imported")
    ask(db_session, workspace, "done", minutes_ago=50)
    mark_judged(db_session, workspace, "done")
    other, _ = create_workspace(db_session)
    ask(db_session, other, "foreign", minutes_ago=60)

    idle_before = NOW - timedelta(minutes=JUDGE_IDLE_MINUTES)
    assert unjudged_sessions(db_session, workspace.id, idle_before) == ["oldest", "newer"]
    assert unjudged_sessions(db_session, workspace.id, None) == ["oldest", "newer", "busy"]


def test_new_version_makes_session_judgeable_again(db_session, world):
    workspace, _, _ = world
    ask(db_session, workspace, "s1", minutes_ago=30, version=1)
    mark_judged(db_session, workspace, "s1", version=1)
    assert unjudged_sessions(db_session, workspace.id, None) == []

    ask(db_session, workspace, "s1", minutes_ago=20, version=2, n=2)
    assert unjudged_sessions(db_session, workspace.id, None) == ["s1"]

    mark_judged(db_session, workspace, "s1", version=2)
    assert unjudged_sessions(db_session, workspace.id, None) == []


# ---- idle scan -------------------------------------------------------------------


def judge_jobs(db):
    return list(db.scalars(select(Job).where(Job.type == JOB_TYPE).order_by(Job.created_at, Job.id)))


def test_scan_enqueues_the_oldest_idle_session_as_a_system_job(db_session, world):
    workspace, _, _ = world
    ask(db_session, workspace, "newer", minutes_ago=10)
    ask(db_session, workspace, "oldest", minutes_ago=30)

    assert scan_and_enqueue(db_session, NOW) == 1

    (job,) = judge_jobs(db_session)
    assert job.workspace_id == workspace.id and job.payload == {"session_id": "oldest", "system": True}


def test_scan_does_nothing_without_work(db_session, world):
    assert scan_and_enqueue(db_session, NOW) == 0
    assert judge_jobs(db_session) == []


@pytest.mark.parametrize("status", ["queued", "running"])
def test_scan_yields_to_flywheel_job(db_session, world, status):
    workspace, _, _ = world
    ask(db_session, workspace, "s1")
    flywheel = enqueue(db_session, workspace.id, "build_tests", {"criterion_id": "x"})
    flywheel.status = status
    db_session.commit()

    assert scan_and_enqueue(db_session, NOW) == 0

    flywheel.status = "done"
    db_session.commit()
    assert scan_and_enqueue(db_session, NOW) == 1


def test_scan_waits_for_chat_quiet_period(db_session, world):
    workspace, _, _ = world
    ask(db_session, workspace, "old", minutes_ago=20)
    ask(db_session, workspace, "chatting", minutes_ago=(CHAT_QUIET_SECONDS - 5) / 60)
    ask(db_session, workspace, "demo", minutes_ago=0, source="imported")  # demo data never blocks the scan

    assert scan_and_enqueue(db_session, NOW) == 0

    assert scan_and_enqueue(db_session, NOW + timedelta(seconds=10)) == 1


def test_scan_respects_idle_time_and_picks_oldest(db_session, world):
    workspace, _, _ = world
    ask(db_session, workspace, "fresh", minutes_ago=JUDGE_IDLE_MINUTES - 1)
    assert scan_and_enqueue(db_session, NOW) == 0  # not idle long enough

    ask(db_session, workspace, "b", minutes_ago=JUDGE_IDLE_MINUTES + 5)
    ask(db_session, workspace, "a", minutes_ago=JUDGE_IDLE_MINUTES + 9)
    assert scan_and_enqueue(db_session, NOW) == 1
    assert judge_jobs(db_session)[0].payload["session_id"] == "a"


def test_scan_picks_the_oldest_across_workspaces(db_session, world):
    workspace, _, _ = world
    other, _ = create_workspace(db_session)
    ask(db_session, workspace, "mine", minutes_ago=10)
    ask(db_session, other, "theirs", minutes_ago=25)

    assert scan_and_enqueue(db_session, NOW) == 1

    (job,) = judge_jobs(db_session)
    assert (job.workspace_id, job.payload["session_id"]) == (other.id, "theirs")


def test_scan_one_at_a_time(db_session, world):
    workspace, _, _ = world
    ask(db_session, workspace, "a", minutes_ago=30)
    ask(db_session, workspace, "b", minutes_ago=20)

    assert scan_and_enqueue(db_session, NOW) == 1
    assert scan_and_enqueue(db_session, NOW) == 0  # queued
    (job,) = judge_jobs(db_session)
    job.status = "running"
    db_session.commit()
    assert scan_and_enqueue(db_session, NOW) == 0  # running

    job.status = "done"
    mark_judged(db_session, workspace, "a")
    assert scan_and_enqueue(db_session, NOW) == 1
    assert [j.payload["session_id"] for j in judge_jobs(db_session)] == ["a", "b"]


def fill_hourly_cap(db, workspace, created_at):
    for index in range(JUDGE_MAX_PER_HOUR):
        job = enqueue(db, workspace.id, JOB_TYPE, {"session_id": f"old-{index}", "system": True})
        job.status = "done"
        job.created_at = created_at
    db.commit()
    return db


def test_scan_hourly_cap_leaves_rest_waiting(db_session, world):
    workspace, _, _ = world
    other, _ = create_workspace(db_session)
    ask(db_session, workspace, "a", minutes_ago=30)
    ask(db_session, workspace, "b", minutes_ago=20)
    ask(db_session, other, "theirs", minutes_ago=10)
    fill_hourly_cap(db_session, workspace, NOW - timedelta(minutes=30))

    assert scan_and_enqueue(db_session, NOW) == 1  # the capped workspace is skipped, the other one is not
    assert judge_jobs(db_session)[-1].workspace_id == other.id
    assert unjudged_sessions(db_session, workspace.id, None) == ["a", "b"]  # still waiting

    for job in judge_jobs(db_session):
        job.status = "done"
    mark_judged(db_session, other, "theirs")
    assert scan_and_enqueue(db_session, NOW) == 0  # the capped workspace's sessions keep waiting

    assert scan_and_enqueue(db_session, NOW + timedelta(hours=1, minutes=1)) == 1  # the hour has passed


UNREADABLE = "The judge could not read this conversation."
UNAVAILABLE = "The judge was unavailable. Try again."


def failed_judge_job(db, workspace, session, error, created_at):
    job = enqueue(db, workspace.id, JOB_TYPE, {"session_id": session, "system": True})
    job.status, job.error, job.created_at = "failed", error, created_at
    db.commit()
    return job


def test_transient_failure_is_retried_by_the_scan(db_session, world):
    workspace, _, _ = world
    ask(db_session, workspace, "a", minutes_ago=30)
    ask(db_session, workspace, "b", minutes_ago=20)
    failed_judge_job(db_session, workspace, "a", UNAVAILABLE, NOW - timedelta(minutes=5))  # retries used up

    assert scan_and_enqueue(db_session, NOW) == 1

    assert judge_jobs(db_session)[-1].payload["session_id"] == "a"  # still the oldest, and still eligible


def test_transient_failure_is_retried_by_evaluate_now_and_counted_as_waiting(db_session, world):
    workspace, _, _ = world
    ask(db_session, workspace, "a", minutes_ago=30)
    ask(db_session, workspace, "b", minutes_ago=20)
    failed_judge_job(db_session, workspace, "a", UNAVAILABLE, NOW - timedelta(minutes=5))

    stats = judge_queue_stats(db_session, workspace.id, NOW)
    assert (stats["waiting_sessions"], stats["unreadable_sessions"]) == (2, 0)
    assert evaluate_now(db_session, workspace.id, NOW) == {"queued": 1, "waiting": 1}
    assert judge_jobs(db_session)[-1].payload["session_id"] == "a"


def test_unreadable_session_is_not_retried_not_waiting_and_shown_apart(db_session, world):
    workspace, _, _ = world
    ask(db_session, workspace, "a", minutes_ago=30)
    ask(db_session, workspace, "b", minutes_ago=20)
    failed_judge_job(db_session, workspace, "a", UNREADABLE, NOW - timedelta(minutes=5))

    stats = judge_queue_stats(db_session, workspace.id, NOW)
    assert (stats["waiting_sessions"], stats["unreadable_sessions"]) == (1, 1)
    assert scan_and_enqueue(db_session, NOW) == 1
    job = judge_jobs(db_session)[-1]
    assert job.payload["session_id"] == "b"  # not starved by the unreadable "a"

    job.status = "done"
    mark_judged(db_session, workspace, "b")
    assert scan_and_enqueue(db_session, NOW) == 0
    assert evaluate_now(db_session, workspace.id, NOW) == {"queued": 0, "waiting": 0}
    stats = judge_queue_stats(db_session, workspace.id, NOW)
    assert (stats["waiting_sessions"], stats["unreadable_sessions"], stats["oldest_waiting_minutes"]) == (0, 1, None)
    assert unjudged_sessions(db_session, workspace.id, None) == ["a"]  # the brief's definition is unchanged


def test_new_question_after_an_unreadable_failure_makes_the_session_eligible_again(db_session, world):
    workspace, _, _ = world
    ask(db_session, workspace, "a", minutes_ago=30)
    failed_judge_job(db_session, workspace, "a", UNREADABLE, NOW - timedelta(minutes=5))
    assert scan_and_enqueue(db_session, NOW) == 0

    ask(db_session, workspace, "a", minutes_ago=4, n=2)  # asked after the failure

    assert judge_queue_stats(db_session, workspace.id, NOW)["unreadable_sessions"] == 0
    assert scan_and_enqueue(db_session, NOW) == 1
    assert judge_jobs(db_session)[-1].payload["session_id"] == "a"


def test_a_later_transient_failure_lifts_the_unreadable_mark(db_session, world):
    workspace, _, _ = world
    ask(db_session, workspace, "a", minutes_ago=30)
    failed_judge_job(db_session, workspace, "a", UNREADABLE, NOW - timedelta(minutes=8))
    failed_judge_job(db_session, workspace, "a", UNAVAILABLE, NOW - timedelta(minutes=4))

    stats = judge_queue_stats(db_session, workspace.id, NOW)
    assert (stats["waiting_sessions"], stats["unreadable_sessions"]) == (1, 0)


# ---- boundaries ------------------------------------------------------------------


def test_idle_time_boundary_is_inclusive(db_session, world):
    workspace, _, _ = world
    ask(db_session, workspace, "short", minutes_ago=JUDGE_IDLE_MINUTES - 1 / 60)
    assert scan_and_enqueue(db_session, NOW) == 0  # one second short of idle

    ask(db_session, workspace, "exact", minutes_ago=JUDGE_IDLE_MINUTES)
    assert scan_and_enqueue(db_session, NOW) == 1  # exactly 3 minutes counts as idle
    assert judge_jobs(db_session)[0].payload["session_id"] == "exact"


def test_chat_quiet_boundary_is_inclusive(db_session, world):
    workspace, _, _ = world
    ask(db_session, workspace, "old", minutes_ago=20)
    ask(db_session, workspace, "chatting", minutes_ago=(CHAT_QUIET_SECONDS - 1) / 60)
    assert scan_and_enqueue(db_session, NOW) == 0  # 59 seconds: still chatting

    assert scan_and_enqueue(db_session, NOW + timedelta(seconds=1)) == 1  # exactly 60 seconds is quiet


def test_hourly_cap_boundary_counts_a_job_created_exactly_one_hour_ago(db_session, world):
    workspace, _, _ = world
    ask(db_session, workspace, "a", minutes_ago=30)
    fill_hourly_cap(db_session, workspace, NOW - timedelta(hours=1))
    assert scan_and_enqueue(db_session, NOW) == 0  # exactly one hour ago still counts

    for job in judge_jobs(db_session):
        job.created_at = NOW - timedelta(hours=1, seconds=1)
    db_session.commit()
    assert scan_and_enqueue(db_session, NOW) == 1


# ---- evaluate now ----------------------------------------------------------------


def test_evaluate_now_queues_one_job_and_reports_the_rest_waiting(db_session, world):
    workspace, _, _ = world
    ask(db_session, workspace, "a", minutes_ago=0.1)  # not idle: Evaluate now does not care
    ask(db_session, workspace, "b", minutes_ago=0.2)
    ask(db_session, workspace, "c", minutes_ago=0.3)

    assert evaluate_now(db_session, workspace.id, NOW) == {"queued": 1, "waiting": 2}
    assert judge_jobs(db_session)[0].payload == {"session_id": "c", "system": True}
    assert evaluate_now(db_session, workspace.id, NOW) == {"queued": 0, "waiting": 2}  # one at a time


def test_evaluate_now_with_nothing_to_judge(db_session, world):
    workspace, _, _ = world
    assert evaluate_now(db_session, workspace.id, NOW) == {"queued": 0, "waiting": 0}


def test_evaluate_now_endpoint_ignores_idle_but_obeys_cap(client, db_session, world):
    workspace, _, headers = world
    for session in ("a", "b", "c"):
        ask(db_session, workspace, session, minutes_ago=0.5, now=utcnow())

    response = client.post(EVALUATE, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"queued": 1, "waiting": 2}
    assert client.post(EVALUATE, headers=headers).json() == {"queued": 0, "waiting": 2}

    (job,) = judge_jobs(db_session)
    job.status = "done"
    mark_judged(db_session, workspace, job.payload["session_id"])
    for index in range(JUDGE_MAX_PER_HOUR - 1):
        extra = enqueue(db_session, workspace.id, JOB_TYPE, {"session_id": f"old-{index}", "system": True})
        extra.status = "done"
    db_session.commit()

    assert client.post(EVALUATE, headers=headers).json() == {"queued": 0, "waiting": 2}  # cap reached


def test_evaluate_now_endpoint_requires_workspace_token_and_assist_agent(client, db_session, world):
    assert client.post(EVALUATE).status_code == 401
    assert client.post(EVALUATE, headers={"X-Workspace-Token": "nope"}).status_code == 401

    _, other_token = create_workspace(db_session)
    response = client.post(EVALUATE, headers={"X-Workspace-Token": other_token})
    assert response.status_code == 404
    assert response.json()["detail"] == "No assistant agent in this workspace."


def test_evaluate_now_endpoint_is_mounted_once(client):
    routes = [r for r in client.app.routes if getattr(r, "path", None) == EVALUATE]
    assert len(routes) == 1


# ---- queue stats -----------------------------------------------------------------


def test_judge_queue_stats(db_session, world):
    workspace, _, _ = world
    empty = judge_queue_stats(db_session, workspace.id, NOW)
    assert empty == {
        "waiting_sessions": 0, "unreadable_sessions": 0, "queued_jobs": 0, "oldest_waiting_minutes": None,
        "judged_last_hour": 0, "hourly_cap": JUDGE_MAX_PER_HOUR,
    }

    ask(db_session, workspace, "a", minutes_ago=30)
    ask(db_session, workspace, "b", minutes_ago=10)
    ask(db_session, workspace, "c", minutes_ago=50)
    mark_judged(db_session, workspace, "c")
    evaluate_now(db_session, workspace.id, NOW)  # queues "a"

    stats = judge_queue_stats(db_session, workspace.id, NOW)
    assert stats["waiting_sessions"] == 1 and stats["queued_jobs"] == 1
    assert stats["oldest_waiting_minutes"] == 10
    assert stats["judged_last_hour"] == 1  # "c" (stamped with the real clock, which is after NOW)
    assert judge_queue_stats(db_session, workspace.id, utcnow() + timedelta(hours=2))["judged_last_hour"] == 0


# ---- worker idle pass ------------------------------------------------------------


def test_worker_idle_pass_scans_and_never_raises(db_session, monkeypatch, caplog):
    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=db_session.get_bind()))
    scan = MagicMock(side_effect=RuntimeError("scan blew up"))
    monkeypatch.setattr(worker, "scan_and_enqueue", scan)

    assert worker._run_pass() is False

    scan.assert_called_once()
    assert "Idle judge scan failed" in caplog.text


def test_worker_scans_only_when_idle(db_session, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=db_session.get_bind()))
    scan = MagicMock(return_value=0)
    monkeypatch.setattr(worker, "scan_and_enqueue", scan)
    workspace, _ = create_workspace(db_session)
    monkeypatch.setitem(HANDLERS, "fake", MagicMock(return_value={}))
    enqueue(db_session, workspace.id, "fake", {})

    assert worker._run_pass() is True
    scan.assert_not_called()  # a job ran: not idle

    assert worker._run_pass() is False
    scan.assert_called_once()


def test_idle_scan_is_throttled(db_session, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=db_session.get_bind()))
    scan = MagicMock(return_value=0)
    monkeypatch.setattr(worker, "scan_and_enqueue", scan)
    clock = [1000.0]
    monkeypatch.setattr(worker, "_clock", lambda: clock[0])

    assert worker._run_pass() is False
    clock[0] += worker.SCAN_INTERVAL_SECONDS - 1
    assert worker._run_pass() is False
    assert scan.call_count == 1  # two idle passes inside the interval: one scan

    clock[0] += 1
    assert worker._run_pass() is False
    assert scan.call_count == 2  # the interval has passed


def test_a_raising_scan_is_throttled_too_and_never_breaks_the_pass(db_session, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=db_session.get_bind()))
    scan = MagicMock(side_effect=RuntimeError("scan blew up"))
    monkeypatch.setattr(worker, "scan_and_enqueue", scan)
    clock = [1000.0]
    monkeypatch.setattr(worker, "_clock", lambda: clock[0])

    assert worker._run_pass() is False
    assert worker._run_pass() is False
    assert scan.call_count == 1  # a broken scan is not retried every second
    clock[0] += worker.SCAN_INTERVAL_SECONDS
    assert worker._run_pass() is False
    assert scan.call_count == 2


def test_throttle_constant():
    assert worker.SCAN_INTERVAL_SECONDS == 10
