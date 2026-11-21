import pytest

from backend.db.events import EVENT_NAMES, log_event
from backend.db.models import Event
from backend.db.workspaces import create_workspace


def test_event_names():
    assert EVENT_NAMES == {
        "agent_created",
        "run_saved",
        "loop_detected",
        "criterion_created",
        "test_created",
        "replay_started",
        "replay_passed",
        "replay_failed",
        "fix_suggested",
        "fix_approved",
        "fix_rejected",
        "fix_verified",
        "fix_verification_failed",
        "fix_promoted",
        "version_restored",
        "report_viewed",
        "test_generated",
    }


def test_log_event_persists_properties_and_created_at(db_session):
    workspace, _ = create_workspace(db_session)
    event = log_event(db_session, workspace.id, "run_saved", {"position": 3})
    stored = db_session.get(Event, event.id)
    assert stored.workspace_id == workspace.id
    assert stored.name == "run_saved"
    assert stored.properties == {"position": 3}
    assert stored.created_at is not None


def test_log_event_defaults_properties_to_empty_dict(db_session):
    workspace, _ = create_workspace(db_session)
    event = log_event(db_session, workspace.id, "agent_created")
    assert event.properties == {}


def test_try_log_event_logs_on_success(db_session):
    from backend.db.events import try_log_event

    workspace, _ = create_workspace(db_session)
    assert try_log_event(db_session, workspace.id, "agent_created", {"source": "preset"}) is None
    assert db_session.query(Event).filter_by(name="agent_created").count() == 1


def test_try_log_event_swallows_errors_rolls_back_and_logs(db_session, monkeypatch, caplog):
    from backend.db import events
    from backend.db.models import Workspace

    workspace, _ = create_workspace(db_session)

    def boom(*args, **kwargs):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(events, "log_event", boom)
    rolled_back = []
    real_rollback = db_session.rollback

    def tracking_rollback():
        rolled_back.append(1)
        real_rollback()

    monkeypatch.setattr(db_session, "rollback", tracking_rollback)

    with caplog.at_level("ERROR"):
        assert events.try_log_event(db_session, workspace.id, "agent_created") is None
    assert rolled_back == [1]
    assert "db exploded" in caplog.text
    assert db_session.query(Workspace).count() == 1  # session still usable


def test_try_log_event_unknown_name_does_not_raise(db_session):
    from backend.db.events import try_log_event

    workspace, _ = create_workspace(db_session)
    try_log_event(db_session, workspace.id, "bogus")
    assert db_session.query(Event).count() == 0


def test_log_event_unknown_name_raises_and_persists_nothing(db_session):
    workspace, _ = create_workspace(db_session)
    with pytest.raises(ValueError):
        log_event(db_session, workspace.id, "bogus")
    assert db_session.query(Event).count() == 0
