# Background Process Management — Spec

## Goal

Add background process management tools so the agent can start long-running processes (servers, watchers), verify they're working, and shut them down. Unblocks autonomous "start → test → stop" workflows.

Covers issue: #203.

## 1. New tools

All four tools live in a new module `src/decafclaw/tools/background_tools.py`.

### `shell_background_start(command)`

Start a background process. Returns immediately with a job ID.

**Parameters:**
- `command` (required) — shell command to run

**Behavior:**
- Uses `asyncio.create_subprocess_shell` with stdout/stderr piped
- Runs in the workspace path (same as `shell` tool)
- Spawns an async task to continuously read stdout/stderr into tail buffers
- Registers the job with the `BackgroundJobManager` on the conversation context
- Returns structured result with `job_id`, `status: "running"`, `command`, `pid`

**Confirmation:** Same model as `shell` tool — checks allowlist patterns, requests user approval if not matched. Reuse helpers from `confirmation.py` and allowlist logic from `shell_tools.py`.

### `shell_background_status(job_id)`

Check the status of a background job and get recent output.

**Parameters:**
- `job_id` (required) — job ID from `shell_background_start`

**Returns structured result:**
- `status` — `running`, `completed`, `error`, `expired`
- `exit_code` — process exit code (null if still running)
- `stdout` — last 500 lines of stdout
- `stderr` — last 500 lines of stderr
- `pid` — process ID
- `command` — the original command
- `elapsed_ms` — time since start
- `remaining_ms` — time until auto-kill (null if already finished)

**No confirmation needed** — read-only operation.

### `shell_background_stop(job_id)`

Terminate a background job.

**Parameters:**
- `job_id` (required) — job ID to stop

**Behavior:**
- Sends SIGTERM, waits briefly, then SIGKILL if still alive
- Returns final status and output

**No confirmation needed** — cleanup operation.

### `shell_background_list()`

List all active background jobs for the current conversation.

**Parameters:** none

**Returns:** List of jobs with job_id, command, status, pid, elapsed time.

**No confirmation needed.**

## 2. BackgroundJobManager

Per-conversation state, stored on the context. Manages job lifecycle.

### Job dataclass

```python
@dataclass
class BackgroundJob:
    job_id: str
    command: str
    process: asyncio.subprocess.Process
    pid: int
    started_at: float  # time.monotonic()
    max_lifetime: float  # seconds, default 600 (10 min)
    stdout_buffer: deque  # maxlen=500
    stderr_buffer: deque  # maxlen=500
    reader_task: asyncio.Task  # async task reading output
    exit_code: int | None = None
    status: str = "running"  # running, completed, error, expired
```

### BackgroundJobManager

```python
class BackgroundJobManager:
    jobs: dict[str, BackgroundJob]

    async def start(command, cwd, max_lifetime=600) -> BackgroundJob
    def get(job_id) -> BackgroundJob | None
    async def stop(job_id) -> BackgroundJob | None
    def list_active() -> list[BackgroundJob]
    async def cleanup_expired() -> list[BackgroundJob]  # kill jobs past lifetime
    async def cleanup_all() -> list[BackgroundJob]  # kill everything on shutdown
```

### Output reading

A background async task per job reads from stdout/stderr pipes line by line (`readline()`) and appends to the deque buffers. When the pipe returns empty bytes (EOF), the reader calls `process.wait()` to reap the child and captures `process.returncode`. Status updated to `completed` (exit code 0) or `error` (non-zero exit code).

The reader task handles both stdout and stderr in a single task using `asyncio.gather` on two line-reading coroutines, then waits for the process.

### Lifetime enforcement

Jobs have a default 10-minute max lifetime. `cleanup_expired()` is called lazily (on `start`, `status`, `list` calls) — not on a timer. When a job expires, it's killed (SIGTERM, wait 2 seconds, then SIGKILL if still alive) and status set to `expired`.

### Stop sequence

`stop()` sends SIGTERM, waits up to 2 seconds for the process to exit, then sends SIGKILL if still alive. After kill, calls `process.wait()` to reap. Cancels the reader task.

## 3. Integration with context

The `BackgroundJobManager` is per-conversation but stored in a **module-level dict** keyed by `conv_id`, not in `ctx.skills.data`. This is because `ctx.skills.data` gets JSON-serialized for persistence (`write_skill_data`), and `BackgroundJobManager` contains non-serializable state (subprocess handles, asyncio tasks, deques). Background processes can't survive a restart anyway, so persistence isn't needed.

```python
_managers: dict[str, BackgroundJobManager] = {}

def _get_job_manager(ctx) -> BackgroundJobManager:
    conv_id = ctx.conv_id or ctx.context_id
    if conv_id not in _managers:
        _managers[conv_id] = BackgroundJobManager()
    return _managers[conv_id]
```

No changes to `context.py` needed.

## 4. Design decisions

### Per-conversation, not global
Jobs are scoped to the conversation that started them. Different conversations can't see each other's jobs.

### Lazy expiration, not timer-based
No background timer thread. Expired jobs are cleaned up when any background tool is called. Simple, no concurrency concerns.

### 500-line tail buffer
`deque(maxlen=500)` for each of stdout and stderr. Returns all buffered lines on every status call. No cursor/incremental reads for now.

### Workspace-scoped
Commands run in `ctx.config.workspace_path`, same as the regular `shell` tool.

### No Claude Code integration
The Claude Code SDK manages its own subprocess environment. This is for the parent agent's shell tool only.

## 5. Files changed

- `src/decafclaw/tools/background_tools.py` — new module: BackgroundJob, BackgroundJobManager, 4 tool functions, BACKGROUND_TOOLS dict, BACKGROUND_TOOL_DEFINITIONS list
- `src/decafclaw/tools/__init__.py` — import and merge BACKGROUND_TOOLS and BACKGROUND_TOOL_DEFINITIONS into the combined registries (same pattern as other tool modules)
- Tests for job manager (start, status, stop, list, expiration, output buffering)

## 6. Out of scope

- Background process support inside Claude Code skill (SDK concern)
- Timer-based expiration (lazy is sufficient)
- Incremental output reads / cursor-based polling
- Job persistence across agent restarts
