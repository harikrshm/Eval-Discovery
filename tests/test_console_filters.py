"""Console filters: the pure parsing/matching helpers, and /metrics + /flagged with range, source and version."""

from datetime import datetime, timedelta

import pytest

from backend.core.assist import console_filters as cf
from backend.core.assist.adoption import FLAG_REASONS, PV, Q, compute_adoption, compute_version_comparison
from backend.db.models import AssistPageView, utcnow
from tests.test_api_assist_console import add_q, flag, make_workspace

NOW = datetime(2026, 11, 24, 12, 0, 0)
SECRET = ("SECRET-QUESTION", "SECRET-ANSWER", "SECRET-REASON")


# ---- pure helpers ------------------------------------------------------------------


def test_parse_filters_ranges_and_errors():
    assert cf.RANGES["all"] is None
    assert cf.RANGES["24h"] == timedelta(hours=24)
    assert cf.RANGES["7d"] == timedelta(days=7)
    assert cf.RANGES["30d"] == timedelta(days=30)
    assert cf.SOURCES == ("all", "live", "demo")

    day = cf.parse_filters("24h", "live", 3, now=NOW)
    assert day == cf.Filters(since=NOW - timedelta(hours=24), until=NOW, source="live", version=3)
    week = cf.parse_filters("7d", "all", None, now=NOW)
    assert (week.since, week.until, week.version) == (NOW - timedelta(days=7), NOW, None)
    everything = cf.parse_filters("all", "demo", None, now=NOW)
    assert (everything.since, everything.until, everything.source) == (None, None, "demo")
    assert cf.parse_filters("30d", "all", None).until is not None  # default clock is utcnow

    for bad in [("1y", "all", None), ("7d", "bogus", None), ("", "all", None), ("7d", "all", 0), ("7d", "all", -2)]:
        with pytest.raises(ValueError):
            cf.parse_filters(*bad, now=NOW)


def test_previous_period_and_all():
    week = cf.parse_filters("7d", "demo", 2, now=NOW)
    previous = cf.previous_period(week)
    assert previous == cf.Filters(
        since=NOW - timedelta(days=14), until=NOW - timedelta(days=7), source="demo", version=2
    )
    assert cf.previous_period(cf.parse_filters("all", "all", None, now=NOW)) is None


def test_bucket_for():
    assert cf.bucket_for(cf.parse_filters("24h", "all", None, now=NOW)) == "hour"
    for name in ("7d", "30d", "all"):
        assert cf.bucket_for(cf.parse_filters(name, "all", None, now=NOW)) == "day"


def test_source_matching_demo_and_live():
    everything = cf.parse_filters("all", "all", None, now=NOW)
    live = cf.parse_filters("all", "live", None, now=NOW)
    demo = cf.parse_filters("all", "demo", None, now=NOW)
    assert [cf.matches(s, everything) for s in ("assist", "imported")] == [True, True]
    assert [cf.matches(s, live) for s in ("assist", "imported")] == [True, False]
    assert [cf.matches(s, demo) for s in ("assist", "imported")] == [False, True]


def test_in_range_is_half_open():
    week = cf.parse_filters("7d", "all", None, now=NOW)
    assert cf.in_range(week.since, week) is True
    assert cf.in_range(NOW, week) is False  # the end belongs to the next period
    assert cf.in_range(week.since - timedelta(seconds=1), week) is False
    assert cf.in_range(NOW - timedelta(days=400), cf.parse_filters("all", "all", None, now=NOW)) is True


# ---- /metrics -------------------------------------------------------------------------


def add_view_at(db, workspace, session, started_at, source="assist", version=1, bounce=False):
    db.add(
        AssistPageView(
            session_id=session, workspace_id=workspace.id, patent_number="US10000000B2", dwell_ms=20000,
            sections={"claims": 5000}, bounce=bounce, source=source, assist_version=version, started_at=started_at,
        )
    )
    db.commit()


@pytest.fixture
def timed(db_session):
    """Three views and three questions: live+v2 an hour ago, live+v1 3 days ago, demo+v1 20 days ago."""
    workspace, agent, headers = make_workspace(db_session)
    now = utcnow()
    spec = [
        ("recent", now - timedelta(hours=1), "assist", 2, "claim_explain"),
        ("mid", now - timedelta(days=3), "assist", 1, "claim_explain"),
        ("old", now - timedelta(days=20), "imported", 1, "citations_prior_art"),
    ]
    for session, at, source, version, intent in spec:
        add_view_at(db_session, workspace, session, at, source, version)
        add_q(db_session, workspace, session, intent, "claims", source=source, version=version,
              rating="down" if session == "recent" else None, created_at=at)
    return {"workspace": workspace, "agent": agent, "headers": headers, "now": now}


@pytest.fixture
def seeded_console(db_session):
    workspace, agent, headers = make_workspace(db_session)
    now = utcnow()
    for session, source in (("a", "assist"), ("b", "assist"), ("c", "imported")):
        add_view_at(db_session, workspace, session, now - timedelta(days=2), source)
    add_q(db_session, workspace, "a", "claim_explain", "claims", rating="down")
    add_q(db_session, workspace, "a", "claim_explain", "claims", reasked=True)
    add_q(db_session, workspace, "b", "citations_prior_art", "citations", citation_ok=False)
    add_q(db_session, workspace, "c", "other", source="imported")
    return {"workspace": workspace, "agent": agent, "headers": headers}


def totals(client, headers, query=""):
    body = client.get(f"/assist/console/metrics{query}", headers=headers).json()
    return body["adoption"]["totals"]["page_views"], body["adoption"]["totals"]["questions"]


def test_metrics_unfiltered_matches_old_numbers(client, db_session, seeded_console):
    """No query and explicit all/all give exactly what the unfiltered computation over every row gives."""
    from sqlalchemy import select

    from backend.api.assist_console import _load_questions

    workspace, headers = seeded_console["workspace"], seeded_console["headers"]
    views = [
        PV(r.session_id, r.sections or {}, r.dwell_ms or 0, bool(r.bounce), r.source, r.assist_version)
        for r in db_session.scalars(select(AssistPageView).where(AssistPageView.workspace_id == workspace.id))
    ]
    questions = [
        Q(r.session_id, r.intent, r.section_focus, r.rating, bool(r.reasked), bool(r.citation_ok), r.source,
          r.assist_version, bool(r.judge_flagged))
        for r in _load_questions(db_session, workspace.id)
    ]
    expected_adoption = compute_adoption(views, questions)
    expected_comparison = compute_version_comparison(questions)

    plain = client.get("/assist/console/metrics", headers=headers).json()
    explicit = client.get("/assist/console/metrics?range=all&source=all", headers=headers).json()
    assert plain == explicit
    assert plain["adoption"] == expected_adoption
    assert plain["version_comparison"] == expected_comparison
    assert plain["data_source_label"] == "demo dataset"


def test_metrics_range_source_version_filters(client, timed):
    headers = timed["headers"]
    assert totals(client, headers) == (3, 3)
    assert totals(client, headers, "?range=all&source=all") == (3, 3)
    assert totals(client, headers, "?range=24h") == (1, 1)
    assert totals(client, headers, "?range=7d") == (2, 2)
    assert totals(client, headers, "?range=30d") == (3, 3)
    assert totals(client, headers, "?source=demo") == (1, 1)
    assert totals(client, headers, "?source=live") == (2, 2)
    assert totals(client, headers, "?version=2") == (1, 1)
    assert totals(client, headers, "?version=1") == (2, 2)
    assert totals(client, headers, "?version=9") == (0, 0)
    assert totals(client, headers, "?range=7d&source=demo") == (0, 0)
    assert totals(client, headers, "?range=30d&source=demo&version=1") == (1, 1)

    live_only = client.get("/assist/console/metrics?source=live", headers=headers).json()
    assert live_only["data_source_label"] is None
    assert live_only["sources"]["questions"] == {"live": 2, "imported": 0}
    demo_only = client.get("/assist/console/metrics?source=demo", headers=headers).json()
    assert demo_only["data_source_label"] == "demo dataset"
    assert demo_only["adoption"]["task_mix"]["citations_prior_art"]["count"] == 1


def test_metrics_on_an_empty_filter_result_has_no_nan(client, timed):
    response = client.get("/assist/console/metrics?range=24h&source=demo", headers=timed["headers"])
    assert response.status_code == 200
    adoption = response.json()["adoption"]
    assert adoption["bounce_rate"] is None
    assert adoption["struggle"]["unverified_citation_rate"] is None


def test_version_comparison_ignores_demo_and_range(client, db_session):
    workspace, _, headers = make_workspace(db_session)
    now = utcnow()
    for version, days_ago in ((1, 40), (2, 35)):  # live questions all outside the 30d range
        for i in range(10):
            add_q(db_session, workspace, f"v{version}-{i}", "claim_explain", "claims", version=version,
                  reasked=version == 1 and i < 5, created_at=now - timedelta(days=days_ago))
    for i in range(20):  # demo questions, recent: must never feed the comparison
        add_q(db_session, workspace, f"d-{i}", "other", source="imported", version=3, created_at=now)

    unfiltered = client.get("/assist/console/metrics", headers=headers).json()["version_comparison"]
    assert unfiltered["available"] is True
    assert sorted(unfiltered["versions"]) == ["1", "2"]
    for query in ("?range=24h", "?source=demo", "?range=24h&source=demo&version=3", "?source=live&version=2"):
        body = client.get(f"/assist/console/metrics{query}", headers=headers).json()
        assert body["version_comparison"] == unfiltered, query
    filtered = client.get("/assist/console/metrics?range=24h&source=demo", headers=headers).json()
    assert filtered["adoption"]["totals"]["questions"] == 20


# ---- /flagged -------------------------------------------------------------------------


@pytest.fixture
def flagged_data(db_session):
    workspace, agent, headers = make_workspace(db_session)
    add_q(db_session, workspace, "sess-a", "claim_explain", "claims", rating="down")
    add_q(db_session, workspace, "sess-a", "claim_explain", "claims", reasked=True)
    q_b = add_q(db_session, workspace, "sess-b", "citations_prior_art", "citations", citation_ok=False)
    add_q(db_session, workspace, "sess-c", "claim_explain", "claims", source="imported", rating="no_answer")
    q_b.judge_flagged = True
    db_session.commit()
    runs = {
        "a": flag(db_session, agent, "sess-a"),
        "b": flag(db_session, agent, "sess-b"),
        "c": flag(db_session, agent, "sess-c", source="imported"),
    }
    return {"headers": headers, "runs": runs}


def flagged_ids(client, headers, query):
    response = client.get(f"/assist/console/flagged{query}", headers=headers)
    assert response.status_code == 200, response.text
    return {r["run_id"] for r in response.json()}


def test_flagged_filters_reason_and_source(client, flagged_data):
    headers, runs = flagged_data["headers"], flagged_data["runs"]
    everything = {runs["a"].id, runs["b"].id, runs["c"].id}
    assert flagged_ids(client, headers, "") == everything
    assert flagged_ids(client, headers, "?source=all") == everything
    assert flagged_ids(client, headers, "?source=live") == {runs["a"].id, runs["b"].id}
    assert flagged_ids(client, headers, "?source=demo") == {runs["c"].id}
    assert flagged_ids(client, headers, "?reason=rated_down") == {runs["a"].id}
    assert flagged_ids(client, headers, "?reason=reasked") == {runs["a"].id}
    assert flagged_ids(client, headers, "?reason=no_answer") == {runs["c"].id}
    assert flagged_ids(client, headers, "?reason=citation_unverified") == {runs["b"].id}
    assert flagged_ids(client, headers, "?reason=auto_judge") == {runs["b"].id}
    assert flagged_ids(client, headers, "?reason=auto_judge&source=demo") == set()
    assert flagged_ids(client, headers, "?reason=rated_down&intent=claim_explain&section=claims") == {runs["a"].id}
    assert flagged_ids(client, headers, "?reason=rated_down&intent=citations_prior_art") == set()
    for reason in FLAG_REASONS:  # every documented reason is accepted
        assert client.get(f"/assist/console/flagged?reason={reason}", headers=headers).status_code == 200


# ---- validation and text safety -----------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/assist/console/metrics?range=1y",
        "/assist/console/metrics?source=bogus",
        "/assist/console/metrics?version=abc",
        "/assist/console/metrics?version=0",
        "/assist/console/trends?range=1y",
        "/assist/console/trends?source=bogus",
        "/assist/console/trends?version=abc",
        "/assist/console/flagged?reason=bogus",
        "/assist/console/flagged?source=bogus",
    ],
)
def test_invalid_filter_values_422(client, timed, path):
    response = client.get(path, headers=timed["headers"])
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


def test_responses_contain_no_text(client, db_session):
    workspace, agent, headers = make_workspace(db_session)
    now = utcnow()
    add_view_at(db_session, workspace, "s1", now - timedelta(hours=2))
    question = add_q(db_session, workspace, "s1", "claim_explain", "claims", rating="down", created_at=now)
    question.text, question.answer, question.rating_reason = SECRET
    db_session.commit()
    flag(db_session, agent, "s1")
    paths = [
        "/assist/console/metrics", "/assist/console/metrics?range=24h&source=live&version=1",
        "/assist/console/trends", "/assist/console/trends?range=7d&source=demo",
        "/assist/console/flagged", "/assist/console/flagged?reason=rated_down&source=live",
    ]
    for path in paths:
        response = client.get(path, headers=headers)
        assert response.status_code == 200, path
        assert not any(secret in response.text for secret in SECRET), path
