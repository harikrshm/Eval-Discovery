from fastapi import APIRouter
from backend.models import ChatRequest, ChatResponse
from backend.groq_client import groq_chat
from backend.prompts import ALEX_SYSTEM_PROMPT

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    messages = [{"role": m.role, "content": m.content} for m in request.messages]
    reply = groq_chat(system=ALEX_SYSTEM_PROMPT, messages=messages)
    return ChatResponse(reply=reply)
