import json
import logging
from fastapi import APIRouter, HTTPException
from backend.models import PatternCheckRequest, PatternCheckResponse
from backend.groq_client import groq_chat, MODEL_PATTERN
from backend.prompts import ALEX_SYSTEM_PROMPT, PATTERN_CHECK_SYSTEM, PATTERN_CHECK_USER_TEMPLATE, format_transcripts
from backend.utils import extract_json

logger = logging.getLogger(__name__)

router = APIRouter()

MIN_TURNS_PER_TRANSCRIPT = 3


@router.post("/check-pattern", response_model=PatternCheckResponse)
def check_pattern(request: PatternCheckRequest):
    if not request.transcripts:
        raise HTTPException(status_code=400, detail="transcripts must not be empty.")

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
        raw = groq_chat(
            system=PATTERN_CHECK_SYSTEM,
            messages=[{
                "role": "user",
                "content": PATTERN_CHECK_USER_TEMPLATE.format(
                    system_prompt=ALEX_SYSTEM_PROMPT,
                    transcripts=formatted,
                ),
            }],
            model=MODEL_PATTERN,
        )
        data = json.loads(extract_json(raw))
        return PatternCheckResponse(**data)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Pattern check failed")
        raise HTTPException(status_code=502, detail="Pattern check unavailable, try again.") from exc
