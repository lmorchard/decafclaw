"""Tests for background process management — BackgroundJobManager lifecycle."""

import asyncio
from types import SimpleNamespace

import pytest

from decafclaw.skills.background import tools
from decafclaw.skills.background.parsers import (
    AutoParser,
    NoneParser,
    PercentParser,
    PytestParser,
    StepParser,
    get_progress_parser,
    strip_ansi,
)
from decafclaw.skills.background.tools import BackgroundJobManager

# ---------------------------------------------------------------------------
# Transport cleanup (#605) — close the subprocess transport deterministically
# so BaseSubprocessTransport.__del__ doesn't fire after the event loop closes.
# ---------------------------------------------------------------------------


def test_close_transport_closes_open_transport():
    closed = []

    class FakeTransport:
        def close(self):
            closed.append(True)

    job = SimpleNamespace(process=SimpleNamespace(_transport=FakeTransport()), job_id="j1")
    tools._close_transport(job)
    assert closed == [True]


def test_close_transport_fail_open_on_missing_transport():
    # No _transport attribute — must not raise.
    job = SimpleNamespace(process=SimpleNamespace(), job_id="j2")
    tools._close_transport(job)


def test_close_transport_fail_open_when_close_raises():
    class BoomTransport:
        def close(self):
            raise RuntimeError("boom")

    job = SimpleNamespace(process=SimpleNamespace(_transport=BoomTransport()), job_id="j3")
    tools._close_transport(job)  # swallowed, must not raise


@pytest.mark.asyncio
async def test_transport_closed_even_when_finalize_raises(monkeypatch, tmp_path):
    """The transport must be closed deterministically even if _finalize_job
    raises (or the reader is cancelled mid-finalize) — else the #605 leak
    regresses. Guards the try/finally in _run_reader's cleanup."""

    async def boom(job):
        raise RuntimeError("finalize boom")

    closed = []
    real_close = tools._close_transport

    def spy(job):
        closed.append(job.job_id)
        real_close(job)  # actually close so this test doesn't leak a transport

    monkeypatch.setattr(tools, "_finalize_job", boom)
    monkeypatch.setattr(tools, "_close_transport", spy)

    manager = BackgroundJobManager()
    job = await manager.start("echo hi", cwd=str(tmp_path))
    with pytest.raises(RuntimeError, match="finalize boom"):
        await job.reader_task

    assert closed == [job.job_id]  # ran despite finalize raising
    # No cleanup_all() here: the process already exited and the spy closed the
    # transport, and cleanup_all would re-invoke the patched (raising) finalize.


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
        "sleep 60",
        cwd=str(tmp_path),
        max_lifetime=0.1,
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


@pytest.mark.asyncio
async def test_shutdown_stops_all_jobs_and_clears_managers(tmp_path):
    """The skill's shutdown() (server shutdown) stops every job in the
    module-level _managers registry and clears it. Idempotent: a second
    call with no managers is a no-op."""
    ctx = SimpleNamespace(conv_id="shutdown-conv", context_id="shutdown-conv")
    manager = tools._get_job_manager(ctx)
    job = await manager.start("sleep 10", cwd=str(tmp_path))
    assert job.status == "running"
    assert job.reader_task is not None and not job.reader_task.done()

    try:
        await tools.shutdown()
    finally:
        # Safety net: a failed shutdown must not leak the running process.
        if tools._managers:
            await tools.shutdown()

    assert job.process.returncode is not None  # process exited
    assert job.reader_task.done()  # no reader task remains
    assert job.status != "running"
    assert tools._managers == {}

    await tools.shutdown()  # second call: no managers left, must be a no-op


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
        job_id="j1",
        command="echo",
        status="completed",
        exit_code=0,
        stdout_buffer=big,
        stderr_buffer=deque(),
        elapsed_ms=0,
        completion_tail_lines=500,
    )
    assert len(rec["stdout_tail"].encode("utf-8")) <= 4096


def test_build_background_event_record_zero_tail_lines():
    from collections import deque

    from decafclaw.skills.background.tools import build_background_event_record

    buf = deque(["some output"], maxlen=500)
    rec = build_background_event_record(
        job_id="j1",
        command="echo",
        status="completed",
        exit_code=0,
        stdout_buffer=buf,
        stderr_buffer=deque(),
        elapsed_ms=0,
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
        job_id="j1",
        command="echo",
        status="completed",
        exit_code=0,
        stdout_buffer=buf,
        stderr_buffer=deque(),
        elapsed_ms=0,
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
    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", _yes_approval)

    from decafclaw.skills.background.tools import (
        _get_job_manager,
        tool_shell_background_start,
    )

    result = await tool_shell_background_start(ctx, command="true", completion_tail_lines=123)
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
    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", _yes_approval)

    from decafclaw.skills.background.tools import (
        _OUTPUT_BUFFER_SIZE,
        _get_job_manager,
        tool_shell_background_start,
    )

    # Over-large value → clamped to _OUTPUT_BUFFER_SIZE
    result = await tool_shell_background_start(ctx, command="true", completion_tail_lines=9999)
    mgr = _get_job_manager(ctx)
    job = mgr.get(result.data["job_id"])
    assert job is not None
    assert job.completion_tail_lines == _OUTPUT_BUFFER_SIZE

    # Negative value → clamped to 0
    result = await tool_shell_background_start(ctx, command="true", completion_tail_lines=-5)
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
    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", _yes_approval)

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
    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", _yes_approval)

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
    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", _yes_approval)

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

    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", _yes_approval)

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

    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", _yes_approval)

    # Patch _read_stream to raise a non-Cancelled exception.
    async def boom(*a, **k):
        raise RuntimeError("simulated stream error")

    monkeypatch.setattr("decafclaw.skills.background.tools._read_stream", boom)

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

    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", _yes_approval)

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


# ---------------------------------------------------------------------------
# Task 7: pin_sticky — live progress streaming to the sticky slot (#1104)
# ---------------------------------------------------------------------------


class _StickyCapturingManager:
    """Stands in for ConversationManager: records per-conv emit events."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def emit(self, conv_id: str, event: dict) -> None:
        self.events.append((conv_id, event))

    @property
    def sticky_events(self) -> list[dict]:
        return [event for _cid, event in self.events if event.get("type") in ("sticky_set", "sticky_clear")]


@pytest.fixture
def sticky_registry(config, monkeypatch):
    """Load the bundled widget registry so progress_tracker validates."""
    import decafclaw.widgets as widgets_module
    from decafclaw.widgets import init_widgets

    monkeypatch.setattr(widgets_module, "_registry", init_widgets(config))
    return widgets_module._registry


@pytest.mark.asyncio
async def test_pin_sticky_streams_running_then_completed(tmp_path, config, sticky_registry):
    """pin_sticky=True pins an in-progress tracker on start and a done
    tracker when the job exits cleanly."""
    manager = BackgroundJobManager()
    fake = _StickyCapturingManager()
    job = await manager.start(
        "echo hello",
        cwd=str(tmp_path),
        config=config,
        conv_id="conv-sticky",
        manager=fake,
        pin_sticky=True,
    )

    # Initial "Running" pin lands before the first output line.
    events = fake.sticky_events
    assert events, "expected at least the initial running pin"
    first = events[0]
    assert first["type"] == "sticky_set"
    assert first["widget_type"] == "progress_tracker"
    assert first["data"]["steps"][0]["status"] == "in_progress"
    assert first["data"]["steps"][0]["label"] == "echo hello"

    await job.reader_task

    events = fake.sticky_events
    assert len(events) >= 2, "expected a final sticky state after exit"
    final = events[-1]
    assert final["type"] == "sticky_set"
    assert final["data"]["steps"][0]["status"] == "done"
    assert "exit 0" in final["data"]["steps"][0]["note"]

    # The final pin must be persisted for reload recovery.
    from decafclaw import sticky as sticky_mod

    state = sticky_mod.read_sticky_state(config, "conv-sticky")
    assert state["widget_type"] == "progress_tracker"
    assert state["data"]["steps"][0]["status"] == "done"

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_pin_sticky_failed_job_shows_failed_state(tmp_path, config, sticky_registry):
    manager = BackgroundJobManager()
    fake = _StickyCapturingManager()
    job = await manager.start(
        "exit 3",
        cwd=str(tmp_path),
        config=config,
        conv_id="conv-sticky-fail",
        manager=fake,
        pin_sticky=True,
    )
    await job.reader_task

    final = fake.sticky_events[-1]
    assert final["type"] == "sticky_set"
    assert final["data"]["steps"][0]["status"] == "failed"
    assert "exit 3" in final["data"]["steps"][0]["note"]
    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_pin_sticky_throttles_burst_output(tmp_path, config, sticky_registry):
    """A burst of output lines produces a bounded number of sticky updates,
    never one per line."""
    manager = BackgroundJobManager()
    fake = _StickyCapturingManager()
    job = await manager.start(
        "seq 1 200",
        cwd=str(tmp_path),
        config=config,
        conv_id="conv-sticky-burst",
        manager=fake,
        pin_sticky=True,
    )
    await job.reader_task

    assert len(job.stdout_buffer) == 200, "the hook must not drop buffered output"
    events = fake.sticky_events
    # initial pin + bounded throttled line updates + final state.
    assert 2 <= len(events) < 20, f"expected throttled updates, got {len(events)} sticky events"
    assert events[-1]["data"]["steps"][0]["status"] == "done"
    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_pin_sticky_disabled_emits_nothing(tmp_path, config, sticky_registry):
    manager = BackgroundJobManager()
    fake = _StickyCapturingManager()
    job = await manager.start(
        "echo hi",
        cwd=str(tmp_path),
        config=config,
        conv_id="conv-sticky-off",
        manager=fake,
        pin_sticky=False,
    )
    await job.reader_task
    assert fake.sticky_events == []
    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_pin_sticky_stopped_job_clears_slot(tmp_path, config, sticky_registry):
    manager = BackgroundJobManager()
    fake = _StickyCapturingManager()
    job = await manager.start(
        "sleep 30",
        cwd=str(tmp_path),
        config=config,
        conv_id="conv-sticky-stop",
        manager=fake,
        pin_sticky=True,
    )
    assert fake.sticky_events and fake.sticky_events[0]["type"] == "sticky_set"
    await manager.stop(job.job_id)
    assert fake.sticky_events[-1]["type"] == "sticky_clear"

    from decafclaw import sticky as sticky_mod

    assert sticky_mod.read_sticky_state(config, "conv-sticky-stop") == sticky_mod.empty_sticky_state()
    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_pin_sticky_failure_is_fail_open(tmp_path, config, sticky_registry, monkeypatch):
    """A sticky publish failure must never break job monitoring."""
    import decafclaw.sticky as sticky_mod

    async def boom(*a, **k):
        raise RuntimeError("sticky boom")

    monkeypatch.setattr(sticky_mod, "set_sticky", boom)
    monkeypatch.setattr(sticky_mod, "clear_sticky", boom)

    manager = BackgroundJobManager()
    job = await manager.start(
        "echo hi",
        cwd=str(tmp_path),
        config=config,
        conv_id="conv-sticky-boom",
        manager=None,
        pin_sticky=True,
    )
    await job.reader_task
    assert job.status == "completed"
    assert job.exit_code == 0
    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_pin_sticky_plumbs_to_job(ctx, monkeypatch):
    """pin_sticky passed to the tool propagates into BackgroundJob."""
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", _yes_approval)

    from decafclaw.skills.background.tools import (
        _get_job_manager,
        tool_shell_background_start,
    )

    result = await tool_shell_background_start(ctx, command="true", pin_sticky=True)
    mgr = _get_job_manager(ctx)
    job = mgr.get(result.data["job_id"])
    assert job is not None
    assert job.pin_sticky is True

    # Default stays False.
    result = await tool_shell_background_start(ctx, command="true")
    job = mgr.get(result.data["job_id"])
    assert job is not None
    assert job.pin_sticky is False

    await mgr.cleanup_all()


class _BlockingStickyManager:
    """Allow the initial sticky pin, then block later emits until released."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []
        self.release = asyncio.Event()
        self.emit_started = asyncio.Event()

    async def emit(self, conv_id: str, event: dict) -> None:
        self.events.append((conv_id, event))
        if len(self.events) == 1:
            return
        self.emit_started.set()
        await self.release.wait()


class _StickyTrailingManager:
    """Capture sticky events and signal when a specific output line is published."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []
        self.sticky_event = asyncio.Event()

    async def emit(self, conv_id: str, event: dict) -> None:
        self.events.append((conv_id, event))
        if event.get("type") != "sticky_set":
            return
        data = event.get("data") or {}
        steps = data.get("steps") or []
        if steps and steps[0].get("note") == "second":
            self.sticky_event.set()

    @property
    def sticky_events(self) -> list[dict]:
        return [event for _cid, event in self.events if event.get("type") in ("sticky_set", "sticky_clear")]


@pytest.mark.asyncio
async def test_pin_sticky_emit_timeout_does_not_block_reader(tmp_path, config, sticky_registry, monkeypatch):
    """A slow sticky subscriber cannot block the subprocess output reader."""
    monkeypatch.setattr(tools, "_STICKY_THROTTLE_SEC", 0.01)
    monkeypatch.setattr(tools, "_STICKY_EMIT_TIMEOUT_SEC", 0.05)

    manager = BackgroundJobManager()
    fake = _BlockingStickyManager()
    job = await manager.start(
        "echo hi; sleep 2",
        cwd=str(tmp_path),
        config=config,
        conv_id="conv-sticky-timeout",
        manager=fake,
        pin_sticky=True,
    )
    assert fake.events, "expected initial sticky pin"
    assert job.reader_task is not None
    assert not job.reader_task.done()

    await fake.emit_started.wait()
    task = job.sticky_publish_task
    assert task is not None
    assert not task.done()
    await asyncio.wait_for(task, timeout=1)
    assert task.done()
    fake.release.set()
    await manager.stop(job.job_id)
    assert job.reader_task is not None
    assert job.reader_task.done()
    assert job.status in ("completed", "stopped")
    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_pin_sticky_tracks_latest_output_line_across_streams(tmp_path, config, sticky_registry):
    """A stderr line after stdout must become the latest sticky output line."""
    manager = BackgroundJobManager()
    fake = _StickyCapturingManager()
    job = await manager.start(
        'bash -c "echo stdout-line; echo stderr-line >&2; exit 3"',
        cwd=str(tmp_path),
        config=config,
        conv_id="conv-sticky-mixed",
        manager=fake,
        pin_sticky=True,
    )
    await job.reader_task

    final = fake.sticky_events[-1]
    assert final["data"]["steps"][0]["status"] == "failed"
    assert "stderr-line" in final["data"]["steps"][0]["note"]
    assert "stdout-line" not in final["data"]["steps"][0]["note"]
    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_pin_sticky_schedules_trailing_update_after_burst(tmp_path, config, sticky_registry, monkeypatch):
    """Suppressed throttled lines coalesce into a trailing update."""
    monkeypatch.setattr(tools, "_STICKY_THROTTLE_SEC", 0.2)

    manager = BackgroundJobManager()
    fake = _StickyTrailingManager()
    job = await manager.start(
        'bash -c "echo first; echo second; sleep 2"',
        cwd=str(tmp_path),
        config=config,
        conv_id="conv-sticky-trailing",
        manager=fake,
        pin_sticky=True,
    )
    await fake.sticky_event.wait()
    assert fake.sticky_events[-1]["data"]["steps"][0]["note"] == "second"
    await manager.stop(job.job_id)
    await manager.cleanup_all()


# ---------------------------------------------------------------------------
# Task 8: Progress parser engine & sticky progress integration (#1137)
# ---------------------------------------------------------------------------


def test_strip_ansi_removes_escape_codes():
    raw = "\x1b[32m[1/5]\x1b[0m Compiling core...\r\n"
    assert strip_ansi(raw) == "[1/5] Compiling core...\r\n"


def test_none_parser_line_counts():
    p = NoneParser()
    p.feed_line("line 1")
    p.feed_line("line 2")
    up = p.get_progress()
    assert up.current == 2
    assert up.total is None
    assert up.unit == "lines"
    assert up.label == "line 2"

    sticky = p.format_running_sticky("cat file")
    assert sticky["summary"] == "line 2"
    assert sticky["steps"][0]["note"] == "line 2"


def test_step_parser_formats():
    p = StepParser()
    # Bracket format
    p.feed_line("[1/10] Building foo")
    up = p.get_progress()
    assert up.current == 1
    assert up.total == 10
    assert up.unit == "steps"
    assert up.label == "Building foo"

    # Parenthesis format
    p.feed_line("(2/10) Building bar")
    up = p.get_progress()
    assert up.current == 2
    assert up.total == 10
    assert up.label == "Building bar"

    # Step N of M format
    p.feed_line("Step 3 of 10: Building baz")
    up = p.get_progress()
    assert up.current == 3
    assert up.total == 10
    assert up.label == "Building baz"

    sticky = p.format_running_sticky("make build")
    assert "[3/10] Building baz" in sticky["summary"]
    assert sticky["steps"][0]["note"] == "[3/10] Building baz"


def test_step_parser_invalid_fallbacks():
    p = StepParser()
    p.feed_line("[0/0] Invalid total")
    up = p.get_progress()
    assert up.total is None
    assert up.unit == "lines"

    p.feed_line("Random unrecognized line")
    up = p.get_progress()
    assert up.current == 2
    assert up.total is None


def test_percent_parser_formats():
    p = PercentParser()
    p.feed_line("Downloading assets: 45%")
    up = p.get_progress()
    assert up.current == 45
    assert up.total == 100
    assert up.unit == "%"
    assert "Downloading assets" in up.label

    sticky = p.format_running_sticky("curl url")
    assert "45%" in sticky["summary"]
    assert "45%" in sticky["steps"][0]["note"]


def test_pytest_parser_formats():
    p = PytestParser()
    p.feed_line("collected 50 items")
    assert p.total_tests == 50

    p.feed_line("tests/test_one.py::test_alpha PASSED [ 2%]")
    up = p.get_progress()
    assert p.passed == 1
    assert p.failed == 0
    assert up.current == 1
    assert up.total == 50
    assert up.unit == "tests"

    p.feed_line("tests/test_one.py::test_beta FAILED [ 4%]")
    up = p.get_progress()
    assert p.passed == 1
    assert p.failed == 1
    assert up.status == "failed"

    p.feed_line("=== 48 passed, 2 failed in 5.2s ===")
    up = p.get_progress()
    assert p.passed == 48
    assert p.failed == 2


def test_auto_parser_detects_format_and_falls_back():
    p = AutoParser()
    # Initial lines unrecognized: line counter fallback
    p.feed_line("Starting task...")
    up = p.get_progress()
    assert up.unit == "lines"
    assert up.current == 1

    # Step format encountered
    p.feed_line("[1/4] Preparing environment")
    up = p.get_progress()
    assert up.unit == "steps"
    assert up.current == 1
    assert up.total == 4


@pytest.mark.asyncio
async def test_pin_sticky_with_step_parser(tmp_path, config, sticky_registry):
    """pin_sticky=True with progress_parser='step' updates progress_tracker with step counts."""
    manager = BackgroundJobManager()
    fake = _StickyCapturingManager()
    cmd = (
        'bash -c "echo \\"[1/3] Step one\\"; '
        'sleep 0.1; echo \\"[2/3] Step two\\"; '
        'sleep 0.1; echo \\"[3/3] Step three\\""'
    )
    job = await manager.start(
        cmd,
        cwd=str(tmp_path),
        config=config,
        conv_id="conv-sticky-step",
        manager=fake,
        pin_sticky=True,
        progress_parser="step",
    )
    await job.reader_task

    events = fake.sticky_events
    assert len(events) >= 2
    final = events[-1]
    assert final["data"]["steps"][0]["status"] == "done"
    assert "[3/3]" in final["data"]["steps"][0]["note"]
    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_pin_sticky_with_percent_parser(tmp_path, config, sticky_registry):
    """pin_sticky=True with progress_parser='percent' updates progress_tracker with percentages."""
    manager = BackgroundJobManager()
    fake = _StickyCapturingManager()
    cmd = 'bash -c "echo \\"downloading 25%\\"; sleep 0.1; echo \\"downloading 75%\\""'
    job = await manager.start(
        cmd,
        cwd=str(tmp_path),
        config=config,
        conv_id="conv-sticky-pct",
        manager=fake,
        pin_sticky=True,
        progress_parser="percent",
    )
    await job.reader_task

    events = fake.sticky_events
    assert len(events) >= 2
    final = events[-1]
    assert final["data"]["steps"][0]["status"] == "done"
    assert "75%" in final["data"]["steps"][0]["note"]
    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_pin_sticky_with_pytest_parser(tmp_path, config, sticky_registry):
    """pin_sticky=True with progress_parser='pytest' tracks pass/fail counts."""
    manager = BackgroundJobManager()
    fake = _StickyCapturingManager()
    cmd = (
        'bash -c "echo \\"collected 10 items\\"; '
        'echo \\"tests/test_a.py::test_1 PASSED [ 50%]\\"; '
        'echo \\"tests/test_a.py::test_2 FAILED [100%]\\"; '
        'exit 1"'
    )
    job = await manager.start(
        cmd,
        cwd=str(tmp_path),
        config=config,
        conv_id="conv-sticky-pytest",
        manager=fake,
        pin_sticky=True,
        progress_parser="pytest",
    )
    await job.reader_task

    events = fake.sticky_events
    final = events[-1]
    assert final["data"]["steps"][0]["status"] == "failed"
    assert "1 passed, 1 failed" in final["data"]["steps"][0]["note"]
    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_progress_parser_tool_plumbing(ctx, monkeypatch):
    """progress_parser parameter plumbs from tool_shell_background_start to job."""
    from decafclaw.skills.background.tools import _get_job_manager, tool_shell_background_start

    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", _yes_approval)

    result = await tool_shell_background_start(ctx, command="true", pin_sticky=True, progress_parser="pytest")
    mgr = _get_job_manager(ctx)
    job = mgr.get(result.data["job_id"])
    assert job is not None
    assert job.pin_sticky is True
    assert job.progress_parser == "pytest"
    assert result.data["progress_parser"] == "pytest"

    await mgr.cleanup_all()


def test_pytest_parser_xdist_format():
    p = PytestParser()
    p.feed_line("created: 8/8 workers")
    p.feed_line("8 workers [39 items]")
    assert p.total_tests == 39

    p.feed_line("....................................... [100%]")
    up = p.get_progress()
    assert up.total == 39
    assert up.current == 39


def test_get_progress_parser_factory():
    assert isinstance(get_progress_parser("auto"), AutoParser)
    assert isinstance(get_progress_parser("step"), StepParser)
    assert isinstance(get_progress_parser("percent"), PercentParser)
    assert isinstance(get_progress_parser("pytest"), PytestParser)
    assert isinstance(get_progress_parser("none"), NoneParser)
    # Unknown falls back to AutoParser
    assert isinstance(get_progress_parser("unknown"), AutoParser)


def test_auto_parser_huge_line_safety():
    p = AutoParser()
    huge_line = "A" * 5000 + " [1/2] Step one"
    p.feed_line(huge_line)
    # Should not raise, lines beyond 2000 capped safely
    up = p.get_progress()
    assert up.status == "in_progress"


@pytest.mark.asyncio
async def test_pin_sticky_auto_interleaved_stderr(tmp_path, config, sticky_registry):
    """Interleaved stderr lines do not disrupt step progress in auto mode."""
    manager = BackgroundJobManager()
    fake = _StickyCapturingManager()
    cmd = (
        'bash -c "echo \\"[1/2] Step one\\"; '
        'echo \\"warning: some deprecation\\" >&2; '
        'sleep 0.1; echo \\"[2/2] Step two\\""'
    )
    job = await manager.start(
        cmd,
        cwd=str(tmp_path),
        config=config,
        conv_id="conv-sticky-interleaved",
        manager=fake,
        pin_sticky=True,
        progress_parser="auto",
    )
    await job.reader_task

    events = fake.sticky_events
    final = events[-1]
    assert final["data"]["steps"][0]["status"] == "done"
    assert "[2/2]" in final["data"]["steps"][0]["note"]
    await manager.cleanup_all()


def test_pytest_summary_reordered_failure_before_passed():
    p = PytestParser()
    p.feed_line("=== 2 failed, 35 passed, 1 skipped in 1.23s ===")
    assert p.passed == 35
    assert p.failed == 2
    assert p.skipped == 1


def test_pytest_format_running_sticky_dots_mode():
    p = PytestParser()
    p.feed_line("collected 100 items")
    p.feed_line(".................................................. [ 50%]")
    sticky = p.format_running_sticky("pytest")
    assert "50/100 (50%)" in sticky["summary"]
    assert "50/100 (50%)" in sticky["steps"][0]["note"]


def test_auto_parser_detects_pytest_from_skipped_line():
    p = AutoParser()
    p.feed_line("tests/test_mod.py::test_skipped SKIPPED [ 10%]")
    up = p.get_progress()
    assert p._active_preset == "pytest"
    assert up.unit == "tests"


def test_step_parser_unlabeled_step_resets_label():
    p = StepParser()
    p.feed_line("[1/2] Step one label")
    assert p.step_label == "Step one label"
    p.feed_line("[2/2]")
    assert p.step_label == ""


def test_feed_line_carriage_return_separated():
    p = PercentParser()
    p.feed_line("10%\r50%\r100%")
    up = p.get_progress()
    assert up.current == 100


def test_pytest_summary_undecorated_quiet_mode():
    p = PytestParser()
    p.feed_line("4 passed in 0.10s")
    assert p.passed == 4

    p2 = PytestParser()
    p2.feed_line("2 failed, 4 passed in 1.2s")
    assert p2.passed == 4
    assert p2.failed == 2


def test_pytest_collection_with_deselected():
    p = PytestParser()
    p.feed_line("collected 50 items / 40 deselected / 10 selected")
    assert p.total_tests == 10


def test_pytest_xdist_verbose_outcome_first():
    p = PytestParser()
    p.feed_line("[gw0] [ 50%] FAILED tests/test_a.py::test_one")
    assert p.failed == 1
    assert p.pct == 50
    assert p.current_test == "tests/test_a.py::test_one"


def test_auto_parser_switches_presets_on_format_change():
    p = AutoParser()
    p.feed_line("[1/2] Step one")
    assert p._active_preset == "step"
    # New format lines take over
    p.feed_line("downloading 50%")
    assert p._active_preset == "percent"
    up = p.get_progress()
    assert up.current == 50
    assert up.unit == "%"


@pytest.mark.asyncio
async def test_pin_sticky_streaming_carriage_returns(tmp_path, config, sticky_registry):
    """Processes writing in-place with carriage returns update sticky slot."""
    manager = BackgroundJobManager()
    fake = _StickyCapturingManager()
    cmd = 'bash -c "printf \\"downloading 20%%\\\\r\\"; sleep 0.1; printf \\"downloading 80%%\\\\r\\""'
    job = await manager.start(
        cmd,
        cwd=str(tmp_path),
        config=config,
        conv_id="conv-sticky-cr",
        manager=fake,
        pin_sticky=True,
        progress_parser="percent",
    )
    await job.reader_task

    events = fake.sticky_events
    assert len(events) >= 2
    final = events[-1]
    assert "80%" in final["data"]["steps"][0]["note"]
    await manager.cleanup_all()
