import json
import logging
from fastapi import APIRouter, HTTPException
from backend.models import CriteriaRequest, EvalCriteria
from backend.groq_client import groq_chat, MODEL_RUBRIC
from backend.prompts import RUBRIC_GEN_SYSTEM, RUBRIC_GEN_USER_TEMPLATE
from backend.utils import extract_json

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/generate-criteria", response_model=EvalCriteria)
def generate_criteria(request: CriteriaRequest):
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
        # Normalize dialogue fields: model sometimes returns a list of speaker objects
        for field in ("good_example", "bad_example"):
            if isinstance(data.get(field), list):
                data[field] = " ".join(
                    f"{turn.get('speaker', '')}: '{turn.get('text', turn.get('content', ''))}'"
                    for turn in data[field]
                )
        return EvalCriteria(**data)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Criteria generation failed")
        raise HTTPException(status_code=502, detail="Criteria generation unavailable, try again.") from exc
