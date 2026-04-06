# Background Process Management — Plan

## Overview

Three steps. Step 1 builds the core module (dataclass, manager, output reader). Step 2 adds the four tool functions with confirmation and registration. Step 3 updates docs.

---

## Step 1: BackgroundJob dataclass, BackgroundJobManager, output reader

**What:** Create `src/decafclaw/tools/background_tools.py` with the `BackgroundJob` dataclass, `BackgroundJobManager` class, and the async output reader. Tested independently.

**Files:**
- `src/decafclaw/tools/background_tools.py` — new module
- `tests/test_background_tools.py` — tests for manager lifecycle

**Details:**

1. Create the module with imports: `asyncio`, `logging`, `time`, `dataclasses`, `collections.deque`, `uuid`.

2. `BackgroundJob` dataclass:
   - `job_id: str`, `command: str`, `process: asyncio.subprocess.Process`, `pid: int`
   - `started_at: float` (monotonic), `max_lifetime: float` (default 600)
   - `stdout_buffer: deque`, `stderr_buffer: deque` (both maxlen=500)
   - `reader_task: asyncio.Task | None`
   - `exit_code: int | None = None`, `status: str = "running"`

3. `_read_stream(stream, buffer)` — async helper that reads lines from a stream into a deque until EOF.

4. `_start_reader(process, job)` — creates an asyncio task that:
   - Gathers `_read_stream(process.stdout, job.stdout_buffer)` and `_read_stream(process.stderr, job.stderr_buffer)`
   - After both finish, calls `await process.wait()`
   - Sets `job.exit_code = process.returncode`
   - Sets `job.status = "completed" if returncode == 0 else "error"`

5. `BackgroundJobManager`:
   - `__init__`: `self.jobs: dict[str, BackgroundJob] = {}`
   - `async def start(command, cwd, max_lifetime=600)`: create subprocess, create job, start reader, store in dict, return job
   - `def get(job_id)`: return job or None, call `_cleanup_expired_sync` first
   - `async def stop(job_id)`: SIGTERM, wait 2s, SIGKILL if needed, wait(), cancel reader task, update status, return job
   - `def list_active()`: return all jobs, call `_cleanup_expired_sync` first
   - `async def cleanup_expired()`: find jobs past lifetime, stop each
   - `async def cleanup_all()`: stop all jobs

   Note: `_cleanup_expired_sync` just marks expired jobs — actual kill happens via `cleanup_expired()`. Or simpler: have `get`/`list_active` call `cleanup_expired` but that's async. Instead, have `get`/`list_active` be sync and just check+mark expired status, deferring the actual kill to the next async call. The tool functions will call `await manager.cleanup_expired()` at the start of each tool.

6. `_get_job_manager(ctx)` helper — lazy init from `ctx.skills.data`.

7. Tests:
   - `test_start_job`: start `echo hello && sleep 0.1`, verify job_id, pid, status="running"
   - `test_status_after_exit`: start `echo hello`, wait briefly, verify status="completed", exit_code=0, stdout contains "hello"
   - `test_status_error_exit`: start `exit 1`, wait, verify status="error", exit_code=1
   - `test_stop_running_job`: start `sleep 60`, stop it, verify status not "running"
   - `test_output_buffering`: start a command that outputs many lines, verify buffer is capped at 500
   - `test_cleanup_expired`: start a job with max_lifetime=0.1, wait, cleanup, verify status="expired"
   - `test_list_active`: start two jobs, list, verify both appear

---

## Step 2: Tool functions + registration

**What:** Add the four tool functions to `background_tools.py`. Wire confirmation for `start`. Register in `tools/__init__.py`.

**Files:**
- `src/decafclaw/tools/background_tools.py` — add tool functions, BACKGROUND_TOOLS, BACKGROUND_TOOL_DEFINITIONS
- `src/decafclaw/tools/__init__.py` — import and merge

**Details:**

1. `tool_shell_background_start(ctx, command)`:
   - Reuse shell confirmation logic: check heartbeat admin, preapproved, scoped patterns, allowlist, else request confirmation (import helpers from `shell_tools.py`)
   - If approved: `manager = _get_job_manager(ctx)`, `await manager.cleanup_expired()`, `job = await manager.start(command, ctx.config.workspace_path)`
   - Return `ToolResult(text=..., data={"job_id": ..., "status": "running", "command": ..., "pid": ...})`

2. `tool_shell_background_status(ctx, job_id)`:
   - `manager = _get_job_manager(ctx)`, `await manager.cleanup_expired()`
   - Get job, return error if not found
   - Return `ToolResult` with status, exit_code, stdout (joined lines), stderr, pid, command, elapsed_ms, remaining_ms

3. `tool_shell_background_stop(ctx, job_id)`:
   - `manager = _get_job_manager(ctx)`
   - `await manager.stop(job_id)`, return error if not found
   - Return final status and output

4. `tool_shell_background_list(ctx)`:
   - `manager = _get_job_manager(ctx)`, `await manager.cleanup_expired()`
   - List all jobs with summary info

5. `BACKGROUND_TOOLS` dict and `BACKGROUND_TOOL_DEFINITIONS` list.

6. In `tools/__init__.py`:
   - `from .background_tools import BACKGROUND_TOOL_DEFINITIONS, BACKGROUND_TOOLS`
   - Add to `TOOLS` dict and `TOOL_DEFINITIONS` list

7. Run `make lint`, `make test`, `make check`.

---

## Step 3: Docs

**What:** Document the new tools.

**Files:**
- `CLAUDE.md` — add `background_tools.py` to key files list

**Prompt:** Update `CLAUDE.md` key files to include the new module. No SKILL.md needed since these are core tools, not skill tools.

Run `make check` one final time.
