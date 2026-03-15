def test_extract_json_strips_think_blocks():
    from backend.utils import extract_json
    raw = "<think>Let me analyze this carefully...</think>\n{\"intent\": \"objection_handling\"}"
    result = extract_json(raw)
    assert result == '{"intent": "objection_handling"}'


def test_extract_json_strips_markdown_fences():
    from backend.utils import extract_json
    raw = "```json\n{\"intent\": \"graceful_exit\"}\n```"
    result = extract_json(raw)
    assert result == '{"intent": "graceful_exit"}'


def test_extract_json_strips_both():
    from backend.utils import extract_json
    raw = "<think>hmm</think>\n```json\n{\"found\": true}\n```"
    result = extract_json(raw)
    assert result == '{"found": true}'
