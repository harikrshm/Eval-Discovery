from datetime import timedelta

from backend.db.models import Agent, AssistCall, AssistJudgement, Run, PromptVersion, Workspace, utcnow
from backend.db.workspaces import (
    TOKEN_TTL_DAYS,
    create_workspace,
    find_workspace,
    hash_token,
    purge_expired,
)

DETAIL = "Workspace not found or expired. Start a new one."


def test_create_workspace_returns_token_and_stores_only_hash(client, db_session):
    resp = client.post("/workspaces")
    assert resp.status_code == 201
    tok = resp.json()["token"]
    ws = db_session.query(Workspace).one()
    assert ws.token_hash == hash_token(tok) and tok not in ws.token_hash
    assert len(ws.token_hash) == 64


def test_workspace_expires_after_ttl(db_session):
    now = utcnow()
    ws, _ = create_workspace(db_session, now=now)
    assert ws.created_at == now
    assert ws.expires_at == now + timedelta(days=TOKEN_TTL_DAYS) == now + timedelta(days=30)


def test_missing_or_bad_token_is_401(client):
    resp = client.delete("/workspaces/me")
    assert resp.status_code == 401 and resp.json()["detail"] == DETAIL
    resp = client.delete("/workspaces/me", headers={"X-Workspace-Token": "nope"})
    assert resp.status_code == 401 and resp.json()["detail"] == DETAIL


def test_expired_token_is_401(client, db_session, auth_headers):
    ws = db_session.query(Workspace).one()
    ws.expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    resp = client.delete("/workspaces/me", headers=auth_headers)
    assert resp.status_code == 401 and resp.json()["detail"] == DETAIL


def test_token_unusable_after_delete(client, auth_headers):
    assert client.delete("/workspaces/me", headers=auth_headers).status_code == 204
    assert client.delete("/workspaces/me", headers=auth_headers).status_code == 401


def test_delete_cascades(client, db_session, auth_headers):
    ws = db_session.query(Workspace).one()
    agent = Agent(workspace_id=ws.id, name="A", user_role_label="user")
    db_session.add(agent)
    db_session.flush()
    pv = PromptVersion(agent_id=agent.id, text="p", origin="initial", status="promoted")
    db_session.add(pv)
    db_session.flush()
    db_session.add(Run(agent_id=agent.id, prompt_version_id=pv.id, position=1, messages=[]))
    db_session.commit()

    assert client.delete("/workspaces/me", headers=auth_headers).status_code == 204
    assert db_session.query(Workspace).count() == 0
    assert db_session.query(Agent).count() == 0
    assert db_session.query(Run).count() == 0
    assert db_session.query(PromptVersion).count() == 0


def test_find_workspace_unknown_and_expired(db_session):
    now = utcnow()
    ws, token = create_workspace(db_session, now=now)
    assert find_workspace(db_session, token, now=now).id == ws.id
    assert find_workspace(db_session, "other", now=now) is None
    assert find_workspace(db_session, token, now=now + timedelta(days=TOKEN_TTL_DAYS, seconds=1)) is None


def test_purge_expired_only_removes_expired_and_returns_count(db_session):
    now = utcnow()
    old1, _ = create_workspace(db_session, now=now - timedelta(days=40))
    old2, _ = create_workspace(db_session, now=now - timedelta(days=31))
    fresh, _ = create_workspace(db_session, now=now)
    assert purge_expired(db_session, now=now) == 2
    remaining = db_session.query(Workspace).all()
    assert [w.id for w in remaining] == [fresh.id]
    assert purge_expired(db_session, now=now) == 0


def test_purge_expired_removes_the_workspaces_telemetry_and_judgements_with_it(db_session):
    now = utcnow()
    old, _ = create_workspace(db_session, now=now - timedelta(days=40))
    fresh, _ = create_workspace(db_session, now=now)
    for workspace in (old, fresh):
        db_session.add(
            AssistCall(workspace_id=workspace.id, kind="judge", model="m", latency_ms=5, outcome="ok", source="live")
        )
        db_session.add(
            AssistJudgement(
                workspace_id=workspace.id, session_id="s1", prompt_version=1, rubric_version="1", model="m",
                answered="yes", concise=True, citations_sensible=True, legal="not_applicable", grade=4,
                reason="no_issue", flagged=False,
            )
        )
    db_session.commit()

    assert purge_expired(db_session, now=now) == 1

    assert [c.workspace_id for c in db_session.query(AssistCall).all()] == [fresh.id]
    assert [j.workspace_id for j in db_session.query(AssistJudgement).all()] == [fresh.id]
