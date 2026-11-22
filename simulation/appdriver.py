"""Drive the REAL FastAPI app in-process: no HTTP server, an in-memory SQLite database, and the
background job worker run inline (``drain``).

This mirrors ``tests/conftest.py``: one shared session behind a ``get_db`` override, a
``TestClient`` that is never used as a context manager (so the lifespan worker never starts), and
a workspace token sent on every request. Each method calls the real route and returns what the
route returns; the caller decides when to ``drain`` the queue. The driver never installs a model
client: the caller does that (``simulation.clients.install_client``).
"""

import os

if not os.environ.get("GROQ_SKIP_DOTENV"):  # an empty value counts as unset, as in backend.groq_client
    os.environ["GROQ_SKIP_DOTENV"] = "1"  # before any backend import: never read the real .env

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api import limits
from backend.db import models  # noqa: F401  (register tables on Base.metadata)
from backend.db.session import Base, get_db
from backend.jobs import worker
from backend.main import app

_LIMITER_NAMES = ("llm_limiter", "global_llm_limiter", "workspace_limiter")
_UNLIMITED = 10**9
_MISSING = object()


class DriverError(RuntimeError):
    """A route answered with an unexpected status, or the worker would not go idle.

    ``status`` is the HTTP status (None for a drain that hit its cap); ``detail`` is the route's
    ``detail`` text.
    """

    def __init__(self, status: int | None, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(f"HTTP {status}: {detail}" if status is not None else detail)


def _detail(response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)


class AppDriver:
    """Context manager around the real app. See the module docstring."""

    def __init__(self) -> None:
        self._engine = None
        self._session = None
        self._client: TestClient | None = None
        self._headers: dict[str, str] = {}
        self._saved_limits: dict[str, int] = {}
        self._previous_override = _MISSING

    def __enter__(self) -> "AppDriver":
        try:
            self._open()
        except BaseException:
            self._close()
            raise
        return self

    def __exit__(self, *exc_info) -> None:
        self._close()

    def _open(self) -> None:
        self._engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self._engine)
        self._session = sessionmaker(bind=self._engine, expire_on_commit=False)()
        session = self._session

        def override_get_db():
            yield session

        self._previous_override = app.dependency_overrides.get(get_db, _MISSING)
        app.dependency_overrides[get_db] = override_get_db

        # Raise the in-process rate limits for the driver's lifetime (their instances are module
        # globals read by name, so changing max_calls takes effect everywhere); _close restores them.
        for name in _LIMITER_NAMES:
            limiter = getattr(limits, name)
            self._saved_limits[name] = limiter.max_calls
            limiter.max_calls = _UNLIMITED

        # Deliberately not a context manager: entering it would start the lifespan (startup purge
        # against the real engine, and the background worker).
        self._client = TestClient(app)
        token = self._request("POST", "/workspaces", 201)["token"]
        self._headers = {"X-Workspace-Token": token}

    def _close(self) -> None:
        for name, max_calls in self._saved_limits.items():
            limiter = getattr(limits, name)
            limiter.max_calls = max_calls
            limiter.reset()  # drop the calls recorded while the limits were lifted
        self._saved_limits = {}
        if self._previous_override is _MISSING:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = self._previous_override
        self._previous_override = _MISSING
        if self._session is not None:
            self._session.close()
            self._session = None
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
        self._client = None

    def _request(self, method: str, path: str, expected: int, json=None):
        assert self._client is not None, "AppDriver must be used as a context manager"
        response = self._client.request(method, path, json=json, headers=self._headers)
        if response.status_code != expected:
            raise DriverError(response.status_code, _detail(response))
        return response.json()

    def create_agent(self, name: str, system_prompt: str) -> str:
        body = self._request("POST", "/agents", 201, {"name": name, "system_prompt": system_prompt})
        return body["id"]

    def chat(self, agent_id: str, messages: list[dict]) -> str:
        return self._request("POST", f"/agents/{agent_id}/chat", 200, {"messages": messages})["reply"]

    def save_run(self, agent_id: str, messages: list[dict]) -> dict:
        return self._request("POST", f"/agents/{agent_id}/runs", 201, {"messages": messages})

    def check_pattern(self, agent_id: str) -> dict:
        return self._request("POST", f"/agents/{agent_id}/check-pattern", 200)

    def discover(self, agent_id: str) -> dict:
        """The new criterion plus ``job_id`` of its queued build_tests job (None if not queued)."""
        return self._request("POST", f"/agents/{agent_id}/criteria/discover", 201)

    def suite(self, agent_id: str) -> dict:
        return self._request("GET", f"/agents/{agent_id}/tests", 200)

    def replay_test(self, test_id: str) -> str:
        return self._request("POST", f"/tests/{test_id}/replay", 202)["job_id"]

    def generate_heldout(self, criterion_id: str) -> str:
        return self._request("POST", f"/criteria/{criterion_id}/generate-heldout", 202)["job_id"]

    def suggest_fix(self, test_id: str) -> str:
        return self._request("POST", f"/tests/{test_id}/suggest-fix", 202)["job_id"]

    def approve(self, version_id: str) -> dict:
        """Queues the verification replays (``jobs``); ``drain`` runs them and promotes or fails the fix."""
        return self._request("POST", f"/prompt-versions/{version_id}/approve", 202)

    def reject(self, version_id: str) -> dict:
        return self._request("POST", f"/prompt-versions/{version_id}/reject", 200)

    def edit_prompt(self, agent_id: str, text: str, base_version_id: str | None = None) -> dict:
        return self._request(
            "PUT", f"/agents/{agent_id}/prompt", 200, {"text": text, "base_version_id": base_version_id}
        )

    def agent_prompt(self, agent_id: str) -> tuple[str, int]:
        agent = self._request("GET", f"/agents/{agent_id}/state", 200)["agent"]
        return agent["system_prompt"], agent["prompt_version_number"]

    def drain(self, max_passes: int = 500) -> int:
        """Run the worker inline until it is idle on two passes in a row; return the jobs processed.

        Two idle passes, not one: the end-of-pass reconcile can queue follow-up work (the change
        report). A job the worker keeps requeueing never goes idle, so the cap raises DriverError.
        """
        assert self._session is not None, "AppDriver must be used as a context manager"
        processed = 0
        idle_in_a_row = 0
        for _ in range(max_passes):
            if worker.run_one(self._session):
                processed += 1
                idle_in_a_row = 0
            else:
                idle_in_a_row += 1
                if idle_in_a_row >= 2:
                    return processed
        raise DriverError(None, f"The job queue was still busy after {max_passes} passes.")

    def job(self, job_id: str) -> dict:
        return self._request("GET", f"/jobs/{job_id}", 200)
