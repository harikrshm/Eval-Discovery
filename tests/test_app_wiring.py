from datetime import timedelta

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
