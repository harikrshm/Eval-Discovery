"""Create the Patent Assist agent and the workspace that owns it (a one-time setup step).

Usage: python scripts/setup_assist.py [--force]

Uses the same database as the app: $DATABASE_URL, or the local SQLite file when it is unset. The
schema must already be migrated (`alembic upgrade head`). The script makes no model call.

It prints, once, the workspace token (only its hash is stored, so it cannot be shown again) and the
`.env` line that points the backend at the new agent. It refuses to run when an assist agent already
exists unless --force is given. Neither the token nor the database URL is ever written to a log.
"""

import argparse
import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `python scripts/setup_assist.py` finds `backend`

from sqlalchemy import inspect, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend.core.assist.lawpack import load_system_prompt  # noqa: E402
from backend.core.config import AgentAnalysis, AgentConfig  # noqa: E402
from backend.db.agents import create_agent  # noqa: E402
from backend.db.models import Agent, utcnow  # noqa: E402
from backend.db.session import DEFAULT_DATABASE_URL, make_engine  # noqa: E402
from backend.db.workspaces import create_workspace  # noqa: E402

WORKSPACE_TTL = timedelta(days=3650)  # the demo workspace must outlive the 30-day purge by years

STARTER_MESSAGES = (
    "Explain claim 1",
    "What do these CPC codes mean?",
    "Is this still in force?",
)

ASSIST_CONFIG = AgentConfig(
    name="Patent Assist",
    system_prompt=load_system_prompt(),
    user_role_label="Patent reader",
)

# Fixed, so creating the agent needs no model call: the assistant has no behaviors to watch for yet.
ASSIST_ANALYSIS = AgentAnalysis(
    user_role_label=ASSIST_CONFIG.user_role_label,
    behaviors=(),
    starter_messages=STARTER_MESSAGES,
)


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the Patent Assist agent and its workspace.")
    parser.add_argument(
        "--force", action="store_true", help="create another assist agent although one already exists"
    )
    args = parser.parse_args(argv)

    engine = make_engine(os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL)
    try:
        # Never echo a driver error: it can carry the connection URL.
        try:
            insp = inspect(engine)
            migrated = (
                insp.has_table("agent")
                and insp.has_table("assist_question")
                and "kind" in {c["name"] for c in insp.get_columns("agent")}
            )
        except Exception as exc:
            return _fail(f"could not read the database ({type(exc).__name__}); check DATABASE_URL")
        if not migrated:
            return _fail("the database schema is out of date; run `alembic upgrade head` first")

        with Session(engine, expire_on_commit=False) as db:
            existing = db.scalars(select(Agent.id).where(Agent.kind == "assist")).all()
            if existing and not args.force:
                return _fail(
                    "an assist agent already exists; point ASSIST_AGENT_ID at it, or pass --force "
                    "to create another one"
                )

            workspace, token = create_workspace(db)
            try:
                workspace.expires_at = utcnow() + WORKSPACE_TTL
                db.commit()
                agent = create_agent(db, workspace, ASSIST_CONFIG, ASSIST_ANALYSIS, kind="assist")
            except Exception:
                db.rollback()
                db.delete(workspace)
                db.commit()
                raise

            print("Patent Assist agent created.")
            print(f"Workspace token: {token}")
            print("The token is shown only once and is not stored; keep it somewhere safe.")
            print(f"Agent id: {agent.id}")
            print()
            print("Add these lines to your .env:")
            print(f"ASSIST_AGENT_ID={agent.id}")
            print("ASSIST_KEY=<a long random value of your choice>")
            if existing:
                print(
                    f"WARNING: {len(existing)} other assist agent(s) already exist. "
                    "ASSIST_AGENT_ID selects which one is used.",
                    file=sys.stderr,
                )
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
