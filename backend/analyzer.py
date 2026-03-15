import json
import re
from fastapi import APIRouter, HTTPException
from backend.models import AnalyzeRequest, AnalyzeResponse, Rubric
from backend.groq_client import groq_chat
from backend.prompts import (
    INTENT_DETECTION_SYSTEM,
    INTENT_DETECTION_USER_TEMPLATE,
    RUBRIC_GENERATION_SYSTEM,
    RUBRIC_GENERATION_USER_TEMPLATE,
    format_transcripts,
)

router = APIRouter()

MIN_TURNS_PER_TRANSCRIPT = 3


def extract_json(text: str) -> str:
    """Strip <think> blocks and markdown fences that reasoning models may emit before JSON."""
    # Remove <think>...</think> blocks (qwen-qwq-32b emits these)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # Strip ```json ... ``` or ``` ... ``` markdown fences
    text = re.sub(r"```(?:json)?\s*([\s\S]*?)\s*```", r"\1", text)
    return text.strip()


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest):
    # Validate transcripts not empty
    if not request.transcripts:
        raise HTTPException(status_code=400, detail="transcripts must not be empty.")

    # Validate each transcript meets minimum length
    for i, transcript in enumerate(request.transcripts):
        if len(transcript) < MIN_TURNS_PER_TRANSCRIPT:
            raise HTTPException(
                status_code=400,
                detail=f"Transcript {i + 1} is too short — have a longer conversation first (minimum {MIN_TURNS_PER_TRANSCRIPT} turns).",
            )

    transcripts_as_dicts = [
        [{"role": m.role, "content": m.content} for m in transcript]
        for transcript in request.transcripts
    ]
    formatted = format_transcripts(transcripts_as_dicts)

    try:
        # Step 1: Detect intent
        intent_raw = groq_chat(
            system=INTENT_DETECTION_SYSTEM,
            messages=[{
                "role": "user",
                "content": INTENT_DETECTION_USER_TEMPLATE.format(
                    n=len(request.transcripts),
                    transcripts=formatted,
                ),
            }],
        )
        intent_data = json.loads(extract_json(intent_raw))
        intent = intent_data["intent"]

        # Step 2: Generate rubric
        rubric_raw = groq_chat(
            system=RUBRIC_GENERATION_SYSTEM,
            messages=[{
                "role": "user",
                "content": RUBRIC_GENERATION_USER_TEMPLATE.format(
                    intent=intent,
                    transcripts=formatted,
                ),
            }],
        )
        rubric_data = json.loads(extract_json(rubric_raw))
        rubric = Rubric(**rubric_data)

    except Exception as exc:
        raise HTTPException(status_code=502, detail="Analysis unavailable, try again.") from exc

    return AnalyzeResponse(intent=intent, rubric=rubric)
