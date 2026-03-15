from pydantic import BaseModel, ConfigDict, field_validator
from typing import Literal


class Message(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal["user", "assistant"]
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


class EvalCriteria(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str
    definition: str
    good_example: str
    bad_example: str
    why_discovered: str


class PatternCheckRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    transcripts: list[list[Message]]


class PatternCheckResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    found: bool           # MUST be declared first — validator below reads it via info.data
    pattern_summary: str  # Pydantic v2 validates fields in declaration order; info.data only contains prior fields

    @field_validator("pattern_summary")
    @classmethod
    def non_empty_when_found(cls, v, info):
        if info.data.get("found") and not v.strip():
            raise ValueError("pattern_summary must be non-empty when found=True")
        return v


class CriteriaRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pattern_summary: str

    @field_validator("pattern_summary")
    @classmethod
    def non_empty(cls, v):
        if not v.strip():
            raise ValueError("pattern_summary must not be empty")
        return v
