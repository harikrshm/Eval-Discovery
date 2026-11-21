import json
from unittest.mock import patch

import pytest

from backend.core.config import AgentConfig
from backend.core.criteria import (
    CriterionDraft,
    CriterionGenerationFailed,
    generate_criterion,
)
from backend.groq_client import MODEL_CRITERIA

CONFIG = AgentConfig(
    name="Sam (Customer Support)",
    system_prompt="You are Sam, a support agent. Never promise refunds.",
    user_role_label="Customer",
)

VALID_PATTERN_SUMMARY = (
    "User repeatedly pushed for pricing before agreeing to a demo. "
    "The agent gave the placeholder price on the second ask in Runs 2 and 3. "
    "Evidence - Run 2: Prospect: 'What does it cost?' Agent: '$20/month'."
)

VALID_PAYLOAD = {
    "name": "Pricing Redirect Under Repeated Pressure",
    "definition": "Evaluates whether the agent deflects repeated pricing questions toward demo booking.",
    "good_example": "Prospect: 'Just tell me the price.' Agent: 'The demo is where that all comes to life - 20 minutes?'",
    "bad_example": "Prospect: 'What does it cost?' Agent: '$20/month' - second ask, should have redirected.",
    "why_discovered": "User ran 3 conversations where the prospect pushed for pricing.",
}


def test_valid_json_returns_draft():
    with patch("backend.core.criteria.groq_chat", return_value=json.dumps(VALID_PAYLOAD)) as m:
        draft = generate_criterion(CONFIG, VALID_PATTERN_SUMMARY)
    assert isinstance(draft, CriterionDraft)
    assert draft.name == VALID_PAYLOAD["name"]
    assert draft.definition == VALID_PAYLOAD["definition"]
    assert draft.good_example == VALID_PAYLOAD["good_example"]
    assert draft.bad_example == VALID_PAYLOAD["bad_example"]
    assert draft.why_discovered == VALID_PAYLOAD["why_discovered"]
    assert m.call_args.kwargs["model"] == MODEL_CRITERIA


def test_fenced_json_is_accepted():
    raw = "```json\n" + json.dumps(VALID_PAYLOAD) + "\n```"
    with patch("backend.core.criteria.groq_chat", return_value=raw):
        draft = generate_criterion(CONFIG, VALID_PATTERN_SUMMARY)
    assert draft.name == VALID_PAYLOAD["name"]


def test_draft_is_frozen():
    with patch("backend.core.criteria.groq_chat", return_value=json.dumps(VALID_PAYLOAD)):
        draft = generate_criterion(CONFIG, VALID_PATTERN_SUMMARY)
    with pytest.raises(AttributeError):
        draft.name = "other"


def test_list_examples_are_normalized_to_strings():
    payload = {
        **VALID_PAYLOAD,
        "good_example": [
            {"speaker": "Prospect", "text": "What does it cost?"},
            {"speaker": "Alex", "text": "Let's cover that in the demo."},
        ],
        "bad_example": [
            {"speaker": "Prospect", "content": "Just give me a number."},
            {"speaker": "Alex", "content": "$20/month."},
        ],
    }
    with patch("backend.core.criteria.groq_chat", return_value=json.dumps(payload)):
        draft = generate_criterion(CONFIG, VALID_PATTERN_SUMMARY)
    assert draft.good_example == (
        "Prospect: 'What does it cost?' Alex: 'Let's cover that in the demo.'"
    )
    assert draft.bad_example == "Prospect: 'Just give me a number.' Alex: '$20/month.'"


@pytest.mark.parametrize(
    "key", ["name", "definition", "good_example", "bad_example", "why_discovered"]
)
def test_missing_key_raises(key):
    payload = {k: v for k, v in VALID_PAYLOAD.items() if k != key}
    with patch("backend.core.criteria.groq_chat", return_value=json.dumps(payload)):
        with pytest.raises(CriterionGenerationFailed):
            generate_criterion(CONFIG, VALID_PATTERN_SUMMARY)


def test_non_string_field_raises():
    payload = {**VALID_PAYLOAD, "definition": 42}
    with patch("backend.core.criteria.groq_chat", return_value=json.dumps(payload)):
        with pytest.raises(CriterionGenerationFailed):
            generate_criterion(CONFIG, VALID_PATTERN_SUMMARY)


def test_malformed_dialogue_list_raises():
    payload = {**VALID_PAYLOAD, "good_example": ["not an object"]}
    with patch("backend.core.criteria.groq_chat", return_value=json.dumps(payload)):
        with pytest.raises(CriterionGenerationFailed):
            generate_criterion(CONFIG, VALID_PATTERN_SUMMARY)


def test_non_object_json_raises():
    with patch("backend.core.criteria.groq_chat", return_value="[1, 2, 3]"):
        with pytest.raises(CriterionGenerationFailed):
            generate_criterion(CONFIG, VALID_PATTERN_SUMMARY)


def test_non_json_raises():
    with patch("backend.core.criteria.groq_chat", return_value="Sorry, I cannot do that."):
        with pytest.raises(CriterionGenerationFailed):
            generate_criterion(CONFIG, VALID_PATTERN_SUMMARY)


def test_groq_exception_raises():
    with patch("backend.core.criteria.groq_chat", side_effect=Exception("Groq down")):
        with pytest.raises(CriterionGenerationFailed):
            generate_criterion(CONFIG, VALID_PATTERN_SUMMARY)


@pytest.mark.parametrize("summary", ["", "   ", "\n\t "])
def test_blank_summary_raises_value_error(summary):
    with patch("backend.core.criteria.groq_chat") as m:
        with pytest.raises(ValueError):
            generate_criterion(CONFIG, summary)
    m.assert_not_called()


def test_prompt_uses_config_labels_not_alex():
    with patch("backend.core.criteria.groq_chat", return_value=json.dumps(VALID_PAYLOAD)) as m:
        generate_criterion(CONFIG, VALID_PATTERN_SUMMARY)
    system, messages = m.call_args.args[0], m.call_args.args[1]
    built = system + "\n" + "\n".join(msg["content"] for msg in messages)
    assert CONFIG.name in built
    assert CONFIG.user_role_label in built
    assert "Alex" not in built
