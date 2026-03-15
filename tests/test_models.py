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


def test_analyze_request_valid():
    from backend.models import AnalyzeRequest
    transcript = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hey"}, {"role": "user", "content": "Tell me more"}]
    req = AnalyzeRequest(transcripts=[transcript, transcript, transcript, transcript, transcript], run_count=5)
    assert req.run_count == 5
    assert len(req.transcripts) == 5


def test_analyze_request_single_transcript_valid():
    from backend.models import AnalyzeRequest
    transcript = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hey"}, {"role": "user", "content": "Tell me more"}]
    req = AnalyzeRequest(transcripts=[transcript], run_count=1)
    assert len(req.transcripts) == 1


def test_rubric_response_shape():
    from backend.models import Rubric, AnalyzeResponse
    rubric = Rubric(
        name="Test",
        definition="Def",
        good_example="Good",
        bad_example="Bad",
    )
    resp = AnalyzeResponse(intent="objection_handling", rubric=rubric)
    assert resp.intent == "objection_handling"
