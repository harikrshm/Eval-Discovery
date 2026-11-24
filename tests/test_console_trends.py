"""GET /assist/console/trends: totals, previous period, zero-filled buckets and version markers."""

from datetime import timedelta

import pytest

from backend.db.models import AssistPageView, PromptVersion, utcnow
from tests.test_api_assist_console import add_q, make_workspace

URL = "/assist/console/trends"
KEYS = {"bucket", "totals", "previous", "points", "version_markers"}
COUNTS = ("page_views", "questions", "reasked", "rated_down")


def add_view_at(db, workspace, session, started_at, source="assist", version=1):
    db.add(
        AssistPageView(
            session_id=session, workspace_id=workspace.id, patent_number="US10000000B2", dwell_ms=1000,
            sections={}, bounce=False, source=source, assist_version=version, started_at=started_at,
        )
    )
    db.commit()


def get(client, headers, query=""):
    response = client.get(f"{URL}{query}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_trends_requires_a_token_and_an_assist_agent(client, db_session):
    assert client.get(URL).status_code == 401
    _, _, headers = make_workspace(db_session, with_agent=False)
    assert client.get(URL, headers=headers).status_code == 404


def test_trends_empty_database_returns_empty_points(client, db_session):
    _, _, headers = make_workspace(db_session)
    body = get(client, headers)
    assert set(body) == KEYS
    assert body["points"] == []
    assert body["previous"] is None
    assert body["totals"] == dict.fromkeys(COUNTS, 0)
    assert body["bucket"] == "day"
    # a bounded range on an empty database is zero-filled, not empty
    week = get(client, headers, "?range=7d")
    assert len(week["points"]) == 7
    assert all(p[k] == 0 for p in week["points"] for k in COUNTS)
    assert week["previous"] == dict.fromkeys(COUNTS, 0)


@pytest.fixture
def workspace_with_rows(db_session):
    workspace, agent, headers = make_workspace(db_session)
    now = utcnow()
    add_view_at(db_session, workspace, "s1", now - timedelta(minutes=30))
    add_view_at(db_session, workspace, "s2", now - timedelta(hours=2, minutes=30))
    add_view_at(db_session, workspace, "s3", now - timedelta(hours=30))  # previous 24h
    add_q(db_session, workspace, "s1", "claim_explain", "claims", rating="down", created_at=now - timedelta(minutes=30))
    add_q(db_session, workspace, "s2", "claim_explain", "claims", reasked=True, created_at=now - timedelta(hours=2, minutes=30))
    add_q(db_session, workspace, "s2", "other", created_at=now - timedelta(hours=2, minutes=20))
    add_q(db_session, workspace, "s3", "other", reasked=True, rating="down", created_at=now - timedelta(hours=30))
    return {"workspace": workspace, "agent": agent, "headers": headers, "now": now}


def test_trends_zero_fills_and_buckets(client, workspace_with_rows):
    headers = workspace_with_rows["headers"]

    day = get(client, headers, "?range=24h")
    assert day["bucket"] == "hour"
    assert len(day["points"]) == 24
    assert day["totals"] == {"page_views": 2, "questions": 3, "reasked": 1, "rated_down": 1}
    assert day["previous"] == {"page_views": 1, "questions": 1, "reasked": 1, "rated_down": 1}
    last, third_last = day["points"][23], day["points"][21]
    assert (last["page_views"], last["questions"], last["rated_down"]) == (1, 1, 1)
    assert (third_last["page_views"], third_last["questions"], third_last["reasked"]) == (1, 2, 1)
    assert sum(p["questions"] for p in day["points"]) == 3
    assert sum(1 for p in day["points"] if p["questions"] == 0) == 22  # zero-filled, never missing

    times = [p["t"] for p in day["points"]]
    assert times == sorted(times)
    assert "T" in times[0] and not times[0].endswith("Z") and "+" not in times[0]  # naive UTC iso

    week = get(client, headers, "?range=7d")
    assert week["bucket"] == "day"
    assert len(week["points"]) == 7
    assert week["totals"]["questions"] == 4  # the 30 h old row is inside 7 days
    assert week["previous"] == dict.fromkeys(COUNTS, 0)
    assert len(get(client, headers, "?range=30d")["points"]) == 30

    everything = get(client, headers)
    assert everything["bucket"] == "day" and everything["previous"] is None
    assert everything["totals"]["questions"] == 4
    assert sum(p["questions"] for p in everything["points"]) == 4
    assert len(everything["points"]) in (2, 3)  # calendar days from the oldest row (30 h ago) to today


def test_trends_all_spans_first_row_to_now(client, db_session):
    workspace, _, headers = make_workspace(db_session)
    now = utcnow()
    add_q(db_session, workspace, "old", "other", created_at=now - timedelta(days=10))
    body = get(client, headers)
    assert len(body["points"]) == 11
    assert sum(p["questions"] for p in body["points"]) == 1
    assert body["points"][0]["questions"] == 1


def test_trends_source_and_version_filters(client, workspace_with_rows, db_session):
    workspace, headers = workspace_with_rows["workspace"], workspace_with_rows["headers"]
    now = workspace_with_rows["now"]
    add_view_at(db_session, workspace, "d1", now - timedelta(hours=1), source="imported", version=2)
    add_q(db_session, workspace, "d1", "other", source="imported", version=2, created_at=now - timedelta(hours=1))
    assert get(client, headers, "?range=24h&source=demo")["totals"] == {
        "page_views": 1, "questions": 1, "reasked": 0, "rated_down": 0,
    }
    assert get(client, headers, "?range=24h&source=live")["totals"]["questions"] == 3
    assert get(client, headers, "?range=24h&version=2")["totals"]["questions"] == 1
    assert get(client, headers, "?range=24h&source=demo")["previous"] == dict.fromkeys(COUNTS, 0)


def test_trends_version_markers_live_and_demo_labelled(client, db_session):
    workspace, agent, headers = make_workspace(db_session)
    now = utcnow()
    db_session.add(
        PromptVersion(agent_id=agent.id, text="p2", origin="manual", status="promoted", number=2,
                      created_at=now - timedelta(days=2))
    )
    db_session.commit()
    add_q(db_session, workspace, "d1", "other", source="imported", version=1, created_at=now - timedelta(days=5))
    add_q(db_session, workspace, "d2", "other", source="imported", version=2, created_at=now - timedelta(days=3))
    add_q(db_session, workspace, "d3", "other", source="imported", version=2, created_at=now - timedelta(days=1))
    add_q(db_session, workspace, "l1", "other", version=1, created_at=now - timedelta(days=4))  # live: no demo marker

    markers = get(client, headers, "?range=30d")["version_markers"]
    live = [m for m in markers if m["source"] == "live"]
    demo = [m for m in markers if m["source"] == "demo"]
    assert sorted(m["version"] for m in live) == [1, 2]
    assert [m["version"] for m in demo] == [1, 2]
    assert [m["at"] for m in markers] == sorted(m["at"] for m in markers)
    assert demo[1]["at"] == (now - timedelta(days=3)).isoformat()  # first appearance, not the later row
    live2 = next(m for m in live if m["version"] == 2)
    assert live2["at"] == (now - timedelta(days=2)).isoformat()
    assert all("demo" in m["label"].lower() for m in demo)
    assert all("demo" not in m["label"].lower() for m in live)
    assert all(m["label"] for m in markers)

    assert {m["source"] for m in get(client, headers, "?range=30d&source=live")["version_markers"]} == {"live"}
    assert {m["source"] for m in get(client, headers, "?range=30d&source=demo")["version_markers"]} == {"demo"}
    inside_day = get(client, headers, "?range=24h")["version_markers"]
    assert [m for m in inside_day if m["source"] == "demo"] == []  # markers outside the range drop out
    # demo markers are derived, never stored
    assert db_session.query(PromptVersion).filter(PromptVersion.agent_id == agent.id).count() == 2
