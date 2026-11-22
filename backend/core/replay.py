"""Replay a saved run's user messages against an agent's prompt.

Pure core module: takes plain arguments and never touches the API or database layers.
"""

from backend.core.assist import ASSIST_MODEL
from backend.core.assist.answer import build_answer_request, finalize_answer
from backend.core.assist.assemble import NOT_US_ANSWER
from backend.core.assist.brief import Brief, is_us_patent
from backend.core.config import CHAT_MAX_TOKENS, AgentConfig
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


def replay_assist(
    system_prompt: str, brief: Brief, questions: list[str], *, model: str = ASSIST_MODEL
) -> list[dict]:
    """Replay questions against the assistant exactly as the chat route answers them.

    Each question goes through `build_answer_request` (with the earlier questions and their final answers
    as history), one model call and `finalize_answer`, the same functions the route uses. Returns the
    transcript of questions and answers only; the page text is not part of it. A page that is not a US
    patent gets the route's fixed answer without a model call.
    """
    if not questions:
        raise ValueError("replay needs at least one question")
    if len(questions) > MAX_REPLAY_USER_MESSAGES:
        raise ValueError(f"replay supports at most {MAX_REPLAY_USER_MESSAGES} questions")

    transcript: list[dict] = []
    for question in questions:
        if not is_us_patent(brief.patent_number):
            answer = NOT_US_ANSWER
        else:
            req = build_answer_request(brief, list(transcript), question, system_prompt)
            try:
                reply = groq_chat(req.system, req.messages, model=model, max_tokens=CHAT_MAX_TOKENS, temperature=0)
                if not reply.strip():
                    raise ValueError("empty model reply")
            except Exception as exc:
                raise ReplayFailed(f"replay chat call failed: {exc}") from exc
            answer, _ = finalize_answer(reply, req.intent, brief, req.provided_law_ids)
        transcript.append({"role": "user", "content": question})
        transcript.append({"role": "assistant", "content": answer})
    return transcript
