"""Tests for shell auto-backgrounding on a foreground time budget (#1136).

A command run through `shell` with `foreground_wait_budget_sec` set is started
through the shared background-job runner and awaited up to the budget:

- finishes within the budget -> ordinary foreground result, no job left behind;
- exceeds the budget -> the SAME process continues as a managed background job
  and the tool returns its handle; the completion sequence fires exactly once.

The tiny real budgets (0.1-0.2s vs `sleep` commands) are the mechanism under
test, not synchronization: completion is awaited via reader tasks.
"""

import asyncio
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from decafclaw.conversation_manager import TurnKind
from decafclaw.media import ToolResult
from decafclaw.skills.background import tools as bg_tools
from decafclaw.skills.background.tools import tool_shell_background_status, tool_shell_background_stop
from decafclaw.tools.shell_tools import _save_allow_pattern, tool_shell


@pytest.fixture(autouse=True)
async def _clean_job_managers():
    """Isolate the module-level job registry between tests.

    Every test shares conv_id "test-conv" (root conftest), so leftover jobs
    would bleed across tests. shutdown() stops running processes and clears
    the registry; it is idempotent.
    """
    yield
    await bg_tools.shutdown()


def _prepare_workspace(ctx) -> Path:
    ws = Path(ctx.config.workspace_path)
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _no_jobs() -> bool:
    return not any(m.jobs for m in bg_tools._managers.values())


# -- config -------------------------------------------------------------------


def test_shell_config_defaults():
    from decafclaw.config_types import ShellConfig

    cfg = ShellConfig()
    assert cfg.foreground_wait_budget_sec is None  # opt-in: legacy behavior by default
    assert cfg.max_runtime_sec == 30.0


def test_shell_config_env_resolution(monkeypatch):
    from decafclaw.config import load_sub_config
    from decafclaw.config_types import ShellConfig

    monkeypatch.setenv("SHELL_FOREGROUND_WAIT_BUDGET_SEC", "12.5")
    monkeypatch.setenv("SHELL_MAX_RUNTIME_SEC", "45")
    cfg = load_sub_config(ShellConfig, {}, "SHELL")
    assert cfg.foreground_wait_budget_sec == 12.5
    assert cfg.max_runtime_sec == 45.0


# -- below budget: ordinary foreground result ---------------------------------


@pytest.mark.asyncio
async def test_below_budget_returns_normal_result(ctx):
    """A command that finishes within the budget returns an ordinary result."""
    _prepare_workspace(ctx)
    ctx.config.shell.foreground_wait_budget_sec = 5.0
    _save_allow_pattern(ctx.config, "echo hello-foreground")

    with (
        patch("decafclaw.archive.append_message") as mock_append,
        patch("decafclaw.notifications.notify", new_callable=AsyncMock) as mock_notify,
    ):
        result = await tool_shell(ctx, "echo hello-foreground")

    assert "hello-foreground" in result.text
    assert result.data is None  # no job handle
    mock_append.assert_not_called()
    mock_notify.assert_not_awaited()
    assert _no_jobs()


@pytest.mark.asyncio
async def test_foreground_completion_fires_nothing(ctx):
    """A short command that settles as foreground fires no completion sequence."""
    _prepare_workspace(ctx)
    ctx.config.shell.foreground_wait_budget_sec = 2.0
    _save_allow_pattern(ctx.config, "sleep 0.1 && echo settled")

    with (
        patch("decafclaw.archive.append_message") as mock_append,
        patch("decafclaw.notifications.notify", new_callable=AsyncMock) as mock_notify,
    ):
        result = await tool_shell(ctx, "sleep 0.1 && echo settled")

    assert "settled" in result.text
    mock_append.assert_not_called()
    mock_notify.assert_not_awaited()
    assert _no_jobs()


# -- above budget: handoff to a managed background job -------------------------


@pytest.mark.asyncio
async def test_above_budget_returns_job_handle_same_process(ctx):
    """A command that exceeds the budget hands off: same process, usable job ID."""
    ws = _prepare_workspace(ctx)
    ctx.config.shell.foreground_wait_budget_sec = 0.2
    command = "echo run >> side_effects.txt && sleep 3"
    _save_allow_pattern(ctx.config, command)

    result = await tool_shell(ctx, command)

    assert result.data["status"] == "running"
    job_id = result.data["job_id"]
    pid = result.data["pid"]

    # The same process is still alive and has executed the side effect exactly once.
    os.kill(pid, 0)
    assert (ws / "side_effects.txt").read_text().count("run") == 1

    # Inspectable via the existing status tool.
    status = await tool_shell_background_status(ctx, job_id)
    assert job_id in status.text

    # Stoppable via the existing stop tool.
    await tool_shell_background_stop(ctx, job_id)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)

    job = bg_tools._managers[ctx.conv_id].get(job_id)
    assert job.status == "stopped"


@pytest.mark.asyncio
async def test_handoff_completion_fires_exactly_once(ctx):
    """A handed-off job that completes fires the completion sequence exactly once."""
    ws = _prepare_workspace(ctx)
    ctx.config.shell.foreground_wait_budget_sec = 0.2
    command = "echo done >> side_effects.txt && sleep 0.5"
    _save_allow_pattern(ctx.config, command)

    manager_stub = MagicMock(emit=AsyncMock(), enqueue_turn=AsyncMock())
    ctx.manager = manager_stub

    with (
        patch("decafclaw.archive.append_message") as mock_append,
        patch("decafclaw.notifications.notify", new_callable=AsyncMock) as mock_notify,
    ):
        result = await tool_shell(ctx, command)
        assert result.data["status"] == "running"

        job = bg_tools._managers[ctx.conv_id].get(result.data["job_id"])
        # Wait for the real completion path to settle (finalize runs in the reader).
        await asyncio.wait_for(job.reader_task, timeout=10)

    # One execution across the handoff boundary...
    assert (ws / "side_effects.txt").read_text().count("done") == 1
    # ...one archive record, one notification, one wake.
    assert mock_append.call_count == 1
    record = mock_append.call_args.args[2]
    assert record["role"] == "background_event"
    assert record["job_id"] == result.data["job_id"]
    assert mock_notify.await_count == 1
    assert manager_stub.enqueue_turn.await_count == 1
    wake_kwargs = manager_stub.enqueue_turn.await_args.kwargs
    assert wake_kwargs["kind"] is TurnKind.WAKE
    assert job.status == "completed"


@pytest.mark.asyncio
async def test_handed_off_job_uses_max_runtime(ctx):
    """A handed-off job's lifetime is the configured total runtime limit."""
    _prepare_workspace(ctx)
    ctx.config.shell.foreground_wait_budget_sec = 0.1
    ctx.config.shell.max_runtime_sec = 2.0
    _save_allow_pattern(ctx.config, "sleep 30")

    result = await tool_shell(ctx, "sleep 30")
    job_id = result.data["job_id"]
    manager = bg_tools._managers[ctx.conv_id]
    job = manager.get(job_id)
    assert job.max_lifetime == 2.0

    # The existing expiry policy kills it once the lifetime elapses and settles
    # it exactly once (the reader is cancelled, so finalize runs explicitly).
    with (
        patch("decafclaw.archive.append_message") as mock_append,
        patch("decafclaw.notifications.notify", new_callable=AsyncMock) as mock_notify,
    ):
        job.started_at = time.monotonic() - 3.0  # age it past max_lifetime
        await manager.cleanup_expired()

    assert job.status == "expired"
    assert mock_append.call_count == 1
    assert mock_notify.await_count == 1


# -- failure paths leave no orphans --------------------------------------------


@pytest.mark.asyncio
async def test_approval_denied_spawns_nothing(ctx):
    """A denied command is never spawned — no process, no job."""
    ws = _prepare_workspace(ctx)
    ctx.config.shell.foreground_wait_budget_sec = 5.0

    with patch(
        "decafclaw.tools.shell_tools.check_shell_approval",
        new_callable=AsyncMock,
        return_value={"approved": False, "reason": "denied by user"},
    ):
        result = await tool_shell(ctx, "echo run >> side_effects.txt")

    assert "denied" in result.text
    assert not (ws / "side_effects.txt").exists()
    assert _no_jobs()


@pytest.mark.asyncio
async def test_spawn_failure_returns_error(ctx):
    """A spawn failure returns an error and leaves no job behind."""
    _prepare_workspace(ctx)
    ctx.config.shell.foreground_wait_budget_sec = 5.0
    _save_allow_pattern(ctx.config, "echo hi")

    with patch(
        "asyncio.create_subprocess_shell",
        new_callable=AsyncMock,
        side_effect=OSError("resource exhausted"),
    ):
        result = await tool_shell(ctx, "echo hi")

    assert result.text.startswith("[error:")
    assert _no_jobs()


@pytest.mark.asyncio
async def test_cancellation_during_wait_kills_process(ctx):
    """Cancelling the turn before handoff kills the process; nothing settles."""
    _prepare_workspace(ctx)
    ctx.config.shell.foreground_wait_budget_sec = 10.0
    _save_allow_pattern(ctx.config, "sleep 30")

    with (
        patch("decafclaw.archive.append_message") as mock_append,
        patch("decafclaw.notifications.notify", new_callable=AsyncMock) as mock_notify,
    ):
        task = asyncio.create_task(tool_shell(ctx, "sleep 30"))

        # Wait (yielding only) until the job is spawned and claimed.
        job = None
        for _ in range(1000):
            manager = bg_tools._managers.get(ctx.conv_id)
            if manager and manager.jobs:
                job = next(iter(manager.jobs.values()))
                break
            await asyncio.sleep(0)
        assert job is not None and job.foreground

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    # Process killed and reaped; job dropped without a completion sequence.
    assert job.process.returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(job.pid, 0)
    assert _no_jobs()
    mock_append.assert_not_called()
    mock_notify.assert_not_awaited()


# -- legacy path and fail-open --------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_path_when_budget_disabled(ctx):
    """With no budget configured, the legacy blocking path runs unchanged."""
    assert ctx.config.shell.foreground_wait_budget_sec is None  # default
    _save_allow_pattern(ctx.config, "echo hi")

    sentinel = ToolResult(text="legacy-output")
    with patch("decafclaw.tools.shell_tools._execute_command", return_value=sentinel) as mock_exec:
        result = await tool_shell(ctx, "echo hi")

    assert result is sentinel
    mock_exec.assert_called_once()


@pytest.mark.asyncio
async def test_zero_budget_uses_legacy_path(ctx):
    """A zero budget is treated as disabled (legacy path)."""
    ctx.config.shell.foreground_wait_budget_sec = 0.0
    _save_allow_pattern(ctx.config, "echo hi")

    sentinel = ToolResult(text="legacy-output")
    with patch("decafclaw.tools.shell_tools._execute_command", return_value=sentinel) as mock_exec:
        result = await tool_shell(ctx, "echo hi")

    assert result is sentinel
    mock_exec.assert_called_once()


@pytest.mark.asyncio
async def test_runner_import_failure_falls_back_to_legacy(ctx):
    """If the background runner is unavailable, fall back to legacy execution."""
    ctx.config.shell.foreground_wait_budget_sec = 5.0
    _save_allow_pattern(ctx.config, "echo hi")

    sentinel = ToolResult(text="legacy-output")
    with (
        patch.dict(sys.modules, {"decafclaw.skills.background.tools": None}),
        patch("decafclaw.tools.shell_tools._execute_command", return_value=sentinel) as mock_exec,
    ):
        result = await tool_shell(ctx, "echo hi")

    assert result is sentinel
    mock_exec.assert_called_once()
