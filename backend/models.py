from pydantic import BaseModel, field_validator
from typing import Literal


class Message(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]

    @field_validator("messages")
    @classmethod
    def messages_not_empty(cls, v):
        if not v:
            raise ValueError("messages must not be empty")
        return v


class ChatResponse(BaseModel):
    reply: str


class AnalyzeRequest(BaseModel):
    transcripts: list[list[Message]]
    run_count: int


class Rubric(BaseModel):
    name: str
    definition: str
    good_example: str
    bad_example: str


class AnalyzeResponse(BaseModel):
    intent: str
    rubric: Rubric
