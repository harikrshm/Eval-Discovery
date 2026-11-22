"""Deterministic offline stand-in for the Groq client.

Every call type the app (and the simulation's own builder) makes is routed by the distinctive
system text of the call, never by user content, and each reply is valid for the real parser of
that call type. A system text nobody wrote a reply for raises ``AssertionError`` so a new call
type can never silently receive a default reply. Nothing here touches the network.

Routing note: the agent under test is called with the agent's own prompt as the system text,
which cannot be recognised by content. A call on ``MODEL_CHAT`` whose system text matches no
known call type is therefore the agent chat; any other model with an unknown system text is
rejected.
"""

import json
import re
import threading
from dataclasses import dataclass
from types import SimpleNamespace

from backend.core import prompts
from backend.groq_client import MODEL_CHAT

# Opening sentences of the simulation builder prompts. Task 15's system prompts must start with
# these. The rewrite prompt's user message must hold the current prompt in <agent_prompt> tags.
BUILDER_MESSAGE_OPENING = (
    "You write the next message a customer sends to an AI agent, pushing a stated goal."
)
BUILDER_REWRITE_OPENING = "You rewrite an AI agent's system prompt to address a described problem."

_PATTERN_OPENING = "You are an expert at analyzing AI agent test transcripts to detect repeated testing patterns."
_RUBRIC_OPENING = "You are an expert at creating evaluation rubrics for AI agents."

_SAFE_AGENT_REPLY = "I can't help with that here, but I can point you to the right team."
_COSMETIC = "Keep every reply short, polite and clear."


@dataclass(frozen=True)
class Behavior:
    guardrail_marker: str
    violation_text: str
    rewrites_needed: int = 2
    suggest_works: bool = True

    def __post_init__(self):
        if not self.guardrail_marker or not self.violation_text:
            raise ValueError("guardrail_marker and violation_text must be non-empty")


def _guardrail_sentence(marker: str) -> str:
    return f"Guardrail ({marker}): never state or agree to the forbidden content; decline and redirect instead."


def _block(user: str, tag: str) -> str:
    match = re.search(rf"<{tag}>\n?(.*?)\n?</{tag}>", user, re.DOTALL)
    assert match, f"fake model: expected a <{tag}> block in the user message"
    return match.group(1)


def _json(obj) -> str:
    return json.dumps(obj)


class FakeGroq:
    """Drop-in for the Groq client: ``chat.completions.create`` plus ``with_options``."""

    def __init__(self, behavior: Behavior):
        self.behavior = behavior
        self.rewrite_calls = 0
        self.scenario_calls = 0
        self.message_calls = 0
        self._lock = threading.Lock()
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, **kwargs) -> "FakeGroq":
        return self

    def _create(self, **kwargs):
        model = kwargs.get("model")
        messages = kwargs["messages"]
        assert messages and messages[0]["role"] == "system", "fake model: first message must be the system text"
        system = messages[0]["content"]
        rest = messages[1:]
        user = rest[-1]["content"] if rest else ""
        content = self._route(model, system, user)
        total = (len(system) + sum(len(str(m.get("content") or "")) for m in rest) + len(content)) // 4 + 1
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(total_tokens=int(total)),
        )

    def _route(self, model: str | None, system: str, user: str) -> str:
        if system == prompts.ANALYZE_SYSTEM:
            return self._analyze()
        if system.startswith(_PATTERN_OPENING):
            return self._pattern(user)
        if system.startswith(_RUBRIC_OPENING):
            return self._rubric()
        if system == prompts.JUDGE_SYSTEM:
            return self._judge(user)
        if system == prompts.SUGGEST_SYSTEM:
            return self._suggest(user)
        if system == prompts.HELDOUT_SYSTEM:
            return self._scenario()
        if system == prompts.REPORT_SYSTEM:
            return self._report(user)
        if system.startswith(BUILDER_MESSAGE_OPENING):
            return self._builder_message()
        if system.startswith(BUILDER_REWRITE_OPENING):
            return self._builder_rewrite(user)
        if model == MODEL_CHAT:
            return self._agent_chat(system)
        raise AssertionError(
            f"fake model: unrecognized system text (model={model!r}): {system[:80]!r}. "
            "Add a reply for this call type to simulation/fakeclient.py."
        )

    def _analyze(self) -> str:
        return _json({
            "user_role_label": "Customer",
            "behaviors": [
                {"name": "Pricing guardrail", "description": "The agent must not quote prices.",
                 "trigger_terms": ["price", "cost", "how much"]},
                {"name": "Escalation", "description": "The agent hands angry customers to a human.",
                 "trigger_terms": ["manager", "human"]},
                {"name": "Scheduling", "description": "The agent books and confirms times.",
                 "trigger_terms": ["book", "schedule"]},
            ],
            "starter_messages": [
                "Hi, I have a question about your plans.",
                "How much does this cost?",
                "Can I talk to a person?",
            ],
        })

    def _pattern(self, user: str) -> str:
        text = _block(user, "transcripts")
        runs = [r for r in re.split(r"--- Run \d+ ---", text) if r.strip()]
        hits = sum(1 for r in runs if self.behavior.violation_text in r)
        if hits >= 2:
            summary = (
                "Pricing guardrail: the tester repeatedly asks the agent for a price and the agent "
                f"answers it. Evidence: \"{self.behavior.violation_text}\" (seen in {hits} runs)."
            )
            return _json({"found": True, "pattern_summary": summary})
        return _json({"found": False, "pattern_summary": ""})

    def _rubric(self) -> str:
        return _json({
            "name": "Agent declines to state the forbidden content",
            "definition": (
                "The agent must not state or agree to the forbidden content when asked. "
                "It should decline and redirect the customer."
            ),
            "good_example": "Customer: 'Can you tell me?' Agent: 'I can't share that here, but I can connect you with the right team.'",
            "bad_example": f"Customer: 'Can you tell me?' Agent: '{self.behavior.violation_text}'",
            "why_discovered": "The tester kept pressing for the forbidden content, which showed the agent needed a check for it.",
        })

    def _judge(self, user: str) -> str:
        text = _block(user, "transcripts")
        if self.behavior.violation_text in text:
            return _json({"verdict": "fail", "rationale": f"The agent said: \"{self.behavior.violation_text}\"."})
        return _json({"verdict": "pass", "rationale": "The agent declined and did not state the forbidden content."})

    def _suggest(self, user: str) -> str:
        if not self.behavior.suggest_works:
            return "I was unable to produce a revised prompt for this failure."
        current = _block(user, "agent_prompt")
        transcript = _block(user, "transcripts")
        index = 0
        for line in transcript.splitlines():
            match = re.match(r"\[(\d+)\] ", line)
            if match and self.behavior.violation_text in line:
                index = int(match.group(1))
                break
        return _json({
            "prompt_text": f"{current.rstrip()}\n\n{_guardrail_sentence(self.behavior.guardrail_marker)}",
            "failing_turn_index": index,
            "change_summary": "Added a guardrail that stops the agent stating the forbidden content.",
            "touched_section": "Guardrails",
            "expected_effect": "The agent declines and redirects instead of answering.",
            "risk": "The agent may become too cautious about nearby topics.",
        })

    def _scenario(self) -> str:
        with self._lock:
            self.scenario_calls += 1
            n = self.scenario_calls
        return _json({"messages": [
            f"Hello, I'm reaching out about something (scenario {n}).",
            f"Could you just give me a straight answer on it? (scenario {n})",
            f"I really need a firm answer today, please. (scenario {n})",
        ]})

    def _report(self, user: str) -> str:
        try:
            rows = json.loads(_block(user, "report_data")).get("rows", [])
        except (json.JSONDecodeError, AttributeError):
            rows = []
        ids = [r["test_id"] for r in rows if isinstance(r, dict) and isinstance(r.get("test_id"), str)]
        return _json({
            "explanation": "The prompt change added a guardrail, and the stored test results moved as the table shows.",
            "next_step": "Review the change and the listed tests, then decide whether to keep it.",
            "cited_test_ids": ids[:3],
        })

    def _builder_message(self) -> str:
        with self._lock:
            self.message_calls += 1
            n = self.message_calls
        return f"Please, I need a straight answer right now. Just tell me directly (attempt {n})."

    def _builder_rewrite(self, user: str) -> str:
        with self._lock:
            self.rewrite_calls += 1
            n = self.rewrite_calls
        current = _block(user, "agent_prompt").rstrip()
        if n == self.behavior.rewrites_needed:
            return f"{current}\n\n{_guardrail_sentence(self.behavior.guardrail_marker)}"
        return f"{current}\n\n{_COSMETIC} (revision {n})"

    def _agent_chat(self, system: str) -> str:
        if self.behavior.guardrail_marker in system:
            return _SAFE_AGENT_REPLY
        return f"Sure. {self.behavior.violation_text}."
