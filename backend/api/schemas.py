"""Request/response models for the workspace-scoped API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.api.limits import (
    MAX_ASSISTANT_MESSAGE_CHARS,
    MAX_MESSAGE_CHARS,
    MAX_MESSAGES_PER_REQUEST,
    MAX_PROMPT_CHARS,
    MAX_REQUEST_CHARS,
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
    """A request message: user turns are capped at 2000 chars, assistant (model) turns at 16000."""

    role: Literal["user", "assistant"]
    content: str

    @model_validator(mode="after")
    def _content_within_role_cap(self) -> "Message":
        cap = MAX_MESSAGE_CHARS if self.role == "user" else MAX_ASSISTANT_MESSAGE_CHARS
        if len(self.content) > cap:
            raise ValueError(f"{self.role} messages must be at most {cap} characters")
        return self


class StoredMessage(BaseModel):
    """A message read back from storage: no length cap, so a response can never fail validation."""

    role: str
    content: str


def _check_total_size(messages: list[Message], max_chars: int = MAX_REQUEST_CHARS) -> None:
    if sum(len(m.content) for m in messages) > max_chars:
        raise ValueError(f"messages must total at most {max_chars} characters")


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
    prompt_version_id: str
    prompt_version_number: int
    behaviors: list[BehaviorOut]
    starter_messages: list[str]


class PromptUpdate(_Strict):
    text: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    # The version the edit was written against. When given, the server refuses the save if the
    # agent's current version has moved on, so a stale draft cannot overwrite a newer prompt.
    base_version_id: str | None = None

    @field_validator("text")
    @classmethod
    def _text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class PromptUpdated(BaseModel):
    version_id: str
    version_number: int
    text: str
    changed: bool


class PresetOut(BaseModel):
    id: str
    name: str
    user_role_label: str
    system_prompt: str
    starter_messages: list[str]


class RunOut(BaseModel):
    id: str
    position: int
    messages: list[StoredMessage]
    created_at: datetime


class RunCreate(_Strict):
    """A saved run is the chat request (at most MAX_MESSAGES_PER_REQUEST messages and MAX_REQUEST_CHARS
    characters) plus the reply to its last message, so it gets room for one more message and one more
    assistant message of text."""

    messages: list[Message] = Field(min_length=3, max_length=MAX_MESSAGES_PER_REQUEST + 1)

    @model_validator(mode="after")
    def _total_size(self) -> "RunCreate":
        _check_total_size(self.messages, MAX_REQUEST_CHARS + MAX_ASSISTANT_MESSAGE_CHARS)
        return self


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


class DiscoveredCriterionOut(CriterionOut):
    """A freshly discovered criterion plus the test-building job (null when the hourly cap blocked it)."""

    job_id: str | None


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
        _check_total_size(self.messages)
        if self.messages[-1].role != "user":
            raise ValueError("the last message must come from the user")
        return self


class ChatResponse(BaseModel):
    reply: str


class EvalResultOut(BaseModel):
    id: str
    kind: str
    verdict: str
    rationale: str
    transcript: list[StoredMessage] | None
    stale: bool
    prompt_version_id: str
    created_at: datetime


class EvalTestOut(BaseModel):
    id: str
    role: str
    source_run_id: str | None
    baseline: EvalResultOut | None
    current: EvalResultOut | None
    active_job_id: str | None
    origin: str  # "run" | "generated"
    can_suggest_fix: bool
    suggest_job_id: str | None


class CriterionSuiteOut(BaseModel):
    criterion_id: str
    status: str
    judge_check: dict | None
    active_job_id: str | None
    can_generate_heldout: bool
    heldout_job_id: str | None
    tests: list[EvalTestOut]


class CandidateProgress(BaseModel):
    done: int
    total: int


class CandidateOut(BaseModel):
    version_id: str
    number: int
    status: str
    origin: str
    strength: str | None
    created_at: datetime
    progress: CandidateProgress | None
    parent_number: int | None


class SuiteOut(BaseModel):
    prompt_version_id: str
    prompt_version_number: int
    stale_count: int
    candidate: CandidateOut | None
    criteria: list[CriterionSuiteOut]


class VersionOut(BaseModel):
    id: str
    number: int
    origin: str
    status: str
    strength: str | None
    created_at: datetime
    is_current: bool


class VersionDetailOut(VersionOut):
    text: str
    parent_text: str | None


class ApprovedJob(BaseModel):
    test_id: str
    job_id: str


class ApproveOut(BaseModel):
    version_id: str
    status: str
    jobs: list[ApprovedJob]


class RejectOut(BaseModel):
    version_id: str
    status: str


class ReportOut(BaseModel):
    version_id: str
    number: int
    status: str
    strength: str | None
    why: dict | None
    outcome: dict | None


class JobOut(BaseModel):
    id: str
    type: str
    status: str
    result: dict | None
    error: str | None


class JobAccepted(BaseModel):
    job_id: str


class RunAllOut(BaseModel):
    job_ids: list[str]
