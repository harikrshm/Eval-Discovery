"""POST /assist/page-views, /assist/ratings and /assist/reasks: auth, validation, merge rules and flagged runs."""

import pytest
from sqlalchemy import func, select

from backend.api import assist as assist_api
from backend.api import limits
from backend.api.limits import MAX_ASSISTANT_MESSAGE_CHARS, MAX_REQUEST_CHARS
from backend.db.agents import create_agent, current_prompt_version
from backend.db import assist as assist_db
from backend.db.assist import SessionConflict, add_question, save_flagged_run, upsert_page_view
from backend.db.models import AssistPageView, AssistQuestion, Run
from backend.db.workspaces import create_workspace
from scripts import setup_assist

KEY = "test-assist-key-0123456789"
SESSION = "session-abcdef123"
STORED_SESSION = "stored-session-0001"
NUMBER = "US10000000B2"
HEADERS = {"X-Assist-Key": KEY}


@pytest.fixture
def assist_agent(db_session, monkeypatch):
    workspace, _ = create_workspace(db_session)
    agent = create_agent(
        db_session, workspace, setup_assist.ASSIST_CONFIG, setup_assist.ASSIST_ANALYSIS, kind="assist"
    )
    monkeypatch.setenv("ASSIST_AGENT_ID", agent.id)
    return agent


@pytest.fixture(autouse=True)
def _assist_key(monkeypatch):
    monkeypatch.setenv("ASSIST_KEY", KEY)


def make_brief(number=NUMBER):
    return {
        "patent_number": number,
        "title": "Widget holder",
        "abstract": "A holder for widgets.",
        "claims": [
            {"number": 1, "text": "A widget holder comprising a base.", "depends_on": [], "independent": True},
        ],
        "completeness": {"claims": True, "description": True},
    }


CONVERSATION = [
    {"role": "user", "content": "What is claim 1?"},
    {"role": "assistant", "content": "Claim 1 describes a widget holder."},
]


def view_body(**over):
    payload = {
        "session_id": SESSION,
        "patent_number": NUMBER,
        "dwell_ms": 5000,
        "sections": {"claims": 2000},
        "bounce": False,
    }
    payload.update(over)
    return payload


def make_question(db_session, agent, session_id=STORED_SESSION, **kw):
    return add_question(
        db_session, agent.workspace_id, session_id, NUMBER, "What is claim 1?", "claim_explain",
        "claims", "It is a holder.", True, "live", 1, **kw,
    )


def signal(question_id, **over):
    payload = {"question_id": question_id, "conversation": CONVERSATION, "brief": make_brief()}
    payload.update(over)
    return payload


def rating(question_id, value="down", **over):
    return signal(question_id, rating=value, **over)


def post_view(client, payload=None, headers=HEADERS):
    return client.post("/assist/page-views", json=payload if payload is not None else view_body(), headers=headers)


def post_rating(client, payload, headers=HEADERS):
    return client.post("/assist/ratings", json=payload, headers=headers)


def post_reask(client, payload, headers=HEADERS):
    return client.post("/assist/reasks", json=payload, headers=headers)


def runs(db_session):
    return list(db_session.scalars(select(Run)))


def view_row(db_session, session_id=SESSION):
    db_session.expire_all()
    return db_session.get(AssistPageView, session_id)


def question_row(db_session, question_id):
    db_session.expire_all()
    return db_session.get(AssistQuestion, question_id)


# ---- auth -------------------------------------------------------------------


ENDPOINTS = [
    ("/assist/page-views", lambda: view_body()),
    ("/assist/ratings", lambda: rating("q-1")),
    ("/assist/reasks", lambda: signal("q-1")),
]


@pytest.mark.parametrize("path,make", ENDPOINTS)
def test_missing_or_wrong_key_is_401_and_stores_nothing(client, db_session, assist_agent, path, make):
    assert client.post(path, json=make()).status_code == 401
    assert client.post(path, json=make(), headers={"X-Assist-Key": "nope"}).status_code == 401
    assert client.post(path, json={"nonsense": 1}).status_code == 401  # key is checked before the body
    assert db_session.scalar(select(func.count()).select_from(AssistPageView)) == 0
    assert runs(db_session) == []


@pytest.mark.parametrize("path,make", ENDPOINTS)
def test_unset_assist_key_is_503(client, assist_agent, monkeypatch, path, make):
    monkeypatch.delenv("ASSIST_KEY", raising=False)
    assert client.post(path, json=make(), headers=HEADERS).status_code == 503


@pytest.mark.parametrize("path,make", ENDPOINTS)
def test_missing_assist_agent_is_503(client, monkeypatch, path, make):
    monkeypatch.delenv("ASSIST_AGENT_ID", raising=False)
    r = client.post(path, json=make(), headers=HEADERS)
    assert r.status_code == 503
    assert KEY not in r.text


# ---- page views -------------------------------------------------------------


def test_heartbeat_creates_then_updates_one_row(client, db_session, assist_agent):
    r = post_view(client)
    assert r.status_code == 204 and r.content == b""
    row = view_row(db_session)
    assert (row.dwell_ms, row.sections, row.source) == (5000, {"claims": 2000}, "live")
    assert row.patent_number == NUMBER
    assert row.workspace_id == assist_agent.workspace_id
    assert row.assist_version == current_prompt_version(assist_agent).number
    started = row.started_at

    assert post_view(client, view_body(dwell_ms=9000, sections={"claims": 4000, "family": 1500})).status_code == 204
    assert db_session.scalar(select(func.count()).select_from(AssistPageView)) == 1
    row = view_row(db_session)
    assert (row.dwell_ms, row.sections) == (9000, {"claims": 4000, "family": 1500})
    assert row.started_at == started
    assert row.updated_at >= started


def test_lower_values_never_lower_stored_ones(client, db_session, assist_agent):
    post_view(client, view_body(dwell_ms=9000, sections={"claims": 4000, "family": 1500}))
    r = post_view(client, view_body(dwell_ms=100, sections={"claims": 10, "family": 0}))
    assert r.status_code == 204
    row = view_row(db_session)
    assert row.dwell_ms == 9000
    assert row.sections == {"claims": 4000, "family": 1500}


def test_sections_merge_per_name_and_missing_sections_are_kept(client, db_session, assist_agent):
    post_view(client, view_body(sections={"claims": 4000, "family": 1500}))
    post_view(client, view_body(sections={"description": 700, "claims": 100}))
    assert view_row(db_session).sections == {"claims": 4000, "family": 1500, "description": 700}
    post_view(client, view_body(sections={}))
    assert view_row(db_session).sections == {"claims": 4000, "family": 1500, "description": 700}


@pytest.mark.parametrize(
    "dwell,sections,client_bounce,expected",
    [
        (2000, {}, False, True),  # the client says "no bounce"; the server disagrees
        (2000, {"claims": 2999}, False, True),
        (2000, {"claims": 3000}, True, False),  # a section seen for 3s is not a bounce
        (9999, {"claims": 100}, False, True),
        (10000, {}, True, False),  # the client says "bounce"; the server disagrees
        (60000, {}, True, False),
    ],
)
def test_bounce_is_computed_by_the_server(client, db_session, assist_agent, dwell, sections, client_bounce, expected):
    assert post_view(client, view_body(dwell_ms=dwell, sections=sections, bounce=client_bounce)).status_code == 204
    assert view_row(db_session).bounce is expected


def test_bounce_follows_the_merged_values(client, db_session, assist_agent):
    post_view(client, view_body(dwell_ms=2000, sections={}))
    assert view_row(db_session).bounce is True
    post_view(client, view_body(dwell_ms=2500, sections={"claims": 3500}, bounce=True))
    assert view_row(db_session).bounce is False
    # A later, lower heartbeat cannot turn it back into a bounce.
    post_view(client, view_body(dwell_ms=100, sections={}, bounce=True))
    assert view_row(db_session).bounce is False


def test_bounce_field_is_optional(client, db_session, assist_agent):
    payload = view_body()
    del payload["bounce"]
    assert post_view(client, payload).status_code == 204


@pytest.mark.parametrize("name", ["abstract", "Claims", "", "meta", "claims ", "__proto__"])
def test_unknown_section_name_is_422(client, db_session, assist_agent, name):
    r = post_view(client, view_body(sections={name: 100}))
    assert r.status_code == 422
    assert db_session.scalar(select(func.count()).select_from(AssistPageView)) == 0


def test_all_seven_section_names_are_accepted(client, db_session, assist_agent):
    names = ["claims", "description", "classifications", "citations", "family", "legal_events", "landscapes"]
    assert post_view(client, view_body(sections={n: 10 for n in names})).status_code == 204
    assert set(view_row(db_session).sections) == set(names)


@pytest.mark.parametrize("number", ["EP1234567B1", "WO2020123456A1", "", "US", "hello", "US10000000B2; DROP"])
def test_non_us_patent_number_is_422(client, db_session, assist_agent, number):
    assert post_view(client, view_body(patent_number=number)).status_code == 422
    assert db_session.scalar(select(func.count()).select_from(AssistPageView)) == 0


@pytest.mark.parametrize("session_id", ["short", "x" * 65, "has space 1234", "semi;colon-1234", "ünicode-12345", ""])
def test_odd_session_ids_are_422(client, db_session, assist_agent, session_id):
    assert post_view(client, view_body(session_id=session_id)).status_code == 422


@pytest.mark.parametrize(
    "field,value",
    [
        ("dwell_ms", -1),
        ("dwell_ms", 86_400_001),
        ("dwell_ms", 1.5),
        ("dwell_ms", "100"),
        ("dwell_ms", True),
        ("dwell_ms", None),
        ("sections", {"claims": -1}),
        ("sections", {"claims": 86_400_001}),
        ("sections", {"claims": 2.5}),
        ("sections", {"claims": "5"}),
        ("sections", {"claims": True}),
        ("sections", ["claims"]),
    ],
)
def test_bad_durations_are_422(client, db_session, assist_agent, field, value):
    assert post_view(client, view_body(**{field: value})).status_code == 422
    assert db_session.scalar(select(func.count()).select_from(AssistPageView)) == 0


def test_duration_limits_are_inclusive(client, db_session, assist_agent):
    assert post_view(client, view_body(dwell_ms=0, sections={"claims": 0})).status_code == 204
    assert post_view(client, view_body(dwell_ms=86_400_000, sections={"claims": 86_400_000})).status_code == 204


def test_extra_fields_are_422(client, assist_agent):
    assert post_view(client, view_body(extra="x")).status_code == 422


def test_validation_error_never_echoes_the_key_or_the_input(client, assist_agent):
    r = post_view(client, view_body(patent_number="EP-secret-marker-9"))
    assert r.status_code == 422
    assert KEY not in r.text


def test_session_belonging_to_another_workspace_is_never_taken_over(client, db_session, assist_agent):
    other, _ = create_workspace(db_session)
    upsert_page_view(db_session, other.id, SESSION, NUMBER, 100, {}, False, 1, "live")
    r = post_view(client)
    assert r.status_code >= 400 and KEY not in r.text
    assert view_row(db_session).workspace_id == other.id
    assert view_row(db_session).dwell_ms == 100


# ---- limiters ---------------------------------------------------------------


def test_per_ip_heartbeat_limit_is_429_and_stores_nothing(client, db_session, assist_agent):
    for _ in range(limits.assist_heartbeat_ip_limiter.max_calls):
        assert post_view(client).status_code == 204
    r = post_view(client, view_body(session_id="another-session-1"))
    assert r.status_code == 429
    assert KEY not in r.text
    assert view_row(db_session, "another-session-1") is None


def test_per_session_heartbeat_limit_is_429_and_stores_nothing(client, db_session, assist_agent, monkeypatch):
    monkeypatch.setattr(limits.assist_heartbeat_ip_limiter, "max_calls", 10_000)
    for i in range(limits.ASSIST_HEARTBEATS_PER_SESSION_PER_HOUR):
        assert post_view(client, view_body(dwell_ms=i)).status_code == 204
    r = post_view(client, view_body(dwell_ms=86_000_000))
    assert r.status_code == 429
    assert view_row(db_session).dwell_ms == limits.ASSIST_HEARTBEATS_PER_SESSION_PER_HOUR - 1
    # Another session is unaffected.
    assert post_view(client, view_body(session_id="a-fresh-session-1")).status_code == 204


def test_limiter_budgets():
    assert limits.assist_heartbeat_ip_limiter.max_calls == 300
    assert limits.assist_heartbeat_ip_limiter.window_seconds == 60
    assert limits.assist_heartbeat_limiter.max_calls == 600
    assert limits.assist_heartbeat_limiter.window_seconds == 3600
    assert limits.assist_feedback_limiter.max_calls == 30
    assert limits.assist_feedback_limiter.window_seconds == 60


@pytest.mark.parametrize(
    "name", ["assist_heartbeat_ip_limiter", "assist_heartbeat_limiter", "assist_feedback_limiter"]
)
def test_each_signal_limiter_is_reset_by_reset_limiters(name):
    limiter = getattr(limits, name)
    assert limiter.check("some-key")
    assert limiter._calls
    limits.reset_limiters()
    assert limiter._calls == {}


def test_heartbeat_flood_never_blocks_a_rating(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent)
    for _ in range(limits.assist_heartbeat_ip_limiter.max_calls):
        assert post_view(client).status_code == 204
    assert post_view(client).status_code == 429
    assert post_rating(client, rating(q.id, "down")).status_code == 204
    assert post_reask(client, signal(q.id)).status_code == 204


def test_feedback_limiter_is_429_after_30_and_does_not_block_heartbeats(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent)
    for _ in range(30):
        assert post_rating(client, rating(q.id, "up")).status_code == 204
    assert post_rating(client, rating(q.id, "down")).status_code == 429
    assert post_reask(client, signal(q.id)).status_code == 429
    assert post_view(client).status_code == 204
    assert question_row(db_session, q.id).rating == "up"
    assert runs(db_session) == []


def test_llm_limiter_is_not_touched_by_signals(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent)
    post_view(client)
    post_rating(client, rating(q.id, "up"))
    assert limits.llm_limiter._calls == {}
    assert limits.global_llm_limiter._calls == {}


def test_limited_ip_creates_no_session_keys(client, assist_agent, monkeypatch):
    monkeypatch.setattr(limits.assist_heartbeat_ip_limiter, "max_calls", 1)
    assert post_view(client).status_code == 204
    assert post_view(client, view_body(session_id="denied-session-1")).status_code == 429
    assert "denied-session-1" not in limits.assist_heartbeat_limiter._calls


# ---- db helper merge semantics ----------------------------------------------


def test_upsert_page_view_merges_with_max_and_computes_bounce(db_session, assist_agent):
    ws = assist_agent.workspace_id
    first = upsert_page_view(db_session, ws, "s-merge-1", "US1", 5000, {"claims": 2000}, None, 1, "live")
    assert first.bounce is True
    started = first.started_at
    row = upsert_page_view(db_session, ws, "s-merge-1", "US1", 1000, {"claims": 500, "family": 3000}, None, 2, "live")
    assert row.dwell_ms == 5000
    assert row.sections == {"claims": 2000, "family": 3000}
    assert row.bounce is False  # family reached 3000
    assert row.started_at == started
    assert row.assist_version == 1  # the first version seen stays


def test_upsert_page_view_update_path_never_lowers_a_value(db_session, assist_agent):
    ws = assist_agent.workspace_id
    upsert_page_view(db_session, ws, "s-seq-0001", "US1000000", 9000, {"claims": 4000}, None, 1, "live")
    upsert_page_view(db_session, ws, "s-seq-0001", "US1000000", 100, {"claims": 5}, None, 1, "live")
    row = upsert_page_view(db_session, ws, "s-seq-0001", "US1000000", 9500, {"claims": 3}, None, 1, "live")
    assert (row.dwell_ms, row.sections) == (9500, {"claims": 4000})


def test_upsert_first_insert_race_merges_instead_of_failing(db_session, assist_agent, monkeypatch):
    """Both writers saw no row; the loser hits the primary key, retries and merges (never lowers, no 500)."""
    ws = assist_agent.workspace_id
    upsert_page_view(db_session, ws, "s-race-001", "US1000000", 9000, {"claims": 4000}, None, 1, "live")
    db_session.expunge_all()  # this writer's session has not seen the row the other writer just committed
    real = assist_db._load_view
    calls = []

    def first_read_misses(db, session_id):
        calls.append(session_id)
        return None if len(calls) == 1 else real(db, session_id)

    monkeypatch.setattr(assist_db, "_load_view", first_read_misses)
    row = upsert_page_view(db_session, ws, "s-race-001", "US1000000", 100, {"claims": 10, "family": 50}, None, 2, "live")
    assert len(calls) == 2
    assert (row.dwell_ms, row.sections) == (9000, {"claims": 4000, "family": 50})
    assert row.assist_version == 1
    assert db_session.query(AssistPageView).count() == 1


def test_upsert_reads_the_row_under_a_lock(db_session, assist_agent, monkeypatch):
    from sqlalchemy.dialects import postgresql

    captured = []
    real_scalar = db_session.scalar

    def spy(stmt, *a, **kw):
        captured.append(stmt)
        return real_scalar(stmt, *a, **kw)

    monkeypatch.setattr(db_session, "scalar", spy)
    upsert_page_view(db_session, assist_agent.workspace_id, "s-lock-001", "US1000000", 1, {}, None, 1, "live")
    assert "FOR UPDATE" in str(captured[0].compile(dialect=postgresql.dialect()))


def test_upsert_pins_the_first_patent_number(db_session, assist_agent):
    ws = assist_agent.workspace_id
    upsert_page_view(db_session, ws, "s-pin-0001", "US1000000B2", 100, {}, None, 1, "live")
    with pytest.raises(SessionConflict):
        upsert_page_view(db_session, ws, "s-pin-0001", "US2000000B2", 999, {"claims": 999}, None, 1, "live")
    row = upsert_page_view(db_session, ws, "s-pin-0001", " us1000000b2 ", 200, {}, None, 1, "live")
    assert row.dwell_ms == 200 and row.patent_number == "US1000000B2"


# ---- ratings ----------------------------------------------------------------


def test_down_rating_stores_rating_and_one_flagged_run(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent)
    r = post_rating(client, rating(q.id, "down", reason="wrong claim"))
    assert r.status_code == 204 and r.content == b""
    row = question_row(db_session, q.id)
    assert (row.rating, row.rating_reason) == ("down", "wrong claim")
    [run] = runs(db_session)
    assert run.source == "assist"
    assert run.session_id == STORED_SESSION
    assert run.agent_id == assist_agent.id
    assert run.messages == CONVERSATION
    assert run.brief_snapshot["patent_number"] == NUMBER
    assert run.brief_snapshot["title"] == "Widget holder"


def test_second_down_for_the_same_session_updates_the_same_run(client, db_session, assist_agent):
    q1 = make_question(db_session, assist_agent)
    q2 = make_question(db_session, assist_agent)
    post_rating(client, rating(q1.id, "down"))
    longer = [*CONVERSATION, {"role": "user", "content": "And claim 2?"}]
    assert post_rating(client, rating(q2.id, "down", conversation=longer)).status_code == 204
    db_session.expire_all()
    [run] = runs(db_session)
    assert len(run.messages) == 3


def test_no_answer_rating_flags_the_conversation(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent)
    assert post_rating(client, rating(q.id, "no_answer")).status_code == 204
    assert question_row(db_session, q.id).rating == "no_answer"
    [run] = runs(db_session)
    assert run.source == "assist" and run.session_id == STORED_SESSION


def test_up_rating_creates_no_run(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent)
    assert post_rating(client, rating(q.id, "up")).status_code == 204
    assert question_row(db_session, q.id).rating == "up"
    assert runs(db_session) == []


def test_up_rating_may_omit_the_conversation(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent)
    assert post_rating(client, rating(q.id, "up", conversation=[])).status_code == 204
    assert post_rating(client, rating(q.id, "down", conversation=[])).status_code == 422


def test_a_rating_can_be_changed_and_a_flagged_run_survives_a_later_up(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent)
    post_rating(client, rating(q.id, "down", reason="bad"))
    assert post_rating(client, rating(q.id, "up")).status_code == 204
    row = question_row(db_session, q.id)
    assert row.rating == "up"
    assert len(runs(db_session)) == 1
    post_rating(client, rating(q.id, "no_answer"))
    assert question_row(db_session, q.id).rating == "no_answer"
    assert len(runs(db_session)) == 1


def test_rating_uses_the_stored_session_not_anything_the_client_sends(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent, session_id=STORED_SESSION)
    payload = rating(q.id, "down")
    payload["brief"]["title"] = "Different title"
    # session_id is not an accepted field; the client cannot name the run's session.
    assert post_rating(client, {**payload, "session_id": "forged-session-9999"}).status_code == 422
    assert runs(db_session) == []
    assert post_rating(client, payload).status_code == 204
    [run] = runs(db_session)
    assert run.session_id == STORED_SESSION
    assert run.brief_snapshot["title"] == "Different title"


def test_rating_an_unknown_question_is_404_and_writes_nothing(client, db_session, assist_agent):
    r = post_rating(client, rating("00000000-0000-4000-8000-000000000000", "down", reason="x"))
    assert r.status_code == 404
    assert "00000000" not in r.text and KEY not in r.text
    assert runs(db_session) == []


def test_rating_a_question_of_another_workspace_is_404(client, db_session, assist_agent):
    other, _ = create_workspace(db_session)
    q = add_question(db_session, other.id, STORED_SESSION, NUMBER, "t", "other", None, "a", True, "live", 1)
    assert post_rating(client, rating(q.id, "down")).status_code == 404
    assert question_row(db_session, q.id).rating is None
    assert runs(db_session) == []


def test_bad_question_ids_are_422(client, db_session, assist_agent):
    for bad in ["x" * 37, "", "has space", "semi;colon", "ünï"]:
        assert post_rating(client, rating(bad, "down")).status_code == 422
        assert post_reask(client, signal(bad)).status_code == 422
    assert runs(db_session) == []


def test_bad_rating_value_and_oversized_reason_are_422(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent)
    assert post_rating(client, rating(q.id, "sideways")).status_code == 422
    assert post_rating(client, rating(q.id, "down", reason="x" * 501)).status_code == 422
    assert post_rating(client, rating(q.id, "down", reason="x" * 500)).status_code == 204
    assert question_row(db_session, q.id).rating == "down"


def test_oversized_conversations_are_422_before_any_write(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent)
    too_many = [{"role": "user", "content": "q"}] * 43
    one_user_long = [{"role": "user", "content": "x" * 2001}]
    one_assistant_long = [{"role": "assistant", "content": "x" * (MAX_ASSISTANT_MESSAGE_CHARS + 1)}]
    total = [{"role": "assistant", "content": "x" * MAX_ASSISTANT_MESSAGE_CHARS}] * (MAX_REQUEST_CHARS // MAX_ASSISTANT_MESSAGE_CHARS + 1)
    for conversation in (too_many, one_user_long, one_assistant_long, total):
        assert post_rating(client, rating(q.id, "down", conversation=conversation)).status_code == 422
        assert post_reask(client, signal(q.id, conversation=conversation)).status_code == 422
    assert runs(db_session) == []
    row = question_row(db_session, q.id)
    assert row.rating is None and row.reasked is False


def test_conversation_caps_accept_the_limits(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent)
    at_cap = [{"role": "user" if i % 2 == 0 else "assistant", "content": "x"} for i in range(42)]
    assert post_rating(client, rating(q.id, "down", conversation=at_cap)).status_code == 204
    long_turns = [
        {"role": "user", "content": "x" * 2000},
        {"role": "assistant", "content": "y" * MAX_ASSISTANT_MESSAGE_CHARS},
    ]
    assert post_rating(client, rating(q.id, "down", conversation=long_turns)).status_code == 204


def test_bad_conversation_shape_is_422(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent)
    for conversation in ([{"role": "system", "content": "x"}], [{"role": "user"}], "text", [{"role": "user", "content": "x", "extra": 1}]):
        assert post_rating(client, rating(q.id, "down", conversation=conversation)).status_code == 422
    assert post_rating(client, {k: v for k, v in rating(q.id, "down").items() if k != "brief"}).status_code == 422
    assert runs(db_session) == []


def test_flagged_run_cap_skips_the_run_but_keeps_the_rating(client, db_session, assist_agent, monkeypatch, caplog):
    monkeypatch.setattr(assist_api, "MAX_ASSIST_RUNS", 2)
    for i in range(2):
        save_flagged_run(db_session, assist_agent, f"filler-session-{i}", CONVERSATION, make_brief(), "assist")
    q = make_question(db_session, assist_agent)
    with caplog.at_level("WARNING"):
        r = post_rating(client, rating(q.id, "down", reason="private words"))
    assert r.status_code == 204
    assert question_row(db_session, q.id).rating == "down"
    assert len(runs(db_session)) == 2
    assert all(run.session_id != STORED_SESSION for run in runs(db_session))
    assert "private words" not in caplog.text and "What is claim 1" not in caplog.text


def test_flagged_run_cap_still_updates_an_existing_session_run(client, db_session, assist_agent, monkeypatch):
    monkeypatch.setattr(assist_api, "MAX_ASSIST_RUNS", 2)
    save_flagged_run(db_session, assist_agent, "filler-session-0", CONVERSATION, make_brief(), "assist")
    save_flagged_run(db_session, assist_agent, STORED_SESSION, [{"role": "user", "content": "old"}], make_brief(), "assist")
    q = make_question(db_session, assist_agent)
    assert post_rating(client, rating(q.id, "down")).status_code == 204
    db_session.expire_all()
    assert len(runs(db_session)) == 2
    [mine] = [run for run in runs(db_session) if run.session_id == STORED_SESSION]
    assert mine.messages == CONVERSATION


def test_rating_responses_never_contain_the_key_or_data(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent)
    for r in (
        post_rating(client, rating(q.id, "down")),
        post_rating(client, rating("nope-nope", "down")),
        post_rating(client, rating(q.id, "bogus")),
        post_rating(client, rating(q.id, "down"), headers={"X-Assist-Key": "wrong"}),
    ):
        assert KEY not in r.text
    assert post_rating(client, rating(q.id, "down")).content == b""


def test_ratings_are_per_ip_limited_and_a_429_writes_nothing(client, db_session, assist_agent, monkeypatch):
    q = make_question(db_session, assist_agent)
    monkeypatch.setattr(limits.assist_feedback_limiter, "max_calls", 1)
    assert post_rating(client, rating(q.id, "up")).status_code == 204
    assert post_rating(client, rating(q.id, "down")).status_code == 429
    assert question_row(db_session, q.id).rating == "up"
    assert runs(db_session) == []


# ---- re-asks ----------------------------------------------------------------


def test_reask_marks_the_question_and_flags_the_conversation(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent)
    r = post_reask(client, signal(q.id))
    assert r.status_code == 204 and r.content == b""
    assert question_row(db_session, q.id).reasked is True
    [run] = runs(db_session)
    assert run.source == "assist" and run.session_id == STORED_SESSION
    assert run.messages == CONVERSATION
    assert run.brief_snapshot["patent_number"] == NUMBER


def test_reask_is_idempotent(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent)
    assert post_reask(client, signal(q.id)).status_code == 204
    assert post_reask(client, signal(q.id)).status_code == 204
    assert question_row(db_session, q.id).reasked is True
    assert len(runs(db_session)) == 1


def test_reask_of_an_unknown_question_is_404_and_writes_nothing(client, db_session, assist_agent):
    r = post_reask(client, signal("11111111-1111-4111-8111-111111111111"))
    assert r.status_code == 404
    assert "1111" not in r.text and KEY not in r.text
    assert runs(db_session) == []


def test_reask_shares_a_run_with_a_rating_of_the_same_session(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent)
    post_rating(client, rating(q.id, "down"))
    post_reask(client, signal(q.id))
    assert len(runs(db_session)) == 1


def test_reask_respects_the_flagged_run_cap(client, db_session, assist_agent, monkeypatch):
    monkeypatch.setattr(assist_api, "MAX_ASSIST_RUNS", 1)
    save_flagged_run(db_session, assist_agent, "filler-session-0", CONVERSATION, make_brief(), "assist")
    q = make_question(db_session, assist_agent)
    assert post_reask(client, signal(q.id)).status_code == 204
    assert question_row(db_session, q.id).reasked is True
    assert len(runs(db_session)) == 1


def test_reask_conversation_must_not_be_empty(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent)
    assert post_reask(client, signal(q.id, conversation=[])).status_code == 422
    assert question_row(db_session, q.id).reasked is False


def test_default_run_cap_is_1000():
    assert assist_api.MAX_ASSIST_RUNS == 1000


# ---- session pinning and version -------------------------------------------


def test_heartbeat_for_a_different_patent_is_409_and_changes_nothing(client, db_session, assist_agent):
    post_view(client, view_body(dwell_ms=5000, sections={"claims": 2000}))
    before = view_row(db_session)
    snapshot = (before.patent_number, before.dwell_ms, dict(before.sections), before.updated_at, before.bounce)
    r = post_view(client, view_body(patent_number="US20160266243A1", dwell_ms=80_000, sections={"family": 9000}))
    assert r.status_code == 409
    assert KEY not in r.text and "US2016" not in r.text
    after = view_row(db_session)
    assert (after.patent_number, after.dwell_ms, dict(after.sections), after.updated_at, after.bounce) == snapshot


def test_same_patent_in_another_case_is_accepted(client, db_session, assist_agent):
    post_view(client, view_body(patent_number="US10000000B2"))
    r = post_view(client, view_body(patent_number="us10000000b2", dwell_ms=7000))
    assert r.status_code == 204
    row = view_row(db_session)
    assert row.dwell_ms == 7000 and row.patent_number == "US10000000B2"


def test_assist_version_is_the_first_one_seen(client, db_session, assist_agent):
    from backend.db.agents import new_prompt_version

    post_view(client)
    first = view_row(db_session).assist_version
    new_prompt_version(db_session, assist_agent, "A newer prompt")
    db_session.commit()
    db_session.expire_all()
    assert current_prompt_version(assist_agent).number != first
    post_view(client, view_body(dwell_ms=9000))
    row = view_row(db_session)
    assert row.dwell_ms == 9000
    assert row.assist_version == first


def test_an_unexpected_lookup_or_key_error_is_not_turned_into_a_409(assist_agent, db_session, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.db.session import get_db
    from backend.main import app

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        lenient = TestClient(app, raise_server_exceptions=False)
        for error in (KeyError("a bug"), IndexError("a bug"), LookupError("a bug")):
            def boom(*a, _error=error, **kw):
                raise _error

            monkeypatch.setattr(assist_api, "upsert_page_view", boom)
            assert lenient.post("/assist/page-views", json=view_body(), headers=HEADERS).status_code == 500
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---- brief / question match -------------------------------------------------


def test_rating_with_a_brief_for_another_patent_is_422_and_writes_nothing(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent)
    r = post_rating(client, rating(q.id, "down", brief=make_brief("US20160266243A1")))
    assert r.status_code == 422
    assert "US2016" not in r.text and KEY not in r.text
    assert question_row(db_session, q.id).rating is None
    assert runs(db_session) == []


def test_reask_with_a_brief_for_another_patent_is_422_and_writes_nothing(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent)
    r = post_reask(client, signal(q.id, brief=make_brief("US20160266243A1")))
    assert r.status_code == 422
    assert question_row(db_session, q.id).reasked is False
    assert runs(db_session) == []


def test_a_matching_brief_in_another_case_still_works(client, db_session, assist_agent):
    q = make_question(db_session, assist_agent)
    assert post_rating(client, rating(q.id, "down", brief=make_brief("us10000000b2"))).status_code == 204
    assert post_reask(client, signal(q.id, brief=make_brief(" US10000000B2 "))).status_code == 204


def test_non_us_question_rated_with_its_own_brief_works(client, db_session, assist_agent):
    q = add_question(
        db_session, assist_agent.workspace_id, STORED_SESSION, "EP1234567B1", "t", "other", None, "a", True, "live", 1
    )
    assert post_rating(client, rating(q.id, "down", brief=make_brief("EP1234567B1"))).status_code == 204
    assert post_rating(client, rating(q.id, "down", brief=make_brief())).status_code == 422


# ---- non-ASCII keys ----------------------------------------------------------


@pytest.mark.parametrize("path,make", ENDPOINTS)
@pytest.mark.parametrize("key", [b"\xff\xfe\x80", "k\u00e9y".encode("latin-1")])
def test_non_ascii_key_header_is_401_not_a_crash(client, assist_agent, path, make, key):
    assert client.post(path, json=make(), headers={"X-Assist-Key": key}).status_code == 401
