"""The guard that decides whether the real-Postgres tests run, tested with fake URLs (no database)."""

import pytest

from tests.pg.guard import ENV_ALLOW_RESET, ENV_URL, decide

PASSWORD = "s3cr3tpassw0rd"


def _url(database="app_test", driver="postgresql+psycopg", password=PASSWORD):
    return f"{driver}://user:{password}@localhost:5432/{database}"


def test_skips_when_url_unset():
    decision = decide({})
    assert decision.url is None
    assert "TEST_DATABASE_URL is not set" in decision.skip_reason


def test_skips_when_url_blank():
    assert decide({ENV_URL: "   "}).url is None


def test_allows_database_with_test_in_its_name():
    decision = decide({ENV_URL: _url("eval_discovery_test")})
    assert decision.skip_reason is None
    assert decision.url == _url("eval_discovery_test")
    assert decision.database == "eval_discovery_test"


def test_name_check_ignores_case():
    assert decide({ENV_URL: _url("MyTestDb")}).skip_reason is None


def test_refuses_database_without_test_in_its_name():
    decision = decide({ENV_URL: _url("postgres")})
    assert decision.url is None
    assert "'postgres'" in decision.skip_reason
    assert "ALLOW_PG_TEST_RESET=1" in decision.skip_reason


def test_override_allows_any_database_name():
    decision = decide({ENV_URL: _url("scratch"), ENV_ALLOW_RESET: "1"})
    assert decision.skip_reason is None
    assert decision.database == "scratch"


@pytest.mark.parametrize("value", ["0", "", "true", "yes"])
def test_only_the_value_1_overrides(value):
    assert decide({ENV_URL: _url("scratch"), ENV_ALLOW_RESET: value}).url is None


@pytest.mark.parametrize(
    "env",
    [
        {ENV_URL: _url("postgres")},
        {ENV_URL: _url("scratch", driver="postgresql")},
        {ENV_URL: _url("scratch", driver="sqlite")},
        {ENV_URL: f"postgresql+psycopg://user:{PASSWORD}@localhost:5432/"},
        {ENV_URL: f"not a url {PASSWORD}"},
        {ENV_URL: _url("app_test", driver="postgresql")},
    ],
)
def test_refused_urls_never_leak_the_password(env):
    decision = decide(env)
    assert decision.url is None
    assert PASSWORD not in decision.skip_reason
    assert "localhost" not in decision.skip_reason


def test_requires_the_psycopg_driver():
    decision = decide({ENV_URL: _url("app_test", driver="postgresql")})
    assert decision.url is None
    assert "postgresql+psycopg" in decision.skip_reason


def test_requires_a_database_name():
    decision = decide({ENV_URL: "postgresql+psycopg://user:pw@localhost:5432/"})
    assert decision.url is None
    assert "must name a database" in decision.skip_reason


def test_garbage_url_is_skipped_not_raised():
    decision = decide({ENV_URL: "definitely not a url"})
    assert decision.url is None
    assert "not a valid database URL" in decision.skip_reason
