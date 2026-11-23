"""Final fix wave I3 (imported sessions stay imported) and M11 (the citation-failure path honours the run cap)."""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend import groq_client
from backend.db.agents import create_agent
from backend.db.assist import SessionConflict, add_question, save_flagged_run, upsert_page_view
from backend.db.models import AssistPageView, AssistQuestion, Run
from backend.db.workspaces import create_workspace
from scripts import setup_assist

KEY = "test-assist-key-0123456789"
HEADERS = {"X-Assist-Key": KEY}
SESSION = "session-imported-1"
PATENT = "US10000000B2"


class FakeClient:
    def __init__(self, reply="Claim 1 describes a widget."):
        self.reply = reply
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, max_retries):
        return self

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))])


@pytest.fixture
def fake(monkeypatch):
    rec = FakeClient()
    monkeypatch.setattr(groq_client, "client", rec)
    return rec


@pytest.fixture
def assist(db_session, monkeypatch):
    workspace, _ = create_workspace(db_session)
    agent = create_agent(db_session, workspace, setup_assist.ASSIST_CONFIG, setup_assist.ASSIST_ANALYSIS, kind="assist")
    monkeypatch.setenv("ASSIST_AGENT_ID", agent.id)
    monkeypatch.setenv("ASSIST_KEY", KEY)
    return workspace, agent


def make_brief(number=PATENT):
    return {
        "patent_number": number,
        "title": "Widget holder",
        "claims": [{"number": 1, "text": "A widget holder comprising a base.", "depends_on": [], "independent": True}],
        "completeness": {"claims": True},
    }


def chat_body(session_id=SESSION, question="Explain claim 1"):
    return {"session_id": session_id, "brief": make_brief(), "history": [], "question": question}


def heartbeat(session_id=SESSION, dwell_ms=5000):
    return {"session_id": session_id, "patent_number": PATENT, "dwell_ms": dwell_ms, "sections": {"claims": 4000}}


# ---- I3 --------------------------------------------------------------------------------------------


def test_i3_a_live_heartbeat_cannot_flip_an_imported_page_view(db_session, assist):
    workspace, _ = assist
    upsert_page_view(db_session, workspace.id, SESSION, PATENT, 9000, {"claims": 8000}, None, 1, "imported")
    with pytest.raises(SessionConflict):
        upsert_page_view(db_session, workspace.id, SESSION, PATENT, 20000, {"claims": 9000}, None, 1, "live")
    view = db_session.scalar(select(AssistPageView).where(AssistPageView.session_id == SESSION))
    assert view.source == "imported" and view.dwell_ms == 9000 and view.sections == {"claims": 8000}


def test_i3_imported_over_imported_and_live_over_live_are_still_allowed(db_session, assist):
    workspace, _ = assist
    upsert_page_view(db_session, workspace.id, "s-imp", PATENT, 100, {}, None, 1, "imported")
    upsert_page_view(db_session, workspace.id, "s-imp", PATENT, 200, {}, None, 1, "imported")
    upsert_page_view(db_session, workspace.id, "s-live", PATENT, 100, {}, None, 1, "live")
    upsert_page_view(db_session, workspace.id, "s-live", PATENT, 200, {}, None, 1, "live")
    rows = {v.session_id: v for v in db_session.scalars(select(AssistPageView))}
    assert rows["s-imp"].dwell_ms == 200 and rows["s-imp"].source == "imported"
    assert rows["s-live"].dwell_ms == 200 and rows["s-live"].source == "live"


def test_i3_heartbeat_route_returns_409_for_an_imported_session_and_changes_nothing(client, db_session, assist):
    workspace, _ = assist
    upsert_page_view(db_session, workspace.id, SESSION, PATENT, 9000, {"claims": 8000}, None, 1, "imported")
    r = client.post("/assist/page-views", json=heartbeat(), headers=HEADERS)
    assert r.status_code == 409
    view = db_session.scalar(select(AssistPageView).where(AssistPageView.session_id == SESSION))
    db_session.refresh(view)
    assert view.source == "imported" and view.dwell_ms == 9000


def test_i3_add_question_refuses_a_live_question_for_an_imported_session(db_session, assist):
    workspace, _ = assist
    add_question(db_session, workspace.id, SESSION, PATENT, "q", "other", None, "a", True, "imported", 1)
    with pytest.raises(SessionConflict):
        add_question(db_session, workspace.id, SESSION, PATENT, "q2", "other", None, "a", True, "live", 1)
    add_question(db_session, workspace.id, SESSION, PATENT, "q3", "other", None, "a", True, "imported", 1)
    assert len(list(db_session.scalars(select(AssistQuestion)))) == 2


@pytest.mark.parametrize("seed", ["page_view", "question"])
def test_i3_chat_on_an_imported_session_is_409_with_no_model_call_and_nothing_stored(client, db_session, assist, fake, seed):
    workspace, _ = assist
    if seed == "page_view":
        upsert_page_view(db_session, workspace.id, SESSION, PATENT, 9000, {}, None, 1, "imported")
    else:
        add_question(db_session, workspace.id, SESSION, PATENT, "old", "other", None, "a", True, "imported", 1)
    before = len(list(db_session.scalars(select(AssistQuestion))))
    r = client.post("/assist/chat", json=chat_body(), headers=HEADERS)
    assert r.status_code == 409
    assert fake.calls == []
    assert len(list(db_session.scalars(select(AssistQuestion)))) == before


def test_i3_chat_on_a_fresh_session_still_works(client, db_session, assist, fake):
    r = client.post("/assist/chat", json=chat_body("session-fresh-0001"), headers=HEADERS)
    assert r.status_code == 200 and len(fake.calls) == 1


# ---- M11 -------------------------------------------------------------------------------------------

BAD_REPLY = "Claim 99 adds a lid."  # claim 99 is not on the page: the citation check fails


def test_m11_citation_failure_at_the_run_cap_saves_no_new_session_run(client, db_session, assist, fake, monkeypatch):
    _, agent = assist
    fake.reply = BAD_REPLY
    monkeypatch.setattr("backend.api.assist.MAX_ASSIST_RUNS", 1)
    other = [{"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}]
    save_flagged_run(db_session, agent, "session-already-1", other, make_brief(), "assist")
    r = client.post("/assist/chat", json=chat_body("session-new-0000001"), headers=HEADERS)
    assert r.status_code == 200 and r.json()["citation_check"]["ok"] is False
    assert [run.session_id for run in db_session.scalars(select(Run))] == ["session-already-1"]
    assert len(list(db_session.scalars(select(AssistQuestion)))) == 1  # the question itself is still stored


def test_m11_at_the_cap_an_existing_session_run_is_still_updated(client, db_session, assist, fake, monkeypatch):
    _, agent = assist
    fake.reply = BAD_REPLY
    monkeypatch.setattr("backend.api.assist.MAX_ASSIST_RUNS", 1)
    other = [{"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}]
    save_flagged_run(db_session, agent, "session-existing-1", other, make_brief(), "assist")
    r = client.post("/assist/chat", json=chat_body("session-existing-1"), headers=HEADERS)
    assert r.status_code == 200
    run = db_session.scalar(select(Run))
    db_session.refresh(run)
    assert len(list(db_session.scalars(select(Run)))) == 1
    assert run.messages[-1]["content"].startswith(BAD_REPLY)


def test_m11_below_the_cap_a_citation_failure_still_saves_the_run(client, db_session, assist, fake):
    fake.reply = BAD_REPLY
    r = client.post("/assist/chat", json=chat_body("session-below-cap-1"), headers=HEADERS)
    assert r.status_code == 200
    assert [run.session_id for run in db_session.scalars(select(Run))] == ["session-below-cap-1"]
