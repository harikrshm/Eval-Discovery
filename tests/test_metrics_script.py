import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert

from backend.db import models  # noqa: F401
from backend.db.models import Event, Workspace
from backend.db.session import Base
from scripts import metrics

T0 = datetime(2026, 1, 1, 12, 0, 0)


def _rows(spec):
    """spec: list of (workspace_id, name); created_at increases with list order."""
    return [(ws, name, T0 + timedelta(seconds=i)) for i, (ws, name) in enumerate(spec)]


SEED = (
    # workspace A: 3 runs, then a failing replay, then a pass
    [("A", "agent_created")]
    + [("A", "run_saved")] * 2
    + [("A", "loop_detected")] * 2
    + [("A", "criterion_created")]
    + [("A", "test_created")]
    + [("A", "run_saved")]
    + [("A", "replay_failed")]
    + [("A", "fix_suggested")] * 2
    + [("A", "fix_approved")]
    + [("A", "fix_rejected")]
    + [("A", "fix_verified")]
    + [("A", "replay_passed")]
    + [("A", "run_saved")]  # after the first pass: not counted
    # workspace B: criteria but no test, no pass
    + [("B", "run_saved")] * 4
    + [("B", "loop_detected")] * 2
    + [("B", "criterion_created")] * 2
    + [("B", "fix_suggested")] * 2
    + [("B", "fix_approved")] * 2
    + [("B", "fix_verification_failed")]
    + [("B", "fix_verified")]
    + [("B", "fix_promoted")]
    # workspace C: a test but NO criterion (separates the bounded from the literal formula)
    + [("C", "test_created")]
)


def test_counts_per_workspace_and_total():
    m = metrics.compute_metrics(_rows(SEED))
    a, b = m["workspaces"]["A"], m["workspaces"]["B"]
    assert a["counts"]["run_saved"] == 4
    assert a["counts"]["loop_detected"] == 2
    assert a["counts"]["fix_rejected"] == 1
    assert b["counts"]["run_saved"] == 4
    assert b["counts"]["fix_verification_failed"] == 1
    assert b["counts"]["fix_promoted"] == 1
    assert m["totals"]["counts"]["run_saved"] == 8
    assert m["totals"]["counts"]["criterion_created"] == 3
    assert m["totals"]["counts"]["test_created"] == 2
    assert m["workspaces"]["C"]["counts"]["test_created"] == 1
    assert m["totals"]["counts"]["agent_created"] == 1
    assert m["totals"]["counts"]["replay_passed"] == 1
    assert set(m["totals"]["counts"]) == set(metrics.COUNTED_EVENTS)


def test_every_rate_in_total():
    r = metrics.compute_metrics(_rows(SEED))["totals"]["rates"]
    assert r["acceptance_rate"] == pytest.approx(3 / 4)
    # Bounded definition: workspaces with BOTH a criterion and a test (A only) over workspaces
    # with a criterion (A, B) = 1/2. The literal "workspaces with a test / workspaces with a
    # criterion" would count C too and give 2/2 = 100%.
    assert r["test_conversion"] == pytest.approx(0.5)
    assert r["test_conversion"] != pytest.approx(1.0)
    assert r["fix_approval_rate"] == pytest.approx(3 / 4)
    assert r["verification_pass_rate"] == pytest.approx(2 / 3)


def test_per_workspace_rates():
    m = metrics.compute_metrics(_rows(SEED))["workspaces"]
    assert m["A"]["rates"]["acceptance_rate"] == pytest.approx(1 / 2)
    assert m["B"]["rates"]["acceptance_rate"] == pytest.approx(1.0)
    assert m["B"]["rates"]["fix_approval_rate"] == pytest.approx(1.0)
    assert m["B"]["rates"]["verification_pass_rate"] == pytest.approx(1 / 2)


def test_runs_until_first_pass():
    m = metrics.compute_metrics(_rows(SEED))
    assert m["workspaces"]["A"]["runs_until_first_pass"] == 3
    assert m["workspaces"]["B"]["runs_until_first_pass"] is None
    assert m["totals"]["runs_until_first_pass"] == {"median": 3, "workspaces_with_value": 1}


def test_runs_until_first_pass_median_and_zero():
    rows = _rows(
        [("A", "run_saved")] * 2 + [("A", "replay_passed")]
        + [("B", "replay_passed")]
        + [("C", "run_saved")] * 5 + [("C", "replay_passed")]
        + [("D", "run_saved")]
    )
    m = metrics.compute_metrics(rows)
    assert m["workspaces"]["B"]["runs_until_first_pass"] == 0
    assert m["totals"]["runs_until_first_pass"] == {"median": 2, "workspaces_with_value": 3}


def test_runs_until_first_pass_uses_created_at_order_not_input_order():
    rows = [
        ("A", "replay_passed", T0 + timedelta(seconds=5)),
        ("A", "run_saved", T0 + timedelta(seconds=1)),
        ("A", "run_saved", T0 + timedelta(seconds=2)),
        ("A", "run_saved", T0 + timedelta(seconds=9)),
    ]
    assert metrics.compute_metrics(rows)["workspaces"]["A"]["runs_until_first_pass"] == 2


def test_zero_denominators_are_none_and_print_na():
    m = metrics.compute_metrics(_rows([("A", "agent_created"), ("A", "run_saved")]))
    assert m["totals"]["rates"] == {
        "acceptance_rate": None,
        "test_conversion": None,
        "fix_approval_rate": None,
        "verification_pass_rate": None,
    }
    assert m["totals"]["runs_until_first_pass"] == {"median": None, "workspaces_with_value": 0}
    assert "n/a" in metrics.format_text(m)


def test_empty_rows():
    m = metrics.compute_metrics([])
    assert m["workspaces"] == {}
    assert m["totals"]["counts"]["run_saved"] == 0
    assert m["totals"]["rates"]["acceptance_rate"] is None


def test_unrelated_events_ignored_in_counts():
    m = metrics.compute_metrics(_rows([("A", "report_viewed"), ("A", "run_saved")]))
    assert "report_viewed" not in m["totals"]["counts"]
    assert m["totals"]["counts"]["run_saved"] == 1


def test_text_format_has_table():
    text = metrics.format_text(metrics.compute_metrics(_rows(SEED)))
    assert "acceptance rate" in text.lower()
    assert "75.0%" in text
    assert "loop_detected" in text
    assert "A" in text and "B" in text


# --- end to end against a seeded SQLite file ---------------------------------


@pytest.fixture
def seeded_url(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for ws in ("A", "B", "C"):
            conn.execute(insert(Workspace.__table__).values(id=ws, token_hash=f"h{ws}", created_at=T0, expires_at=T0))
        for i, (ws, name) in enumerate(SEED):
            conn.execute(
                insert(Event.__table__).values(
                    id=f"{i:04d}",
                    workspace_id=ws,
                    name=name,
                    properties={},
                    created_at=T0 + timedelta(seconds=i),
                )
            )
    engine.dispose()
    return url


def test_main_json(seeded_url, capsys):
    code = metrics.main(["--database-url", seeded_url, "--format", "json"], {})
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data) == {"totals", "workspaces", "definitions"}
    assert set(data["totals"]) == {"counts", "rates", "runs_until_first_pass"}
    assert data["totals"]["rates"]["acceptance_rate"] == pytest.approx(0.75)
    assert data["totals"]["rates"]["test_conversion"] == pytest.approx(0.5)
    assert data["workspaces"]["A"]["runs_until_first_pass"] == 3
    assert data["workspaces"]["B"]["runs_until_first_pass"] is None
    assert "both a criterion and a test" in data["definitions"]["test_conversion"]
    assert "only computed for the total" in data["definitions"]["test_conversion"]


def test_main_text_default_and_env_url(seeded_url, capsys):
    code = metrics.main([], {"DATABASE_URL": seeded_url})
    assert code == 0
    out = capsys.readouterr().out
    assert "acceptance rate" in out.lower()
    assert "n/a" in out  # workspace B has no passing test


def test_main_flag_beats_env(seeded_url, capsys):
    code = metrics.main(
        ["--database-url", seeded_url], {"DATABASE_URL": "sqlite:///nonexistent-dir/x.db"}
    )
    assert code == 0


def test_main_is_read_only(seeded_url):
    engine = create_engine(seeded_url)
    with engine.connect() as conn:
        before = conn.exec_driver_sql("select count(*) from event").scalar()
    engine.dispose()
    metrics.main(["--database-url", seeded_url], {})
    engine = create_engine(seeded_url)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("select count(*) from event").scalar() == before
    engine.dispose()


def test_main_missing_url_exits_nonzero(capsys):
    code = metrics.main([], {})
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "DATABASE_URL" in captured.err


def test_password_never_printed_on_failure(capsys):
    # unreachable server: the failure path must not leak the password either
    url = "postgresql://user:s3cretPW@127.0.0.1:1/db"
    for fmt in ("text", "json"):
        code = metrics.main(["--database-url", url, "--format", fmt], {})
        captured = capsys.readouterr()
        assert code != 0
        assert "s3cretPW" not in captured.out + captured.err


def test_password_never_printed_for_bad_url(capsys):
    code = metrics.main(["--database-url", "nonsense://u:s3cretPW@h/db"], {})
    captured = capsys.readouterr()
    assert code != 0
    assert "s3cretPW" not in captured.out + captured.err


def test_masked_url_helper():
    masked = metrics.mask_url("postgresql://user:s3cretPW@host/db")
    assert "s3cretPW" not in masked
    assert "user" in masked and "host" in masked


def test_output_header_shows_masked_database(seeded_url, capsys):
    metrics.main(["--database-url", seeded_url], {})
    assert "m.db" in capsys.readouterr().out


def test_text_footer_and_help_state_test_conversion_definition(seeded_url, capsys):
    metrics.main(["--database-url", seeded_url], {})
    out = capsys.readouterr().out
    assert "workspaces that have both a criterion and a test" in out
    assert "only computed for the total" in out
    with pytest.raises(SystemExit):
        metrics.main(["--help"], {})
    help_text = " ".join(capsys.readouterr().out.split())
    assert "workspaces that have both a criterion and a test" in help_text
    assert "only computed for the total" in help_text


# --- masking on the success path ------------------------------------------------

SECRET_URL = (
    "postgresql://reader:S3cretPW@db.example.com:5432/app"
    "?sslmode=require&password=QueryPW1&sslpassword=QueryPW2&api_key=QueryKey3"
    "&auth_token=QueryTok4&client_secret=QuerySec5&application_name=metrics"
)
SECRETS = ("S3cretPW", "QueryPW1", "QueryPW2", "QueryKey3", "QueryTok4", "QuerySec5")


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_secrets_absent_on_success_path(seeded_url, capsys, fmt):
    seen = []

    def factory(url):  # open the seeded sqlite file whatever URL we were handed
        seen.append(url)
        return create_engine(seeded_url)

    code = metrics.main(["--database-url", SECRET_URL, "--format", fmt], {}, engine_factory=factory)
    captured = capsys.readouterr()
    assert code == 0
    assert seen == [SECRET_URL]  # the real URL is what gets connected to
    for secret in SECRETS:
        assert secret not in captured.out
        assert secret not in captured.err
    if fmt == "text":  # the header was reached, shows the masked form and keeps harmless parts
        assert "Database: postgresql://reader:***@db.example.com:5432/app" in captured.out
        assert "sslmode=require" in captured.out
        assert "application_name=metrics" in captured.out
    else:
        assert json.loads(captured.out)["totals"]["counts"]["run_saved"] == 8


def test_secrets_absent_on_connection_failure(capsys):
    def factory(url):
        raise RuntimeError("could not connect with " + url)

    code = metrics.main(["--database-url", SECRET_URL], {}, engine_factory=factory)
    captured = capsys.readouterr()
    assert code == 1
    assert "db.example.com" in captured.err
    for secret in SECRETS:
        assert secret not in captured.out + captured.err


def test_mask_url_hides_password_and_secret_query_params():
    masked = metrics.mask_url(SECRET_URL)
    for secret in SECRETS:
        assert secret not in masked
    assert "reader" in masked and "db.example.com" in masked
    assert "sslmode=require" in masked


@pytest.mark.parametrize("key", ["PASSWORD", "SslPassword", "Secret", "access_token", "private_KEY"])
def test_mask_url_query_key_match_is_case_insensitive(key):
    masked = metrics.mask_url(f"postgresql://u@h/db?{key}=ValueToHide")
    assert "ValueToHide" not in masked
