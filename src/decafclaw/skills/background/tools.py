"""Background process management — start, monitor, and stop long-running processes."""

import asyncio
import codecs
import logging
import math
import os
import re
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from uuid import uuid4

from decafclaw.media import ToolResult, WidgetRequest
from decafclaw.progress_tracker import progress_payload, progress_step
from decafclaw.skills.background.parsers import (
    BaseProgressParser,
    ProgressParserPreset,
    get_progress_parser,
    parse_progress_preset,
)
from decafclaw.subprocess_limits import describe_kill
from decafclaw.tools.shell_tools import _resolve_shell_cwd, shell_cap_prefix

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)

_OUTPUT_BUFFER_SIZE = 500
_DEFAULT_RUNNING_TAIL_LINES = 20  # a running-job poll shows the recent tail, not all 500 lines
_DEFAULT_MAX_LIFETIME = 600  # 10 minutes
_STOP_GRACE_PERIOD = 2  # seconds between SIGTERM and SIGKILL
_STICKY_THROTTLE_SEC = 0.5  # min interval between sticky-slot progress updates
_STICKY_EMIT_TIMEOUT_SEC = 2.0  # delivery timeout for background sticky updates
_STICKY_NOTE_MAX = 200  # max chars of a single output line shown in the sticky slot
_MAX_PENDING_CHARS = 65536  # 64 KB cap to bound pending stream buffer


def _format_lifetime(seconds: float) -> str:
    """Render a job lifetime; ``inf`` means expiry is disabled (#1746)."""
    if not math.isfinite(seconds):
        return "none (expiry disabled)"
    return f"{seconds:.0f}s"


class JobStatus(StrEnum):
    """Lifecycle state of a background job (``BackgroundJob.status``, #1383).

    String-valued so archive records, tool-result data, and any JSON
    round-trip keep the existing wire values; the enum makes a typo'd
    status impossible to construct. Transitions: ``RUNNING`` →
    ``COMPLETED``/``ERROR`` (process exit), ``STOPPED`` (user stop), or
    ``EXPIRED`` (max lifetime, via ``cleanup_expired``).
    """

    RUNNING = "running"  # process alive and being monitored
    COMPLETED = "completed"  # exited with code 0
    ERROR = "error"  # non-zero exit, or the reader failed before exit
    EXPIRED = "expired"  # killed after max_lifetime elapsed
    STOPPED = "stopped"  # user-stopped via shell_background_stop


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
    # Absolute line counts survive buffer eviction, so an incremental read can
    # tell "no new output" from "the lines you missed scrolled out" (#1846).
    stdout_total_lines: int = 0
    stderr_total_lines: int = 0
    # Server-owned cursor for shell_background_status(cursor="auto"). The caller
    # never carries position state; it lives with the per-conversation job.
    cursor_stdout: int = 0
    cursor_stderr: int = 0
    # Set exactly once when the job leaves RUNNING, so a blocking status read
    # wakes on completion instead of polling.
    done_event: asyncio.Event = field(default_factory=asyncio.Event)
    reader_task: asyncio.Task | None = None
    exit_code: int | None = None
    status: JobStatus = JobStatus.RUNNING
    # Correlation for the exit-notification (populated by BackgroundJobManager.start).
    config: Any = None
    conv_id: str = ""
    event_bus: Any = None
    # Started on an attended turn: the human is (or was) in this conversation,
    # so the WAKE turn is the user-facing signal and the inbox record would only
    # add bell noise (#1842). Unattended jobs keep their inbox notification.
    attended: bool = False
    completion_tail_lines: int = 50
    pin_sticky: bool = False  # stream live output to the sticky slot (#1104)
    progress_parser: ProgressParserPreset = ProgressParserPreset.AUTO  # parser preset (#1137, #1384)
    parser: BaseProgressParser | None = None
    last_output_line: str = ""  # latest stdout/stderr line observed (#1104)
    finalized: bool = False
    manager: Any = None
    sticky_publish_task: asyncio.Task | None = None
    # Foreground-claim flag (#1136): set by the budgeted `shell` path BEFORE it
    # starts waiting on the process. While True, the reader's finally skips
    # _finalize_job — a command that settles as a foreground result must not also
    # fire the background completion sequence (archive record / notification /
    # wake). The handoff path clears it synchronously on budget expiry, so a job
    # that becomes background always settles exactly once through _finalize_job.
    foreground: bool = False


async def _read_stream(
    stream: asyncio.StreamReader | None,
    buffer: deque,
    on_line: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """Read lines/chunks from an async stream into a deque until EOF.

    Frames on \r\n, \r, and \n so in-place progress bars updating via
    carriage returns are delivered promptly to the on_line hook.
    Uses an incremental UTF-8 decoder and bounds pending buffer size.
    """
    if stream is None:
        return
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    pending = ""
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            pending += decoder.decode(b"", final=True)
            break
        pending += decoder.decode(chunk)
        if len(pending) >= _MAX_PENDING_CHARS and "\r" not in pending and "\n" not in pending:
            buffer.append(pending)
            if on_line is not None:
                try:
                    await on_line(pending)
                except Exception:
                    log.warning("on_line hook raised while reading stream; continuing", exc_info=True)
            pending = ""
            continue
        lines = re.split(r"\r\n|\r|\n", pending)
        pending = lines.pop()
        for line in lines:
            buffer.append(line)
            if on_line is not None:
                try:
                    await on_line(line)
                except Exception:
                    log.warning("on_line hook raised while reading stream; continuing", exc_info=True)
    if pending:
        buffer.append(pending)
        if on_line is not None:
            try:
                await on_line(pending)
            except Exception:
                log.warning("on_line hook raised while reading stream; continuing", exc_info=True)


async def _run_reader(job: BackgroundJob) -> None:
    """Read stdout/stderr until process exits, then capture exit code."""
    cancelled = False
    stdout_hook, stderr_hook = _stream_hooks(job)
    try:
        await asyncio.gather(
            _read_stream(job.process.stdout, job.stdout_buffer, on_line=stdout_hook),
            _read_stream(job.process.stderr, job.stderr_buffer, on_line=stderr_hook),
        )
        await job.process.wait()
        job.exit_code = job.process.returncode
        job.status = JobStatus.COMPLETED if job.exit_code == 0 else JobStatus.ERROR
    except asyncio.CancelledError:
        cancelled = True
        raise
    except Exception as e:
        log.warning(f"Background job {job.job_id} reader error: {e}")
        job.status = JobStatus.ERROR
        if job.process.returncode is not None:
            job.exit_code = job.process.returncode
    finally:
        # Close the transport even if _finalize_job raises or the reader is
        # cancelled mid-finalize — the deterministic-cleanup guarantee (#605)
        # must not depend on finalize succeeding. Foreground-claimed jobs
        # (#1136) skip finalize: the foreground path settles them (or aborts
        # them on cancellation), so no background completion sequence fires.
        try:
            if not cancelled and not job.foreground:
                await _finalize_job(job)
        finally:
            _close_transport(job)


def _close_transport(job: BackgroundJob) -> None:
    """Deterministically close the subprocess transport once the job is done.

    ``Process.wait()`` reaps the child but never closes the transport; asyncio
    otherwise leaves it for GC. Under pytest's per-test event loops that means
    ``BaseSubprocessTransport.__del__`` runs after the loop is closed, raising
    ``RuntimeError('Event loop is closed')`` as an unraisable and tripping
    ``PytestUnraisableExceptionWarning`` (#605). Closing it here — inside the
    still-live loop — sets ``_closed`` so ``__del__`` is a no-op. ``_transport``
    is asyncio-internal (no public accessor on ``Process``); ``close()`` is
    idempotent. Fail-open: cleanup must never break the reader.
    """
    transport = getattr(job.process, "_transport", None)
    if transport is None:
        return
    try:
        transport.close()
    except Exception as exc:
        log.debug("closing subprocess transport for %s failed: %s", job.job_id, exc)


async def _notify_job_exit(job: BackgroundJob) -> None:
    """Append an inbox notification for a background-job exit.

    Attended jobs skip the inbox: their WAKE turn already reports the result in
    the live conversation, so a bell entry is duplicate noise (#1842). The
    archive record and wake turn still fire for both kinds.
    """
    if job.config is None or job.attended:
        return
    from decafclaw import notifications

    title = "Background job completed"
    priority = "normal"
    if job.status == JobStatus.EXPIRED:
        title = "Background job expired"
        priority = "high"
    elif job.status == JobStatus.STOPPED:
        title = "Background job stopped"
    elif job.exit_code != 0:
        title = "Background job failed"
        priority = "high"
    cmd_preview = job.command[:80] + ("..." if len(job.command) > 80 else "")
    body = f"{cmd_preview} (exit {job.exit_code})"
    # An interrupted job must not read like a clean run: say that partial
    # output exists (and where it is) instead of reporting only an exit code
    # (#1746). The status tool and the archive record carry the tails.
    if job.status in (JobStatus.EXPIRED, JobStatus.STOPPED):
        body += (
            f" — captured {len(job.stdout_buffer)} stdout / {len(job.stderr_buffer)} stderr line(s) before termination"
        )
    # Say when a cgroup limit killed it: "-9" alone reads as a mystery crash,
    # and "it hit its memory cap" is the difference between a bug to chase and
    # a budget to raise.
    kill_note = describe_kill(job.exit_code, label="Background command")
    if kill_note:
        body += f" — {kill_note}"
    last_stderr = job.stderr_buffer[-1] if job.stderr_buffer else ""
    if last_stderr and job.exit_code != 0:
        body += f" — {last_stderr[:120]}"
    try:
        await notifications.notify(
            job.config,
            job.event_bus,
            category="background",
            title=title,
            body=body,
            priority=priority,
            conv_id=job.conv_id or None,
        )
    except Exception as e:
        log.warning(f"Failed to emit background-job notification: {e}")


async def _enqueue_wake(job: BackgroundJob) -> None:
    """Wake the agent via ConversationManager.enqueue_turn(kind=WAKE).
    Fail-open: a missing manager or a failed enqueue is logged but not raised
    — the archive record and user-facing inbox notification already landed."""
    if job.manager is None or not job.conv_id:
        log.debug("No manager or conv_id for job %s — skipping wake", job.job_id)
        return
    # Import here to avoid top-level circular dependency.
    from decafclaw.conversation_manager import TurnKind

    nudge = (
        "A background job you started has completed. Its status and output "
        "are in your history above. Review the result and take any follow-up "
        "action (respond to the user, call other tools, or reply with "
        "BACKGROUND_WAKE_OK to end the turn silently if no action is needed)."
    )
    try:
        await job.manager.enqueue_turn(
            conv_id=job.conv_id,
            kind=TurnKind.WAKE,
            prompt=nudge,
            metadata={"job_id": job.job_id},
        )
    except Exception as e:
        log.warning(f"Failed to enqueue wake turn for {job.job_id}: {e}")


def _cmd_preview(command: str) -> str:
    """Short command preview for sticky-slot labels."""
    return command[:80] + ("..." if len(command) > 80 else "")


def _last_output_line(job: BackgroundJob) -> str:
    """Most recent output line observed across stdout/stderr; '' when both empty."""
    return job.last_output_line


def _running_sticky_data(job: BackgroundJob) -> dict:
    """progress_tracker payload for a running job."""
    if job.parser is not None:
        try:
            return job.parser.format_running_sticky(job.command)
        except Exception:
            log.warning("Parser format_running_sticky failed for job %s", job.job_id, exc_info=True)
    last_line = _last_output_line(job)[:_STICKY_NOTE_MAX]
    step = progress_step(_cmd_preview(job.command), "in_progress", note=last_line or None)
    return progress_payload("Background job", last_line or f"Running: {_cmd_preview(job.command)}", [step])


def _final_sticky_data(job: BackgroundJob) -> dict:
    """progress_tracker payload for a completed or failed job."""
    if job.parser is not None:
        try:
            return job.parser.format_final_sticky(job.command, job.exit_code, job.status)
        except Exception:
            log.warning("Parser format_final_sticky failed for job %s", job.job_id, exc_info=True)
    last_line = _last_output_line(job)
    if job.status == JobStatus.COMPLETED:
        step_status = "done"
        summary = f"Completed: {_cmd_preview(job.command)} (exit {job.exit_code})"
        note = f"exit {job.exit_code}"
    else:
        step_status = "failed"
        summary = f"Failed: {_cmd_preview(job.command)} (exit {job.exit_code})"
        note = f"exit {job.exit_code}" + (f" — {last_line[:120]}" if last_line else "")
        # Name the cgroup cap in the sticky too (#1825). A run killed for
        # memory looks identical to a crash here otherwise, and the sticky is
        # often the only surface visible while the job is being watched.
        kill_note = describe_kill(job.exit_code, label="Background command")
        if kill_note:
            summary = f"{summary} — {kill_note}"
    step = progress_step(_cmd_preview(job.command), step_status, note=note)
    return progress_payload("Background job", summary, [step])


async def _publish_job_sticky(job: BackgroundJob) -> None:
    """Pin the job's running status into the sticky slot. Fail-open: a
    sticky failure must never break job monitoring."""
    if not job.pin_sticky or job.config is None or not job.conv_id:
        return
    try:
        from decafclaw import sticky as sticky_mod

        emit = job.manager.emit if job.manager is not None else None
        await asyncio.wait_for(
            sticky_mod.set_sticky(job.config, job.conv_id, "progress_tracker", _running_sticky_data(job), emit=emit),
            timeout=_STICKY_EMIT_TIMEOUT_SEC,
        )
    except Exception:
        log.warning("Failed to publish sticky update for background job %s", job.job_id, exc_info=True)


async def _sticky_publish_when_due(job: BackgroundJob, due_at: float, last: dict | None = None) -> None:
    """Publish the latest sticky snapshot once the throttle interval has elapsed."""
    delay = due_at - time.monotonic()
    if delay > 0:
        await asyncio.sleep(delay)
    if job.finalized:
        return
    now = time.monotonic()
    if last is not None:
        last["t"] = now
    await _publish_job_sticky(job)


async def _cancel_sticky_publish(job: BackgroundJob) -> None:
    """Cancel a pending throttled sticky publish before finalization."""
    task = job.sticky_publish_task
    job.sticky_publish_task = None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _finish_job_sticky(job: BackgroundJob) -> None:
    """Transition the sticky slot to the job's final state: done/failed for
    a completed/error exit, cleared for stopped/expired. Fail-open."""
    if not job.pin_sticky or job.config is None or not job.conv_id:
        return
    await _cancel_sticky_publish(job)
    try:
        from decafclaw import sticky as sticky_mod

        emit = job.manager.emit if job.manager is not None else None
        if job.status in (JobStatus.COMPLETED, JobStatus.ERROR):
            await sticky_mod.set_sticky(job.config, job.conv_id, "progress_tracker", _final_sticky_data(job), emit=emit)
        else:
            await sticky_mod.clear_sticky(job.config, job.conv_id, emit=emit)
    except Exception:
        log.warning("Failed to finalize sticky slot for background job %s", job.job_id, exc_info=True)


def _sticky_stream_hooks(
    job: BackgroundJob,
) -> tuple[Callable[[str], Awaitable[None]] | None, Callable[[str], Awaitable[None]] | None]:
    """Build throttled sticky callbacks for stdout and stderr streams."""
    if not job.pin_sticky:
        return None, None
    last = {"t": 0.0}

    def _make_hook(stream: str) -> Callable[[str], Awaitable[None]]:
        async def on_line(line: str) -> None:
            job.last_output_line = line
            if job.parser is not None:
                try:
                    job.parser.feed_line(line, stream=stream)
                except Exception:
                    log.warning("Progress parser feed_line failed for job %s", job.job_id, exc_info=True)
            now = time.monotonic()
            if now - last["t"] < _STICKY_THROTTLE_SEC:
                task = job.sticky_publish_task
                if task is None or task.done():
                    due_at = last["t"] + _STICKY_THROTTLE_SEC
                    job.sticky_publish_task = asyncio.create_task(_sticky_publish_when_due(job, due_at, last))
                return
            last["t"] = now
            task = job.sticky_publish_task
            if task is not None and not task.done():
                return
            job.sticky_publish_task = asyncio.create_task(_sticky_publish_when_due(job, now, last))

        return on_line

    return _make_hook("stdout"), _make_hook("stderr")


def _sticky_line_hook(job: BackgroundJob) -> Callable[[str], Awaitable[None]] | None:
    """Backwards-compatible single hook for tests."""
    stdout_hook, _ = _sticky_stream_hooks(job)
    return stdout_hook


def _stream_hooks(
    job: BackgroundJob,
) -> tuple[Callable[[str], Awaitable[None]], Callable[[str], Awaitable[None]]]:
    """Count every line and, when pin_sticky is on, forward it to the sticky hook.

    The counters always run: incremental status reads need an absolute
    position even when nothing is pinned (#1846).
    """
    sticky_stdout, sticky_stderr = _sticky_stream_hooks(job)

    def _counting(stream: str, sticky: Callable[[str], Awaitable[None]] | None) -> Callable[[str], Awaitable[None]]:
        async def on_line(line: str) -> None:
            if stream == "stdout":
                job.stdout_total_lines += 1
            else:
                job.stderr_total_lines += 1
            if sticky is not None:
                await sticky(line)

        return on_line

    return _counting("stdout", sticky_stdout), _counting("stderr", sticky_stderr)


async def _finalize_job(job: BackgroundJob) -> None:
    """Run the post-exit sequence: append archive record, finalize the sticky
    slot, emit inbox notification, enqueue a wake turn. Called EXACTLY ONCE
    per job at the moment status transitions out of 'running'.
    """
    if job.finalized:
        return
    job.finalized = True
    # Unblock any shell_background_status(wait=...) caller before the slower
    # archive/notification/wake sequence.
    job.done_event.set()

    # Deterministically release the subprocess transport now that the process
    # has exited, while the event loop is still alive. asyncio otherwise only
    # closes it when the Process is garbage-collected; if that GC runs after
    # the loop is gone (e.g. pytest-asyncio's function-scoped loop),
    # BaseSubprocessTransport.__del__ raises against a dead loop and surfaces
    # as a PytestUnraisableExceptionWarning (#632). Harmless in production
    # (single long-lived loop), but we close explicitly so cleanup is
    # deterministic regardless of loop lifetime. `_transport` is a real
    # asyncio.subprocess.Process attribute that its type stub omits.
    transport = job.process._transport  # type: ignore[attr-defined]
    if transport is not None:
        transport.close()

    if job.config is None:
        return  # no config => nothing we can do

    # 1. Append background_event to conversation archive.
    try:
        from decafclaw.archive import append_message

        elapsed_ms = int((time.monotonic() - job.started_at) * 1000)
        rec = build_background_event_record(
            job_id=job.job_id,
            command=job.command,
            status=job.status,
            exit_code=job.exit_code,
            stdout_buffer=job.stdout_buffer,
            stderr_buffer=job.stderr_buffer,
            elapsed_ms=elapsed_ms,
            completion_tail_lines=job.completion_tail_lines,
        )
        if job.conv_id:
            append_message(job.config, job.conv_id, rec)
            # Emit on the conv event stream so live subscribers (web UI, etc.)
            # see the completion immediately when the record is appended.
            if job.manager is not None:
                try:
                    await job.manager.emit(
                        job.conv_id,
                        {
                            "type": "background_event",
                            "record": rec,
                        },
                    )
                except Exception as emit_e:
                    log.warning(f"Failed to emit background_event event for {job.job_id}: {emit_e}")
    except Exception as e:
        log.warning(f"Failed to append background_event for {job.job_id}: {e}")

    # 2. Finalize the sticky slot if this job was streaming progress there
    #    (#1104): done/failed on a normal exit, cleared when stopped/expired.
    await _finish_job_sticky(job)

    # 3. User-facing inbox notification.
    await _notify_job_exit(job)

    # 4. Enqueue wake turn.
    await _enqueue_wake(job)


def _tail_clamped(lines: deque, n: int, max_bytes: int = 4096) -> str:
    """Take the last n lines; join with \\n; drop oldest lines until under max_bytes."""
    if n <= 0:
        return ""
    tail = list(lines)[-n:]
    s = "\n".join(tail)
    # Drop from the oldest end until within byte budget.
    while len(s.encode("utf-8")) > max_bytes and tail:
        tail = tail[1:]
        s = "\n".join(tail)
    return s


def _output_window(
    buffer: deque,
    total: int,
    *,
    cursor: int | None,
    from_start: bool,
    tail_lines: int | None,
) -> tuple[list[str], int, int, bool]:
    """Select the output lines for one status read (#1846).

    Returns ``(lines, next_cursor, gap_lines, buffer_truncated)``.

    - ``cursor is None``: full/default read; ``tail_lines`` may trim it.
    - ``from_start``: explicit re-read of everything still buffered; the cursor
      is not advanced.
    - otherwise: incremental read from ``cursor``. If that position has left
      the 500-line buffer, return the whole buffer and report the gap — never
      silently drop output.
    """
    buffered = list(buffer)
    available_start = max(0, total - len(buffered))
    if from_start or cursor is None:
        lines = buffered
        if tail_lines is not None:
            lines = lines[-tail_lines:] if tail_lines else []
        return lines, cursor if cursor is not None else total, 0, total > len(buffered)

    gap = max(0, available_start - cursor) + max(0, cursor - total)
    if gap:
        return buffered, total, gap, False
    return buffered[cursor - available_start :], total, 0, False


def _window_notes(
    label: str,
    lines: list[str],
    total: int,
    gap_lines: int,
    buffer_truncated: bool,
    *,
    status: JobStatus,
    cursor_mode: bool,
    tail_applied: bool,
) -> list[str]:
    """Explain anything a status read is not showing, so it is never quietly partial."""
    notes: list[str] = []
    if gap_lines:
        notes.append(
            f"{label} cursor gap: {gap_lines} line(s) were not shown because they left "
            f"the {_OUTPUT_BUFFER_SIZE}-line buffer."
        )
    if buffer_truncated and not tail_applied:
        notes.append(
            f"{label}: showing the last {len(lines)} of {total} captured line(s); "
            f"older lines left the {_OUTPUT_BUFFER_SIZE}-line buffer."
        )
    if cursor_mode and not lines and total > 0:
        if status == JobStatus.RUNNING:
            notes.append(f"{label}: no new output since the last cursor read.")
        else:
            notes.append(
                f"{label}: cursor caught up; no unread output. Use from_start=true to re-read the buffered output."
            )
    elif tail_applied and len(lines) < total:
        notes.append(f"{label}: showing the last {len(lines)} of {total} captured line(s) while the job runs.")
    return notes


def build_background_event_record(
    *,
    job_id: str,
    command: str,
    status: JobStatus,
    exit_code: int | None,
    stdout_buffer: deque,
    stderr_buffer: deque,
    elapsed_ms: int,
    completion_tail_lines: int,
) -> dict:
    """Build a background_event archive record.

    Tails are each clamped to `completion_tail_lines` lines AND a 4KB byte
    ceiling (whichever is tighter). The in-memory buffer (_OUTPUT_BUFFER_SIZE
    lines) remains the authoritative source for richer data via
    shell_background_status.
    """
    n = max(0, min(completion_tail_lines, _OUTPUT_BUFFER_SIZE))
    return {
        "role": "background_event",
        "timestamp": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "job_id": job_id,
        "command": command,
        "status": status,
        "exit_code": exit_code,
        # Persisted with the record, not derived at render time (#1825): the
        # woken agent expands this archive row into status text, and a bare
        # exit code there is the same dead end the live status used to be.
        "kill_note": describe_kill(exit_code, label="Background command"),
        "stdout_tail": _tail_clamped(stdout_buffer, n),
        "stderr_tail": _tail_clamped(stderr_buffer, n),
        "elapsed_ms": elapsed_ms,
        "completion_tail_lines": n,
    }


def format_status_text(
    *,
    job_id: str,
    status: str,
    command: str,
    pid: int,
    elapsed_ms: int,
    remaining_ms: int | None,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    kill_note: str | None = None,
    captured_output: bool = False,
) -> str:
    """Render a background-job status as markdown text.

    Shared by tool_shell_background_status (when the agent polls) and the
    ContextComposer's expansion of background_event archive records (so a
    woken agent sees the same format).

    ``kill_note`` lets a caller forward a note persisted on the archive record;
    when omitted it is derived from ``exit_code``. ``captured_output`` says the
    job produced lines that this particular view omitted (a caught-up cursor),
    so the "none captured before termination" fallback must not fire.
    """
    parts = [f"**Job `{job_id}`** — {status}"]
    parts.append(f"- **Command:** `{command}`")
    parts.append(f"- **PID:** {pid}")
    parts.append(f"- **Elapsed:** {elapsed_ms / 1000:.1f}s")
    if remaining_ms is not None:
        parts.append(f"- **Remaining:** {remaining_ms / 1000:.1f}s")
    if exit_code is not None:
        parts.append(f"- **Exit code:** {exit_code}")
        # Say when a cgroup limit killed it (#1825). A bare "-15" reads as a
        # mystery crash and sends the agent chasing a supervisor bug that does
        # not exist; the caps config, not the code, is the thing to change.
        # _notify_job_exit has always carried this note, but the status text
        # is what the agent reads while debugging, so it has to carry it too.
        note = kill_note or describe_kill(exit_code, label="Background command")
        if note:
            parts.append(f"- **Likely cause:** {note}")
    if stdout:
        parts.append(f"**stdout:**\n```\n{stdout}\n```")
    if stderr:
        parts.append(f"**stderr:**\n```\n{stderr}\n```")
    if (
        not stdout
        and not stderr
        and not captured_output
        and status not in (JobStatus.COMPLETED.value, JobStatus.RUNNING.value)
    ):
        # A killed or failed run with no captured lines is not a clean
        # "no output" success; say so explicitly (#1746).
        parts.append("- **Output:** none captured before termination")
    return "\n".join(parts)


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

    async def start(
        self,
        command: str,
        cwd: str,
        max_lifetime: float = _DEFAULT_MAX_LIFETIME,
        config: Any = None,
        conv_id: str = "",
        event_bus: Any = None,
        attended: bool = False,
        completion_tail_lines: int = 50,
        manager: Any = None,
        pin_sticky: bool = False,
        progress_parser: str | ProgressParserPreset = ProgressParserPreset.AUTO,
        env: dict | None = None,
    ) -> BackgroundJob:
        """Start a background process. Returns immediately.

        ``config``, ``conv_id``, ``event_bus``, ``manager``, and ``pin_sticky``
        are carried into the job so the reader task can emit an inbox
        notification (and fan out to channel adapters), stream progress to the
        sticky slot, and enqueue a WAKE turn when the process exits.
        ``attended`` records whether the originating turn had a human watching;
        attended jobs skip the inbox notification (#1842). ``env``
        replaces the inherited environment (None = inherit, as before); the
        budgeted foreground shell path (#1136) uses it to expose
        DECAFCLAW_WORKSPACE like the legacy foreground runner did.
        """
        # Same cgroup caps as the foreground shell path: a long-running command
        # is more likely to grow, not less, so a background job that outgrows
        # its budget must die in its scope rather than starve the daemon.
        #
        # See shell_tools._execute_command for why the command text bounces
        # through DECAFCLAW_SHELL_CMD + execl instead of sitting on argv:
        # systemd < 256 unescapes "$$" to "$" and expands "$VAR" in ExecStart.
        cap_prefix = shell_cap_prefix(config, label="background command")
        if env is None:
            child_env = os.environ.copy()
        else:
            child_env = env.copy()

        if cap_prefix:
            child_env["DECAFCLAW_SHELL_CMD"] = command
            spawn_args = [
                *cap_prefix,
                sys.executable,
                "-c",
                "import os; os.execl('/bin/sh', 'sh', '-c', os.environ['DECAFCLAW_SHELL_CMD'])",
            ]
        else:
            spawn_args = ["/bin/sh", "-c", command]

        process = await asyncio.create_subprocess_exec(
            *spawn_args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env=child_env,
        )

        preset = parse_progress_preset(progress_parser)
        parser = get_progress_parser(preset) if pin_sticky else None
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
            attended=attended,
            completion_tail_lines=completion_tail_lines,
            manager=manager,
            pin_sticky=pin_sticky,
            progress_parser=preset,
            parser=parser,
        )
        job.reader_task = asyncio.create_task(_run_reader(job))
        self.jobs[job.job_id] = job
        log.info(f"Started background job {job.job_id}: pid={job.pid}, command={command[:80]}")
        if job.pin_sticky:
            # Initial "Running" pin so the slot appears before the first
            # output line arrives.
            await _publish_job_sticky(job)
        return job

    def get(self, job_id: str) -> BackgroundJob | None:
        """Get a job by ID, or None if not found."""
        return self.jobs.get(job_id)

    async def stop(self, job_id: str) -> BackgroundJob | None:
        """Stop a job. Returns the job, or None if not found."""
        job = self.jobs.get(job_id)
        if job is None:
            return None

        if job.status == JobStatus.RUNNING:
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
                if job.status == JobStatus.RUNNING:
                    job.exit_code = job.process.returncode
                    job.status = JobStatus.STOPPED

        log.info(f"Stopped background job {job.job_id}")
        await _finalize_job(job)
        return job

    async def abort(self, job_id: str) -> None:
        """Kill a job WITHOUT the completion sequence (#1136).

        No archive record, no inbox notification, no wake: the job never
        "completed" — it was aborted. Used when a foreground-claimed command's
        turn is cancelled before the handoff, so no unmanaged process survives.
        Idempotent; unknown job IDs are a no-op.
        """
        job = self.jobs.pop(job_id, None)
        if job is None:
            return
        try:
            await _kill_process(job.process)
        except Exception as exc:  # noqa: BLE001 - abort must not raise
            log.warning(f"abort: killing process for job {job_id} failed: {exc}")
        if job.reader_task is not None and not job.reader_task.done():
            # The reader's finally skips _finalize_job for a cancelled reader
            # and still closes the transport deterministically (#605).
            job.reader_task.cancel()
            try:
                await job.reader_task
            except asyncio.CancelledError:
                pass  # expected: we just cancelled it

    def list_jobs(self) -> list[BackgroundJob]:
        """Return all tracked jobs (running and finished)."""
        return list(self.jobs.values())

    async def cleanup_expired(self) -> list[BackgroundJob]:
        """Kill expired jobs and remove stale finished jobs."""
        now = time.monotonic()
        expired = []
        to_remove = []
        for job in list(self.jobs.values()):
            if job.status == JobStatus.RUNNING and (now - job.started_at) > job.max_lifetime:
                log.info(f"Background job {job.job_id} expired after {job.max_lifetime}s")
                await _kill_process(job.process)
                if job.reader_task and not job.reader_task.done():
                    job.reader_task.cancel()
                    try:
                        await job.reader_task
                    except asyncio.CancelledError:
                        pass
                job.exit_code = job.process.returncode
                job.status = JobStatus.EXPIRED
                await _finalize_job(job)
                expired.append(job)
            elif job.status != JobStatus.RUNNING:
                # Remove stale finished jobs to prevent unbounded growth. A
                # job with expiry disabled still needs a retention window,
                # or it would never be collected (#1746).
                retention = job.max_lifetime if math.isfinite(job.max_lifetime) else _DEFAULT_MAX_LIFETIME
                if (now - job.started_at) > retention:
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


def _get_job_manager(ctx: "Context") -> BackgroundJobManager:
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


async def tool_shell_background_start(
    ctx: "Context",
    command: str,
    completion_tail_lines: int | None = None,
    pin_sticky: bool = False,
    progress_parser: str | ProgressParserPreset = ProgressParserPreset.AUTO,
    purpose: str = "",
    cwd: str = "",
    max_lifetime_sec: float | None = None,
) -> ToolResult:
    """Start a background process. Returns immediately with a job ID."""
    log.info(f"[tool:shell_background_start] command={command[:80]}")
    preset = parse_progress_preset(progress_parser)

    from decafclaw.tools.shell_tools import _redirect_text, check_shell_approval

    # Resolve cwd before approval so the judge and the confirmation see the
    # real starting directory, and an unusable path fails loudly (#1746).
    try:
        workdir = _resolve_shell_cwd(ctx, cwd)
    except ValueError as exc:
        log.info(f"[tool:shell_background_start] rejected cwd {cwd!r}: {exc}")
        return ToolResult(text=f"[error: {exc}]", data={"status": "error"})

    result = await check_shell_approval(
        ctx,
        command,
        tool_name="shell_background_start",
        cwd=str(workdir),
        message=f"Background process: `{command}`",
        purpose=purpose,
    )
    if result.get("redirected"):
        return ToolResult(
            text=_redirect_text(str(result.get("feedback") or "")),
            data={"status": "error"},
        )
    if not result.get("approved"):
        reason = result.get("reason") or "background process was denied by user"
        feedback = str(result.get("feedback") or "").strip()
        text = f"[error: {reason}]"
        if feedback:
            text = f"[error: {reason}. Feedback: {feedback}]"
        return ToolResult(
            text=text,
            data={"status": "error"},
        )

    tail_lines: int = (
        ctx.config.background.default_completion_tail_lines if completion_tail_lines is None else completion_tail_lines
    )
    clamped_tail_lines = max(0, min(tail_lines, _OUTPUT_BUFFER_SIZE))

    # Lifetime: per-call override wins; 0 disables expiry (#1746). A negative
    # value is a caller mistake, not a request to disable — fail loudly.
    if max_lifetime_sec is None:
        max_lifetime = ctx.config.background.default_max_lifetime_sec
    else:
        max_lifetime = float(max_lifetime_sec)
    if max_lifetime < 0 or not math.isfinite(max_lifetime):
        return ToolResult(
            text="[error: max_lifetime_sec must be a finite number >= 0 (0 disables expiry)]",
            data={"status": "error"},
        )
    if max_lifetime == 0:
        max_lifetime = math.inf
        log.info("[tool:shell_background_start] expiry disabled by caller")

    manager = _get_job_manager(ctx)
    await manager.cleanup_expired()
    conv_manager = ctx.manager

    try:
        job = await manager.start(
            command,
            str(workdir),
            max_lifetime=max_lifetime,
            config=ctx.config,
            conv_id=ctx.conv_id,
            event_bus=ctx.event_bus,
            attended=not ctx.is_unattended,
            completion_tail_lines=clamped_tail_lines,
            manager=conv_manager,
            pin_sticky=pin_sticky,
            progress_parser=preset,
        )
    except Exception as e:
        return ToolResult(
            text=f"[error: failed to start background process: {e}]",
            data={"status": "error"},
        )

    return ToolResult(
        text=(
            f"Background process started.\n"
            f"- **Job ID:** `{job.job_id}`\n"
            f"- **PID:** {job.pid}\n"
            f"- **Command:** `{command}`\n"
            f"- **CWD:** `{workdir}`\n"
            f"- **Max lifetime:** {_format_lifetime(job.max_lifetime)}"
        ),
        data={
            "job_id": job.job_id,
            "status": JobStatus.RUNNING,
            "command": command,
            "cwd": str(workdir),
            "max_lifetime_sec": None if math.isinf(job.max_lifetime) else job.max_lifetime,
            "pid": job.pid,
            **({"pin_sticky": True, "progress_parser": preset} if pin_sticky else {}),
        },
    )


async def tool_shell_background_status(
    ctx: "Context",
    job_id: str,
    wait: float = 0.0,
    cursor: str = "",
    from_start: bool = False,
    tail_lines: int | None = None,
) -> ToolResult:
    """Check a background job, optionally blocking and/or reading incrementally.

    ``wait`` blocks up to that many seconds until the job leaves RUNNING.
    ``cursor="auto"`` returns only stdout/stderr lines since this
    conversation's previous cursor read; the cursor lives on the job, so the
    caller never carries position state. ``from_start=true`` re-reads the whole
    buffer and leaves the cursor untouched. ``tail_lines`` trims a running-job
    read (default 20); a finished job always returns everything unread.
    """
    log.info(
        f"[tool:shell_background_status] job_id={job_id} wait={wait} "
        f"cursor={cursor!r} from_start={from_start} tail_lines={tail_lines}"
    )
    if wait < 0:
        return ToolResult(text="[error: wait must be >= 0]", data={"status": "error"})
    if cursor not in ("", "auto"):
        return ToolResult(
            text="[error: cursor must be empty or 'auto']",
            data={"status": "error"},
        )
    if cursor and from_start:
        return ToolResult(
            text="[error: cursor and from_start are mutually exclusive]",
            data={"status": "error"},
        )
    if tail_lines is not None and tail_lines < 0:
        return ToolResult(text="[error: tail_lines must be >= 0]", data={"status": "error"})

    manager = _get_job_manager(ctx)
    await manager.cleanup_expired()

    job = manager.get(job_id)
    if job is None:
        return ToolResult(
            text=f"[error: job '{job_id}' not found]",
            data={"status": "error"},
        )

    wait_timed_out = False
    if wait > 0 and job.status == JobStatus.RUNNING:
        try:
            await asyncio.wait_for(job.done_event.wait(), timeout=wait)
        except asyncio.TimeoutError:
            # Completion may have landed as the timeout fired; report the
            # actual state rather than a stale timeout.
            wait_timed_out = job.status == JobStatus.RUNNING

    now = time.monotonic()
    elapsed_ms = int((now - job.started_at) * 1000)
    remaining_ms = None
    if job.status == JobStatus.RUNNING and math.isfinite(job.max_lifetime):
        remaining = job.max_lifetime - (now - job.started_at)
        remaining_ms = max(0, int(remaining * 1000))

    cursor_mode = cursor == "auto" and not from_start
    running = job.status == JobStatus.RUNNING
    # A finished job returns everything unread/buffered. While running, the
    # default is a small tail; an explicit cursor read ignores tail_lines
    # because the point is the complete unread delta.
    effective_tail: int | None = None
    if not cursor_mode and not from_start and running:
        effective_tail = _DEFAULT_RUNNING_TAIL_LINES if tail_lines is None else min(tail_lines, _OUTPUT_BUFFER_SIZE)

    stdout_lines, next_stdout, stdout_gap, stdout_truncated = _output_window(
        job.stdout_buffer,
        job.stdout_total_lines,
        cursor=job.cursor_stdout if cursor_mode else None,
        from_start=from_start,
        tail_lines=effective_tail,
    )
    stderr_lines, next_stderr, stderr_gap, stderr_truncated = _output_window(
        job.stderr_buffer,
        job.stderr_total_lines,
        cursor=job.cursor_stderr if cursor_mode else None,
        from_start=from_start,
        tail_lines=effective_tail,
    )
    if cursor_mode:
        job.cursor_stdout = next_stdout
        job.cursor_stderr = next_stderr

    stdout = "\n".join(stdout_lines)
    stderr = "\n".join(stderr_lines)

    text = format_status_text(
        job_id=job_id,
        status=job.status,
        command=job.command,
        pid=job.pid,
        elapsed_ms=elapsed_ms,
        remaining_ms=remaining_ms,
        exit_code=job.exit_code,
        stdout=stdout,
        stderr=stderr,
        captured_output=bool(job.stdout_total_lines or job.stderr_total_lines),
    )
    notes = _window_notes(
        "stdout",
        stdout_lines,
        job.stdout_total_lines,
        stdout_gap,
        stdout_truncated,
        status=job.status,
        cursor_mode=cursor_mode,
        tail_applied=effective_tail is not None,
    ) + _window_notes(
        "stderr",
        stderr_lines,
        job.stderr_total_lines,
        stderr_gap,
        stderr_truncated,
        status=job.status,
        cursor_mode=cursor_mode,
        tail_applied=effective_tail is not None,
    )
    if wait_timed_out:
        notes.insert(0, f"Wait timed out after {wait:.1f}s; the job is still running.")
    if notes:
        text += "\n\n" + "\n".join(f"- {note}" for note in notes)

    return ToolResult(
        text=text,
        data={
            "status": job.status,
            "exit_code": job.exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "pid": job.pid,
            "command": job.command,
            "elapsed_ms": elapsed_ms,
            "remaining_ms": remaining_ms,
            "wait_timed_out": wait_timed_out,
            "cursor": {
                "stdout": job.cursor_stdout,
                "stderr": job.cursor_stderr,
                "stdout_total": job.stdout_total_lines,
                "stderr_total": job.stderr_total_lines,
            },
        },
    )


async def tool_shell_background_stop(ctx: "Context", job_id: str) -> ToolResult:
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
        text=(f"Background job `{job_id}` stopped.\n- **Status:** {job.status}\n- **Exit code:** {job.exit_code}"),
        data={
            "status": job.status,
            "exit_code": job.exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "pid": job.pid,
            "command": job.command,
        },
    )


async def tool_shell_background_list(ctx: "Context") -> ToolResult:
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
        lines.append(f"- `{job.job_id}` — {job.status} | `{job.command[:50]}` | pid={job.pid} | elapsed={elapsed:.0f}s")
        job_list.append(
            {
                "job_id": job.job_id,
                "command": job.command,
                "status": job.status,
                "pid": job.pid,
                "elapsed_ms": int(elapsed * 1000),
            }
        )

    widget = None
    if jobs:
        widget = WidgetRequest(
            widget_type="data_table",
            data={
                "caption": f"shell_background_list: {len(jobs)} job(s)",
                "columns": [
                    {"key": "job_id", "label": "Job ID"},
                    {"key": "status", "label": "Status"},
                    {"key": "command", "label": "Command"},
                    {"key": "pid", "label": "PID"},
                    {"key": "elapsed_ms", "label": "Elapsed (ms)"},
                ],
                "rows": job_list,
            },
            target="inline",
        )
    return ToolResult(
        text="\n".join(lines),
        data={"jobs": job_list},
        widget=widget,
    )


async def shutdown() -> None:
    """Stop every background job across all conversations (server shutdown).

    Called once at server shutdown by the loader (see docs/skills.md).
    Idempotent: a second call finds no managers and does nothing.
    """
    for manager in list(_managers.values()):
        await manager.cleanup_all()
    _managers.clear()


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
        "priority": "low",
        "timeout": None,
        "writes": True,
        "function": {
            "name": "shell_background_start",
            "description": (
                "Start a long-running command or background process (server, watcher, build, progress monitor). "
                "Returns immediately with a job_id. Use when a command runs longer than a few seconds. "
                "Use shell_background_status to monitor progress and check back on output, "
                "shell_background_stop to terminate. Pair with wait or schedule_turn to check status without "
                "busy-looping. REQUIRES USER CONFIRMATION unless the command matches an allow pattern. "
                "Jobs expire after config.background.default_max_lifetime_sec (default 1 hour); "
                "pass max_lifetime_sec to extend it, or 0 to disable expiry for unbounded runs. "
                "On expiry or stop, shell_background_status and the completion record keep the "
                "partial stdout/stderr captured before termination. Always fill `purpose` with what you are "
                "trying to accomplish; the security and auto-approval evaluators use it, and the "
                "user sees it on the confirmation prompt. If a command is denied with feedback, "
                "read it carefully and run the safer alternative."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {
                        "type": "string",
                        "description": (
                            "Working directory, relative to the workspace root (or an absolute "
                            "path inside it). Defaults to the workspace root. An invalid path "
                            "fails before any process starts."
                        ),
                    },
                    "command": {
                        "type": "string",
                        "description": "The shell command to run in the background",
                    },
                    "max_lifetime_sec": {
                        "type": "number",
                        "description": (
                            "Maximum seconds before the job is terminated "
                            "(SIGTERM, then SIGKILL after 2s). Optional; defaults to "
                            "config.background.default_max_lifetime_sec (3600s). "
                            "Set 0 to disable expiry for runs whose duration you cannot "
                            "bound, such as a full test suite. Partial stdout/stderr is "
                            "kept in shell_background_status and the completion record."
                        ),
                    },
                    "purpose": {
                        "type": "string",
                        "description": (
                            "Brief explanation of what you intend to accomplish with this "
                            "command and why it is needed. Used by the security and "
                            "auto-approval evaluators, and shown to the user on confirmation."
                        ),
                    },
                    "completion_tail_lines": {
                        "type": "integer",
                        "description": (
                            "Number of trailing stdout/stderr lines to capture "
                            "in the archive record when the job exits. "
                            "Optional; default from config.background."
                            "default_completion_tail_lines; clamped to [0, 500]."
                        ),
                    },
                    "pin_sticky": {
                        "type": "boolean",
                        "description": (
                            "Stream the job's live output to the sticky slot "
                            "above the conversation controls (progress_tracker widget: "
                            "throttled updates while running, final "
                            "completed/failed state on exit). Use for long "
                            "jobs the user should watch instead of polling. "
                            "Optional; default false."
                        ),
                    },
                    "progress_parser": {
                        "type": "string",
                        # Preset list derived from ProgressParserPreset so the schema stays in sync (#1384).
                        "enum": [p.value for p in ProgressParserPreset],
                        "description": (
                            "Progress parser preset to derive live progress_tracker "
                            "updates from ordinary command output when pin_sticky is true. "
                            "Options: 'auto' (detects format, falls back to line counts), "
                            "'step' (e.g. [3/10], Step 3 of 10), 'percent' (e.g. 45%), "
                            "'pytest' (collected count, passed/failed counts), "
                            "or 'none' (raw line counting). Optional; default 'auto'."
                        ),
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "priority": "low",
        "writes": False,
        "timeout": None,  # wait= can intentionally block past the default tool timeout
        "function": {
            "name": "shell_background_status",
            "description": (
                "Check the status and monitor progress of a background job or running build. "
                # Status list derived from JobStatus so the schema stays in sync (#1383).
                f"Returns {'/'.join(s.value for s in JobStatus)} status, exit code, and recent stdout/stderr. "
                "While the job runs, the default is its last 20 lines (tail_lines overrides); "
                "a finished job returns its full buffered output. "
                "Use wait to block until the job finishes or the timeout expires instead of "
                "guessing sleeps. Use cursor='auto' for only the output since the previous "
                "cursor read in this conversation: the cursor is server-owned, the completion "
                "read returns everything unread, and a gap is reported instead of silently "
                "dropping lines. from_start=true re-reads the whole buffer and leaves the "
                "cursor untouched."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Job ID from shell_background_start",
                    },
                    "wait": {
                        "type": "number",
                        "description": (
                            "Seconds to block waiting for the job to leave running. "
                            "Returns immediately when it finishes. Optional; default 0 "
                            "(do not block). Negative values are rejected."
                        ),
                    },
                    "cursor": {
                        "type": "string",
                        "enum": ["auto"],
                        "description": (
                            "Set to 'auto' to return only stdout/stderr lines since this "
                            "conversation's previous cursor read. The server owns the "
                            "position. A finished job's next cursor read returns "
                            "everything unread."
                        ),
                    },
                    "from_start": {
                        "type": "boolean",
                        "description": (
                            "Re-read the entire buffered output instead of the cursor "
                            "delta. Does not advance the cursor. Mutually exclusive "
                            "with cursor='auto'. Optional; default false."
                        ),
                    },
                    "tail_lines": {
                        "type": "integer",
                        "description": (
                            "Number of trailing buffered lines to return while the job "
                            "is still running (default 20, max 500). Ignored for a "
                            "finished job and for cursor='auto'. Optional."
                        ),
                    },
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "priority": "low",
        "writes": True,
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
        "priority": "low",
        "writes": False,
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
