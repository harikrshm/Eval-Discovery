import re


def extract_json(text: str) -> str:
    """Strip <think> blocks and markdown fences that reasoning models may emit before JSON.
    Falls back to extracting the first {...} object if the model wrapped JSON in prose."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"```(?:json)?\s*([\s\S]*?)\s*```", r"\1", text)
    text = text.strip()
    # If surrounded by prose, extract the first JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        text = text[start:end + 1]
    return text
