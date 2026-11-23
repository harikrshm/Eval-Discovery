from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.db.models import Workspace, utcnow
from backend.db.workspaces import create_workspace
from backend.main import ALLOWED_ORIGINS, app


def test_legacy_routes_are_gone(client):
    for path in ("/chat", "/check-pattern", "/generate-criteria"):
        assert client.post(path, json={}).status_code == 404


def test_startup_purges_expired_workspaces(db_session, monkeypatch):
    now = utcnow()
    expired, _ = create_workspace(db_session, now=now - timedelta(days=60))
    live, _ = create_workspace(db_session, now=now)
    expired_id, live_id = expired.id, live.id

    monkeypatch.setenv("WORKER_ENABLED", "0")  # this test is about the purge, not the worker
    # Make the lifespan use the same in-memory engine the test seeded.
    monkeypatch.setattr("backend.main.SessionLocal", sessionmaker(bind=db_session.get_bind()))
    with TestClient(app):
        pass

    db_session.expire_all()
    assert db_session.get(Workspace, expired_id) is None
    assert db_session.get(Workspace, live_id) is not None


def test_cors_preflight_allows_workspace_token_header(client):
    origin = ALLOWED_ORIGINS[0]
    r = client.options(
        "/agents",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-Workspace-Token",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin
    assert "x-workspace-token" in r.headers["access-control-allow-headers"].lower()


_EXTENSION_ORIGIN = "chrome-extension://" + "a" * 32


def _preflight(client, origin):
    return client.options(
        "/assist/chat",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-assist-key",
        },
    )


def test_cors_preflight_allows_an_extension_origin(client):
    # Chrome sends a preflight for the extension's service worker requests (seen on a real run), so the
    # origin of an unpacked extension must pass; the assist key is what actually authorises the call.
    r = _preflight(client, _EXTENSION_ORIGIN)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == _EXTENSION_ORIGIN
    assert "x-assist-key" in r.headers["access-control-allow-headers"].lower()


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://127.0.0.1:9999",
        "chrome-extension://" + "a" * 31,  # not a 32-letter extension id
        "chrome-extension://" + "a" * 32 + ".evil.example",
        "chrome-extension://" + "A" * 32,  # ids are lower case
        "moz-extension://" + "a" * 32,
    ],
)
def test_cors_preflight_still_rejects_other_origins(client, origin):
    assert _preflight(client, origin).status_code == 400


def _patch_lifespan(db_session, monkeypatch):
    """Point the startup purge at the test database and replace the worker loop with a recorder."""
    monkeypatch.setattr("backend.main.SessionLocal", sessionmaker(bind=db_session.get_bind()))
    events = {"started": 0, "stopped": 0, "stop_event": None}

    async def fake_worker_loop(stop, poll_seconds=1.0):
        events["started"] += 1
        events["stop_event"] = stop
        await stop.wait()
        events["stopped"] += 1

    monkeypatch.setattr("backend.main.worker_loop", fake_worker_loop)
    return events


def test_lifespan_with_worker_disabled_starts_no_task(db_session, monkeypatch):
    events = _patch_lifespan(db_session, monkeypatch)
    monkeypatch.setenv("WORKER_ENABLED", "0")

    with TestClient(app):
        pass

    assert events["started"] == 0


@pytest.mark.parametrize("value", [None, "1"])
def test_lifespan_starts_worker_and_stops_it_on_shutdown(db_session, monkeypatch, value):
    events = _patch_lifespan(db_session, monkeypatch)
    if value is None:
        monkeypatch.delenv("WORKER_ENABLED", raising=False)
    else:
        monkeypatch.setenv("WORKER_ENABLED", value)

    with TestClient(app):
        pass

    assert events["started"] == 1
    assert events["stop_event"].is_set()
    assert events["stopped"] == 1  # shutdown awaited the task to completion
