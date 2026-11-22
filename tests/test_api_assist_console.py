"""GET /assist/console/{status,metrics,flagged}: workspace-token auth, scoping, filters and no text leaks."""

from datetime import timedelta

import pytest

from backend.db.agents import create_agent
from backend.db.assist import add_question, save_flagged_run
from backend.db.models import AssistPageView, utcnow
from backend.db.workspaces import create_workspace
from scripts import setup_assist

MARK_Q = "MARKERQUESTIONTEXT"
MARK_A = "MARKERANSWERTEXT"
MARK_B = "MARKERBRIEFTEXT"
MARK_M = "MARKERMESSAGETEXT"
MARKERS = (MARK_Q, MARK_A, MARK_B, MARK_M)


def make_workspace(db, with_agent=True):
    workspace, token = create_workspace(db)
    agent = (
        create_agent(db, workspace, setup_assist.ASSIST_CONFIG, setup_assist.ASSIST_ANALYSIS, kind="assist")
        if with_agent
        else None
    )
    return workspace, agent, {"X-Workspace-Token": token}


def add_view(db, workspace, session, sections, bounce=False, source="live", version=1):
    db.add(
        AssistPageView(
            session_id=session, workspace_id=workspace.id, patent_number="US10000000B2", dwell_ms=20000,
            sections=sections, bounce=bounce, source=source, assist_version=version,
        )
    )
    db.commit()


def add_q(db, workspace, session, intent, focus=None, source="live", version=1, **kw):
    return add_question(
        db, workspace.id, session, "US10000000B2", f"{MARK_Q} {intent}", intent, focus, f"{MARK_A} answer",
        kw.pop("citation_ok", True), source, version, **kw,
    )


def flag(db, agent, session, source="assist"):
    messages = [{"role": "user", "content": MARK_M}, {"role": "assistant", "content": MARK_A}]
    brief = {"patent_number": "US10000000B2", "title": MARK_B, "abstract": MARK_B, "claims": []}
    return save_flagged_run(db, agent, session, messages, brief, source)


@pytest.fixture
def seeded(db_session):
    """One workspace: 2 live views + 1 bounce, 4 questions, 3 flagged runs (one imported)."""
    workspace, agent, headers = make_workspace(db_session)
    add_view(db_session, workspace, "sess-a", {"claims": 5000, "description": 1000})
    add_view(db_session, workspace, "sess-b", {"claims": 4000, "citations": 3500})
    add_view(db_session, workspace, "sess-c", {"claims": 300}, bounce=True)
    add_q(db_session, workspace, "sess-a", "claim_explain", "claims", rating="down")
    add_q(db_session, workspace, "sess-a", "claim_explain", "claims", reasked=True)
    add_q(db_session, workspace, "sess-b", "citations_prior_art", "citations", citation_ok=False)
    add_q(db_session, workspace, "sess-c", "other")
    run_a = flag(db_session, agent, "sess-a")
    run_b = flag(db_session, agent, "sess-b")
    run_c = flag(db_session, agent, "sess-c", source="imported")
    return {
        "workspace": workspace, "agent": agent, "headers": headers,
        "runs": {"a": run_a, "b": run_b, "c": run_c},
    }


# ---- auth and scoping -----------------------------------------------------------

PATHS = ["/assist/console/status", "/assist/console/metrics", "/assist/console/flagged"]


@pytest.mark.parametrize("path", PATHS)
def test_requires_a_workspace_token(client, seeded, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-Workspace-Token": "nope"}).status_code == 401


@pytest.mark.parametrize("path", PATHS)
def test_assist_key_is_not_accepted(client, seeded, monkeypatch, path):
    monkeypatch.setenv("ASSIST_KEY", "some-assist-key-0123456789")
    assert client.get(path, headers={"X-Assist-Key": "some-assist-key-0123456789"}).status_code == 401


@pytest.mark.parametrize("path", PATHS)
def test_404_when_the_workspace_has_no_assist_agent(client, db_session, path):
    _, _, headers = make_workspace(db_session, with_agent=False)
    response = client.get(path, headers=headers)
    assert response.status_code == 404
    assert response.json()["detail"] == "No assistant agent in this workspace."


def test_a_general_agent_is_not_an_assist_agent(client, db_session):
    workspace, token = create_workspace(db_session)
    create_agent(db_session, workspace, setup_assist.ASSIST_CONFIG, setup_assist.ASSIST_ANALYSIS)
    assert client.get("/assist/console/status", headers={"X-Workspace-Token": token}).status_code == 404


def test_a_foreign_workspace_never_sees_another_workspaces_data(client, db_session, seeded):
    _, _, other_headers = make_workspace(db_session)
    status = client.get("/assist/console/status", headers=other_headers).json()
    assert status["agent_id"] != seeded["agent"].id
    assert status["run_count"] == 0
    metrics = client.get("/assist/console/metrics", headers=other_headers).json()
    assert metrics["adoption"]["totals"] == {"page_views": 0, "questions": 0, "non_bounce_views": 0}
    assert metrics["data_source_label"] is None
    assert client.get("/assist/console/flagged", headers=other_headers).json() == []


# ---- status ---------------------------------------------------------------------


def test_status_counts(client, seeded):
    body = client.get("/assist/console/status", headers=seeded["headers"]).json()
    assert body == {
        "agent_id": seeded["agent"].id,
        "agent_name": seeded["agent"].name,
        "current_version_number": 1,
        "run_count": 3,
        "flagged_run_count": 3,
        "imported_run_count": 1,
    }


def test_status_run_count_includes_manual_runs_but_flagged_does_not(client, db_session, seeded):
    from backend.db.models import Run

    agent = seeded["agent"]
    db_session.add(
        Run(agent_id=agent.id, prompt_version_id=agent.current_prompt_version_id, position=99,
            messages=[], source="manual")
    )
    db_session.commit()
    body = client.get("/assist/console/status", headers=seeded["headers"]).json()
    assert (body["run_count"], body["flagged_run_count"], body["imported_run_count"]) == (4, 3, 1)


# ---- metrics --------------------------------------------------------------------


def test_metrics_match_the_seeded_rows(client, seeded):
    body = client.get("/assist/console/metrics", headers=seeded["headers"]).json()
    adoption = body["adoption"]
    assert adoption["totals"] == {"page_views": 3, "questions": 4, "non_bounce_views": 2}
    assert adoption["bounce_rate"] == 0.3333
    assert adoption["feature_coverage"]["claims"] == 1.0
    assert adoption["feature_coverage"]["citations"] == 0.5
    assert adoption["views_by_section"]["claims"] == 2
    assert adoption["help_demand"]["claims"] == 100.0  # 2 questions / 2 views
    assert adoption["task_mix"]["claim_explain"] == {"count": 2, "share": 0.5}
    assert adoption["struggle"]["unverified_citation_rate"] == 0.25
    assert body["sources"] == adoption["sources"] == {
        "page_views": {"live": 3, "imported": 0},
        "questions": {"live": 4, "imported": 0},
    }
    assert body["data_source_label"] is None
    assert body["version_comparison"]["available"] is False


def test_data_source_label_is_demo_dataset_iff_imported_rows_exist(client, db_session, seeded):
    path = "/assist/console/metrics"
    assert client.get(path, headers=seeded["headers"]).json()["data_source_label"] is None
    add_view(db_session, seeded["workspace"], "imp-view", {"claims": 4000}, source="imported")
    body = client.get(path, headers=seeded["headers"]).json()
    assert body["data_source_label"] == "demo dataset"
    assert body["sources"]["page_views"] == {"live": 3, "imported": 1}


def test_an_imported_question_alone_also_sets_the_label(client, db_session, seeded):
    add_q(db_session, seeded["workspace"], "imp-q", "other", source="imported")
    body = client.get("/assist/console/metrics", headers=seeded["headers"]).json()
    assert body["data_source_label"] == "demo dataset"
    assert body["sources"]["questions"] == {"live": 4, "imported": 1}


def test_imported_only_workspace_never_has_a_version_comparison(client, db_session):
    workspace, _, headers = make_workspace(db_session)
    for version in (1, 2):
        for i in range(15):
            add_q(db_session, workspace, f"s{version}-{i}", "claim_explain", source="imported", version=version)
    body = client.get("/assist/console/metrics", headers=headers).json()
    assert body["version_comparison"]["available"] is False
    assert body["data_source_label"] == "demo dataset"


def test_version_comparison_available_with_two_versions_of_live_questions(client, db_session):
    workspace, _, headers = make_workspace(db_session)
    for version in (1, 2):
        for i in range(10):
            add_q(db_session, workspace, f"s{version}-{i}", "claim_explain", version=version, reasked=i < version)
    body = client.get("/assist/console/metrics", headers=headers).json()
    comparison = body["version_comparison"]
    assert comparison["available"] is True
    assert comparison["versions"]["1"]["reask_rate"] == 0.1
    assert comparison["versions"]["2"]["reask_rate"] == 0.2


# ---- flagged --------------------------------------------------------------------


def get_flagged(client, headers, query=""):
    response = client.get(f"/assist/console/flagged{query}", headers=headers)
    return response


def test_flagged_lists_runs_newest_first_with_question_rows(client, db_session, seeded):
    runs = seeded["runs"]
    base = utcnow()
    for offset, key in enumerate("abc"):
        runs[key].created_at = base + timedelta(minutes=offset)
    db_session.commit()
    body = get_flagged(client, seeded["headers"]).json()
    assert [r["run_id"] for r in body] == [runs["c"].id, runs["b"].id, runs["a"].id]
    run_a = body[2]
    assert run_a["position"] == 1
    assert run_a["source"] == "assist"
    assert run_a["session_id"] == "sess-a"
    assert run_a["patent_number"] == "US10000000B2"
    assert run_a["intents"] == ["claim_explain"]
    assert run_a["sections"] == ["claims"]
    assert run_a["reasons"] == ["rated_down", "reasked"]
    assert {q["intent"] for q in run_a["questions"]} == {"claim_explain"}
    assert len(run_a["questions"]) == 2
    assert set(run_a["questions"][0]) == {
        "question_id", "intent", "section_focus", "rating", "reasked", "citation_ok", "assist_version",
    }
    assert body[1]["reasons"] == ["citation_unverified"]
    assert body[0]["source"] == "imported"
    assert body[0]["reasons"] == []  # its only question is not flagged


def test_flagged_filters_by_intent(client, seeded):
    body = get_flagged(client, seeded["headers"], "?intent=citations_prior_art").json()
    assert [r["session_id"] for r in body] == ["sess-b"]
    assert get_flagged(client, seeded["headers"], "?intent=law_concept").json() == []
    # a run matches when any question of its session has the intent, flagged or not
    assert [r["session_id"] for r in get_flagged(client, seeded["headers"], "?intent=other").json()] == ["sess-c"]


def test_flagged_filters_by_section(client, seeded):
    body = get_flagged(client, seeded["headers"], "?section=claims").json()
    assert [r["session_id"] for r in body] == ["sess-a"]
    body = get_flagged(client, seeded["headers"], "?section=citations&intent=citations_prior_art").json()
    assert [r["session_id"] for r in body] == ["sess-b"]
    assert get_flagged(client, seeded["headers"], "?section=citations&intent=claim_explain").json() == []


@pytest.mark.parametrize("query", ["?intent=bogus", "?section=bogus", "?limit=0", "?limit=abc", "?section=Claims"])
def test_flagged_rejects_invalid_filters(client, seeded, query):
    assert get_flagged(client, seeded["headers"], query).status_code == 422


def test_flagged_limit_default_cap_and_clamp(client, db_session):
    workspace, agent, headers = make_workspace(db_session)
    for i in range(205):
        save = flag(db_session, agent, f"sess-{i:03d}")
        save.created_at = utcnow() + timedelta(seconds=i)
    db_session.commit()
    assert len(get_flagged(client, headers).json()) == 50
    assert len(get_flagged(client, headers, "?limit=7").json()) == 7
    assert len(get_flagged(client, headers, "?limit=200").json()) == 200
    assert len(get_flagged(client, headers, "?limit=100000").json()) == 200  # capped, not an error


def test_flagged_excludes_manual_runs(client, db_session, seeded):
    from backend.db.models import Run

    agent = seeded["agent"]
    db_session.add(
        Run(agent_id=agent.id, prompt_version_id=agent.current_prompt_version_id, position=99,
            messages=[], source="manual")
    )
    db_session.commit()
    body = get_flagged(client, seeded["headers"]).json()
    assert len(body) == 3
    assert all(r["source"] in ("assist", "imported") for r in body)


# ---- no text anywhere -----------------------------------------------------------


@pytest.mark.parametrize("query", ["/assist/console/status", "/assist/console/metrics", "/assist/console/flagged"])
def test_no_question_answer_brief_or_message_text_in_any_response(client, seeded, query):
    response = client.get(query, headers=seeded["headers"])
    assert response.status_code == 200
    for marker in MARKERS:
        assert marker not in response.text
    for secret in ("token", "key"):
        assert f'"{secret}"' not in response.text.lower()
