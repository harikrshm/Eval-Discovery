import os
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

MODEL = "qwen-qwq-32b"

# Initialize with a default key if not set to avoid initialization errors in tests
_api_key = os.getenv("GROQ_API_KEY") or "sk-test-placeholder"
client = Groq(api_key=_api_key)


def groq_chat(system: str, messages: list[dict]) -> str:
    """Send messages to Groq and return the assistant reply text."""
    full_messages = [{"role": "system", "content": system}] + messages
    response = client.chat.completions.create(
        model=MODEL,
        messages=full_messages,
    )
    return response.choices[0].message.content
