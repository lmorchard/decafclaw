# Claude Code Subagent Improvements — Plan

## Overview

Six steps, each building on the previous. Each step ends with lint + test + commit.

---

## Step 1: Add `data` field to ToolResult + agent loop serialization

**What:** Add `data: dict | None = None` to `ToolResult` dataclass. Wire the agent loop to append a fenced JSON block to tool result content when `data` is present.

**Files:**
- `src/decafclaw/media.py` — add field
- `src/decafclaw/agent.py` — serialize `data` in `_execute_single_tool_call()` at line 492

**Details:**

1. In `media.py`, add `data: dict | None = None` field to `ToolResult` after `display_short_text`.

2. In `agent.py`, in `_execute_single_tool_call()`, change the tool_msg content construction (around line 489-492):
   ```python
   content = result.text
   if result.data is not None:
       import json
       content += "\n\n```json\n" + json.dumps(result.data, indent=2) + "\n```"
   tool_msg = {
       "role": "tool",
       "tool_call_id": tool_call_id,
       "content": content,
   }
   ```

3. Add a test in `tests/` that verifies:
   - `ToolResult(text="hello", data={"key": "val"})` produces a tool message with JSON block appended
   - `ToolResult(text="hello")` (no data) produces unchanged content
   - `ToolResult(text="hello", data=None)` also unchanged

**Prompt:**

> In `src/decafclaw/media.py`, add a `data: dict | None = None` field to the `ToolResult` dataclass, after the `display_short_text` field.
>
> In `src/decafclaw/agent.py`, in the `_execute_single_tool_call()` function, modify the tool_msg construction (around line 489-492). When `result.data` is not None, append a fenced JSON block to the content string: `"\n\n```json\n" + json.dumps(result.data, indent=2) + "\n```"`. Import `json` at the top of the file if not already imported.
>
> Write a test in `tests/test_tool_result_data.py` that:
> - Creates a `ToolResult` with `data={"exit_status": "success", "cost": 1.23}` and verifies the field is accessible
> - Tests that `data=None` is the default
> - Tests the JSON serialization logic: given a `ToolResult` with text="hello" and data={"k": "v"}, verify that combining them produces `"hello\n\n```json\n...\n```"` with valid JSON in the block
>
> Run `make lint` and `make test` and fix any issues.

---

## Step 2: Add `approved` flag to Session + `build_data()` to SessionLogger

**What:** Add the `approved: bool` field to `Session`. Add `build_data()` method to `SessionLogger` that returns the structured dict.

**Files:**
- `src/decafclaw/skills/claude_code/sessions.py` — add `approved` field
- `src/decafclaw/skills/claude_code/output.py` — add `build_data()` method

**Details:**

1. In `sessions.py`, add `approved: bool = False` to the `Session` dataclass.

2. In `output.py`, add a `build_data()` method to `SessionLogger`:
   ```python
   def build_data(self, session_id: str = "", exit_status: str = "success",
                  sdk_session_id: str | None = None, send_count: int = 0) -> dict:
       """Return structured result dict with JSON-safe types only."""
       from collections import Counter
       tool_counts = dict(Counter(self.tools_used))
       unique_files = list(dict.fromkeys(self.files_changed))
       errors = [{"message": e} for e in self.errors[:10]]
       return {
           "exit_status": exit_status,
           "files_changed": unique_files,
           "tools_used": tool_counts,
           "errors": errors,
           "cost_usd": self.total_cost_usd,
           "duration_ms": self.duration_ms,
           "send_count": send_count,
           "num_turns": self.num_turns,
           "result_text": self.result_text,
           "sdk_session_id": sdk_session_id or "",
           "log_path": str(self.path),
       }
   ```

3. Write tests for `build_data()` output shape and JSON serializability.

**Prompt:**

> In `src/decafclaw/skills/claude_code/sessions.py`, add `approved: bool = False` to the `Session` dataclass, after the `send_count` field.
>
> In `src/decafclaw/skills/claude_code/output.py`, add a `build_data()` method to `SessionLogger`. It should accept `session_id: str = ""`, `exit_status: str = "success"`, `sdk_session_id: str | None = None`, and `send_count: int = 0`. It returns a dict with these fields:
> - `exit_status`: the passed-in value
> - `files_changed`: deduplicated list from `self.files_changed`
> - `tools_used`: dict of tool_name → count (use `collections.Counter` on `self.tools_used`)
> - `errors`: list of `{"message": str}` dicts from `self.errors` (limit 10)
> - `cost_usd`: `self.total_cost_usd`
> - `duration_ms`: `self.duration_ms`
> - `send_count`: passed-in value
> - `num_turns`: `self.num_turns`
> - `result_text`: `self.result_text`
> - `sdk_session_id`: passed-in value or ""
> - `log_path`: `str(self.path)`
>
> All values must be JSON-safe (str, int, float, bool, None, list, dict). No Path objects.
>
> Write a test in `tests/test_session_logger.py` that creates a `SessionLogger`, manually sets some metrics (files_changed, tools_used, errors, etc.), calls `build_data()`, and verifies:
> - The return value is a dict with the expected keys
> - `json.dumps()` succeeds on the result (no serialization errors)
> - `tools_used` is a count dict (e.g., `{"Edit": 2, "Read": 3}`)
> - `files_changed` is deduplicated
>
> Run `make lint` and `make test`.

---

## Step 3: Wire structured results into `claude_code_send`

**What:** Change `claude_code_send` to return `ToolResult(text=summary, data=structured_dict)`. Set the `approved` flag on confirmation. Determine `exit_status` from send outcomes.

**Files:**
- `src/decafclaw/skills/claude_code/tools.py` — modify `tool_claude_code_send`

**Details:**

1. Import `ToolResult` from `decafclaw.media`.

2. After the confirmation block succeeds, set `session.approved = True`.

3. For early returns (error, budget exhausted, cancelled), return `ToolResult` with appropriate `data`:
   - Denied: `ToolResult(text="[error: ...]", data={"exit_status": "cancelled"})`
   - Budget: `ToolResult(text="[error: ...]", data={"exit_status": "budget_exhausted", "cost_usd": session.total_cost_usd})`

4. After the SDK streaming completes, build both summary and data:
   ```python
   summary = logger.build_summary(session_id)
   data = logger.build_data(
       session_id=session_id,
       exit_status="error" if logger.errors else "success",
       sdk_session_id=session.sdk_session_id,
       send_count=session.send_count,
   )
   return ToolResult(text=summary, data=data)
   ```

5. For the SDK exception case, return `ToolResult` with `exit_status="error"`.

**Prompt:**

> In `src/decafclaw/skills/claude_code/tools.py`, modify `tool_claude_code_send` to return `ToolResult` instead of plain strings.
>
> 1. Add `from decafclaw.media import ToolResult` at the top of the file.
>
> 2. After the confirmation block succeeds (after the `if not confirm.get("approved")` check, around line 136-140), add `session.approved = True`.
>
> 3. Change the return type annotation from `-> str` to `-> ToolResult`.
>
> 4. For the denied-by-user case (line 137), return:
>    `ToolResult(text="[error: Claude Code task was denied by user]", data={"exit_status": "cancelled"})`
>
> 5. For the budget-exhausted case (lines 143-148), return:
>    `ToolResult(text=<existing error string>, data={"exit_status": "budget_exhausted", "cost_usd": session.total_cost_usd, "budget_usd": session.budget_usd})`
>
> 6. For the SDK exception case (line 218), return:
>    `ToolResult(text=f"[error: Claude Code failed: {e}]", data={"exit_status": "error", "errors": [{"message": str(e)}]})`
>
> 7. At the end of the function (replacing line 226), build and return structured result:
>    ```python
>    summary = logger.build_summary(session_id)
>    data = logger.build_data(
>        session_id=session_id,
>        exit_status="error" if logger.errors else "success",
>        sdk_session_id=session.sdk_session_id,
>        send_count=session.send_count,
>    )
>    return ToolResult(text=summary, data=data)
>    ```
>
> Run `make lint` and `make test`.

---

## Step 4: Environment probe for `claude_code_start`

**What:** Add environment probing and optional setup command to `claude_code_start`. Return `ToolResult` with structured data.

**Files:**
- `src/decafclaw/skills/claude_code/tools.py` — modify `tool_claude_code_start`, add probe helper

**Details:**

1. Add an `async def _probe_environment(cwd: str) -> dict` helper that:
   - Runs a single shell script via `asyncio.create_subprocess_shell` to batch-check tools on PATH
   - The script: `for cmd in python3 node go uv pip npm pnpm make git cargo rustc; do which $cmd >/dev/null 2>&1 && echo $cmd; done`
   - Checks for project files: scan cwd for known filenames (`Makefile`, `pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml`, `CLAUDE.md`, `README.md`, `.env`)
   - If `.git` exists in cwd, runs `git -C <cwd> branch --show-current` and `git -C <cwd> status --porcelain` to get branch and clean/dirty
   - Whole probe has 5-second timeout via `asyncio.wait_for`
   - Returns the structured dict: `{"tools_available": [...], "project_files": [...], "git": {"branch": "...", "clean": true} | None}`

2. Add an `async def _run_setup_command(cwd: str, command: str) -> dict` helper that:
   - Runs the command via `asyncio.create_subprocess_shell` in the cwd
   - Captures stdout/stderr
   - Returns `{"command": command, "exit_code": int, "stdout": str, "stderr": str, "status": "success"|"error"}`

3. Add `setup_command: str = ""` parameter to `tool_claude_code_start`.

4. After session creation, run the probe. If `setup_command` is provided, check confirmation (allowlist or request), then run it. Set `session.approved = True` if confirmed.

5. Return `ToolResult` with text summary and structured data.

6. Update `TOOL_DEFINITIONS` for `claude_code_start` to include the new `setup_command` parameter.

**Prompt:**

> In `src/decafclaw/skills/claude_code/tools.py`:
>
> 1. Add two helper functions before `tool_claude_code_start`:
>
>    `async def _probe_environment(cwd: str) -> dict`:
>    - Run a shell script via `asyncio.create_subprocess_shell` in the given cwd:
>      `for cmd in python3 node go uv pip npm pnpm make git cargo rustc; do which $cmd >/dev/null 2>&1 && echo $cmd; done`
>    - Parse stdout lines as `tools_available` list
>    - Scan cwd directory for project files: `Makefile`, `pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml`, `CLAUDE.md`, `README.md`, `.env` — check with `Path(cwd, name).exists()` for each
>    - If `Path(cwd, ".git").exists()`, run `git -C {cwd} branch --show-current` and `git -C {cwd} status --porcelain` to get branch name and whether the working tree is clean
>    - Wrap the entire probe in `asyncio.wait_for(..., timeout=5.0)`. On timeout, return partial results with what succeeded
>    - Return `{"tools_available": list[str], "project_files": list[str], "git": {"branch": str, "clean": bool} | None}`
>
>    `async def _run_setup_command(cwd: str, command: str) -> dict`:
>    - Run `command` via `asyncio.create_subprocess_shell` with `cwd=cwd`, capturing stdout and stderr
>    - Return `{"command": command, "exit_code": proc.returncode, "stdout": stdout_str, "stderr": stderr_str, "status": "success" if returncode == 0 else "error"}`
>
> 2. Modify `tool_claude_code_start` to accept `setup_command: str = ""` parameter.
>
> 3. After session creation (after the try/except ValueError block), run `_probe_environment(cwd)`.
>
> 4. If `setup_command` is non-empty:
>    - Check the allowlist for "claude_code_setup" pattern. If not matched, request confirmation with the command as the message. If confirmed, set `session.approved = True`. If "always", save the pattern. If denied, skip setup but still return the session (note the skip in the result).
>    - If approved (or allowlisted), run `_run_setup_command(cwd, setup_command)`.
>
> 5. Build structured data dict:
>    ```python
>    data = {
>        "session_id": session.session_id,
>        "cwd": session.cwd,
>        "model": session.model or (_skill_config.model if _skill_config else "") or "(SDK default)",
>        "budget_usd": session.budget_usd,
>        "environment": env_info,
>        "setup": setup_result,  # None if no setup_command
>    }
>    ```
>
> 6. Build text summary including environment info (tools available, project files, git branch) and setup result if applicable.
>
> 7. Return `ToolResult(text=text_summary, data=data)`. Change the return type annotation to `-> ToolResult`. Change the error returns to also use `ToolResult`.
>
> 8. Update the `claude_code_start` entry in `TOOL_DEFINITIONS` to add the `setup_command` property:
>    ```python
>    "setup_command": {
>        "type": "string",
>        "description": "Shell command to run for setup before the session is ready (e.g., 'uv sync', 'npm install')",
>    },
>    ```
>
> Run `make lint` and `make test`.

---

## Step 5: Add `claude_code_exec` tool

**What:** New tool for running shell commands in a session's cwd without an LLM turn. Structured results, session logging, inherited confirmation.

**Files:**
- `src/decafclaw/skills/claude_code/tools.py` — add tool function and definition
- `src/decafclaw/skills/claude_code/output.py` — add exec logging method

**Details:**

1. In `output.py`, add a `log_exec()` method to `SessionLogger`:
   ```python
   def log_exec(self, command: str, exit_code: int | None,
                stdout: str, stderr: str, duration_ms: int) -> None:
       record = {
           "type": "exec",
           "command": command,
           "exit_code": exit_code,
           "stdout": stdout,
           "stderr": stderr,
           "duration_ms": duration_ms,
           "timestamp": datetime.utcnow().isoformat(),
       }
       with open(self.path, "a") as f:
           f.write(json.dumps(record) + "\n")
   ```

2. In `tools.py`, add `tool_claude_code_exec`:
   - Look up session via manager (return error if not found/expired)
   - Check `session.approved` — if not approved, request confirmation. If approved, set flag. If denied, return error.
   - Run command via `asyncio.create_subprocess_shell` with `cwd=session.cwd`
   - Use `asyncio.wait_for` with the timeout parameter (default 30s, capped at 120s)
   - On timeout: kill process, capture partial output, set status to "timeout"
   - Log via `SessionLogger.log_exec()`
   - Touch session (update last_active)
   - Return `ToolResult(text=formatted_output, data=structured_dict)`

3. Add to `TOOLS` dict and `TOOL_DEFINITIONS` list.

**Prompt:**

> 1. In `src/decafclaw/skills/claude_code/output.py`, add a `log_exec()` method to `SessionLogger`:
>    - Parameters: `command: str`, `exit_code: int | None`, `stdout: str`, `stderr: str`, `duration_ms: int`
>    - Writes a JSON record to the session's JSONL log file with fields: `type` ("exec"), `command`, `exit_code`, `stdout`, `stderr`, `duration_ms`, `timestamp` (UTC ISO format)
>    - Import `datetime` from the standard library at the top
>
> 2. In `src/decafclaw/skills/claude_code/tools.py`, add `async def tool_claude_code_exec(ctx, session_id: str, command: str, timeout: int = 30) -> ToolResult`:
>    - Look up session via `_get_manager().get(session_id)`. Return `ToolResult(text="[error: session not found...]", data={"exit_status": "error"})` if not found.
>    - **Confirmation**: If `session.approved` is False, load allowlist and check for "claude_code_exec". If not matched, request confirmation with the command. If confirmed, set `session.approved = True`. If "always", save pattern. If denied, return error ToolResult.
>    - Clamp timeout to range [1, 120].
>    - Record start time with `time.monotonic()`.
>    - Run `command` via `asyncio.create_subprocess_shell(command, cwd=session.cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)`.
>    - Use `asyncio.wait_for(proc.communicate(), timeout=timeout)` to get stdout/stderr.
>    - On `asyncio.TimeoutError`: call `proc.kill()`, `await proc.wait()`, read any partial output, set exit_code=None and status="timeout".
>    - Calculate `duration_ms = int((time.monotonic() - start) * 1000)`.
>    - Decode stdout/stderr as UTF-8 (errors='replace').
>    - Determine status: "timeout" if timed out, "success" if exit_code == 0, "error" otherwise.
>    - Log via `SessionLogger(log_dir, session_id).log_exec(command, exit_code, stdout, stderr, duration_ms)` where log_dir is `_config.workspace_path / "claude-code-logs"`.
>    - Call `_get_manager().touch(session_id)`.
>    - Build text summary: command, exit code, stdout (if any), stderr (if any).
>    - Build data dict: `{"exit_code": exit_code, "stdout": stdout, "stderr": stderr, "status": status, "duration_ms": duration_ms, "command": command}`
>    - Return `ToolResult(text=text, data=data)`.
>
> 3. Add `"claude_code_exec": tool_claude_code_exec` to the `TOOLS` dict.
>
> 4. Add the tool definition to `TOOL_DEFINITIONS`:
>    ```python
>    {
>        "type": "function",
>        "function": {
>            "name": "claude_code_exec",
>            "description": (
>                "Run a shell command in an active Claude Code session's working directory. "
>                "No LLM turn — direct subprocess execution. Use for quick verification "
>                "(make test, git status, etc.) between claude_code_send calls."
>            ),
>            "parameters": {
>                "type": "object",
>                "properties": {
>                    "session_id": {
>                        "type": "string",
>                        "description": "Session ID from claude_code_start",
>                    },
>                    "command": {
>                        "type": "string",
>                        "description": "Shell command to run",
>                    },
>                    "timeout": {
>                        "type": "integer",
>                        "description": "Timeout in seconds (default 30, max 120)",
>                    },
>                },
>                "required": ["session_id", "command"],
>            },
>        },
>    }
>    ```
>
> Run `make lint` and `make test`.

---

## Step 6: Update SKILL.md + docs

**What:** Update skill documentation with new tool, updated parameters, structured result formats, and the verify loop workflow pattern.

**Files:**
- `src/decafclaw/skills/claude_code/SKILL.md`
- `CLAUDE.md` (if key files list needs updating)

**Prompt:**

> Update `src/decafclaw/skills/claude_code/SKILL.md`:
>
> 1. Update the `claude_code_start` tool section:
>    - Add `setup_command` parameter description
>    - Document the environment probe output (tools available, project files, git info)
>    - Note that it returns structured data with session info and environment diagnostics
>
> 2. Update the `claude_code_send` tool section:
>    - Document that it returns structured data alongside the text summary
>    - List the structured fields: exit_status, files_changed, tools_used, errors, cost_usd, duration_ms, send_count, num_turns, result_text, sdk_session_id, log_path
>    - Note the exit_status values: success, error, budget_exhausted, timeout, cancelled
>
> 3. Add a new `claude_code_exec` tool section:
>    - Description: run a shell command directly in a session's cwd, no LLM turn
>    - Parameters: session_id (required), command (required), timeout (optional, default 30, max 120)
>    - Document structured result: exit_code, stdout, stderr, status, duration_ms, command
>    - Note confirmation model: inherits from session approval
>
> 4. Update the workflow section to show the verify loop pattern:
>    ```
>    start → send (implement) → exec (make test) → send (fix failures) → exec (verify) → stop
>    ```
>
> 5. Read the current `CLAUDE.md` and check if any key files or conventions need updating for this work. The `ToolResult.data` field is a new convention worth noting.
>
> Run `make lint` and `make test` one final time. Then `make check` if available.
