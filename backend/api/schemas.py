"""Request/response models for the workspace-scoped API."""

import re
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
from backend.core.assist.brief import Brief, is_us_patent
from backend.core.assist.intent import SECTION_NAMES
from backend.core.config import (  # noqa: F401  (single source in core.config; names kept here)
    ASSIST_MAX_CONVERSATION_TURNS,
    ASSIST_MAX_HISTORY_TURNS,
    ASSIST_MAX_REASON_CHARS,
    ASSIST_MAX_TURN_CHARS,
    ASSIST_SESSION_ID_RE,
    MAX_DURATION_MS,
    DurationMs,
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


class ExportAgentOut(BaseModel):
    name: str
    user_role_label: str


class ExportPromptOut(BaseModel):
    text: str
    version_number: int


class ExportTestOut(BaseModel):
    id: str
    role: str
    origin: str
    scripted_user_messages: list[str]
    current_verdict: Literal["pass", "fail", "not_applicable"] | None


class ExportCriterionOut(BaseModel):
    id: str
    name: str
    definition: str
    good_example: str
    bad_example: str
    tests: list[ExportTestOut]


class SuiteExportOut(BaseModel):
    """The `eval-discovery.suite` v1 document the CI gate consumes. Exactly these fields, nothing else."""

    format: Literal["eval-discovery.suite"] = "eval-discovery.suite"
    version: Literal[1] = 1
    agent: ExportAgentOut
    prompt: ExportPromptOut
    criteria: list[ExportCriterionOut]


# ---- Patent Assist -------------------------------------------------------------



class AssistTurn(_Strict):
    """A history turn: user turns are capped at 2000 chars; assistant turns (the model's earlier
    answers, which the user cannot shorten) at MAX_ASSISTANT_MESSAGE_CHARS, so any answer can be sent back."""

    role: Literal["user", "assistant"]
    content: str

    @model_validator(mode="after")
    def _content_within_role_cap(self) -> "AssistTurn":
        cap = ASSIST_MAX_TURN_CHARS if self.role == "user" else MAX_ASSISTANT_MESSAGE_CHARS
        if len(self.content) > cap:
            raise ValueError(f"{self.role} turns must be at most {cap} characters")
        return self


class AssistChatRequest(_Strict):
    """What the extension sends. The brief is untrusted page text, validated by its own caps."""

    session_id: str
    brief: Brief
    history: list[AssistTurn] = Field(default_factory=list, max_length=ASSIST_MAX_HISTORY_TURNS)
    question: str = Field(max_length=ASSIST_MAX_TURN_CHARS)

    @field_validator("session_id")
    @classmethod
    def _session_id_shape(cls, value: str) -> str:
        if ASSIST_SESSION_ID_RE.fullmatch(value) is None:
            raise ValueError("must be 8-64 characters: letters, digits and hyphens")
        return value

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def _total_size(self) -> "AssistChatRequest":
        total = len(self.question) + sum(len(t.content) for t in self.history)
        if total > MAX_REQUEST_CHARS:
            raise ValueError(f"history and question must total at most {MAX_REQUEST_CHARS} characters")
        return self


_QUESTION_ID_RE = re.compile(r"[A-Za-z0-9-]{1,36}", re.ASCII)


class AssistPageViewRequest(_Strict):
    """A tracker heartbeat. `bounce` is accepted for the extension's sake but the server recomputes it."""

    session_id: str
    patent_number: str
    dwell_ms: DurationMs
    sections: dict[str, DurationMs] = Field(default_factory=dict)
    bounce: bool = False

    @field_validator("session_id")
    @classmethod
    def _session_id_shape(cls, value: str) -> str:
        if ASSIST_SESSION_ID_RE.fullmatch(value) is None:
            raise ValueError("must be 8-64 characters: letters, digits and hyphens")
        return value

    @field_validator("patent_number")
    @classmethod
    def _us_patent_only(cls, value: str) -> str:
        if not is_us_patent(value):
            raise ValueError("must be a US patent number")
        return value.strip()

    @field_validator("sections")
    @classmethod
    def _known_sections(cls, value: dict[str, int]) -> dict[str, int]:
        if any(name not in SECTION_NAMES for name in value):
            raise ValueError("unknown section name")
        return value


class _AssistConversationSignal(_Strict):
    """Shared by ratings and re-asks: the question it is about, the conversation to flag and the page brief."""

    question_id: str
    conversation: list[AssistTurn] = Field(max_length=ASSIST_MAX_CONVERSATION_TURNS)
    brief: Brief

    @field_validator("question_id")
    @classmethod
    def _question_id_shape(cls, value: str) -> str:
        if _QUESTION_ID_RE.fullmatch(value) is None:
            raise ValueError("must be an id of at most 36 letters, digits and hyphens")
        return value

    @model_validator(mode="after")
    def _total_size(self) -> "_AssistConversationSignal":
        if sum(len(t.content) for t in self.conversation) > MAX_REQUEST_CHARS:
            raise ValueError(f"conversation must total at most {MAX_REQUEST_CHARS} characters")
        return self


class AssistRatingRequest(_AssistConversationSignal):
    rating: Literal["up", "down", "no_answer"]
    reason: str | None = Field(default=None, max_length=ASSIST_MAX_REASON_CHARS)

    @model_validator(mode="after")
    def _flagged_rating_needs_a_conversation(self) -> "AssistRatingRequest":
        if self.rating != "up" and not self.conversation:
            raise ValueError("a flagged rating needs the conversation")
        return self


class AssistReaskRequest(_AssistConversationSignal):
    @model_validator(mode="after")
    def _needs_a_conversation(self) -> "AssistReaskRequest":
        if not self.conversation:
            raise ValueError("a re-ask needs the conversation")
        return self


class CitationCheckOut(BaseModel):
    ok: bool
    unverified_claims: list[int]
    unverified_law: list[str]
    unverified_paragraphs: list[str] = []
    unverified_sections: list[str] = []


class ReferenceOut(BaseModel):
    """A validated claim, paragraph or section reference; offsets are UTF-16 units into `answer`."""

    start: int
    end: int
    kind: Literal["claim", "paragraph", "section"]
    target: str
    label: str


class AssistChatResponse(BaseModel):
    answer: str
    question_id: str
    intent: str
    assist_version: int
    citation_check: CitationCheckOut
    references: list[ReferenceOut] = []


class AssistConsoleStatusOut(BaseModel):
    agent_id: str
    agent_name: str
    current_version_number: int | None
    run_count: int
    flagged_run_count: int
    imported_run_count: int


class AssistConsoleMetricsOut(BaseModel):
    adoption: dict
    version_comparison: dict
    data_source_label: str | None
    sources: dict


class TrendCountsOut(BaseModel):
    """Counts only: page views, questions, re-asked questions and thumbs-down questions."""

    page_views: int
    questions: int
    reasked: int
    rated_down: int


class TrendPointOut(TrendCountsOut):
    t: str  # ISO start of the bucket, naive UTC


class VersionMarkerOut(BaseModel):
    at: str  # ISO, naive UTC
    version: int
    source: Literal["live", "demo"]
    label: str


class AssistConsoleTrendsOut(BaseModel):
    bucket: Literal["hour", "day"]
    totals: TrendCountsOut
    previous: TrendCountsOut | None
    points: list[TrendPointOut]
    version_markers: list[VersionMarkerOut]


class GradePointOut(BaseModel):
    t: str  # ISO start of the bucket, naive UTC
    avg_grade: float | None
    n: int


class QualityOut(BaseModel):
    """The automatic judge's verdicts in aggregate: counts, shares and fixed labels only."""

    judged: int
    unjudged: int
    grade_trend: list[GradePointOut]
    answered: dict[str, int]
    concise_share: float | None
    citations_share: float | None
    legal: dict[str, int]
    reasons: dict[str, int]
    ratings: dict[str, int]
    bucket: Literal["hour", "day"]
    version_markers: list[VersionMarkerOut]


class LatencyPointOut(BaseModel):
    t: str
    p50_ms: int | None
    p95_ms: int | None
    n: int


class OutcomePointOut(BaseModel):
    t: str
    ok: int
    error: int
    rate_limited: int
    timeout: int


class RequestPointOut(BaseModel):
    t: str
    n: int


class OperationsTotalsOut(BaseModel):
    calls: int
    error_rate: float | None
    rate_limit_rate: float | None
    p50_ms: int | None
    p95_ms: int | None
    tokens: int


class FailedJobOut(BaseModel):
    """A failed background job: ids, type, time and the fixed error message only, never its payload."""

    id: str
    type: str
    created_at: datetime
    error: str | None


class OperationsOut(BaseModel):
    bucket: Literal["hour", "day"]
    latency: list[LatencyPointOut]
    outcomes: list[OutcomePointOut]
    requests: list[RequestPointOut]
    tokens_by_kind: dict[str, int]
    totals: OperationsTotalsOut
    judge_queue: dict
    recent_failed_jobs: list[FailedJobOut]


class AlertOut(BaseModel):
    id: str
    severity: Literal["warning", "critical"]
    title: str
    detail: str
    tab: str
    anchor: str
    demo: bool


class KpiOut(BaseModel):
    """One overview tile. `unit` is count | percent | version; a percent value is 0-100 and its delta is in points."""

    id: str
    label: str
    value: int | float | str | None
    unit: Literal["count", "percent", "version"]
    delta: float | None
    delta_good: Literal["up", "down"]
    sparkline: list[float | None]


class PipelineStepOut(BaseModel):
    id: str
    label: str
    state: Literal["done", "current", "todo"]
    detail: str | None


class PipelineOut(BaseModel):
    steps: list[PipelineStepOut]
    cycle_note: str


class QualityPointOut(BaseModel):
    t: str
    avg_grade: float | None


class OverviewOut(BaseModel):
    versions: list[int]
    kpis: list[KpiOut]
    alerts: list[AlertOut]
    loop: PipelineOut
    quality_trend: list[QualityPointOut]
    version_markers: list[VersionMarkerOut]


class JudgeEvaluateOut(BaseModel):
    """What "Evaluate now" did: judge jobs queued (0 or 1) and conversations still waiting."""

    queued: int
    waiting: int


class AssistInsightsOut(BaseModel):
    """A model-written brief with the counts it was given and whether every figure in it checks out."""

    brief: str
    counts_used: dict
    data_source_label: str | None
    grounded: bool
    warnings: list[str]


class FlaggedQuestionOut(BaseModel):
    """Labels and flags only: never the question or answer text."""

    question_id: str
    intent: str
    section_focus: str | None
    rating: str | None
    reasked: bool
    citation_ok: bool
    assist_version: int


class FlaggedRunOut(BaseModel):
    """A flagged assistant conversation: ids and labels only, never the messages or the brief."""

    run_id: str
    position: int
    source: str
    session_id: str | None
    patent_number: str | None
    intents: list[str]
    sections: list[str]
    reasons: list[str]
    created_at: datetime
    questions: list[FlaggedQuestionOut]
