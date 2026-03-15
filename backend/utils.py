import re


def extract_json(text: str) -> str:
    """Strip <think> blocks and markdown fences that reasoning models may emit before JSON."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"```(?:json)?\s*([\s\S]*?)\s*```", r"\1", text)
    return text.strip()
