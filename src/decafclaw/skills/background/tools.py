"""Background process management — start, monitor, and stop long-running processes."""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from decafclaw.media import ToolResult

log = logging.getLogger(__name__)

_OUTPUT_BUFFER_SIZE = 500
_DEFAULT_MAX_LIFETIME = 600  # 10 minutes
_STOP_GRACE_PERIOD = 2  # seconds between SIGTERM and SIGKILL


@dataclass
class BackgroundJob:
    """A background process tracked by the job manager."""
    job_id: str
    command: str
    process: asyncio.subprocess.Process
    pid: int
    started_at: float  # time.monotonic()
    max_lifetime: float = _DEFAULT_MAX_LIFETIME
    stdout_buffer: deque = field(default_factory=lambda: deque(maxlen=_OUTPUT_BUFFER_SIZE))
    stderr_buffer: deque = field(default_factory=lambda: deque(maxlen=_OUTPUT_BUFFER_SIZE))
    reader_task: asyncio.Task | None = None
    exit_code: int | None = None
    status: str = "running"  # running, completed, error, expired, stopped
    # Correlation for the exit-notification (populated by BackgroundJobManager.start).
    config: Any = None
    conv_id: str = ""
    event_bus: Any = None


async def _read_stream(stream: asyncio.StreamReader | None, buffer: deque) -> None:
    """Read lines from an async stream into a deque until EOF."""
    if stream is None:
        return
    while True:
        line = await stream.readline()
        if not line:
            break
        buffer.append(line.decode(errors="replace").rstrip("\n"))


async def _run_reader(job: BackgroundJob) -> None:
    """Read stdout/stderr until process exits, then capture exit code."""
    try:
        await asyncio.gather(
            _read_stream(job.process.stdout, job.stdout_buffer),
            _read_stream(job.process.stderr, job.stderr_buffer),
        )
        await job.process.wait()
        job.exit_code = job.process.returncode
        job.status = "completed" if job.exit_code == 0 else "error"
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.warning(f"Background job {job.job_id} reader error: {e}")
        job.status = "error"
    else:
        await _notify_job_exit(job)


async def _notify_job_exit(job: BackgroundJob) -> None:
    """Append an inbox notification for a background-job exit."""
    if job.config is None:
        return
    from decafclaw import notifications
    title = ("Background job completed" if job.exit_code == 0
             else "Background job failed")
    priority = "normal" if job.exit_code == 0 else "high"
    cmd_preview = job.command[:80] + ("..." if len(job.command) > 80 else "")
    body = f"{cmd_preview} (exit {job.exit_code})"
    last_stderr = job.stderr_buffer[-1] if job.stderr_buffer else ""
    if last_stderr and job.exit_code != 0:
        body += f" — {last_stderr[:120]}"
    try:
        await notifications.notify(
            job.config, job.event_bus,
            category="background", title=title, body=body,
            priority=priority, conv_id=job.conv_id or None,
        )
    except Exception as e:
        log.warning(f"Failed to emit background-job notification: {e}")


async def _kill_process(process: asyncio.subprocess.Process) -> None:
    """SIGTERM the process group, wait grace period, SIGKILL if needed."""
    import os
    import signal

    if process.returncode is not None:
        return  # already exited
    try:
        # Kill the whole process group (created via start_new_session=True)
        pgid = os.getpgid(process.pid)
        os.killpg(pgid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=_STOP_GRACE_PERIOD)
        except asyncio.TimeoutError:
            os.killpg(pgid, signal.SIGKILL)
            await process.wait()
    except (ProcessLookupError, PermissionError):
        pass  # already gone or can't signal


class BackgroundJobManager:
    """Manages background process lifecycle for a conversation."""

    def __init__(self):
        self.jobs: dict[str, BackgroundJob] = {}

    async def start(self, command: str, cwd: str,
                    max_lifetime: float = _DEFAULT_MAX_LIFETIME,
                    config: Any = None,
                    conv_id: str = "",
                    event_bus: Any = None) -> BackgroundJob:
        """Start a background process. Returns immediately.

        ``config``, ``conv_id``, and ``event_bus`` are carried into the
        job so the reader task can emit an inbox notification (and fan
        out to channel adapters) when the process exits.
        """
        process = await asyncio.create_subprocess_shell(
            command, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

        job = BackgroundJob(
            job_id=uuid4().hex[:12],
            command=command,
            process=process,
            pid=process.pid,
            started_at=time.monotonic(),
            max_lifetime=max_lifetime,
            config=config,
            conv_id=conv_id,
            event_bus=event_bus,
        )
        job.reader_task = asyncio.create_task(_run_reader(job))
        self.jobs[job.job_id] = job
        log.info(f"Started background job {job.job_id}: pid={job.pid}, command={command[:80]}")
        return job

    def get(self, job_id: str) -> BackgroundJob | None:
        """Get a job by ID, or None if not found."""
        return self.jobs.get(job_id)

    async def stop(self, job_id: str) -> BackgroundJob | None:
        """Stop a job. Returns the job, or None if not found."""
        job = self.jobs.get(job_id)
        if job is None:
            return None

        if job.status == "running":
            # If process already exited, let the reader finish updating status
            if job.process.returncode is not None:
                if job.reader_task and not job.reader_task.done():
                    try:
                        await asyncio.wait_for(job.reader_task, timeout=1.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass
            else:
                # Process still running — kill it
                await _kill_process(job.process)
                if job.reader_task and not job.reader_task.done():
                    job.reader_task.cancel()
                    try:
                        await job.reader_task
                    except asyncio.CancelledError:
                        pass
                # Only set "stopped" if reader didn't set a final status
                if job.status == "running":
                    job.exit_code = job.process.returncode
                    job.status = "stopped"

        log.info(f"Stopped background job {job.job_id}")
        return job

    def list_jobs(self) -> list[BackgroundJob]:
        """Return all tracked jobs (running and finished)."""
        return list(self.jobs.values())

    async def cleanup_expired(self) -> list[BackgroundJob]:
        """Kill expired jobs and remove stale finished jobs."""
        now = time.monotonic()
        expired = []
        to_remove = []
        for job in list(self.jobs.values()):
            if job.status == "running" and (now - job.started_at) > job.max_lifetime:
                log.info(f"Background job {job.job_id} expired after {job.max_lifetime}s")
                await _kill_process(job.process)
                if job.reader_task and not job.reader_task.done():
                    job.reader_task.cancel()
                    try:
                        await job.reader_task
                    except asyncio.CancelledError:
                        pass
                job.exit_code = job.process.returncode
                job.status = "expired"
                expired.append(job)
            elif job.status != "running" and (now - job.started_at) > job.max_lifetime:
                # Remove stale finished jobs to prevent unbounded growth
                to_remove.append(job.job_id)
        for job_id in to_remove:
            del self.jobs[job_id]
        return expired

    async def cleanup_all(self) -> list[BackgroundJob]:
        """Stop all jobs. For shutdown."""
        stopped = []
        for job_id in list(self.jobs.keys()):
            job = await self.stop(job_id)
            if job:
                stopped.append(job)
        self.jobs.clear()
        return stopped


# Module-level registry keyed by conv_id. We can't use ctx.skills.data
# because it gets JSON-serialized for persistence, and BackgroundJobManager
# contains non-serializable state (subprocess handles, asyncio tasks, deques).
_managers: dict[str, BackgroundJobManager] = {}


def _get_job_manager(ctx) -> BackgroundJobManager:
    """Get or create the per-conversation BackgroundJobManager."""
    conv_id = ctx.conv_id or ctx.context_id
    if conv_id not in _managers:
        _managers[conv_id] = BackgroundJobManager()
    manager = _managers[conv_id]
    # Clean up empty managers to prevent unbounded growth of _managers dict
    empty = [cid for cid, m in _managers.items() if not m.jobs and cid != conv_id]
    for cid in empty:
        del _managers[cid]
    return manager


# -- Tool functions -----------------------------------------------------------

async def tool_shell_background_start(ctx, command: str) -> ToolResult:
    """Start a background process. Returns immediately with a job ID."""
    log.info(f"[tool:shell_background_start] command={command[:80]}")

    from decafclaw.tools.shell_tools import check_shell_approval

    result = await check_shell_approval(
        ctx, command, tool_name="shell_background_start",
        message=f"Background process: `{command}`",
    )
    if not result.get("approved"):
        return ToolResult(
            text="[error: background process was denied by user]",
            data={"status": "error"},
        )

    manager = _get_job_manager(ctx)
    await manager.cleanup_expired()

    try:
        job = await manager.start(
            command, str(ctx.config.workspace_path),
            config=ctx.config, conv_id=ctx.conv_id,
            event_bus=ctx.event_bus,
        )
    except Exception as e:
        return ToolResult(
            text=f"[error: failed to start background process: {e}]",
            data={"status": "error"},
        )

    return ToolResult(
        text=(f"Background process started.\n"
              f"- **Job ID:** `{job.job_id}`\n"
              f"- **PID:** {job.pid}\n"
              f"- **Command:** `{command}`\n"
              f"- **Max lifetime:** {job.max_lifetime:.0f}s"),
        data={"job_id": job.job_id, "status": "running",
              "command": command, "pid": job.pid},
    )


async def tool_shell_background_status(ctx, job_id: str) -> ToolResult:
    """Check the status of a background job and get recent output."""
    log.info(f"[tool:shell_background_status] job_id={job_id}")

    manager = _get_job_manager(ctx)
    await manager.cleanup_expired()

    job = manager.get(job_id)
    if job is None:
        return ToolResult(
            text=f"[error: job '{job_id}' not found]",
            data={"status": "error"},
        )

    now = time.monotonic()
    elapsed_ms = int((now - job.started_at) * 1000)
    remaining_ms = None
    if job.status == "running":
        remaining = job.max_lifetime - (now - job.started_at)
        remaining_ms = max(0, int(remaining * 1000))

    stdout = "\n".join(job.stdout_buffer)
    stderr = "\n".join(job.stderr_buffer)

    # Text summary
    parts = [f"**Job `{job_id}`** — {job.status}"]
    parts.append(f"- **Command:** `{job.command}`")
    parts.append(f"- **PID:** {job.pid}")
    parts.append(f"- **Elapsed:** {elapsed_ms / 1000:.1f}s")
    if remaining_ms is not None:
        parts.append(f"- **Remaining:** {remaining_ms / 1000:.1f}s")
    if job.exit_code is not None:
        parts.append(f"- **Exit code:** {job.exit_code}")
    if stdout:
        parts.append(f"**stdout:**\n```\n{stdout}\n```")
    if stderr:
        parts.append(f"**stderr:**\n```\n{stderr}\n```")

    return ToolResult(
        text="\n".join(parts),
        data={
            "status": job.status,
            "exit_code": job.exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "pid": job.pid,
            "command": job.command,
            "elapsed_ms": elapsed_ms,
            "remaining_ms": remaining_ms,
        },
    )


async def tool_shell_background_stop(ctx, job_id: str) -> ToolResult:
    """Terminate a background job."""
    log.info(f"[tool:shell_background_stop] job_id={job_id}")

    manager = _get_job_manager(ctx)
    job = await manager.stop(job_id)

    if job is None:
        return ToolResult(
            text=f"[error: job '{job_id}' not found]",
            data={"status": "error"},
        )

    stdout = "\n".join(job.stdout_buffer)
    stderr = "\n".join(job.stderr_buffer)

    return ToolResult(
        text=(f"Background job `{job_id}` stopped.\n"
              f"- **Status:** {job.status}\n"
              f"- **Exit code:** {job.exit_code}"),
        data={
            "status": job.status,
            "exit_code": job.exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "pid": job.pid,
            "command": job.command,
        },
    )


async def tool_shell_background_list(ctx) -> ToolResult:
    """List all background jobs for the current conversation."""
    log.info("[tool:shell_background_list]")

    manager = _get_job_manager(ctx)
    await manager.cleanup_expired()

    jobs = manager.list_jobs()
    if not jobs:
        return ToolResult(text="No background jobs.", data={"jobs": []})

    now = time.monotonic()
    lines = [f"**Background jobs:** ({len(jobs)})\n"]
    job_list = []
    for job in jobs:
        elapsed = now - job.started_at
        lines.append(
            f"- `{job.job_id}` — {job.status} | "
            f"`{job.command[:50]}` | pid={job.pid} | "
            f"elapsed={elapsed:.0f}s"
        )
        job_list.append({
            "job_id": job.job_id,
            "command": job.command,
            "status": job.status,
            "pid": job.pid,
            "elapsed_ms": int(elapsed * 1000),
        })

    return ToolResult(
        text="\n".join(lines),
        data={"jobs": job_list},
    )


# -- Registration -------------------------------------------------------------

TOOLS = {
    "shell_background_start": tool_shell_background_start,
    "shell_background_status": tool_shell_background_status,
    "shell_background_stop": tool_shell_background_stop,
    "shell_background_list": tool_shell_background_list,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "shell_background_start",
            "description": (
                "Start a long-running background process (server, watcher, etc.). "
                "Returns immediately with a job_id. Use shell_background_status to "
                "check output, shell_background_stop to terminate. REQUIRES USER "
                "CONFIRMATION unless the command matches an allow pattern. "
                "Jobs auto-expire after 10 minutes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to run in the background",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_background_status",
            "description": (
                "Check the status of a background job. Returns running/completed/"
                "error/expired/stopped status, exit code, and the last 500 lines of "
                "stdout and stderr."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Job ID from shell_background_start",
                    },
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_background_stop",
            "description": (
                "Terminate a background job. Sends SIGTERM, then SIGKILL after "
                "2 seconds if the process is still alive. Returns final output."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Job ID to stop",
                    },
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_background_list",
            "description": (
                "List all background jobs for the current conversation. "
                "Shows job ID, command, status, PID, and elapsed time."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
