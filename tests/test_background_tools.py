"""Tests for background process management — BackgroundJobManager lifecycle."""

import asyncio

import pytest

from decafclaw.skills.background.tools import BackgroundJobManager

# ---------------------------------------------------------------------------
# Helpers used by tool-layer tests
# ---------------------------------------------------------------------------

async def _yes_approval(*a, **k):
    return {"approved": True}


@pytest.mark.asyncio
async def test_start_job(tmp_path):
    """Start a job and verify initial state."""
    manager = BackgroundJobManager()
    job = await manager.start("sleep 10", cwd=str(tmp_path))

    assert job.job_id
    assert len(job.job_id) == 12
    assert job.pid > 0
    assert job.status == "running"
    assert job.command == "sleep 10"
    assert job.exit_code is None

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_status_after_exit(tmp_path):
    """Job that exits normally shows completed status with output."""
    manager = BackgroundJobManager()
    job = await manager.start("echo hello", cwd=str(tmp_path))

    # Wait for the reader task to drain + process exit — no fixed sleep.
    assert job.reader_task is not None
    await job.reader_task

    assert job.status == "completed"
    assert job.exit_code == 0
    stdout = list(job.stdout_buffer)
    assert any("hello" in line for line in stdout)

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_status_error_exit(tmp_path):
    """Job with non-zero exit shows error status."""
    manager = BackgroundJobManager()
    job = await manager.start("exit 1", cwd=str(tmp_path))

    assert job.reader_task is not None
    await job.reader_task

    assert job.status == "error"
    assert job.exit_code == 1

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_stop_running_job(tmp_path):
    """Stop a running job."""
    manager = BackgroundJobManager()
    job = await manager.start("sleep 60", cwd=str(tmp_path))

    assert job.status == "running"

    stopped = await manager.stop(job.job_id)
    assert stopped is not None
    assert stopped.status != "running"

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_stop_nonexistent():
    """Stop returns None for unknown job_id."""
    manager = BackgroundJobManager()
    result = await manager.stop("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_output_buffering(tmp_path):
    """Output buffer is capped at 500 lines."""
    manager = BackgroundJobManager()
    # Generate 600 lines of output
    job = await manager.start(
        "for i in $(seq 1 600); do echo line$i; done",
        cwd=str(tmp_path),
    )

    assert job.reader_task is not None
    await job.reader_task

    assert job.status == "completed"
    stdout = list(job.stdout_buffer)
    assert len(stdout) == 500
    # Should have the last 500 lines (line101 through line600)
    assert stdout[-1] == "line600"
    assert stdout[0] == "line101"  # first 100 lines evicted

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_cleanup_expired(tmp_path):
    """Expired jobs are killed during cleanup."""
    manager = BackgroundJobManager()
    job = await manager.start(
        "sleep 60", cwd=str(tmp_path), max_lifetime=0.1,
    )

    # Backdate started_at so the lifetime check trips immediately — no
    # need to actually sleep out the timer.
    job.started_at -= 1.0
    expired = await manager.cleanup_expired()

    assert len(expired) == 1
    assert expired[0].job_id == job.job_id
    assert expired[0].status == "expired"

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_cleanup_expired_ignores_finished(tmp_path):
    """Cleanup doesn't touch already-finished jobs."""
    manager = BackgroundJobManager()
    job = await manager.start("echo done", cwd=str(tmp_path), max_lifetime=0.1)

    assert job.reader_task is not None
    await job.reader_task
    assert job.status == "completed"

    expired = await manager.cleanup_expired()
    assert len(expired) == 0  # already completed, not expired

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_list_active(tmp_path):
    """List returns all tracked jobs."""
    manager = BackgroundJobManager()
    job1 = await manager.start("sleep 10", cwd=str(tmp_path))
    job2 = await manager.start("sleep 10", cwd=str(tmp_path))

    jobs = manager.list_jobs()
    assert len(jobs) == 2
    job_ids = {j.job_id for j in jobs}
    assert job1.job_id in job_ids
    assert job2.job_id in job_ids

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_get_job(tmp_path):
    """Get returns job by ID or None."""
    manager = BackgroundJobManager()
    job = await manager.start("sleep 10", cwd=str(tmp_path))

    assert manager.get(job.job_id) is job
    assert manager.get("nonexistent") is None

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_stderr_captured(tmp_path):
    """Stderr output is captured in stderr_buffer."""
    manager = BackgroundJobManager()
    job = await manager.start("echo error >&2", cwd=str(tmp_path))

    assert job.reader_task is not None
    await job.reader_task

    stderr = list(job.stderr_buffer)
    assert any("error" in line for line in stderr)

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_cleanup_all(tmp_path):
    """Cleanup all stops all running jobs."""
    manager = BackgroundJobManager()
    await manager.start("sleep 60", cwd=str(tmp_path))
    await manager.start("sleep 60", cwd=str(tmp_path))

    stopped = await manager.cleanup_all()
    assert len(stopped) == 2

    for job in stopped:
        assert job.status != "running"


def test_build_background_event_record_truncates_tails():
    from collections import deque

    from decafclaw.skills.background.tools import build_background_event_record

    stdout_buf = deque([f"line-{i}" for i in range(100)], maxlen=500)
    stderr_buf = deque([f"err-{i}" for i in range(100)], maxlen=500)

    rec = build_background_event_record(
        job_id="j1",
        command="echo",
        status="completed",
        exit_code=0,
        stdout_buffer=stdout_buf,
        stderr_buffer=stderr_buf,
        elapsed_ms=1234,
        completion_tail_lines=10,
    )

    assert rec["role"] == "background_event"
    assert rec["job_id"] == "j1"
    assert rec["status"] == "completed"
    assert rec["exit_code"] == 0
    assert rec["command"] == "echo"
    # last 10 lines
    assert rec["stdout_tail"].startswith("line-90\n")
    assert rec["stdout_tail"].endswith("line-99")
    assert rec["completion_tail_lines"] == 10
    assert "timestamp" in rec


def test_build_background_event_record_clamps_4kb():
    from collections import deque

    from decafclaw.skills.background.tools import build_background_event_record

    # 100 lines of 200 chars each = ~20KB
    big = deque(["x" * 200 for _ in range(100)], maxlen=500)
    rec = build_background_event_record(
        job_id="j1", command="echo", status="completed", exit_code=0,
        stdout_buffer=big, stderr_buffer=deque(), elapsed_ms=0,
        completion_tail_lines=500,
    )
    assert len(rec["stdout_tail"].encode("utf-8")) <= 4096


def test_build_background_event_record_zero_tail_lines():
    from collections import deque

    from decafclaw.skills.background.tools import build_background_event_record

    buf = deque(["some output"], maxlen=500)
    rec = build_background_event_record(
        job_id="j1", command="echo", status="completed", exit_code=0,
        stdout_buffer=buf, stderr_buffer=deque(), elapsed_ms=0,
        completion_tail_lines=0,
    )
    assert rec["stdout_tail"] == ""
    assert rec["completion_tail_lines"] == 0


def test_build_background_event_record_clamps_completion_tail_lines_max():
    """values above _OUTPUT_BUFFER_SIZE are clamped down to the buffer size."""
    from collections import deque

    from decafclaw.skills.background.tools import (
        _OUTPUT_BUFFER_SIZE,
        build_background_event_record,
    )

    buf = deque([f"line-{i}" for i in range(100)], maxlen=500)
    rec = build_background_event_record(
        job_id="j1", command="echo", status="completed", exit_code=0,
        stdout_buffer=buf, stderr_buffer=deque(), elapsed_ms=0,
        completion_tail_lines=999_999,
    )
    assert rec["completion_tail_lines"] == _OUTPUT_BUFFER_SIZE


# ---------------------------------------------------------------------------
# Task 6.1: completion_tail_lines plumbing tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_completion_tail_lines_plumbs_to_job(ctx, monkeypatch):
    """completion_tail_lines passed to the tool propagates into BackgroundJob."""
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "decafclaw.tools.shell_tools.check_shell_approval", _yes_approval
    )

    from decafclaw.skills.background.tools import (
        _get_job_manager,
        tool_shell_background_start,
    )

    result = await tool_shell_background_start(
        ctx, command="true", completion_tail_lines=123
    )
    mgr = _get_job_manager(ctx)
    job_id = result.data["job_id"]
    job = mgr.get(job_id)
    assert job is not None
    assert job.completion_tail_lines == 123

    await mgr.cleanup_all()


@pytest.mark.asyncio
async def test_completion_tail_lines_clamps_out_of_range(ctx, monkeypatch):
    """Values outside [0, _OUTPUT_BUFFER_SIZE] are clamped at the tool layer."""
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "decafclaw.tools.shell_tools.check_shell_approval", _yes_approval
    )

    from decafclaw.skills.background.tools import (
        _OUTPUT_BUFFER_SIZE,
        _get_job_manager,
        tool_shell_background_start,
    )

    # Over-large value → clamped to _OUTPUT_BUFFER_SIZE
    result = await tool_shell_background_start(
        ctx, command="true", completion_tail_lines=9999
    )
    mgr = _get_job_manager(ctx)
    job = mgr.get(result.data["job_id"])
    assert job is not None
    assert job.completion_tail_lines == _OUTPUT_BUFFER_SIZE

    # Negative value → clamped to 0
    result = await tool_shell_background_start(
        ctx, command="true", completion_tail_lines=-5
    )
    job = mgr.get(result.data["job_id"])
    assert job is not None
    assert job.completion_tail_lines == 0

    await mgr.cleanup_all()


# ---------------------------------------------------------------------------
# Task 6.2: _finalize_job — archive write and idempotency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_job_exit_appends_background_event(ctx, monkeypatch):
    """When a job exits cleanly, a background_event record is written to the archive."""
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "decafclaw.tools.shell_tools.check_shell_approval", _yes_approval
    )

    from decafclaw.archive import restore_history
    from decafclaw.skills.background.tools import (
        _get_job_manager,
        tool_shell_background_start,
    )

    result = await tool_shell_background_start(ctx, command="true")
    mgr = _get_job_manager(ctx)
    job = mgr.get(result.data["job_id"])
    assert job is not None

    # Wait for the reader task to detect completion and finalize.
    await job.reader_task

    history = restore_history(ctx.config, ctx.conv_id) or []
    events = [m for m in history if m.get("role") == "background_event"]
    assert len(events) == 1
    assert events[0]["job_id"] == result.data["job_id"]
    assert events[0]["status"] == "completed"

    await mgr.cleanup_all()


@pytest.mark.asyncio
async def test_finalize_job_is_idempotent(ctx, monkeypatch):
    """Stopping a job whose reader already finalized does not double-fire notifications."""
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "decafclaw.tools.shell_tools.check_shell_approval", _yes_approval
    )

    import decafclaw.notifications as notifications_module

    notify_count = 0
    orig_notify = notifications_module.notify

    async def counting_notify(*a, **k):
        nonlocal notify_count
        notify_count += 1
        return await orig_notify(*a, **k)

    monkeypatch.setattr(notifications_module, "notify", counting_notify)

    from decafclaw.skills.background.tools import (
        _get_job_manager,
        tool_shell_background_start,
    )

    result = await tool_shell_background_start(ctx, command="true")
    mgr = _get_job_manager(ctx)
    job = mgr.get(result.data["job_id"])
    assert job is not None

    # Reader finalizes here (sets job.finalized = True, fires notify once).
    await job.reader_task

    # stop() should detect job.finalized and skip the second notification.
    await mgr.stop(job.job_id)

    assert notify_count == 1  # exactly once

    await mgr.cleanup_all()


# ---------------------------------------------------------------------------
# Task 6.4: _enqueue_wake — real ConversationManager.enqueue_turn dispatch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_job_exit_enqueues_wake_turn(ctx, monkeypatch):
    """After a background job exits, a WAKE turn is enqueued on the
    originating conv via ConversationManager.enqueue_turn."""
    from decafclaw.conversation_manager import ConversationManager, TurnKind

    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    # Ensure ctx has a conv_id.
    ctx.conv_id = "c1"

    # Attach a real-ish manager so monkeypatch can target it.
    conv_mgr = ConversationManager(ctx.config, ctx.event_bus)
    ctx.manager = conv_mgr

    seen_kinds = []

    async def fake_enqueue(conv_id, *, kind, prompt, **kwargs):
        seen_kinds.append({"conv_id": conv_id, "kind": kind, "metadata": kwargs.get("metadata")})
        fut = asyncio.get_event_loop().create_future()
        fut.set_result(None)
        return fut

    monkeypatch.setattr(ctx.manager, "enqueue_turn", fake_enqueue)
    monkeypatch.setattr(
        "decafclaw.tools.shell_tools.check_shell_approval", _yes_approval
    )

    from decafclaw.skills.background.tools import (
        _get_job_manager,
        tool_shell_background_start,
    )

    result = await tool_shell_background_start(ctx, command="true")
    mgr = _get_job_manager(ctx)
    job = mgr.get(result.data["job_id"])
    assert job is not None

    # Wait for reader to finalize.
    await job.reader_task
    # Let _enqueue_wake dispatch run (it's awaited inside _finalize_job).
    await asyncio.sleep(0)

    wake_events = [s for s in seen_kinds if s["kind"] is TurnKind.WAKE]
    assert len(wake_events) == 1
    assert wake_events[0]["conv_id"] == "c1"
    assert wake_events[0]["metadata"] == {"job_id": result.data["job_id"]}

    await mgr.cleanup_all()


@pytest.mark.asyncio
async def test_job_exit_skips_wake_when_no_manager(ctx, monkeypatch):
    """If the ctx has no manager (e.g. test fixture), wake is skipped
    gracefully — no crash, archive and notification still fire."""
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    ctx.manager = None
    ctx.conv_id = "c1"

    monkeypatch.setattr(
        "decafclaw.tools.shell_tools.check_shell_approval", _yes_approval
    )

    from decafclaw.archive import restore_history
    from decafclaw.skills.background.tools import (
        _get_job_manager,
        tool_shell_background_start,
    )

    result = await tool_shell_background_start(ctx, command="true")
    mgr = _get_job_manager(ctx)
    job = mgr.get(result.data["job_id"])
    assert job is not None

    await job.reader_task
    # No crash means success.
    # Archive still has the event.
    history = restore_history(ctx.config, "c1") or []
    events = [m for m in history if m.get("role") == "background_event"]
    assert len(events) == 1

    await mgr.cleanup_all()


# ---------------------------------------------------------------------------
# Item 1: _run_reader exception path still finalizes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_reader_error_path_still_finalizes(ctx, monkeypatch):
    """If the stream-reader hits a generic Exception, _finalize_job still runs
    so the archive record, inbox notification, and wake turn all fire."""
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    ctx.conv_id = "c-err-1"
    ctx.manager = None

    monkeypatch.setattr(
        "decafclaw.tools.shell_tools.check_shell_approval", _yes_approval
    )

    # Patch _read_stream to raise a non-Cancelled exception.
    async def boom(*a, **k):
        raise RuntimeError("simulated stream error")

    monkeypatch.setattr(
        "decafclaw.skills.background.tools._read_stream", boom
    )

    from decafclaw.archive import restore_history
    from decafclaw.skills.background.tools import (
        _get_job_manager,
        tool_shell_background_start,
    )

    result = await tool_shell_background_start(ctx, command="true")
    mgr = _get_job_manager(ctx)
    job = mgr.get(result.data["job_id"])
    assert job is not None

    # Reader task should complete despite the exception.
    await job.reader_task

    assert job.status == "error"

    # _finalize_job ran → archive has event.
    history = restore_history(ctx.config, "c-err-1") or []
    events = [m for m in history if m.get("role") == "background_event"]
    assert len(events) == 1
    assert events[0]["status"] == "error"

    await mgr.cleanup_all()


# ---------------------------------------------------------------------------
# Item 2: completion_tail_lines defaults from config
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_completion_tail_lines_defaults_from_config(ctx, monkeypatch):
    """When completion_tail_lines is not passed, the value from
    config.background.default_completion_tail_lines is used."""
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    ctx.config.background.default_completion_tail_lines = 123

    monkeypatch.setattr(
        "decafclaw.tools.shell_tools.check_shell_approval", _yes_approval
    )

    from decafclaw.skills.background.tools import (
        _get_job_manager,
        tool_shell_background_start,
    )

    result = await tool_shell_background_start(ctx, command="true")
    mgr = _get_job_manager(ctx)
    job = mgr.get(result.data["job_id"])
    assert job is not None
    assert job.completion_tail_lines == 123

    await mgr.cleanup_all()
