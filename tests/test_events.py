import pytest

from backend.db.events import EVENT_NAMES, log_event
from backend.db.models import Event
from backend.db.workspaces import create_workspace


def test_event_names():
    assert EVENT_NAMES == {"agent_created", "run_saved", "loop_detected", "criterion_created"}


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


def test_log_event_unknown_name_raises_and_persists_nothing(db_session):
    workspace, _ = create_workspace(db_session)
    with pytest.raises(ValueError):
        log_event(db_session, workspace.id, "bogus")
    assert db_session.query(Event).count() == 0
