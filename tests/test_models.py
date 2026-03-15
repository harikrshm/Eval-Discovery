import pytest
from pydantic import ValidationError


def test_chat_request_valid():
    from backend.models import ChatRequest
    req = ChatRequest(messages=[{"role": "user", "content": "Hello"}])
    assert len(req.messages) == 1
    assert req.messages[0].role == "user"


def test_chat_request_empty_messages_fails():
    from backend.models import ChatRequest
    with pytest.raises(ValidationError):
        ChatRequest(messages=[])


def test_rubric_requires_why_discovered():
    from backend.models import Rubric
    with pytest.raises(ValidationError):
        Rubric(name="Test", definition="Def", good_example="Good", bad_example="Bad")


def test_rubric_valid_with_why_discovered():
    from backend.models import Rubric
    rubric = Rubric(
        name="Pricing Redirect",
        definition="Evaluates whether Alex deflects pricing questions.",
        good_example="Alex: Let's cover that in the demo.",
        bad_example="Alex: It's $20/month.",
        why_discovered="User pushed for pricing in 3 consecutive runs.",
    )
    assert rubric.why_discovered == "User pushed for pricing in 3 consecutive runs."


def test_pattern_check_request_valid():
    from backend.models import PatternCheckRequest
    transcript = [
        {"role": "user", "content": "What does it cost?"},
        {"role": "assistant", "content": "Let's cover that in the demo."},
        {"role": "user", "content": "Just tell me the price."},
    ]
    req = PatternCheckRequest(transcripts=[transcript, transcript])
    assert len(req.transcripts) == 2


def test_pattern_check_response_found_requires_non_empty_summary():
    from backend.models import PatternCheckResponse
    with pytest.raises(ValidationError):
        PatternCheckResponse(found=True, pattern_summary="")


def test_pattern_check_response_found_with_summary():
    from backend.models import PatternCheckResponse
    resp = PatternCheckResponse(found=True, pattern_summary="User repeatedly asked for pricing.")
    assert resp.found is True
    assert len(resp.pattern_summary) > 0


def test_pattern_check_response_not_found_allows_empty_summary():
    from backend.models import PatternCheckResponse
    resp = PatternCheckResponse(found=False, pattern_summary="")
    assert resp.found is False


def test_rubric_request_rejects_empty_summary():
    from backend.models import RubricRequest
    with pytest.raises(ValidationError):
        RubricRequest(pattern_summary="")


def test_rubric_request_rejects_whitespace_only_summary():
    from backend.models import RubricRequest
    with pytest.raises(ValidationError):
        RubricRequest(pattern_summary="   ")


def test_rubric_request_valid():
    from backend.models import RubricRequest
    req = RubricRequest(pattern_summary="User repeatedly pushed for pricing before booking demo.")
    assert req.pattern_summary == "User repeatedly pushed for pricing before booking demo."
