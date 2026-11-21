"""Request/response models for the workspace-scoped API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.api.limits import (
    MAX_MESSAGE_CHARS,
    MAX_MESSAGES_PER_REQUEST,
    MAX_PROMPT_CHARS,
)

MAX_NAME_CHARS = 60
MAX_ROLE_LABEL_CHARS = 40


def sanitize_label(value: str, max_chars: int) -> str:
    """Strip, collapse whitespace to single spaces, reject `<`/`>`; raises ValueError."""
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError("must not be blank")
    if "<" in cleaned or ">" in cleaned:
        raise ValueError("must not contain '<' or '>'")
    if len(cleaned) > max_chars:
        raise ValueError(f"must be at most {max_chars} characters")
    return cleaned


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Message(_Strict):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_MESSAGE_CHARS)


class AgentCreate(_Strict):
    preset_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME_CHARS)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=MAX_PROMPT_CHARS)
    user_role_label: str | None = Field(default=None, min_length=1, max_length=MAX_ROLE_LABEL_CHARS)

    @field_validator("system_prompt")
    @classmethod
    def _prompt_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        return None if value is None else sanitize_label(value, MAX_NAME_CHARS)

    @field_validator("user_role_label")
    @classmethod
    def _clean_label(cls, value: str | None) -> str | None:
        return None if value is None else sanitize_label(value, MAX_ROLE_LABEL_CHARS)

    @model_validator(mode="after")
    def _exactly_one_mode(self) -> "AgentCreate":
        if self.preset_id is not None:
            if self.name is not None or self.system_prompt is not None or self.user_role_label is not None:
                raise ValueError("preset_id cannot be combined with name, system_prompt or user_role_label")
        elif self.name is None or self.system_prompt is None:
            raise ValueError("provide either preset_id or both name and system_prompt")
        return self


class BehaviorOut(BaseModel):
    name: str
    description: str
    trigger_terms: list[str]


class AgentOut(BaseModel):
    id: str
    name: str
    user_role_label: str
    system_prompt: str
    behaviors: list[BehaviorOut]
    starter_messages: list[str]


class PresetOut(BaseModel):
    id: str
    name: str
    user_role_label: str
    system_prompt: str
    starter_messages: list[str]


class RunOut(BaseModel):
    id: str
    position: int
    messages: list[Message]
    created_at: datetime


class RunCreate(_Strict):
    messages: list[Message] = Field(min_length=3, max_length=MAX_MESSAGES_PER_REQUEST)


class RunSaved(BaseModel):
    run: RunOut
    run_count: int
    check_due: bool


class PatternCheckOut(BaseModel):
    found: bool
    pattern_summary: str


class CriterionOut(BaseModel):
    id: str
    name: str
    definition: str
    good_example: str
    bad_example: str
    why_discovered: str
    source: str


MAX_CRITERION_NAME_CHARS = 200
MAX_CRITERION_TEXT_CHARS = 2000


class CriterionCreate(_Strict):
    name: str = Field(min_length=1, max_length=MAX_CRITERION_NAME_CHARS)
    definition: str = Field(max_length=MAX_CRITERION_TEXT_CHARS)
    good_example: str = Field(max_length=MAX_CRITERION_TEXT_CHARS)
    bad_example: str = Field(max_length=MAX_CRITERION_TEXT_CHARS)


class CriterionUpdate(_Strict):
    """Partial update: only the fields present in the body are changed."""

    name: str | None = Field(default=None, min_length=1, max_length=MAX_CRITERION_NAME_CHARS)
    definition: str | None = Field(default=None, max_length=MAX_CRITERION_TEXT_CHARS)
    good_example: str | None = Field(default=None, max_length=MAX_CRITERION_TEXT_CHARS)
    bad_example: str | None = Field(default=None, max_length=MAX_CRITERION_TEXT_CHARS)
    why_discovered: str | None = Field(default=None, max_length=MAX_CRITERION_TEXT_CHARS)

    @model_validator(mode="after")
    def _no_explicit_null(self) -> "CriterionUpdate":
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} must not be null")
        return self


class AgentState(BaseModel):
    agent: AgentOut
    runs: list[RunOut]
    criteria: list[CriterionOut]
    pattern_summary: str | None
    run_count: int
    last_checked_at_run: int


class ChatRequest(_Strict):
    messages: list[Message] = Field(min_length=1, max_length=MAX_MESSAGES_PER_REQUEST)

    @model_validator(mode="after")
    def _last_is_user(self) -> "ChatRequest":
        if self.messages[-1].role != "user":
            raise ValueError("the last message must come from the user")
        return self


class ChatResponse(BaseModel):
    reply: str
