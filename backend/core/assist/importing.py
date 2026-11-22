"""Validation and expansion of hand-written Patent Assist page-view records (JSON Lines). Pure: no I/O.

One record is one page view (one visitor session on one patent) plus the questions asked in it. The importer
(scripts/import_page_views.py) validates every line with `validate_record`, then turns each record into the
rows the console reads with `expand`. Nothing here calls a model.

The caps are the live API's, taken from the same constants (backend/api/schemas.py and
backend/core/config.py), so an imported row could always have been written by the live server:
a session id of 8-64 ASCII letters, digits and hyphens; durations of 0..86,400,000 ms; only the seven section
names; questions of at most 2000 characters and answers of at most MAX_ASSISTANT_MESSAGE_CHARS; reasons of
at most 500; US patent numbers only. Unknown keys are rejected at every level, and every error names its
1-based line number and the field.

A question is FLAGGED when it was rated `down` or `no_answer`, re-asked, or its citations failed the check.
A flagged question needs the record's `brief` (the page it was asked on), so the conversation can be saved as
a run exactly like the live server does. A record with flagged questions also has to fit the live conversation
caps (ASSIST_MAX_CONVERSATION_TURNS turns, MAX_REQUEST_CHARS characters).
"""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    ValidationError,
    field_validator,
)

from backend.api.limits import MAX_REQUEST_CHARS
from backend.api.schemas import (
    ASSIST_MAX_CONVERSATION_TURNS,
    ASSIST_MAX_REASON_CHARS,
    ASSIST_MAX_TURN_CHARS,
    DurationMs,
)
from backend.core.assist.bounce import is_bounce
from backend.core.assist.brief import Brief, is_us_patent
from backend.core.assist.intent import SECTION_NAMES, Intent, classify_intent, primary_section
from backend.core.config import MAX_ASSISTANT_MESSAGE_CHARS

IMPORTED_SOURCE = "imported"
MAX_QUESTIONS_PER_RECORD = 40
MAX_ASSIST_VERSION = 1_000_000
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9-]{8,64}", re.ASCII)  # the live pattern (a test pins the parity)


# ---- the record format ------------------------------------------------------------------------------


def _iso_string(value: Any) -> Any:
    if not isinstance(value, str):
        raise ValueError("must be an ISO 8601 date-time string, for example 2026-11-02T09:14:03Z")
    return value


def _to_utc_naive(value: datetime) -> datetime:
    """Aware timestamps are converted to UTC; naive ones are read as UTC. The database stores naive UTC."""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


Timestamp = Annotated[datetime, BeforeValidator(_iso_string), AfterValidator(_to_utc_naive)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImportQuestion(_Strict):
    text: Annotated[StrictStr, Field(min_length=1, max_length=ASSIST_MAX_TURN_CHARS)]
    answer: Annotated[StrictStr, Field(min_length=1, max_length=MAX_ASSISTANT_MESSAGE_CHARS)]
    assist_version: Annotated[int, Field(strict=True, ge=1, le=MAX_ASSIST_VERSION)]
    intent: Intent | None = None
    section_focus: StrictStr | None = None
    rating: Literal["up", "down", "no_answer"] | None = None
    rating_reason: Annotated[StrictStr, Field(max_length=ASSIST_MAX_REASON_CHARS)] | None = None
    reasked: StrictBool = False
    citation_ok: StrictBool = True
    asked_at: Timestamp | None = None

    @field_validator("text")
    @classmethod
    def _text_not_blank(cls, value: str) -> str:
        value = value.strip()  # the live API strips the question
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("answer")
    @classmethod
    def _answer_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("section_focus")
    @classmethod
    def _known_section(cls, value: str | None) -> str | None:
        if value is not None and value not in SECTION_NAMES:
            raise ValueError(f"unknown section {value!r}; allowed: {', '.join(SECTION_NAMES)}")
        return value


class ImportRecord(_Strict):
    session_id: StrictStr
    patent_number: StrictStr
    started_at: Timestamp
    dwell_ms: DurationMs
    sections: dict[StrictStr, DurationMs] = Field(default_factory=dict)
    brief: Brief | None = None
    questions: list[ImportQuestion] = Field(default_factory=list, max_length=MAX_QUESTIONS_PER_RECORD)

    @field_validator("session_id")
    @classmethod
    def _session_id_shape(cls, value: str) -> str:
        if _SESSION_ID_RE.fullmatch(value) is None:
            raise ValueError("must be 8-64 characters: letters, digits and hyphens")
        return value

    @field_validator("patent_number")
    @classmethod
    def _us_patent_only(cls, value: str) -> str:
        if not is_us_patent(value):
            raise ValueError("must be a US patent number such as US12401720B1")
        return value.strip().upper()

    @field_validator("sections")
    @classmethod
    def _known_sections(cls, value: dict[str, int]) -> dict[str, int]:
        unknown = [name for name in value if name not in SECTION_NAMES]
        if unknown:
            raise ValueError(f"unknown section {unknown[0]!r}; allowed: {', '.join(SECTION_NAMES)}")
        return value


# ---- validation ---------------------------------------------------------------------------------------


class ImportValidationError(ValueError):
    """A record that cannot be imported. `errors` holds one "line N: field: message" string per problem."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _field_path(loc: tuple) -> str:
    return ".".join(str(part) for part in loc) or "record"


def _describe(error: dict) -> str:
    """One pydantic error as "field: message" (never echoing the offending value, which can be long)."""
    kind = error["type"]
    if kind == "missing":
        message = "required"
    elif kind == "extra_forbidden":
        message = "unknown field (not allowed)"
    else:
        message = error["msg"].removeprefix("Value error, ")
    return f"{_field_path(error['loc'])}: {message}"


def question_flags(question: ImportQuestion) -> list[str]:
    """Why a question counts as flagged (empty when it does not)."""
    reasons = []
    if question.rating in ("down", "no_answer"):
        reasons.append(f"rating {question.rating}")
    if question.reasked:
        reasons.append("reasked")
    if not question.citation_ok:
        reasons.append("citation_ok false")
    return reasons


def _record_problems(record: ImportRecord) -> list[str]:
    """Rules that span several fields, each as "field: message"."""
    problems = []
    for index, question in enumerate(record.questions):
        if question.rating_reason is not None and question.rating is None:
            problems.append(f"questions.{index}.rating_reason: needs a rating")
    flagged = [(i, question_flags(q)) for i, q in enumerate(record.questions) if question_flags(q)]
    if record.brief is not None and record.brief.patent_number.strip().upper() != record.patent_number:
        problems.append(f"brief.patent_number: must equal patent_number ({record.patent_number})")
    if flagged:
        first_index, reasons = flagged[0]
        if record.brief is None:
            problems.append(
                f"brief: required because question {first_index + 1} is flagged ({', '.join(reasons)}); "
                "a flagged conversation is saved with the page it was asked on"
            )
        if 2 * len(record.questions) > ASSIST_MAX_CONVERSATION_TURNS:
            problems.append(
                f"questions: a record with a flagged question can have at most {ASSIST_MAX_CONVERSATION_TURNS // 2} "
                f"questions (the live conversation cap of {ASSIST_MAX_CONVERSATION_TURNS} turns)"
            )
        elif sum(len(q.text) + len(q.answer) for q in record.questions) > MAX_REQUEST_CHARS:
            problems.append(
                f"questions: a record with a flagged question can hold at most {MAX_REQUEST_CHARS} characters "
                "of questions and answers (the live conversation cap)"
            )
    return problems


def validate_record(obj: Any, line_no: int) -> ImportRecord:
    """The record for one parsed JSON line; raises ImportValidationError naming the line and each bad field."""
    prefix = f"line {line_no}: "
    if not isinstance(obj, dict):
        raise ImportValidationError([prefix + "the record must be a JSON object"])
    try:
        record = ImportRecord.model_validate(obj)
    except ValidationError as exc:
        raise ImportValidationError([prefix + _describe(e) for e in exc.errors()]) from None
    problems = _record_problems(record)
    if problems:
        raise ImportValidationError([prefix + p for p in problems])
    return record


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


@dataclass
class ParseResult:
    records: list[tuple[int, ImportRecord]] = field(default_factory=list)  # (1-based line number, record)
    errors: list[str] = field(default_factory=list)


def parse_lines(lines: Iterable[str]) -> ParseResult:
    """Validate every line; blank lines are skipped but still counted. Collects ALL errors, never stops early.

    A session id may appear on one line only: a second occurrence is an error naming the first line.
    """
    result = ParseResult()
    first_line: dict[str, int] = {}
    for line_no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line, object_pairs_hook=_reject_duplicate_keys)
        except json.JSONDecodeError as exc:
            result.errors.append(f"line {line_no}: invalid JSON ({exc.msg} at column {exc.colno})")
            continue
        except ValueError as exc:  # a duplicate key
            result.errors.append(f"line {line_no}: invalid JSON ({exc})")
            continue
        try:
            record = validate_record(obj, line_no)
        except ImportValidationError as exc:
            result.errors.extend(exc.errors)
            continue
        if record.session_id in first_line:
            result.errors.append(
                f"line {line_no}: session_id: {record.session_id!r} already appears on line "
                f"{first_line[record.session_id]}"
            )
            continue
        first_line[record.session_id] = line_no
        result.records.append((line_no, record))
    return result


# ---- expansion ----------------------------------------------------------------------------------------


@dataclass
class PageViewRow:
    session_id: str
    patent_number: str
    started_at: datetime
    updated_at: datetime
    dwell_ms: int
    sections: dict
    bounce: bool
    assist_version: int | None  # the version first seen in the session; None when it had no questions
    source: str = IMPORTED_SOURCE


@dataclass
class QuestionRow:
    session_id: str
    patent_number: str
    text: str
    answer: str
    intent: str
    section_focus: str | None
    rating: str | None
    rating_reason: str | None
    reasked: bool
    citation_ok: bool
    assist_version: int
    created_at: datetime
    source: str = IMPORTED_SOURCE


@dataclass
class FlaggedConversation:
    session_id: str
    messages: list[dict]  # [{role, content}] for the session's turns in order
    brief: dict
    started_at: datetime
    source: str = IMPORTED_SOURCE


def expand(record: ImportRecord) -> tuple[PageViewRow, list[QuestionRow], list[FlaggedConversation]]:
    """The rows one record stands for.

    A missing `intent` is `classify_intent(text)` and a missing `section_focus` is `primary_section(intent)`
    (an explicit null keeps "no section"). `bounce` uses the live rule (`is_bounce`). A question's
    `created_at` is its `asked_at`, else `started_at` plus its 1-based position in seconds. A session with a
    flagged question yields one FlaggedConversation: every turn, user question then answer, in order, plus
    the brief.
    """
    sections = dict(record.sections)
    first_version = record.questions[0].assist_version if record.questions else None
    view = PageViewRow(
        session_id=record.session_id,
        patent_number=record.patent_number,
        started_at=record.started_at,
        updated_at=record.started_at,
        dwell_ms=record.dwell_ms,
        sections=sections,
        bounce=is_bounce(record.dwell_ms, sections),
        assist_version=first_version,
    )
    rows = []
    for number, question in enumerate(record.questions, 1):
        intent = question.intent if question.intent is not None else classify_intent(question.text)
        if "section_focus" in question.model_fields_set:
            section_focus = question.section_focus
        else:
            section_focus = primary_section(intent)
        rows.append(
            QuestionRow(
                session_id=record.session_id,
                patent_number=record.patent_number,
                text=question.text,
                answer=question.answer,
                intent=intent.value,
                section_focus=section_focus,
                rating=question.rating,
                rating_reason=question.rating_reason,
                reasked=question.reasked,
                citation_ok=question.citation_ok,
                assist_version=question.assist_version,
                created_at=question.asked_at or record.started_at + timedelta(seconds=number),
            )
        )
    flagged = []
    if record.brief is not None and any(question_flags(q) for q in record.questions):
        messages = []
        for question in record.questions:
            messages.append({"role": "user", "content": question.text})
            messages.append({"role": "assistant", "content": question.answer})
        flagged.append(
            FlaggedConversation(
                session_id=record.session_id,
                messages=messages,
                brief=record.brief.model_dump(mode="json"),
                started_at=record.started_at,
            )
        )
    return view, rows, flagged
