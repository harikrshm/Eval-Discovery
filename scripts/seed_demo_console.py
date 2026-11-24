"""Seed a labelled 30-day demo dataset for the Patent Assist console.

Usage: python scripts/seed_demo_console.py --confirm [--end YYYY-MM-DD] [--seed N] [--database-url URL]
       python scripts/seed_demo_console.py --remove --confirm [--database-url URL]

Writes, into the workspace of the configured assist agent (ASSIST_AGENT_ID), about 30 days of generated page
views, questions, model-call telemetry, conversation judgements and flagged runs, all with source "imported" and
(except the calls) session ids starting `demo30-`. The data tells one story so the console has something to
show: a weekday/weekend rhythm, a latency bump on days 8-10, a rate-limit burst on day 15, a quality dip on days
14-20 (judge grades and thumbs-up down, re-asks up) answered by a "version 2" marker on day 20, and a "version 3"
on day 27.

The assist versions 1, 2 and 3 are demo-only labels: the script never creates or changes a prompt version, and
the agent's current version stays as it is. Question and answer text are short canned templates; the judgements
carry model "demo-seed" and rubric "demo". The script never calls a model. It is deterministic: the same --seed,
--end and agent give the same rows (apart from the generated run ids).

Without --confirm it prints what it would write and exits 2; nothing is written. A re-run deletes the previous
seed first (delete, then insert), so running it twice leaves the same rows. --remove deletes everything the seed
wrote: the rows of this workspace with a `demo30-` session id and source "imported", and ALL of its imported
model calls (calls with source "imported" are only ever written by this script). It never touches rows without
the prefix (such as the hand-written example import) or live rows; a live row that uses the prefix aborts it.

The database is --database-url, else $DATABASE_URL, else the local SQLite file. The password is masked in all
output and no driver error text is ever echoed (it can carry the connection URL).

Exit codes: 0 ok, 2 invalid input (bad --end, no --confirm, a live row using the prefix), 3 database or
configuration error (no assist agent, schema out of date, connection failure, a failed write).
"""

import argparse
import math
import os
import random
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `python scripts/seed_demo_console.py` finds `backend`

from sqlalchemy import delete, func, inspect, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend.core.assist.bounce import is_bounce  # noqa: E402
from backend.core.assist.convo_judge import FLAG_GRADE_AT_OR_BELOW, REASONS  # noqa: E402
from backend.core.assist.importing import IMPORTED_SOURCE  # noqa: E402
from backend.core.assist.intent import SECTION_NAMES, Intent, primary_section  # noqa: E402
from backend.db.assist import get_assist_agent, save_flagged_run  # noqa: E402
from backend.db.models import (  # noqa: E402
    AssistCall,
    AssistJudgement,
    AssistPageView,
    AssistQuestion,
    Run,
)
from backend.db.session import DEFAULT_DATABASE_URL, make_engine  # noqa: E402
from scripts.metrics import mask_url  # noqa: E402

EXIT_OK, EXIT_INVALID, EXIT_DATABASE = 0, 2, 3
PREFIX = "demo30-"
DEMO_MODEL = "demo-seed"
DEMO_RUBRIC = "demo"
REQUIRED_TABLES = ("agent", "assist_page_view", "assist_question", "assist_call", "assist_judgement", "run")
NO_AGENT = (
    "the assist agent is not configured; run scripts/setup_assist.py, then set ASSIST_AGENT_ID "
    "to the agent id it prints"
)

# ---- the storyline (day 1 is 29 days before --end, day 30 is --end) ------------------------------------------

DAYS = 30
DIP_FIRST_DAY, MARKER_V2_DAY, MARKER_V3_DAY = 14, 20, 27  # the dip runs from day 14 until the version-2 marker
MARKER_HOUR = 12  # the marker takes effect at noon UTC of its day
LATENCY_DAYS = (8, 10)
LATENCY_FACTOR = 2.2
BURST_DAY = 15
BURST_CALLS = 9
FLAGGED_RUNS = 25
QUESTION_SHARE = 0.5  # share of page views that ask something

# ---- canned content ------------------------------------------------------------------------------------------

BRIEFS = {
    "US12401720B1": (
        "Demo: system and method for server based control",
        "A building or vehicle system in which a router passes sensor data to a control server that decides how an actuator should respond.",
    ),
    "US11893021B2": (
        "Demo: wireless sensor device",
        "A battery-powered enclosure holding a sensor, a radio and a small processor that reports readings to a gateway.",
    ),
    "US12120894B2": (
        "Demo: method of routing network traffic",
        "A method that picks a network path for each packet flow based on measured delay and cost.",
    ),
    "US11736012B1": (
        "Demo: modular storage container",
        "A stackable container with a locking frame, sized to fit standard shelving.",
    ),
    "US12254317B2": (
        "Demo: battery pack thermal control",
        "A battery pack with a coolant channel and a controller that limits charging current by cell temperature.",
    ),
    "US11960204B2": (
        "Demo: image compression apparatus",
        "An encoder that splits an image into regions and applies a different quantisation step to each region.",
    ),
    "US12080315B1": (
        "Demo: voice assistant device",
        "A speaker device with several microphones that wakes on a spoken phrase and answers by voice.",
    ),
    "US11587463B2": (
        "Demo: surgical instrument handle",
        "A handle with a rotating collar that locks the angle of an instrument shaft.",
    ),
}
PATENTS = tuple(BRIEFS)
PATENT_WEIGHTS = (30, 18, 14, 10, 9, 8, 6, 5)

INTENT_WEIGHTS = (
    (Intent.claim_explain, 25),
    (Intent.claim_scope, 15),
    (Intent.status_family, 12),
    (Intent.citations_prior_art, 12),
    (Intent.law_concept, 12),
    (Intent.classification, 8),
    (Intent.navigation_help, 8),
    (Intent.legal_opinion_request, 5),
    (Intent.other, 3),
)

QUESTIONS = {
    Intent.claim_explain: (
        "Explain claim {n} in plain English",
        "What does claim {n} say?",
        "Which claims are independent?",
        "Summarize the dependent claims",
        "What does claim {n} add to claim 1?",
    ),
    Intent.claim_scope: (
        "How broad is the protection of claim {n}?",
        "What is the scope of claim {n}?",
        "What are the limitations in claim {n}?",
        "What does claim {n} cover?",
    ),
    Intent.status_family: (
        "Is this patent still in force?",
        "When does this patent expire?",
        "Are there any continuations in the family?",
        "What is the priority date?",
        "What is the legal status?",
    ),
    Intent.citations_prior_art: (
        "Which references did the examiner cite?",
        "What prior art is cited?",
        "Which patents does this one cite?",
    ),
    Intent.classification: (
        "What CPC classes is this patent in?",
        "What does the classification mean?",
        "Which category does it fall in?",
    ),
    Intent.law_concept: (
        "What does obviousness mean for a patent?",
        "What is an abstract idea under section 101?",
        "What is the doctrine of equivalents?",
        "What does the written description requirement ask for?",
    ),
    Intent.navigation_help: (
        "Where do I see the full text?",
        "How do I open the drawings?",
        "Where can I download the PDF?",
    ),
    Intent.legal_opinion_request: (
        "Is claim {n} valid?",
        "Would this patent survive a court challenge?",
        "Does my product infringe this patent?",
        "Do I need a license to sell this?",
    ),
    Intent.other: (
        "Thanks, that helps",
        "Can you say more?",
    ),
}

ANSWERS = {
    Intent.claim_explain: (
        "Claim {n} defines the main elements of the invention and how they fit together [claim {n}]. Dependent claims only add narrower details.",
        "In plain terms, claim {n} describes a device with a few named parts working together [claim {n}]. It is not a full summary of the patent.",
    ),
    Intent.claim_scope: (
        "Claim {n} is limited by every element it lists [claim {n}]; leaving one out of a design means the claim does not read on it.",
        "The scope of claim {n} comes from its wording, not the title [claim {n}]. Broader terms cover more variations.",
    ),
    Intent.status_family: (
        "The brief lists the status and expiry from the page. Check the register for maintenance fee payments; this is not legal advice.",
        "The family section shows related applications and their status. Continuations share the earliest priority date.",
    ),
    Intent.citations_prior_art: (
        "The citations section lists the references on the patent. A reference is not the same as a rejection.",
        "Cited documents are prior art the examiner or applicant considered. The page lists them by number.",
    ),
    Intent.classification: (
        "The classifications section lists the codes assigned to the patent. They group patents by subject.",
        "A classification code names a technical area; several codes can apply to one patent.",
    ),
    Intent.law_concept: (
        "This is a general legal concept, not a conclusion about this patent. Treat it as background and ask a patent attorney for advice.",
        "In general terms the rule asks a fixed question of the claims. How it applies here is for a qualified professional.",
    ),
    Intent.navigation_help: (
        "Use the tabs near the top of the page to switch between the sections of the patent.",
        "The section you want is on the page itself; scroll or use the tab bar to move between sections.",
    ),
    Intent.legal_opinion_request: (
        "I cannot give a legal opinion on validity or infringement. I can explain what the claims say; for advice talk to a patent attorney.",
        "That is a legal judgement I should not make. I can walk you through the claim language so you can discuss it with an attorney.",
    ),
    Intent.other: (
        "Happy to help. Ask about the claims, status, citations or classifications on this page.",
        "Sure. Tell me which part of the patent you want to look at.",
    ),
}

REASK_ANSWER_FIX = "Let me restate that more directly. "

# ---- the plan: every row, in memory, no database --------------------------------------------------------------


@dataclass
class Plan:
    page_views: list[dict] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    judgements: list[dict] = field(default_factory=list)
    runs: list[dict] = field(default_factory=list)  # {session_id, messages, brief, created_at}

    def counts(self) -> dict[str, int]:
        return {
            "page views": len(self.page_views),
            "questions": len(self.questions),
            "model calls": len(self.calls),
            "judgements": len(self.judgements),
            "flagged runs": len(self.runs),
        }


def _uuid(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def _day_start(end: date, day: int) -> datetime:
    return datetime.combine(end - timedelta(days=DAYS - day), time())


def version_at(end: date, when: datetime) -> int:
    """The demo version label of a moment: 1, then 2 at the day-20 marker, then 3 at the day-27 marker."""
    if when >= _day_start(end, MARKER_V3_DAY) + timedelta(hours=MARKER_HOUR):
        return 3
    if when >= _day_start(end, MARKER_V2_DAY) + timedelta(hours=MARKER_HOUR):
        return 2
    return 1


def in_dip(end: date, when: datetime) -> bool:
    return _day_start(end, DIP_FIRST_DAY) <= when < _day_start(end, MARKER_V2_DAY) + timedelta(hours=MARKER_HOUR)


def _lognormal_ms(rng: random.Random, median: float, sigma: float, factor: float = 1.0) -> int:
    return max(1, int(rng.lognormvariate(math.log(median), sigma) * factor))


def _weighted(rng: random.Random, pairs):
    return rng.choices([p[0] for p in pairs], weights=[p[1] for p in pairs])[0]


def _fill(template: str, n: int) -> str:
    return template.format(n=n)


@dataclass
class _Session:
    session_id: str
    patent: str
    start: datetime
    version: int
    dip: bool
    questions: list[dict] = field(default_factory=list)


def _make_questions(rng: random.Random, session: _Session) -> None:
    count = rng.choices((1, 2, 3, 4, 5), weights=(35, 30, 20, 10, 5))[0]
    offset = rng.randint(15, 90)
    previous = None
    for index in range(count):
        reasked = index > 0 and rng.random() < (0.25 if session.dip else 0.05)
        if reasked:
            intent, text, number = previous
        else:
            intent = _weighted(rng, INTENT_WEIGHTS)
            number = rng.randint(1, 12)
            text = _fill(rng.choice(QUESTIONS[intent]), number)
        answer = _fill(rng.choice(ANSWERS[intent]), number)
        if reasked:
            answer = REASK_ANSWER_FIX + answer
        previous = (intent, text, number)
        if rng.random() < 0.5:  # rated
            weights = (30, 52, 18) if session.dip else (82, 12, 6)
            rating = rng.choices(("up", "down", "no_answer"), weights=weights)[0]
        else:
            rating = None
        session.questions.append(
            {
                "id": _uuid(rng),
                "session_id": session.session_id,
                "patent_number": session.patent,
                "text": text,
                "intent": intent.value,
                "section_focus": primary_section(intent),
                "answer": answer,
                "rating": rating,
                "rating_reason": None,
                "reasked": reasked,
                "citation_ok": rng.random() >= (0.08 if session.dip else 0.03),
                "judge_flagged": False,
                "source": IMPORTED_SOURCE,
                "assist_version": session.version,
                "created_at": session.start + timedelta(seconds=offset),
            }
        )
        offset += rng.randint(25, 150)


def _page_view(rng: random.Random, session: _Session) -> dict:
    asked = bool(session.questions)
    if asked:
        last = session.questions[-1]["created_at"] - session.start
        dwell_s = max(int(last.total_seconds()) + rng.randint(20, 120), rng.randint(60, 420))
        names = rng.sample(SECTION_NAMES, rng.randint(2, 4))
        sections = {name: rng.randint(3000, 90000) for name in names}
        dwell_ms = max(dwell_s * 1000, sum(sections.values()) // 2)
    elif rng.random() < 0.3:  # a bounce
        dwell_ms = rng.randint(1500, 9000)
        sections = {rng.choice(SECTION_NAMES[:3]): rng.randint(0, 2500)}
    else:
        dwell_ms = rng.randint(20000, 240000)
        names = rng.sample(SECTION_NAMES, rng.randint(1, 3))
        sections = {name: rng.randint(2000, 60000) for name in names}
    return {
        "session_id": session.session_id,
        "patent_number": session.patent,
        "started_at": session.start,
        "updated_at": session.start + timedelta(milliseconds=dwell_ms),
        "dwell_ms": dwell_ms,
        "sections": sections,
        "bounce": is_bounce(dwell_ms, sections),
        "source": IMPORTED_SOURCE,
        "assist_version": session.questions[0]["assist_version"] if asked else None,
    }


def _grade(rng: random.Random, session: _Session) -> int:
    if session.dip:
        return rng.choices((3, 4, 5), weights=(60, 30, 10))[0]
    if session.version == 3:
        return rng.choices((3, 4, 5), weights=(5, 35, 60))[0]
    return rng.choices((3, 4, 5), weights=(7, 40, 53))[0]


def _reason(rng: random.Random, grade: int, has_legal: bool, dip: bool) -> str:
    if grade >= 4:
        return rng.choices(("no_issue", "too_long", "unsupported_claim"), weights=(90, 6, 4))[0]
    pool = {
        "missed_question": 4 if dip else 2,
        "too_long": 3 if dip else 1,
        "unsupported_claim": 2,
        "bad_citation": 2,
        "off_topic": 1,
    }
    if has_legal:
        pool.update(legal_advice_given=1, refusal_mishandled=1)
    if grade == 3:
        pool["no_issue"] = 3
    names = [name for name in REASONS if name in pool]  # REASONS order keeps this deterministic
    return rng.choices(names, weights=[pool[name] for name in names])[0]


def _judgement(rng: random.Random, session: _Session, grade: int, worst_id: str | None) -> dict:
    has_legal = any(q["intent"] == Intent.legal_opinion_request.value for q in session.questions)
    reason = _reason(rng, grade, has_legal, session.dip)
    if grade >= 4:
        answered = "yes"
    elif grade == 3:
        answered = "partly"
    else:
        answered = "no" if grade == 1 else rng.choice(("partly", "no"))
    if has_legal:
        legal = "mishandled" if reason in ("legal_advice_given", "refusal_mishandled") else "handled"
    else:
        legal = "not_applicable"
    ended = session.questions[-1]["created_at"] + timedelta(minutes=rng.randint(15, 40))
    return {
        "id": _uuid(rng),
        "session_id": session.session_id,
        "prompt_version": session.version,
        "rubric_version": DEMO_RUBRIC,
        "model": DEMO_MODEL,
        "created_at": ended,
        "answered": answered,
        "concise": reason != "too_long",
        "citations_sensible": reason not in ("bad_citation", "unsupported_claim"),
        "legal": legal,
        "grade": grade,
        "reason": reason,
        "worst_question_id": worst_id,
        "flagged": grade <= FLAG_GRADE_AT_OR_BELOW,
        "source": IMPORTED_SOURCE,
    }


def _call(rng, *, created_at, kind, outcome, latency_ms, version, attempts=1, tokens=None) -> dict:
    prompt = completion = total = None
    if tokens is not None:
        prompt = rng.randint(*tokens[0])
        completion = rng.randint(*tokens[1])
        total = prompt + completion
    return {
        "id": _uuid(rng),
        "created_at": created_at,
        "kind": kind,
        "model": DEMO_MODEL,
        "latency_ms": latency_ms,
        "outcome": outcome,
        "attempts": attempts,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "prompt_version": version,
        "source": IMPORTED_SOURCE,
    }


CHAT_TOKENS = ((2800, 5200), (120, 450))
JUDGE_TOKENS = ((900, 2200), (60, 130))
INSIGHTS_TOKENS = ((3000, 6000), (300, 700))


def build_plan(end: date, seed: int = 2026) -> Plan:
    """Every row of the demo dataset for the 30 days ending on `end`. Pure and deterministic: one Random(seed)."""
    rng = random.Random(seed)
    plan = Plan()
    sessions: list[_Session] = []
    marker_times = [_day_start(end, d) + timedelta(hours=MARKER_HOUR) for d in (MARKER_V2_DAY, MARKER_V3_DAY)]
    forced = [False, False]

    for day in range(1, DAYS + 1):
        midnight = _day_start(end, day)
        weekend = midnight.weekday() >= 5
        views = rng.randint(5, 9) if weekend else rng.randint(14, 20)
        starts = sorted(
            midnight
            + timedelta(
                hours=rng.choices(range(7, 22), weights=(2, 4, 6, 7, 6, 5, 5, 6, 7, 6, 5, 4, 3, 2, 1))[0],
                minutes=rng.randint(0, 59),
                seconds=rng.randint(0, 59),
            )
            for _ in range(views)
        )
        for marker in marker_times:  # the marker's day always has a session after the marker
            late = marker + timedelta(minutes=rng.randint(5, 180))
            if day == (marker.date() - _day_start(end, 1).date()).days + 1 and starts[-1] < marker:
                starts[-1] = late
        for index, start in enumerate(starts):
            session = _Session(
                session_id=f"{PREFIX}{day:02d}-{index:03d}-{rng.getrandbits(24):06x}",
                patent=rng.choices(PATENTS, weights=PATENT_WEIGHTS)[0],
                start=start,
                version=version_at(end, start),
                dip=in_dip(end, start),
            )
            asks = rng.random() < QUESTION_SHARE
            for slot, marker in enumerate(marker_times):  # the first session after each marker always asks
                if start >= marker and not forced[slot] and start.date() == marker.date():
                    forced[slot] = True
                    asks = True
            if asks:
                _make_questions(rng, session)
            sessions.append(session)

    asked = [s for s in sessions if s.questions]
    # Choose the flagged conversations: low judge grade, weighted towards the dip. Top keys of u ** (1 / weight).
    keys = [(rng.random() ** (1 / (6 if s.dip else 1)), index) for index, s in enumerate(asked)]
    chosen = {index for _, index in sorted(keys, reverse=True)[:FLAGGED_RUNS]}

    for index, session in enumerate(asked):
        worst_id = None
        if index in chosen:
            grade = rng.choices((1, 2), weights=(40, 60))[0]
            worst = rng.choice(session.questions)
            worst["judge_flagged"] = True
            worst_id = worst["id"]
            if worst["rating"] is None and rng.random() < 0.5:
                worst["rating"] = "down"
            session_questions = session.questions
            messages = []
            for q in session_questions:
                messages += [{"role": "user", "content": q["text"]}, {"role": "assistant", "content": q["answer"]}]
            title, abstract = BRIEFS[session.patent]
            plan.runs.append(
                {
                    "session_id": session.session_id,
                    "messages": messages,
                    "brief": {
                        "patent_number": session.patent,
                        "title": title,
                        "abstract": abstract,
                        "status": "Active",
                        "claims": [{"number": 1, "text": "A demo claim: an apparatus comprising the parts named in the title."}],
                    },
                    "created_at": session.start,
                }
            )
        else:
            grade = _grade(rng, session)
        plan.judgements.append(_judgement(rng, session, grade, worst_id))

    for session in sessions:
        plan.page_views.append(_page_view(rng, session))
        plan.questions.extend(session.questions)

    # Model-call telemetry: one chat call per question, one judge call per judgement, and the story's extras.
    def factor(when: datetime) -> float:
        day = (when.date() - _day_start(end, 1).date()).days + 1
        return LATENCY_FACTOR if LATENCY_DAYS[0] <= day <= LATENCY_DAYS[1] else 1.0

    for q in plan.questions:
        when = q["created_at"]
        plan.calls.append(
            _call(rng, created_at=when, kind="chat", outcome="ok", version=q["assist_version"], tokens=CHAT_TOKENS,
                  latency_ms=_lognormal_ms(rng, 2200, 0.4, factor(when)))
        )
    for j in plan.judgements:
        when = j["created_at"]
        plan.calls.append(
            _call(rng, created_at=when, kind="judge", outcome="ok", version=j["prompt_version"], tokens=JUDGE_TOKENS,
                  attempts=1 if rng.random() < 0.9 else 2, latency_ms=_lognormal_ms(rng, 1500, 0.4, factor(when)))
        )
    for day in range(1, DAYS + 1):
        midnight = _day_start(end, day)
        if day % 3 == 1:  # an insights run now and then
            when = midnight + timedelta(hours=6, minutes=rng.randint(0, 30))
            plan.calls.append(
                _call(rng, created_at=when, kind="insights", outcome="ok", version=version_at(end, when),
                      tokens=INSIGHTS_TOKENS, latency_ms=_lognormal_ms(rng, 4000, 0.3, factor(when)))
            )
        if rng.random() < 0.3:  # an occasional failed chat call (no question was stored for it)
            when = midnight + timedelta(hours=rng.randint(8, 20), minutes=rng.randint(0, 59))
            outcome = rng.choices(("error", "timeout", "rate_limited"), weights=(5, 3, 2))[0]
            latency = {"timeout": rng.randint(18000, 25000), "error": rng.randint(300, 1500)}.get(outcome, rng.randint(100, 400))
            plan.calls.append(
                _call(rng, created_at=when, kind="chat", outcome=outcome, latency_ms=latency,
                      version=version_at(end, when), attempts=rng.randint(1, 3))
            )
    burst_start = _day_start(end, BURST_DAY) + timedelta(hours=14, minutes=2)
    for step in range(BURST_CALLS):
        when = burst_start + timedelta(seconds=step * rng.randint(40, 90))
        plan.calls.append(
            _call(rng, created_at=when, kind="chat", outcome="rate_limited", latency_ms=rng.randint(100, 450),
                  version=version_at(end, when))
        )

    plan.page_views.sort(key=lambda r: r["session_id"])
    plan.questions.sort(key=lambda r: (r["created_at"], r["id"]))
    plan.calls.sort(key=lambda r: (r["created_at"], r["id"]))
    plan.judgements.sort(key=lambda r: r["session_id"])
    plan.runs.sort(key=lambda r: r["created_at"])
    return plan


# ---- the database ---------------------------------------------------------------------------------------------


def _like_prefix():
    return PREFIX + "%"


def foreign_rows(db: Session, agent, workspace_id: str) -> int:
    """Rows that use the demo30- prefix but are not imported data (live rows): the seed must not touch them."""
    total = 0
    for model in (AssistPageView, AssistQuestion, AssistJudgement):
        total += db.scalar(
            select(func.count())
            .select_from(model)
            .where(model.session_id.like(_like_prefix()), model.source != IMPORTED_SOURCE)
        )
    total += db.scalar(
        select(func.count())
        .select_from(Run)
        .where(Run.agent_id == agent.id, Run.session_id.like(_like_prefix()), Run.source != IMPORTED_SOURCE)
    )
    return total


def delete_seed(db: Session, agent, workspace_id: str) -> dict[str, int]:
    """Delete what a previous seed wrote (flushed, not committed); returns the rows deleted per table."""
    deleted = {}
    for label, model in (("judgements", AssistJudgement), ("questions", AssistQuestion), ("page views", AssistPageView)):
        result = db.execute(
            delete(model)
            .where(
                model.workspace_id == workspace_id,
                model.session_id.like(_like_prefix()),
                model.source == IMPORTED_SOURCE,
            )
            .execution_options(synchronize_session=False)
        )
        deleted[label] = result.rowcount
    result = db.execute(
        delete(Run)
        .where(Run.agent_id == agent.id, Run.session_id.like(_like_prefix()), Run.source == IMPORTED_SOURCE)
        .execution_options(synchronize_session=False)
    )
    deleted["flagged runs"] = result.rowcount
    result = db.execute(
        delete(AssistCall)
        .where(AssistCall.workspace_id == workspace_id, AssistCall.source == IMPORTED_SOURCE)
        .execution_options(synchronize_session=False)
    )
    deleted["model calls"] = result.rowcount
    return deleted


def write_plan(db: Session, agent, workspace_id: str, plan: Plan, progress: list[str]) -> None:
    """Insert the plan. The flagged runs commit as they go (save_flagged_run does); `progress` gets "committed"
    before the first of them, so a caller that sees an error knows whether a rollback is enough."""
    db.add_all(AssistPageView(workspace_id=workspace_id, **row) for row in plan.page_views)
    db.add_all(AssistQuestion(workspace_id=workspace_id, **row) for row in plan.questions)
    db.add_all(AssistJudgement(workspace_id=workspace_id, **row) for row in plan.judgements)
    db.add_all(AssistCall(workspace_id=workspace_id, **row) for row in plan.calls)
    db.flush()
    for run in plan.runs:
        progress.append("committed")
        saved = save_flagged_run(db, agent, run["session_id"], run["messages"], run["brief"], IMPORTED_SOURCE)
        saved.created_at = run["created_at"]
    db.commit()


def _error(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)


def _print_counts(title: str, counts: dict[str, int]) -> None:
    print(title)
    for label, value in counts.items():
        print(f"  {label}: {value}")


def _end_date(text: str | None) -> date:
    if text is None:
        return datetime.now(timezone.utc).date()
    return date.fromisoformat(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed a labelled 30-day demo dataset for the Patent Assist console.")
    parser.add_argument("--confirm", action="store_true", help="required to write (or to remove) anything")
    parser.add_argument("--remove", action="store_true", help="delete everything the seed wrote")
    parser.add_argument("--end", default=None, help="last day of the 30-day span, YYYY-MM-DD (default: today, UTC)")
    parser.add_argument("--seed", type=int, default=2026, help="seed of the generator (default 2026)")
    parser.add_argument("--database-url", default=None, help="defaults to $DATABASE_URL, else the local SQLite file")
    args = parser.parse_args(argv)

    try:
        end = _end_date(args.end)
    except ValueError:
        _error(f"--end must be a date such as 2026-11-24, not {args.end!r}; nothing was written")
        return EXIT_INVALID

    if not args.confirm:
        if args.remove:
            print(
                f"Would remove every row with a {PREFIX} session id and source {IMPORTED_SOURCE!r}, and every "
                f"imported model call, from the assist workspace. Nothing was written; pass --confirm to remove."
            )
        else:
            plan = build_plan(end, args.seed)
            _print_counts(
                f"Would seed the 30 days ending {end.isoformat()} (seed {args.seed}) as demo dataset:", plan.counts()
            )
            print("Nothing was written; pass --confirm to write. A re-run replaces the previous seed.")
        return EXIT_INVALID

    url = args.database_url or os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL
    masked = mask_url(url)
    engine = None
    try:
        # Never echo a driver error: it can carry the connection URL.
        try:
            engine = make_engine(url)
            insp = inspect(engine)
            migrated = all(insp.has_table(name) for name in REQUIRED_TABLES)
        except Exception as exc:
            _error(f"could not read the database {masked} ({type(exc).__name__}); check DATABASE_URL")
            return EXIT_DATABASE
        if not migrated:
            _error("the database schema is out of date; run `alembic upgrade head` first")
            return EXIT_DATABASE

        with Session(engine, expire_on_commit=False) as db:
            try:
                agent, workspace = get_assist_agent(db)
            except LookupError:
                _error(NO_AGENT)
                return EXIT_DATABASE
            except Exception as exc:
                _error(f"could not read the database {masked} ({type(exc).__name__})")
                return EXIT_DATABASE
            try:
                if foreign_rows(db, agent, workspace.id):
                    _error(
                        f"rows that are not imported data already use the {PREFIX} session id prefix; "
                        "the seed never changes live data, so nothing was written"
                    )
                    return EXIT_INVALID
                deleted = delete_seed(db, agent, workspace.id)
                if args.remove:
                    db.commit()
                    _print_counts(f"Removed the demo seed from {masked}:", deleted)
                    return EXIT_OK
                plan = build_plan(end, args.seed)
                progress: list[str] = []
                try:
                    write_plan(db, agent, workspace.id, plan, progress)
                except Exception:
                    db.rollback()
                    if progress:  # some runs were already committed: take the whole seed out again
                        try:
                            delete_seed(db, agent, workspace.id)
                            db.commit()
                        except Exception:
                            db.rollback()
                    raise
            except Exception as exc:
                db.rollback()
                _error(f"the seed failed and was rolled back ({type(exc).__name__}); nothing was written")
                return EXIT_DATABASE
    finally:
        if engine is not None:
            engine.dispose()

    _print_counts(f"Seeded the 30 days ending {end.isoformat()} into {masked} (demo dataset):", plan.counts())
    print("The assist versions 1-3 are demo labels: no prompt version was created or changed.")
    print("Demo data shows as demo dataset in the console and never counts as measured effect.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
