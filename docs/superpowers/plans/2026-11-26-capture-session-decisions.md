# Capture Session Decisions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python script + PreCompact hook that automatically extracts key decisions from every Claude Code session and writes them to the correct project memory folder, with a manually-invocable skill for mid-session use.

**Architecture:** A `PreCompact` hook in `~/.claude/settings.json` runs `capture-decisions.py` before every compaction. The script reads the session JSONL, calls Claude Haiku via the Anthropic API (with a model fallback chain for retirement), writes three memory files (project-state, session log, global feedback), and prints a one-paragraph summary to stdout which Claude Code injects into the compact. A companion skill wraps the same script with `--verbose --model claude-sonnet-4-6` for manual mid-session invocation.

**Tech Stack:** Python 3.8+, `anthropic` PyPI package, `pathlib`, `argparse`, `pytest` + `unittest.mock`

**Spec:** `docs/superpowers/specs/2026-11-26-capture-session-decisions-design.md`

## Global Constraints

- Script path: `C:\Users\91978\.claude\scripts\capture-decisions.py` (Windows); use `pathlib.Path.home() / ".claude"` everywhere — never hardcode the user's home
- Test file: `C:\Users\91978\.claude\scripts\test_capture_decisions.py`; run with `python -m pytest C:\Users\91978\.claude\scripts\test_capture_decisions.py -v`
- Skill path: `C:\Users\91978\.claude\skills\capture-session-decisions\SKILL.md`
- Python 3.8+ only — no walrus operator, no `match`, no `|` union types
- `onFailure: continue` on the hook — script must always exit 0 (never 2 or crash), even on total failure
- Model fallback chain (in priority order): read `model` from `~/.claude/settings.json` first, then `"claude-haiku-4-5-20251001"`, `"claude-haiku-4-6-20260101"`, `"claude-3-5-haiku-latest"`, `"claude-haiku-5-5"`; on `invalid_request_error` or `not_found_error` for a model, try next
- Max 150 messages extracted from JSONL (assistant + user text only; skip tool_use, tool_result, system)
- Memory frontmatter schema: `name`, `description`, `metadata.type`, `metadata.modified`, `metadata.captured_by: "capture-decisions-hook"`
- MEMORY.md index line format: `- [Title](filename.md) — one-line hook`

## Review Focus

1. **First run on a new project (no memory folder)** — script must create `~/.claude/projects/{slug}/memory/` and write files without crashing; tested in Task 3.
2. **JSONL not found** (session ID mismatch, empty project folder, or Windows path edge case) — script writes a skeleton file and exits 0; tested in Task 1.
3. **All models deprecated** (every model in chain returns `invalid_request_error`) — script writes skeleton with `"extraction unavailable"` body and exits 0; tested in Task 2.
4. **ANTHROPIC_API_KEY missing from env** — script writes skeleton, prints warning to stderr, exits 0; tested in Task 2.
5. **JSONL contains only tool/system messages (no user or assistant text)** — `extract_transcript` returns empty string; script writes skeleton rather than calling API with empty input; tested in Task 1.

---

### Task 1: Script infrastructure — slug, JSONL discovery, transcript extraction

**Files:**
- Create: `C:\Users\91978\.claude\scripts\capture-decisions.py`
- Create: `C:\Users\91978\.claude\scripts\test_capture_decisions.py`

**Interfaces:**
- Produces:
  - `slug_from_path(pwd: str) -> str` — lowercases path, replaces every `\`, `/`, `:` with `-`, strips leading `-`
  - `find_jsonl(slug: str, session_id: Optional[str], claude_dir: Path) -> Optional[Path]` — returns path or None
  - `extract_transcript(jsonl_path: Path, max_messages: int = 150) -> str` — returns newline-joined `"USER: ..."` / `"ASSISTANT: ..."` lines; empty string if none found
  - `parse_args() -> argparse.Namespace` — `--dry-run` (bool), `--verbose` (bool), `--model` (str, default `""`)

- [ ] **Step 1: Write failing tests**

```python
# test_capture_decisions.py
import json, pathlib, tempfile
from capture_decisions import slug_from_path, find_jsonl, extract_transcript

def test_slug_windows_path():
    assert slug_from_path("C:\\Users\\foo\\Eval-Discovery") == "c--users-foo-eval-discovery"

def test_slug_unix_path():
    assert slug_from_path("/home/foo/my-project") == "-home-foo-my-project"

def test_find_jsonl_by_session_id(tmp_path):
    (tmp_path / "abc123.jsonl").write_text('{"type":"user"}')
    result = find_jsonl("proj", "abc123", tmp_path.parent)
    # find_jsonl looks in claude_dir / "projects" / slug / "*.jsonl"
    # arrange: tmp_path IS claude_dir/projects/proj
    assert result is None  # slug mismatch; test proper path in next assertion

def test_find_jsonl_uses_most_recent_when_no_session_id(tmp_path):
    proj = tmp_path / "projects" / "myslug"
    proj.mkdir(parents=True)
    f1 = proj / "old.jsonl"; f1.write_text('{}')
    import time; time.sleep(0.01)
    f2 = proj / "new.jsonl"; f2.write_text('{}')
    result = find_jsonl("myslug", None, tmp_path)
    assert result == f2

def test_find_jsonl_returns_none_when_missing(tmp_path):
    assert find_jsonl("nope", "nope", tmp_path) is None

def test_extract_transcript_filters_tool_messages(tmp_path):
    lines = [
        json.dumps({"type": "user", "message": {"role": "user", "content": "hello"}}),
        json.dumps({"type": "tool_result", "message": {"role": "tool", "content": "ignored"}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "hi"}}),
    ]
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(lines))
    t = extract_transcript(p)
    assert "USER: hello" in t
    assert "ASSISTANT: hi" in t
    assert "tool" not in t.lower()

def test_extract_transcript_empty_when_no_text_messages(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"type": "tool_use", "message": {"role": "tool", "content": "x"}}))
    assert extract_transcript(p) == ""

def test_extract_transcript_respects_max_messages(tmp_path):
    lines = [json.dumps({"type": "user", "message": {"role": "user", "content": f"msg {i}"}}) for i in range(200)]
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(lines))
    t = extract_transcript(p, max_messages=10)
    assert t.count("USER:") == 10
    assert "msg 199" in t  # takes the LAST max_messages
```

- [ ] **Step 2: Run tests — expect FAIL (ImportError)**

```
python -m pytest C:\Users\91978\.claude\scripts\test_capture_decisions.py -v
```
Expected: `ImportError: No module named 'capture_decisions'`

- [ ] **Step 3: Implement `slug_from_path`, `find_jsonl`, `extract_transcript`, `parse_args` in `capture-decisions.py`**

`capture-decisions.py` starts with module-level functions so tests can import them. `extract_transcript` reads the last `max_messages` lines that have `role == "user"` or `role == "assistant"` and a string `content`; it skips any line that fails JSON parse. `find_jsonl` looks for `claude_dir / "projects" / slug / f"{session_id}.jsonl"` first; if `session_id` is falsy, returns the most-recently-modified `.jsonl` in that folder; returns `None` if the folder or any file is missing.

- [ ] **Step 4: Run tests — expect PASS**

```
python -m pytest C:\Users\91978\.claude\scripts\test_capture_decisions.py -v
```
Expected: 7 passed

- [ ] **Step 5: Commit**

```
git -C C:\Users\91978\.claude add scripts/capture-decisions.py scripts/test_capture_decisions.py
git -C C:\Users\91978\.claude commit -m "feat(capture): script skeleton — slug, JSONL discovery, transcript extraction"
```

---

### Task 2: Anthropic API call with model fallback chain

**Files:**
- Modify: `C:\Users\91978\.claude\scripts\capture-decisions.py`
- Modify: `C:\Users\91978\.claude\scripts\test_capture_decisions.py`

**Interfaces:**
- Consumes: `extract_transcript` → `str` (from Task 1)
- Produces:
  - `build_model_list(override: str, claude_dir: Path) -> list[str]` — prepends `override` if non-empty, then settings.json `model`, then the fixed fallback list; deduplicates preserving order
  - `call_extraction_api(transcript: str, models: list[str], api_key: str) -> dict` — returns parsed JSON dict on success; on all models exhausted OR empty transcript OR missing key returns `{"project_state_changes": [], "rulings": [], "open_items": ["extraction unavailable — see stderr"], "feedback_patterns": [], "one_paragraph_summary": "Session capture unavailable."}`

The extraction prompt is exactly:
```
You are extracting structured memory from a Claude Code session transcript.
Return ONLY valid JSON, no prose, no markdown fences.

{
  "project_state_changes": [{"what": "...", "detail": "...", "pending": false}],
  "rulings": [{"decision": "...", "why": "...", "cost_if_wrong": "..."}],
  "open_items": ["..."],
  "feedback_patterns": [{"pattern": "...", "cross_project": false}],
  "one_paragraph_summary": "..."
}

project_state_changes: code merged, files created/deleted, tests run, configs changed.
rulings: explicit design decisions with tradeoffs (not every small choice).
open_items: things waiting for the user, blocked work, known gaps.
feedback_patterns: how the user likes to work (cross_project=true means all projects).
one_paragraph_summary: 3–5 sentences, concrete, no preamble.

Transcript (last 150 messages):
{transcript}
```

- [ ] **Step 1: Write failing tests**

```python
# add to test_capture_decisions.py
from unittest.mock import patch, MagicMock
from capture_decisions import build_model_list, call_extraction_api
import anthropic

def test_build_model_list_prepends_override(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text('{"model": "claude-sonnet-4-6"}')
    models = build_model_list("claude-haiku-override", tmp_path)
    assert models[0] == "claude-haiku-override"
    assert "claude-sonnet-4-6" in models
    assert "claude-haiku-4-5-20251001" in models

def test_build_model_list_no_override_reads_settings(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text('{"model": "claude-sonnet-4-6"}')
    models = build_model_list("", tmp_path)
    assert models[0] == "claude-sonnet-4-6"

def test_build_model_list_no_settings_file(tmp_path):
    models = build_model_list("", tmp_path)
    assert "claude-haiku-4-5-20251001" in models

def test_call_extraction_api_returns_skeleton_when_no_key():
    result = call_extraction_api("hello", ["claude-haiku-4-5-20251001"], "")
    assert result["one_paragraph_summary"] == "Session capture unavailable."
    assert "extraction unavailable" in result["open_items"][0]

def test_call_extraction_api_returns_skeleton_on_empty_transcript():
    result = call_extraction_api("", ["claude-haiku-4-5-20251001"], "sk-ant-fake")
    assert result["one_paragraph_summary"] == "Session capture unavailable."

def test_call_extraction_api_falls_back_on_model_not_found():
    good_response = '{"project_state_changes":[],"rulings":[],"open_items":[],"feedback_patterns":[],"one_paragraph_summary":"ok"}'
    mock_client = MagicMock()
    # first model raises NotFoundError, second succeeds
    err = anthropic.NotFoundError(response=MagicMock(status_code=404), body={"error": {"type": "not_found_error"}}, message="model not found")
    good = MagicMock()
    good.content = [MagicMock(text=good_response)]
    mock_client.messages.create.side_effect = [err, good]
    with patch("capture_decisions.anthropic.Anthropic", return_value=mock_client):
        result = call_extraction_api("transcript", ["bad-model", "good-model"], "sk-ant-fake")
    assert result["one_paragraph_summary"] == "ok"
    assert mock_client.messages.create.call_count == 2

def test_call_extraction_api_skeleton_when_all_models_fail():
    mock_client = MagicMock()
    err = anthropic.NotFoundError(response=MagicMock(status_code=404), body={"error": {"type": "not_found_error"}}, message="model not found")
    mock_client.messages.create.side_effect = err
    with patch("capture_decisions.anthropic.Anthropic", return_value=mock_client):
        result = call_extraction_api("transcript", ["bad1", "bad2"], "sk-ant-fake")
    assert result["one_paragraph_summary"] == "Session capture unavailable."
```

- [ ] **Step 2: Run tests — expect FAIL**

```
python -m pytest C:\Users\91978\.claude\scripts\test_capture_decisions.py -v -k "build_model or call_extraction"
```
Expected: ImportError or NameError on the new functions

- [ ] **Step 3: Implement `build_model_list` and `call_extraction_api`**

`call_extraction_api` catches `anthropic.NotFoundError` and `anthropic.BadRequestError` (for deprecated model messages); parses the response `content[0].text` as JSON with `json.loads`; on `json.JSONDecodeError` tries once more by stripping markdown fences before returning skeleton. Install `anthropic` if needed: `pip install anthropic`.

- [ ] **Step 4: Run tests — expect PASS**

```
python -m pytest C:\Users\91978\.claude\scripts\test_capture_decisions.py -v
```
Expected: all prior tests + 7 new = 14 passed

- [ ] **Step 5: Commit**

```
git -C C:\Users\91978\.claude add scripts/capture-decisions.py scripts/test_capture_decisions.py
git -C C:\Users\91978\.claude commit -m "feat(capture): Anthropic API call with model fallback chain"
```

---

### Task 3: Memory file writing + main entrypoint

**Files:**
- Modify: `C:\Users\91978\.claude\scripts\capture-decisions.py`
- Modify: `C:\Users\91978\.claude\scripts\test_capture_decisions.py`

**Interfaces:**
- Consumes: `call_extraction_api` → `dict` (Task 2); `slug_from_path`, `find_jsonl`, `extract_transcript` (Task 1)
- Produces:
  - `write_project_state(memory_dir: Path, data: dict, today: str) -> Path`
  - `write_session_file(memory_dir: Path, data: dict, today: str) -> Path`
  - `update_memory_index(memory_dir: Path, entries: list[tuple[str, str, str]]) -> None` — each tuple is `(title, filename, hook_line)`
  - `write_global_feedback(claude_dir: Path, patterns: list[dict]) -> None` — appends only patterns not already in the file (deduplicate on `pattern` field)
  - `main() -> None` — wires everything together; always exits 0; prints `one_paragraph_summary` to stdout

**project-state.md** exact frontmatter:
```
---
name: project-state
description: "{slug} state as of {today}: {first state change 'what' or 'no changes'}"
metadata:
  type: project
  modified: {datetime.utcnow().isoformat()}
  captured_by: capture-decisions-hook
---
```

**session-YYYY-MM-DD.md** exact frontmatter:
```
---
name: session-{today}
description: "Session decisions {today}: {first ruling 'decision' or first state change 'what' or 'no decisions'}"
metadata:
  type: project
  session_date: {today}
  captured_by: capture-decisions-hook
---
```

- [ ] **Step 1: Write failing tests**

```python
# add to test_capture_decisions.py
from capture_decisions import write_project_state, write_session_file, update_memory_index, write_global_feedback
import datetime

SAMPLE_DATA = {
    "project_state_changes": [{"what": "merged console-v2", "detail": "fast-forward", "pending": False}],
    "rulings": [{"decision": "use haiku for hook", "why": "cheap", "cost_if_wrong": "lower quality"}],
    "open_items": ["run real browser checklist"],
    "feedback_patterns": [{"pattern": "no opus without permission", "cross_project": True}],
    "one_paragraph_summary": "Merged console v2 to main.",
}

def test_write_project_state_creates_file(tmp_path):
    p = write_project_state(tmp_path, SAMPLE_DATA, "2026-11-26")
    text = p.read_text()
    assert "name: project-state" in text
    assert "merged console-v2" in text
    assert "run real browser checklist" in text
    assert "captured_by: capture-decisions-hook" in text

def test_write_project_state_overwrites(tmp_path):
    write_project_state(tmp_path, SAMPLE_DATA, "2026-11-26")
    data2 = dict(SAMPLE_DATA, one_paragraph_summary="new summary")
    data2["project_state_changes"] = [{"what": "pushed to origin", "detail": "", "pending": False}]
    p = write_project_state(tmp_path, data2, "2026-11-26")
    text = p.read_text()
    assert "pushed to origin" in text
    assert "merged console-v2" not in text

def test_write_session_file_creates_new(tmp_path):
    p = write_session_file(tmp_path, SAMPLE_DATA, "2026-11-26")
    assert p.name == "session-2026-11-26.md"
    text = p.read_text()
    assert "use haiku for hook" in text
    assert "run real browser checklist" in text

def test_update_memory_index_adds_entries(tmp_path):
    update_memory_index(tmp_path, [("Project State", "project-state.md", "current project state")])
    idx = (tmp_path / "MEMORY.md").read_text()
    assert "- [Project State](project-state.md) — current project state" in idx

def test_update_memory_index_does_not_duplicate(tmp_path):
    update_memory_index(tmp_path, [("Project State", "project-state.md", "current project state")])
    update_memory_index(tmp_path, [("Project State", "project-state.md", "current project state")])
    idx = (tmp_path / "MEMORY.md").read_text()
    assert idx.count("project-state.md") == 1

def test_write_global_feedback_appends(tmp_path):
    write_global_feedback(tmp_path, [{"pattern": "no opus without permission", "cross_project": True}])
    f = (tmp_path / "memory" / "feedback-cross-project.md")
    assert f.exists()
    assert "no opus without permission" in f.read_text()

def test_write_global_feedback_deduplicates(tmp_path):
    write_global_feedback(tmp_path, [{"pattern": "no opus without permission", "cross_project": True}])
    write_global_feedback(tmp_path, [{"pattern": "no opus without permission", "cross_project": True}])
    text = (tmp_path / "memory" / "feedback-cross-project.md").read_text()
    assert text.count("no opus without permission") == 1

def test_write_global_feedback_skips_project_only_patterns(tmp_path):
    write_global_feedback(tmp_path, [{"pattern": "use port 8010", "cross_project": False}])
    f = tmp_path / "memory" / "feedback-cross-project.md"
    assert not f.exists()
```

- [ ] **Step 2: Run tests — expect FAIL on new functions**

```
python -m pytest C:\Users\91978\.claude\scripts\test_capture_decisions.py -v -k "write_ or update_memory or global_feedback"
```

- [ ] **Step 3: Implement `write_project_state`, `write_session_file`, `update_memory_index`, `write_global_feedback`, `main`**

`main()` flow:
1. `args = parse_args()`
2. `claude_dir = Path.home() / ".claude"`
3. `pwd = os.environ.get("PWD") or str(Path.cwd())`
4. `slug = slug_from_path(pwd)`
5. `session_id = os.environ.get("CLAUDE_SESSION_ID")`
6. `jsonl = find_jsonl(slug, session_id, claude_dir)` — if None, write skeleton, print skeleton summary, return
7. `transcript = extract_transcript(jsonl)` — if empty, write skeleton, print, return
8. `api_key = os.environ.get("ANTHROPIC_API_KEY", "")`
9. `models = build_model_list(args.model, claude_dir)`
10. `data = call_extraction_api(transcript, models, api_key)`
11. `memory_dir = claude_dir / "projects" / slug / "memory"`; `memory_dir.mkdir(parents=True, exist_ok=True)`
12. `today = datetime.utcnow().strftime("%Y-%m-%d")`
13. If not `args.dry_run`: write files, update indexes
14. `print(data["one_paragraph_summary"])` — this is captured by PreCompact as the hook's stdout injection
15. If `args.verbose`: print file paths written to stderr

Wrap the entire `main()` body in `try/except Exception` — log to stderr, print fallback summary to stdout, always return (never raise).

- [ ] **Step 4: Run all tests — expect PASS**

```
python -m pytest C:\Users\91978\.claude\scripts\test_capture_decisions.py -v
```
Expected: 22 passed

- [ ] **Step 5: Smoke test with --dry-run**

```powershell
cd C:\Users\91978\Eval-Discovery
python C:\Users\91978\.claude\scripts\capture-decisions.py --dry-run --verbose
```
Expected: prints a one-paragraph summary to stdout; prints "dry-run: would write …" lines to stderr; exits 0; no files created.

- [ ] **Step 6: Commit**

```
git -C C:\Users\91978\.claude add scripts/capture-decisions.py scripts/test_capture_decisions.py
git -C C:\Users\91978\.claude commit -m "feat(capture): memory file writing + main entrypoint"
```

---

### Task 4: settings.json hook update

**Files:**
- Modify: `C:\Users\91978\.claude\settings.json`

**Interfaces:**
- Consumes: `capture-decisions.py --dry-run` exits 0 (Task 3)

- [ ] **Step 1: Replace the `prompt` hook with the `command` hook**

In `C:\Users\91978\.claude\settings.json`, replace the `PreCompact` array content:

```json
"PreCompact": [
  {
    "matcher": "",
    "hooks": [
      {
        "type": "command",
        "command": "python C:\\Users\\91978\\.claude\\scripts\\capture-decisions.py",
        "shell": "powershell",
        "timeout": 60,
        "statusMessage": "Capturing session decisions to memory…",
        "onFailure": "continue"
      }
    ]
  }
]
```

- [ ] **Step 2: Verify settings.json is valid**

```powershell
Get-Content C:\Users\91978\.claude\settings.json | python -c "import sys,json; json.load(sys.stdin); print('valid')"
```
Expected: `valid`

- [ ] **Step 3: Verify the hook script runs without crashing from repo root**

```powershell
cd C:\Users\91978\Eval-Discovery
python C:\Users\91978\.claude\scripts\capture-decisions.py --dry-run
```
Expected: prints a one-paragraph summary and exits 0.

- [ ] **Step 4: Commit**

```
git -C C:\Users\91978\.claude add settings.json
git -C C:\Users\91978\.claude commit -m "feat(capture): replace prompt hook with command hook for PreCompact"
```

---

### Task 5: Skill file

**Files:**
- Create: `C:\Users\91978\.claude\skills\capture-session-decisions\SKILL.md`

**Interfaces:**
- Consumes: `capture-decisions.py --verbose --model MODEL` (Task 3)

- [ ] **Step 1: Write `SKILL.md`**

```markdown
---
name: capture-session-decisions
description: >
  Capture key decisions, rulings, task completions, and open items from
  the current session into long-term project and global memory.
  Use when you want to save what happened before context is lost.
  Runs automatically at PreCompact; invoke manually at any time with /capture-session-decisions.
triggers:
  - capture session decisions
  - save decisions to memory
  - capture what we did
  - save session memory
  - capture decisions
---

# Capture Session Decisions

## When to use

- Invoked manually: `/capture-session-decisions`
- Automatically: fires at every PreCompact event via the hook in `~/.claude/settings.json`

## What it does

Runs `capture-decisions.py` with `--verbose` and the current session model, then reports
to the user which files were written and what the one-paragraph summary says.

## Steps

1. Announce: "Capturing session decisions to long-term memory."

2. Run the script:

```powershell
python C:\Users\91978\.claude\scripts\capture-decisions.py --verbose --model claude-sonnet-4-6
```

3. Read stderr output to find the file paths written (the `--verbose` flag prints them to stderr).

4. Report to the user:
   - The one-paragraph summary (from stdout)
   - The files written (from stderr), as a bullet list
   - Any warnings (missing API key, model fallback triggered, etc.)

5. If `--dry-run` was passed (the user asked to preview only), say "Dry run — no files written" and show what would have been written.

## Notes

- The script always exits 0. If extraction failed, the summary will say "Session capture unavailable." — report that plainly.
- The script uses `claude-sonnet-4-6` when invoked via this skill (better quality than the automatic Haiku call).
- Memory files land in `~/.claude/projects/{slug}/memory/` for project-specific items and `~/.claude/memory/` for cross-project feedback patterns.
- To undo a capture, delete the `session-YYYY-MM-DD.md` file for that date and re-run `update_memory_index` manually, or simply edit the file.
```

- [ ] **Step 2: Verify skill is discoverable**

```powershell
Get-Content "C:\Users\91978\.claude\skills\capture-session-decisions\SKILL.md"
```
Expected: file contents printed without error.

- [ ] **Step 3: Smoke-test manual invocation**

In a Claude Code session in `C:\Users\91978\Eval-Discovery`, type `/capture-session-decisions`.  
Expected: Claude runs the script with `--verbose --model claude-sonnet-4-6`, reports files written and the summary.

- [ ] **Step 4: Commit**

```
git -C C:\Users\91978\.claude add skills/capture-session-decisions/SKILL.md
git -C C:\Users\91978\.claude commit -m "feat(capture): add capture-session-decisions skill"
```
