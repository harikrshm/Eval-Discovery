import json
from fastapi import APIRouter, HTTPException
from backend.models import RubricRequest, Rubric
from backend.groq_client import groq_chat, MODEL_RUBRIC
from backend.prompts import RUBRIC_GEN_SYSTEM, RUBRIC_GEN_USER_TEMPLATE
from backend.utils import extract_json

router = APIRouter()


@router.post("/generate-rubric", response_model=Rubric)
def generate_rubric(request: RubricRequest):
    try:
        raw = groq_chat(
            system=RUBRIC_GEN_SYSTEM,
            messages=[{
                "role": "user",
                "content": RUBRIC_GEN_USER_TEMPLATE.format(
                    pattern_summary=request.pattern_summary,
                ),
            }],
            model=MODEL_RUBRIC,
        )
        data = json.loads(extract_json(raw))
        return Rubric(**data)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Rubric generation unavailable, try again.") from exc
