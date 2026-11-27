# Capture Session Decisions — Design

**Date:** 2026-11-26  
**Status:** Approved  
**Scope:** Global (not project-specific) — the script and skill live in `~/.claude/`; this spec lives in Eval-Discovery because that is where it was designed

---

## Intent and Why

Claude Code compacts long sessions, losing key decisions, rulings, and project state changes from context. The next session starts oriented only from whatever is in `MEMORY.md` and the memory files — which are updated manually and go stale. This system makes memory capture automatic: every time compaction fires, a script extracts what mattered and writes it to the right memory folder, so the next session (or a fresh session on the same project) is immediately oriented.

---

## Goals

- Automatically capture decisions, rulings, task completions, and open items at every `PreCompact` event
- Write to the correct project memory folder (or global memory for cross-project preferences)
- Work for every project without any per-project configuration
- Never block compaction — if extraction fails, compact still proceeds
- Handle model retirement gracefully without manual intervention
- Also be invocable manually mid-session via `/capture-session-decisions`

## Non-Goals

- Not a replacement for manual memory writes (Claude still writes memory when it notices something worth saving)
- Not a full conversation archive — targeted extraction only
- Not a Git commit or push (memory files are written to disk only)
- Does not capture question/answer text, only structural decisions and state

## Anti-Goals

- Must not block or slow the compact if the API is unavailable
- Must not write stale or wrong project state — it updates existing files, never blindly overwrites with hallucinated content

---

## Constraints

- `ANTHROPIC_API_KEY` must be a system/user environment variable (already true for Claude Code users)
- Python 3.8+ must be available on `PATH` (already true in the user's venv environment)
- Script must complete in under 60 seconds (hook timeout)
- No per-project `.env` dependency

---

## Approaches Considered

### A — Prompt hook only
Injects a summary into the compact via `PreCompact` prompt hook. Survives this compaction but not a new session. **Rejected** — too shallow.

### B — Command hook + Python script (chosen)
Hook runs `python ~/.claude/scripts/capture-decisions.py`. Script reads the session JSONL, calls Claude Haiku via Anthropic API, writes memory files, prints summary to stdout (injected into compact). Writes durable memory AND injects into the compact.

### C — Prompt + command hooks in sequence
Redundant — B already does both. **Rejected**.

---

## Design

### 1. The Hook (settings.json — global)

Replace the current `prompt` hook in `PreCompact` with:

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

`onFailure: continue` ensures compaction always proceeds even if the script errors.

### 2. The Script (`~/.claude/scripts/capture-decisions.py`)

**Inputs (from environment):**
- `PWD` — current working directory → used to derive project slug and find JSONL
- `ANTHROPIC_API_KEY` — from system env
- `CLAUDE_SESSION_ID` — session identifier (if set by Claude Code)
- `~/.claude/settings.json` — read to get the active model (prepended to fallback chain)

**Model fallback chain:**
```python
PREFERRED_MODELS = [
    # prepend model from ~/.claude/settings.json at runtime
    "claude-haiku-4-5-20251001",
    "claude-haiku-4-6-20260101",   # hypothetical next
    "claude-3-5-haiku-latest",     # Anthropic's own alias
    "claude-haiku-5-5",
]
```
Tries each; on `invalid_request_error` (model not found/deprecated) or `model_not_found`, moves to next. If all fail: writes a skeleton memory file noting the extraction failed, prints a minimal summary to stdout, exits 0 (not 2).

**Steps:**
1. Derive project slug from `PWD` (replace `\` and `/` with `-`, lowercase)
2. Find JSONL: `~/.claude/projects/{slug}/{session_id}.jsonl` (or most-recently-modified JSONL in that folder if `CLAUDE_SESSION_ID` not set)
3. Read last 150 messages from JSONL (assistant + user turns only, skipping tool results to stay under token limit)
4. Call Claude Haiku with extraction prompt (see below)
5. Parse structured JSON response
6. Write/update memory files
7. Print one-paragraph summary to stdout (for compact injection)
8. Exit 0

**Extraction prompt (sent to Claude Haiku):**
```
You are extracting structured memory from a Claude Code session transcript.

Return JSON with these fields:
{
  "project_state_changes": [
    {"what": "...", "detail": "...", "pending": true/false}
  ],
  "rulings": [
    {"decision": "...", "why": "...", "cost_if_wrong": "..."}
  ],
  "open_items": ["..."],
  "feedback_patterns": [
    {"pattern": "...", "cross_project": true/false}
  ],
  "one_paragraph_summary": "..."
}

Rules:
- project_state_changes: code merged, files created/deleted, tests run, configs changed
- rulings: explicit design decisions with tradeoffs (not every small choice)
- open_items: things waiting for the user, blocked work, known gaps
- feedback_patterns: how the user likes to work (cross_project=true means it applies to all projects)
- one_paragraph_summary: 3-5 sentences, concrete, no preamble

Transcript:
{transcript}
```

**Memory files written:**

| File | Condition | Action |
|---|---|---|
| `~/.claude/projects/{slug}/memory/project-state.md` | Always | Overwrite — single authoritative project state |
| `~/.claude/projects/{slug}/memory/session-YYYY-MM-DD.md` | Always | Create — session-specific decisions and rulings |
| `~/.claude/projects/{slug}/memory/MEMORY.md` | Always | Update index entries for files written |
| `~/.claude/memory/feedback-cross-project.md` | If `feedback_patterns` with `cross_project=true` | Append new patterns (deduplicated) |
| `~/.claude/memory/MEMORY.md` | If global feedback written | Update index |

**project-state.md format:**
```markdown
---
name: project-state
description: "{project name} state as of {YYYY-MM-DD}: {one-line summary}"
metadata:
  type: project
  modified: {ISO timestamp}
  captured_by: capture-decisions-hook
---

{project_state_changes rendered as prose}

**Open items:** {open_items as bullet list}

**Why:** {preserved from prior file if exists, else inferred}

**How to apply:** {open_items as actionable bullets}
```

**session-YYYY-MM-DD.md format:**
```markdown
---
name: session-{YYYY-MM-DD}
description: "Session decisions {YYYY-MM-DD}: {first ruling or first state change}"
metadata:
  type: project
  session_date: {YYYY-MM-DD}
  captured_by: capture-decisions-hook
---

## State changes
{project_state_changes}

## Rulings
{rulings with cost-if-wrong}

## Open items
{open_items}
```

### 3. The Skill (`~/.claude/skills/capture-session-decisions/SKILL.md`)

Invocable as `/capture-session-decisions` from any session. Same logic as the script but:
- Uses `claude-sonnet-4-6` (better quality, user is present and not in a hurry)
- Shows the user what it wrote (file paths + a summary) after writing
- Can be run mid-session (not just at compact time)
- Trigger description: "Use when you want to save key decisions from this session to long-term memory before they are lost. Runs automatically at PreCompact; invoke manually at any time."

The skill calls the same script with `--model claude-sonnet-4-6 --verbose` flags.

---

## File Locations Summary

```
~/.claude/
  scripts/
    capture-decisions.py        ← the script (global)
  skills/
    capture-session-decisions/
      SKILL.md                  ← the skill (global)
  settings.json                 ← hook added here (global)
  memory/
    feedback-cross-project.md   ← cross-project user patterns
    MEMORY.md                   ← global memory index
  projects/
    {slug}/
      memory/
        project-state.md        ← per-project state (auto-updated)
        session-YYYY-MM-DD.md   ← per-session decisions
        MEMORY.md               ← project memory index
```

---

## Failure Modes

| Failure | Behaviour |
|---|---|
| `ANTHROPIC_API_KEY` missing | Script prints warning to stderr, writes skeleton file, exits 0 |
| All models deprecated | Same as above — compact still proceeds |
| JSONL not found | Writes skeleton file with just timestamp, exits 0 |
| Memory folder doesn't exist | Script creates it |
| Script crashes | `onFailure: continue` — compact proceeds; error shown in Claude Code |
