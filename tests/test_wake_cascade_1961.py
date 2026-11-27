"""Wake-cascade regressions for #1961.

Two defects, both producing the same symptom: N background jobs that finish
while the conversation is busy turn into N autonomous WAKE turns, each costing a
full model round-trip to discover nothing new.

    1. `_finalize_job` enqueues a wake even when the agent already consumed the
       result by blocking on `shell_background_status(wait=...)`. #1136 solved
       this shape for the budgeted-foreground `shell` path with
       `BackgroundJob.foreground`, but the status tool never sets an equivalent.
    2. `_drain_pending` coalesces consecutive USER turns into one but pops
       WAKE turns one at a time, and each turn's completion re-drains — a serial
       cascade of one wake turn per queued job.

The tests below pin both, plus the over-suppression guards that make the fix
safe: a job nobody waited on must still wake, and a plain poll that happens to
land after completion must NOT count as having consumed the result.
"""

import asyncio

import pytest

from decafclaw.context import TaskMode
from decafclaw.conversation_manager import ConversationManager, TurnKind
from decafclaw.inbox import _read_inbox
from decafclaw.media import ToolResult
from decafclaw.skills.background.tools import (
    _get_job_manager,
    tool_shell_background_start,
    tool_shell_background_status,
)


async def _yes_approval(*a, **k):
    return {"approved": True}


def _wake_nudges(config, conv_id: str) -> list[dict]:
    """WAKE entries currently queued in the conversation inbox."""
    return [m for m in _read_inbox(config, conv_id) if m.get("kind") == TurnKind.WAKE.value]


@pytest.mark.asyncio
async def test_blocking_status_read_suppresses_wake(ctx, config, monkeypatch):
    """A result consumed via status(wait=...) must not enqueue a wake (#1961 fix 1).

    The agent started the job, blocked until it finished, and read the output
    inline. The wake turn would tell it something it already knows.
    """
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    ctx.conv_id = "c-1961-consumed"

    manager = ConversationManager(config, ctx.event_bus)
    ctx.manager = manager
    # Occupy the conversation so a wake, if wrongly enqueued, would queue in the
    # inbox rather than immediately starting a turn.
    manager._get_or_create(ctx.conv_id).busy = True

    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", _yes_approval)

    started = await tool_shell_background_start(ctx, command="true")
    job_manager = _get_job_manager(ctx)
    job = job_manager.get(started.data["job_id"])
    assert job is not None

    # The blocking read that consumes the result.
    status = await tool_shell_background_status(ctx, job_id=job.job_id, wait=10.0)
    assert job.status.value != "running", status.text

    await job.reader_task
    await asyncio.sleep(0)

    assert _wake_nudges(config, ctx.conv_id) == [], "a result read via status(wait=...) still queued a wake turn"

    await job_manager.cleanup_all()


@pytest.mark.asyncio
async def test_unwaited_job_still_wakes(ctx, config, monkeypatch):
    """Over-suppression guard: a job nobody waited on must still wake (#1961).

    This is the behaviour the wake exists for — start a long job, end the turn,
    get told when it finishes.
    """
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    ctx.conv_id = "c-1961-unwaited"

    manager = ConversationManager(config, ctx.event_bus)
    ctx.manager = manager
    manager._get_or_create(ctx.conv_id).busy = True

    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", _yes_approval)

    started = await tool_shell_background_start(ctx, command="true")
    job_manager = _get_job_manager(ctx)
    job = job_manager.get(started.data["job_id"])
    assert job is not None

    await job.reader_task
    await asyncio.sleep(0)

    nudges = _wake_nudges(config, ctx.conv_id)
    assert len(nudges) == 1, "a job the agent never waited on must still enqueue one wake"

    await job_manager.cleanup_all()


@pytest.mark.asyncio
async def test_polling_after_completion_does_not_suppress_wake(ctx, config, monkeypatch):
    """A plain poll that lands after completion must not count as consuming.

    Fix 1 keys on the *blocking* read specifically. A `status(job_id)` with no
    wait could arrive at any time; treating it as consumption would silence
    wakes the agent never asked to wait for.
    """
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    ctx.conv_id = "c-1961-polled"

    manager = ConversationManager(config, ctx.event_bus)
    ctx.manager = manager
    manager._get_or_create(ctx.conv_id).busy = True

    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", _yes_approval)

    started = await tool_shell_background_start(ctx, command="true")
    job_manager = _get_job_manager(ctx)
    job = job_manager.get(started.data["job_id"])
    assert job is not None

    await job.reader_task
    await asyncio.sleep(0)

    # Non-blocking read, after the fact.
    await tool_shell_background_status(ctx, job_id=job.job_id)

    nudges = _wake_nudges(config, ctx.conv_id)
    assert len(nudges) == 1, "a non-blocking poll must not suppress the wake"

    await job_manager.cleanup_all()


@pytest.mark.asyncio
async def test_consecutive_wakes_coalesce_into_one_turn(ctx, config, monkeypatch):
    """K wakes queued during a busy turn must drain as ONE turn, not K (#1961 fix 2).

    This is the actual production shape: a long turn is in flight, several
    background jobs complete, and each queues its own wake. Before this fix each
    one became a separate turn — the cascade.
    """
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    ctx.conv_id = "c-1961-coalesce"

    manager = ConversationManager(config, ctx.event_bus)
    ctx.manager = manager

    captured = []

    async def fake_run_agent_turn(ctx_arg, user_message, history, **kwargs):
        captured.append({"task_mode": getattr(ctx_arg, "task_mode", TaskMode.INTERACTIVE)})
        return ToolResult(text="BACKGROUND_WAKE_OK")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    # Hold the conversation busy, as a long-running turn would, so every wake
    # queues rather than dispatching immediately.
    state = manager._get_or_create(ctx.conv_id)
    state.busy = True

    for i in range(4):
        await manager.enqueue_turn(
            conv_id=ctx.conv_id,
            kind=TurnKind.WAKE,
            prompt=f"background job {i} completed",
        )

    queued = [m for m in _read_inbox(config, ctx.conv_id) if m.get("kind") == TurnKind.WAKE.value]
    assert len(queued) == 4, "all four wakes should have queued while the conversation was busy"

    # Release the hold and let the drain run.
    state.busy = False
    await manager._drain_pending(state)

    for _ in range(40):
        if not state.busy and not _read_inbox(config, ctx.conv_id):
            break
        await asyncio.sleep(0.02)

    wake_calls = [c for c in captured if c["task_mode"] is TaskMode.BACKGROUND_WAKE]
    assert len(wake_calls) == 1, f"expected the four queued wakes to coalesce into one turn, got {len(wake_calls)}"
    assert _read_inbox(config, ctx.conv_id) == []

    await manager.shutdown() if hasattr(manager, "shutdown") else None
