# Claude Code Subagent Improvements — Spec

## Goal

Make the Claude Code skill a capable software development delegate by adding structured results, environment diagnostics, and lightweight shell execution. These three changes form the foundation for autonomous multi-step coding workflows.

Covers issues: #206, #205, #208 (from umbrella #213).

## 1. ToolResult.data field

Add a `data: dict | None = None` field to the shared `ToolResult` dataclass in `media.py`.

When `data` is present, the **agent loop** (in `agent.py`) appends a fenced JSON block to the tool result text before inserting it into conversation history. This happens in one place — tools don't format it themselves.

Format appended to text:

```
\n\n```json\n{... serialized data ...}\n```
```

Tools that don't set `data` are unaffected (field defaults to `None`).

## 2. Structured results from claude_code_send (#206)

### Current behavior

`claude_code_send` returns a markdown summary string built by `SessionLogger.build_summary()`. The parent agent parses prose to understand what happened.

### New behavior

`claude_code_send` returns a `ToolResult` with both `text` (markdown summary, same as today) and `data` (machine-readable dict).

### Structured data shape

```python
{
    "exit_status": str,       # "success" | "error" | "budget_exhausted" | "timeout" | "cancelled"
    "files_changed": list[str],  # paths relative to session cwd
    "tools_used": dict[str, int],  # tool_name → call count
    "errors": list[dict],     # [{"message": str, "file": str | None, "line": int | None}]
    "cost_usd": float,        # running total for the session
    "duration_ms": int,       # wall time for this send
    "send_count": int,        # how many sends in this session (including this one)
    "num_turns": int,         # LLM turns in this send
    "result_text": str,       # final text from Claude Code (may be long)
    "sdk_session_id": str,    # for debugging / resume tracking
    "log_path": str           # path to JSONL log file
}
```

### Implementation

- `SessionLogger.build_summary()` continues to produce the markdown text
- Add `SessionLogger.build_data()` → returns the dict above
- `claude_code_send` returns `ToolResult(text=summary, data=data_dict)`
- `exit_status` derived from: SDK result (success/error), budget check (budget_exhausted), timeout, cancellation
- Most fields already tracked by `SessionLogger` — just need to expose them as a dict instead of formatting into markdown

## 3. Session environment setup and diagnostics (#205)

### Current behavior

`claude_code_start` creates a `Session` dataclass and returns a formatted string. No environment validation.

### New behavior

`claude_code_start` runs a quick environment probe in the session's cwd and optionally executes a setup command. Returns structured results.

### New parameter

- `setup_command: str | None` — optional shell command to run before the session is ready (e.g., `"uv sync"`, `"npm install"`, `"make deps"`)

### Environment probe

A lightweight subprocess that checks:

- **Tools on PATH**: `python3`, `node`, `go`, `uv`, `pip`, `npm`, `pnpm`, `make`, `git`, `cargo`, `rustc` — just existence via `which`
- **Project files present**: `Makefile`, `pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml`, `CLAUDE.md`, `README.md`, `.env`
- **Git info** (if repo): current branch, clean/dirty status

The probe is generic — not language-specific. The parent agent interprets the results and makes project-specific decisions (e.g., "I see `pyproject.toml` and `uv` on PATH, so I'll pass `setup_command='uv sync'`").

### Setup command

If `setup_command` is provided:
- Run it as a subprocess in the session cwd
- Capture stdout, stderr, exit code
- If it fails (non-zero exit), still create the session but report the failure — the parent agent decides whether to proceed or abort

### Structured data shape

```python
{
    "session_id": str,
    "cwd": str,
    "model": str,
    "budget_usd": float,
    "environment": {
        "tools_available": list[str],    # names of tools found on PATH
        "project_files": list[str],      # config/project files found in cwd
        "git": {                         # null if not a git repo
            "branch": str,
            "clean": bool
        }
    },
    "setup": {                           # null if no setup_command provided
        "command": str,
        "exit_code": int,
        "stdout": str,
        "stderr": str,
        "status": str                    # "success" | "error"
    }
}
```

### Implementation

- Add probe logic as a helper function in `tools.py` (or a new `environment.py` if it gets big)
- Probe runs as `asyncio.create_subprocess_exec` calls (one per tool check) or a single shell script
- Prefer a single shell script for speed — batch all `which` checks into one subprocess
- `claude_code_start` becomes async (it already is since it's a tool function)
- Return `ToolResult(text=formatted_summary, data=structured_dict)`

## 4. Lightweight shell exec (#208)

### New tool: `claude_code_exec`

Runs a shell command directly in an active session's cwd. No LLM involved — just subprocess execution.

### Parameters

- `session_id: str` (required) — active session
- `command: str` (required) — shell command to run
- `timeout: int` (optional, default 30) — seconds before killing the process

### Confirmation model

Inherits from the session. If the user has already approved a `claude_code_send` for this session, execs are auto-approved. This avoids confirmation fatigue during tight verify loops (`exec make test` → `send "fix it"` → `exec make test`).

Implementation: track an `approved: bool` flag on the `Session` dataclass. Set to `True` when a `claude_code_send` is confirmed (or the session itself is allowlisted). `claude_code_exec` checks this flag — if approved, skip confirmation; otherwise, request it.

### Structured data shape

```python
{
    "exit_code": int | None,   # None if timeout
    "stdout": str,
    "stderr": str,
    "status": str,             # "success" | "error" | "timeout"
    "duration_ms": int,
    "command": str
}
```

No output capping — the parent agent can advise the subagent to limit output if needed.

### Logging

Commands and results are logged to the session's JSONL log file (same file as `claude_code_send` logs). Log entry format:

```json
{"type": "exec", "command": "...", "exit_code": 0, "stdout": "...", "stderr": "...", "duration_ms": 1234, "timestamp": "..."}
```

### Implementation

- Add `claude_code_exec` to `TOOLS` and `TOOL_DEFINITIONS` in `tools.py`
- Subprocess via `asyncio.create_subprocess_shell` with the session's cwd
- Timeout via `asyncio.wait_for` wrapping `process.communicate()`
- On timeout: kill process, return partial output with `status: "timeout"`
- Update SKILL.md with the new tool definition

## 5. SKILL.md updates

Update the skill documentation to reflect:
- `claude_code_start` new `setup_command` parameter and environment probe output
- `claude_code_send` structured result format
- `claude_code_exec` new tool with full parameter and result documentation
- Updated workflow section showing the verify loop pattern: start → send → exec (verify) → send (fix) → exec (verify) → stop

## 6. Files changed

- `src/decafclaw/media.py` — add `data: dict | None` field to `ToolResult`
- `src/decafclaw/agent.py` — serialize `data` as JSON block when building tool result messages
- `src/decafclaw/skills/claude_code/tools.py` — update `claude_code_start`, add `claude_code_exec`, update `claude_code_send` return
- `src/decafclaw/skills/claude_code/output.py` — add `build_data()` method to `SessionLogger`
- `src/decafclaw/skills/claude_code/sessions.py` — add `approved: bool` to `Session` dataclass
- `src/decafclaw/skills/claude_code/SKILL.md` — update tool docs
- Tests for new/changed functionality

## 7. Edge cases and constraints

### ToolResult return from tool functions

`execute_tool` already handles `ToolResult` passthrough via `_to_tool_result()` in `tools/__init__.py` — if a tool returns a `ToolResult` directly, it's used as-is (no double-wrapping). So `claude_code_send`, `claude_code_start`, and `claude_code_exec` can safely return `ToolResult` objects.

### Session approval flag lifecycle

The `approved` flag on `Session` is set to `True` when:
- A `claude_code_send` is confirmed by the user (or auto-approved via allowlist)
- The flag persists for the session's lifetime

If `claude_code_exec` is called on a session that hasn't been approved yet (e.g., user wants to `git status` before sending work), `exec` falls back to its own confirmation request. If the user approves, that also sets the `approved` flag.

### Environment probe timeout

The environment probe (tool checks, git info, project file scan) has a hard timeout of 5 seconds. If any subprocess hangs, it's killed and the probe returns partial results. The probe is best-effort — failures don't prevent session creation.

### Setup command confirmation

The `setup_command` runs arbitrary shell before any `claude_code_send` has been confirmed. It follows the same confirmation model: check the allowlist, otherwise request user confirmation. If confirmed, this also sets the session's `approved` flag.

### JSON serialization safety

`build_data()` and all structured result builders must return only JSON-safe types (str, int, float, bool, None, list, dict). No `Path` objects, datetimes, or custom types. The agent loop serializes `data` with `json.dumps()` — non-serializable values would crash the tool result.

## 8. Out of scope

- Test result parsing (language-specific, fragile)
- Output capping for exec (let the agent manage it)
- Context injection from parent (#207) — next session
- Diff output (#209) — next session
- Error classification (#210) — next session
- File staging (#211) — next session
- Progress reporting (#212) — next session
- Background process management (#203) — separate concern
- HTTP request tool (#204) — separate concern
