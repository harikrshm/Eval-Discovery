from pydantic import BaseModel, ConfigDict, field_validator
from typing import Literal


class Message(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal["user", "assistant", "system"]
    content: str


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    messages: list[Message]

    @field_validator("messages")
    @classmethod
    def messages_not_empty(cls, v):
        if not v:
            raise ValueError("messages must not be empty")
        return v


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reply: str


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    transcripts: list[list[Message]]
    run_count: int


class Rubric(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str
    definition: str
    good_example: str
    bad_example: str


class AnalyzeResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    intent: str
    rubric: Rubric
