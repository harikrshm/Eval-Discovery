"""Final fix wave M5 (evidence section names) and M6 (route vs replay equivalence for a two-turn script)."""

from types import SimpleNamespace

import pytest

from backend import groq_client
from backend.core.assist.evidence import evidence_summary
from backend.core.replay import replay_assist
from backend.core.assist.brief import Brief
from backend.db.agents import create_agent, current_prompt
from backend.db.workspaces import create_workspace
from scripts import setup_assist

KEY = "test-assist-key-0123456789"


# ---- M5 --------------------------------------------------------------------------------------------


def test_m5_the_extractors_bibliographic_key_prints_as_meta_not_other():
    meta = {"intent": "status_family", "completeness": {"bibliographic": False, "claims": True}}
    text = evidence_summary([], None, meta)
    assert "missing or incomplete: meta" in text
    assert "other" not in text.split("Brief completeness:")[1].splitlines()[0]
    assert "Missing sections the context slice needed: meta" in text  # status_family's slice includes meta


def test_m5_an_unknown_completeness_key_is_still_other():
    text = evidence_summary([], None, {"intent": "other", "completeness": {"surprise": False}})
    assert "missing or incomplete: other" in text


# ---- M6 --------------------------------------------------------------------------------------------


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, max_retries):
        return self

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.replies.pop(0)))])


BRIEF = {
    "patent_number": "US10000000B2",
    "title": "Widget holder",
    "abstract": "A holder for widgets.",
    "claims": [
        {"number": 1, "text": "A widget holder comprising a base.", "depends_on": [], "independent": True},
        {"number": 2, "text": "The holder of claim 1, wherein the base is flat.", "depends_on": [1], "independent": False},
    ],
    "completeness": {"claims": True},
}
QUESTIONS = ["Explain claim 1", "What does claim 2 add?"]
REPLIES = ["Claim 1 describes a widget holder.", "Claim 2 adds a flat base to claim 1."]


def test_m6_a_two_turn_script_assembles_the_same_prompts_through_the_route_and_through_replay(
    client, db_session, monkeypatch
):
    workspace, _ = create_workspace(db_session)
    agent = create_agent(db_session, workspace, setup_assist.ASSIST_CONFIG, setup_assist.ASSIST_ANALYSIS, kind="assist")
    monkeypatch.setenv("ASSIST_AGENT_ID", agent.id)
    monkeypatch.setenv("ASSIST_KEY", KEY)

    route_client = FakeClient(REPLIES)
    monkeypatch.setattr(groq_client, "client", route_client)
    history: list[dict] = []
    for question, reply in zip(QUESTIONS, REPLIES):
        response = client.post(
            "/assist/chat",
            json={"session_id": "session-m6-0001", "brief": BRIEF, "history": history, "question": question},
            headers={"X-Assist-Key": KEY},
        )
        assert response.status_code == 200
        history += [{"role": "user", "content": question}, {"role": "assistant", "content": response.json()["answer"]}]

    replay_client = FakeClient(REPLIES)
    monkeypatch.setattr(groq_client, "client", replay_client)
    transcript = replay_assist(current_prompt(agent), Brief.model_validate(BRIEF), QUESTIONS)

    assert transcript == history  # the same questions and the same final answers
    assert len(route_client.calls) == len(replay_client.calls) == 2
    for live, replayed in zip(route_client.calls, replay_client.calls):
        assert live["messages"] == replayed["messages"]  # system prompt and the assembled turns, byte for byte
        assert live["model"] == replayed["model"]
        assert live["max_tokens"] == replayed["max_tokens"]
    # the two documented differences (ARCHITECTURE, replay section): replay pins temperature 0, the route does not
    assert replayed["temperature"] == 0 and "temperature" not in live


# ---- M14: importer file-size cap and line streaming --------------------------------------------------

from pathlib import Path  # noqa: E402

from scripts import import_page_views  # noqa: E402

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "assist" / "page_views.example.jsonl"


def test_m14_an_oversize_file_is_refused_with_a_fixed_message(tmp_path, capsys, monkeypatch):
    path = tmp_path / "big.jsonl"
    path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(import_page_views, "MAX_FILE_BYTES", path.stat().st_size - 1)
    assert import_page_views.main([str(path), "--dry-run"]) == 2
    err = capsys.readouterr().err
    assert err.strip() == (
        f"error: {path} is larger than the {import_page_views.MAX_FILE_BYTES}-byte limit; nothing was imported"
    )


def test_m14_a_file_exactly_at_the_cap_is_read(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(import_page_views, "MAX_FILE_BYTES", EXAMPLE.stat().st_size)
    assert import_page_views.main([str(EXAMPLE), "--dry-run"]) == 0
    assert "12 page views" in capsys.readouterr().out


def test_m14_the_file_is_streamed_line_by_line_not_read_whole(capsys, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("the importer must not read the whole file at once")

    monkeypatch.setattr(Path, "read_text", boom)
    assert import_page_views.main([str(EXAMPLE), "--dry-run"]) == 0
    assert "12 page views" in capsys.readouterr().out


def test_m14_a_missing_file_and_bad_utf8_keep_their_exit_code_and_message(tmp_path, capsys):
    assert import_page_views.main([str(tmp_path / "missing.jsonl")]) == 2
    assert "could not read" in capsys.readouterr().err
    bad = tmp_path / "bad.jsonl"
    bad.write_bytes(b'{"x": "\xff\xfe"}\n')
    assert import_page_views.main([str(bad)]) == 2
    assert "could not read" in capsys.readouterr().err


def test_m14_crlf_and_bom_files_still_parse(tmp_path, capsys):
    path = tmp_path / "crlf.jsonl"
    path.write_bytes(b"\xef\xbb\xbf" + EXAMPLE.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"))
    assert import_page_views.main([str(path), "--dry-run"]) == 0
    assert "12 page views" in capsys.readouterr().out
