import os
import re
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

MODEL_CHAT    = "openai/gpt-oss-20b"
MODEL_PATTERN = "openai/gpt-oss-120b"
MODEL_CRITERIA = "qwen/qwen3.8-27b"
MODEL_JUDGE = "openai/gpt-oss-120b"

_api_key = os.getenv("GROQ_API_KEY") or "sk-test-placeholder"
# Bound every call: the SDK defaults (10 min timeout) could hold the single job worker for minutes.
GROQ_TIMEOUT_SECONDS = 45.0
GROQ_MAX_RETRIES = 2


def _make_client(api_key: str) -> Groq:
    return Groq(api_key=api_key, timeout=GROQ_TIMEOUT_SECONDS, max_retries=GROQ_MAX_RETRIES)


client = _make_client(_api_key)


def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks that reasoning models may leak into responses."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def groq_chat(
    system: str,
    messages: list[dict],
    model: str = MODEL_CHAT,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> str:
    """Send messages to Groq and return the assistant reply text, stripped of thinking blocks."""
    full_messages = [{"role": "system", "content": system}] + messages
    kwargs = {"model": model, "messages": full_messages}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if temperature is not None:
        kwargs["temperature"] = temperature
    if model == MODEL_CRITERIA:
        kwargs["reasoning_format"] = "hidden"
    response = client.chat.completions.create(**kwargs)
    return _strip_thinking(response.choices[0].message.content)
