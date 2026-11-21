"""Replay a saved run's user messages against an agent's prompt.

Pure core module: takes plain arguments and never touches the API or database layers.
"""

from backend.core.config import AgentConfig
from backend.groq_client import MODEL_CHAT, groq_chat

MAX_REPLAY_USER_MESSAGES = 20
REPLAY_MAX_TOKENS = 3000
MAX_REPLY_CHARS = 16000


class ReplayFailed(RuntimeError):
    """Raised when a replay chat call fails."""


def user_messages(transcript: list[dict]) -> list[str]:
    """User-role contents of a transcript, in order, capped at MAX_REPLAY_USER_MESSAGES."""
    out = [
        turn["content"]
        for turn in transcript
        if turn.get("role") == "user" and isinstance(turn.get("content"), str)
    ]
    return out[:MAX_REPLAY_USER_MESSAGES]


def replay(config: AgentConfig, scripted_user_messages: list[str]) -> list[dict]:
    """Run the scripted user messages against config.system_prompt, one chat call per message.

    Returns the full transcript as {role, content} turns.
    """
    if not scripted_user_messages:
        raise ValueError("replay needs at least one user message")
    if len(scripted_user_messages) > MAX_REPLAY_USER_MESSAGES:
        raise ValueError(f"replay supports at most {MAX_REPLAY_USER_MESSAGES} user messages")

    history: list[dict] = []
    for message in scripted_user_messages:
        history.append({"role": "user", "content": message})
        try:
            reply = groq_chat(
                config.system_prompt,
                list(history),
                model=MODEL_CHAT,
                max_tokens=REPLAY_MAX_TOKENS,
                temperature=0,
            )
        except Exception as exc:
            raise ReplayFailed(f"replay chat call failed: {exc}") from exc
        history.append({"role": "assistant", "content": reply[:MAX_REPLY_CHARS]})
    return history
