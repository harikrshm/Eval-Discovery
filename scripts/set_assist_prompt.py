"""Make the current system prompt file the live prompt version of the Patent Assist agent.

Usage: python scripts/set_assist_prompt.py

The live prompt is a row in the database (a prompt version of the assist agent), not the file, so
editing backend/core/assist/system_prompt.md changes nothing until this script runs. It uses the same
database as the app ($DATABASE_URL, or the local SQLite file when it is unset) and the agent named by
$ASSIST_AGENT_ID. When the agent's current prompt already equals the file, it changes nothing;
otherwise it adds a new manual version and makes it current. The script makes no model call, and it
prints no key, token or database URL.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `python scripts/set_assist_prompt.py` finds `backend`

from sqlalchemy.orm import Session  # noqa: E402

from backend.core.assist.lawpack import load_system_prompt  # noqa: E402
from backend.db.agents import current_prompt_version, new_prompt_version  # noqa: E402
from backend.db.assist import get_assist_agent  # noqa: E402
from backend.db.session import DEFAULT_DATABASE_URL, make_engine  # noqa: E402


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Make the current system prompt the live prompt version of the Patent Assist agent."
    )
    parser.parse_args(argv)

    engine = make_engine(os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL)
    try:
        with Session(engine, expire_on_commit=False) as db:
            # Never echo a driver error: it can carry the connection URL.
            try:
                agent, _ = get_assist_agent(db)
            except LookupError as exc:
                return _fail(f"{exc} Set ASSIST_AGENT_ID to the id of an assist agent.")
            except Exception as exc:
                return _fail(f"could not read the database ({type(exc).__name__}); check DATABASE_URL")

            text = load_system_prompt()
            current = current_prompt_version(agent)
            if current.text == text:
                print(f"Prompt already current (version {current.number}).")
                return 0
            version = new_prompt_version(db, agent, text)
            db.commit()
            print(f"Created prompt version {version.number} and made it current.")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
