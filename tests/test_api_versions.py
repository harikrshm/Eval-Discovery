from backend.db.models import Agent, PromptVersion
from backend.db.workspaces import create_workspace


def _agent(client, auth_headers):
    return client.post("/agents", json={"preset_id": "alex-sales"}, headers=auth_headers).json()["id"]


def _add_version(db_session, agent_id, number, status, origin="suggested", parent_id=None, **fields):
    version = PromptVersion(
        agent_id=agent_id,
        text=f"text {number}",
        parent_id=parent_id,
        origin=origin,
        status=status,
        number=number,
        **fields,
    )
    db_session.add(version)
    db_session.commit()
    return version


def _other_headers(db_session):
    _, token = create_workspace(db_session)
    return {"X-Workspace-Token": token}


def test_versions_list_is_newest_first_with_is_current_and_strength(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    agent = db_session.get(Agent, agent_id)
    first = db_session.get(PromptVersion, agent.current_prompt_version_id)
    second = _add_version(
        db_session, agent_id, 2, "promoted", parent_id=first.id, verification={"strength": "full"}
    )
    third = _add_version(db_session, agent_id, 3, "proposed", parent_id=second.id)
    first.status = "superseded"
    agent.current_prompt_version_id = second.id
    db_session.commit()

    r = client.get(f"/agents/{agent_id}/prompt-versions", headers=auth_headers)
    assert r.status_code == 200
    rows = r.json()
    assert [row["number"] for row in rows] == [3, 2, 1]
    assert [row["is_current"] for row in rows] == [False, True, False]
    assert rows[0] == {
        "id": third.id,
        "number": 3,
        "origin": "suggested",
        "status": "proposed",
        "strength": None,
        "created_at": rows[0]["created_at"],
        "is_current": False,
    }
    assert rows[1]["strength"] == "full"
    assert rows[2]["origin"] == "initial"
    assert rows[2]["status"] == "superseded"


def test_version_detail_returns_text_and_parent_text(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    agent = db_session.get(Agent, agent_id)
    first = db_session.get(PromptVersion, agent.current_prompt_version_id)
    child = _add_version(db_session, agent_id, 2, "proposed", parent_id=first.id)

    r = client.get(f"/prompt-versions/{child.id}", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == child.id
    assert body["number"] == 2
    assert body["text"] == "text 2"
    assert body["parent_text"] == first.text
    assert body["is_current"] is False

    root = client.get(f"/prompt-versions/{first.id}", headers=auth_headers).json()
    assert root["parent_text"] is None
    assert root["is_current"] is True
    assert root["text"] == first.text


def test_version_detail_with_a_missing_parent_row_has_null_parent_text(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    orphan = _add_version(db_session, agent_id, 2, "proposed", parent_id="gone")
    body = client.get(f"/prompt-versions/{orphan.id}", headers=auth_headers).json()
    assert body["parent_text"] is None


def test_versions_routes_are_workspace_scoped(client, auth_headers, db_session):
    agent_id = _agent(client, auth_headers)
    version_id = db_session.get(Agent, agent_id).current_prompt_version_id
    other = _other_headers(db_session)
    assert client.get(f"/agents/{agent_id}/prompt-versions", headers=other).status_code == 404
    assert client.get(f"/prompt-versions/{version_id}", headers=other).status_code == 404
    assert client.get("/agents/nope/prompt-versions", headers=auth_headers).status_code == 404
    assert client.get("/prompt-versions/nope", headers=auth_headers).status_code == 404


def test_versions_routes_require_auth(client):
    assert client.get("/agents/x/prompt-versions").status_code == 401
    assert client.get("/prompt-versions/x").status_code == 401
