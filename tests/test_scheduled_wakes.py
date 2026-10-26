"""Unit tests for scheduled wake turns (#1103)."""

import asyncio
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.context import Context
from decafclaw.conversation_paths import scheduled_wakes_path
from decafclaw.media import WidgetRequest
from decafclaw.scheduled_wakes import (
    ScheduledWake,
    ScheduledWakeManager,
    load_scheduled_wakes,
    save_scheduled_wakes,
)
from decafclaw.tools.schedule_tools import (
    tool_schedule_cancel,
    tool_schedule_list,
    tool_schedule_turn,
)


@pytest.fixture
def mock_manager(config):
    mgr = MagicMock()
    mgr.config = config
    mgr.enqueue_turn = AsyncMock()
    mgr.scheduled_wakes = ScheduledWakeManager(config, manager=mgr)
    return mgr


@pytest.mark.asyncio
async def test_schedule_turn_persists_and_returns_wake(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "test-conv-1"

    result = await tool_schedule_turn(
        ctx,
        delay_seconds=60,
        reason="Check CI status",
        metadata={"job_id": "123"},
    )
    assert "[error:" not in result.text
    assert "Scheduled wake turn" in result.text
    assert "test-conv-1" not in result.text  # Clean user-facing text
    assert result.data["reason"] == "Check CI status"
    assert result.data["delay_seconds"] == 60
    assert result.data["metadata"] == {"job_id": "123"}
    schedule_id = result.data["schedule_id"]

    # Verify sidecar persistence
    schedules = load_scheduled_wakes(ctx.config, "test-conv-1")
    assert len(schedules) == 1
    assert schedules[0].schedule_id == schedule_id
    assert schedules[0].reason == "Check CI status"
    assert schedules[0].delay_seconds == 60


@pytest.mark.asyncio
async def test_schedule_turn_validation_errors(ctx):
    ctx.conv_id = "test-conv-1"

    # Negative / zero delay
    res0 = await tool_schedule_turn(ctx, delay_seconds=0, reason="test")
    assert "[error:" in res0.text
    assert "delay_seconds must be a positive integer" in res0.text

    # Empty reason
    res_empty = await tool_schedule_turn(ctx, delay_seconds=10, reason="")
    assert "[error:" in res_empty.text
    assert "reason is required" in res_empty.text

    # Missing conv_id
    ctx.conv_id = ""
    res_no_conv = await tool_schedule_turn(ctx, delay_seconds=10, reason="test")
    assert "[error:" in res_no_conv.text
    assert "No active conversation ID" in res_no_conv.text


@pytest.mark.asyncio
async def test_schedule_turn_rejects_child_agents_and_unattended(ctx):
    """Child agents and unattended turns must not schedule autonomous wake turns (#1119 review)."""
    ctx.conv_id = "conv-child"
    ctx.is_child = True

    result = await tool_schedule_turn(ctx, delay_seconds=60, reason="Check something")
    assert "[error: schedule_turn is only available in interactive user conversations]" in result.text
    assert result.data["error"] == "unattended_or_child_not_permitted"

    ctx.is_child = False
    ctx.task_mode = "scheduled"
    assert ctx.is_unattended is True
    result2 = await tool_schedule_turn(ctx, delay_seconds=60, reason="Check something")
    assert "[error: schedule_turn is only available in interactive user conversations]" in result2.text
    assert result2.data["error"] == "unattended_or_child_not_permitted"


@pytest.mark.asyncio
async def test_cross_conversation_cancellation_prevention(ctx, mock_manager):
    """Cancelling a schedule in conversation B with conversation A's ID must not cancel A's timer (#1119 review)."""
    ctx.manager = mock_manager
    ctx.conv_id = "conv-A"

    res_a = await tool_schedule_turn(ctx, delay_seconds=300, reason="Task for A")
    id_a = res_a.data["schedule_id"]

    # Switch to conversation B and attempt to cancel A's schedule
    ctx.conv_id = "conv-B"
    res_b = await tool_schedule_cancel(ctx, id_a)
    assert "[error: Scheduled wake" in res_b.text
    assert res_b.data["cancelled"] is False

    # Verify A's schedule still exists and task is still running
    schedules_a = load_scheduled_wakes(ctx.config, "conv-A")
    assert len(schedules_a) == 1
    assert schedules_a[0].schedule_id == id_a
    assert id_a in mock_manager.scheduled_wakes._tasks


@pytest.mark.asyncio
async def test_schedule_cancel_success_and_not_found(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "test-conv-cancel"

    res = await tool_schedule_turn(ctx, delay_seconds=100, reason="To cancel")
    schedule_id = res.data["schedule_id"]

    # Verify cancel succeeds
    cancel_res = await tool_schedule_cancel(ctx, schedule_id)
    assert "[error:" not in cancel_res.text
    assert cancel_res.data["cancelled"] is True
    assert f"Cancelled scheduled wake '{schedule_id}'." in cancel_res.text

    # Verify sidecar is empty
    assert load_scheduled_wakes(ctx.config, "test-conv-cancel") == []

    # Second cancel fails with not found error
    cancel_res2 = await tool_schedule_cancel(ctx, schedule_id)
    assert "[error:" in cancel_res2.text
    assert cancel_res2.data["cancelled"] is False


@pytest.mark.asyncio
async def test_schedule_list(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "test-conv-list"

    empty_res = tool_schedule_list(ctx)
    assert "No pending scheduled wake turns" in empty_res.text
    assert empty_res.data["schedules"] == []
    assert empty_res.widget is None

    await tool_schedule_turn(ctx, delay_seconds=60, reason="First wake")
    await tool_schedule_turn(ctx, delay_seconds=120, reason="Second wake")

    list_res = tool_schedule_list(ctx)
    assert "Found 2 pending scheduled wake turn(s):" in list_res.text
    assert len(list_res.data["schedules"]) == 2
    assert list_res.data["schedules"][0]["reason"] == "First wake"
    assert list_res.data["schedules"][1]["reason"] == "Second wake"
    assert isinstance(list_res.widget, WidgetRequest)
    assert list_res.widget.widget_type == "data_table"
    assert list_res.widget.target == "inline"
    assert "2 pending wake turn(s)" in list_res.widget.data["caption"]
    assert len(list_res.widget.data["rows"]) == 2
    assert list_res.widget.data["rows"][0]["reason"] == "First wake"
    assert list_res.widget.data["columns"] == [
        {"key": "schedule_id", "label": "Schedule ID"},
        {"key": "fire_at", "label": "Fire At"},
        {"key": "remaining", "label": "Remaining (s)"},
        {"key": "reason", "label": "Reason"},
    ]
    assert isinstance(list_res.widget.data["rows"][0]["remaining"], int)


@pytest.mark.asyncio
async def test_scheduled_wake_fires_and_enqueues_turn(config, mock_manager):
    wake_mgr = ScheduledWakeManager(config, manager=mock_manager)

    wake = await wake_mgr.schedule(
        conv_id="conv-fire",
        delay_seconds=1,
        reason="Verify deploy",
        metadata={"env": "prod"},
    )

    # Let the timer fire (patched sleep or fast delay)
    # We can await the internal task
    task = wake_mgr._tasks.get(wake.schedule_id)
    assert task is not None
    await task

    # Assert TurnKind.WAKE was enqueued on mock_manager
    mock_manager.enqueue_turn.assert_awaited_once()
    call_kwargs = mock_manager.enqueue_turn.await_args.kwargs
    assert call_kwargs["conv_id"] == "conv-fire"
    assert "Scheduled wake triggered: Verify deploy" in call_kwargs["prompt"]
    assert "BACKGROUND_WAKE_OK" in call_kwargs["prompt"]
    assert call_kwargs["metadata"]["schedule_id"] == wake.schedule_id
    assert call_kwargs["metadata"]["env"] == "prod"

    # Verify sidecar is cleared
    assert load_scheduled_wakes(config, "conv-fire") == []


@pytest.mark.asyncio
async def test_startup_scan_resumes_future_wakes(config, mock_manager):
    wake_mgr = ScheduledWakeManager(config, manager=mock_manager)

    # Manually create a pending schedule in sidecar
    conv_id = "conv-offline"
    now = datetime.now(timezone.utc)
    future_dt = now + timedelta(seconds=300)
    wake = ScheduledWake(
        schedule_id="wake_offline_1",
        conv_id=conv_id,
        delay_seconds=300,
        reason="Offline wake",
        scheduled_at=now.isoformat(),
        fire_at=future_dt.isoformat(),
        fire_at_ts=future_dt.timestamp(),
        metadata={},
    )
    save_scheduled_wakes(config, conv_id, [wake])

    resumed_count = await wake_mgr.startup_scan()
    assert resumed_count == 1
    assert "wake_offline_1" in wake_mgr._tasks

    # Clean up
    await wake_mgr.shutdown()


@pytest.mark.asyncio
async def test_startup_scan_fires_overdue_wakes_immediately(config, mock_manager):
    wake_mgr = ScheduledWakeManager(config, manager=mock_manager)

    conv_id = "conv-overdue"
    past_dt = datetime.now(timezone.utc) - timedelta(seconds=60)
    wake = ScheduledWake(
        schedule_id="wake_overdue_1",
        conv_id=conv_id,
        delay_seconds=60,
        reason="Past due wake",
        scheduled_at=past_dt.isoformat(),
        fire_at=past_dt.isoformat(),
        fire_at_ts=past_dt.timestamp(),
        metadata={},
    )
    save_scheduled_wakes(config, conv_id, [wake])

    resumed_count = await wake_mgr.startup_scan()
    assert resumed_count == 1

    # Overdue task fires immediately
    task = wake_mgr._tasks.get("wake_overdue_1")
    if task:
        await task

    mock_manager.enqueue_turn.assert_awaited_once()
    assert "Past due wake" in mock_manager.enqueue_turn.await_args.kwargs["prompt"]
    assert load_scheduled_wakes(config, conv_id) == []


@pytest.mark.asyncio
async def test_shutdown_cancels_tasks_without_dropping_sidecar(config, mock_manager):
    wake_mgr = ScheduledWakeManager(config, manager=mock_manager)
    wake = await wake_mgr.schedule(conv_id="conv-shutdown", delay_seconds=500, reason="Wait long")

    # Before shutdown, task is active and sidecar exists
    assert wake.schedule_id in wake_mgr._tasks
    assert len(load_scheduled_wakes(config, "conv-shutdown")) == 1

    await wake_mgr.shutdown()

    # After shutdown, in-memory tasks cleared but sidecar is preserved
    assert len(wake_mgr._tasks) == 0
    schedules = load_scheduled_wakes(config, "conv-shutdown")
    assert len(schedules) == 1
    assert schedules[0].schedule_id == wake.schedule_id


@pytest.mark.asyncio
async def test_deleted_conversation_timer_drops_without_recreating_sidecar(config, mock_manager):
    """When a conversation's files are deleted, timer expiry drops cleanly without recreating the dir (#1119 review)."""
    from decafclaw.conversation_paths import delete_conversation_files

    wake_mgr = ScheduledWakeManager(config, manager=mock_manager)
    wake = await wake_mgr.schedule(conv_id="conv-del-test", delay_seconds=1, reason="Short wait")

    # Simulate deleting the conversation files before timer fires
    delete_conversation_files(config, "conv-del-test")

    # Run the timer to completion
    task = wake_mgr._tasks.get(wake.schedule_id)
    assert task is not None
    await task

    # Enqueue was never called and sidecar file was not recreated
    mock_manager.enqueue_turn.assert_not_called()
    assert load_scheduled_wakes(config, "conv-del-test") == []


@pytest.mark.asyncio
async def test_persistence_failure_propagation(ctx, mock_manager, monkeypatch):
    """Persistence failures in save_scheduled_wakes are propagated to tool callers (#1119 review)."""
    ctx.manager = mock_manager
    ctx.conv_id = "conv-disk-full"

    def mock_save_fail(*a, **k):
        raise OSError("Disk full")

    monkeypatch.setattr("decafclaw.scheduled_wakes.save_scheduled_wakes", mock_save_fail)

    result = await tool_schedule_turn(ctx, delay_seconds=60, reason="Will fail save")
    assert "[error: Failed to persist schedule: Disk full]" in result.text
    assert result.data["error"] == "persistence_failure"
    assert len(mock_manager.scheduled_wakes._tasks) == 0


@pytest.mark.asyncio
async def test_wake_retained_in_sidecar_if_enqueue_raises(config, mock_manager):
    """If manager.enqueue_turn raises, the wake is retained in sidecar for recovery (#1119 review)."""
    mock_manager.enqueue_turn.side_effect = RuntimeError("Enqueue exploded")
    wake_mgr = ScheduledWakeManager(config, manager=mock_manager)

    wake = await wake_mgr.schedule(conv_id="conv-enqueue-fail", delay_seconds=1, reason="Test enqueue fail")
    task = wake_mgr._tasks.get(wake.schedule_id)
    assert task is not None

    # Wait for task - it will log the error and catch it in _run_timer
    await task

    # The schedule must still be in the sidecar!
    schedules = load_scheduled_wakes(config, "conv-enqueue-fail")
    assert len(schedules) == 1
    assert schedules[0].schedule_id == wake.schedule_id


@pytest.mark.asyncio
async def test_manager_shutdown_cancels_timers_when_idle(config):
    """Manager shutdown must cancel pending wake timers even when no agent tasks are active (#1119 review)."""
    from decafclaw.conversation_manager import ConversationManager

    manager = ConversationManager(config, None)
    wake = await manager.scheduled_wakes.schedule(conv_id="conv-idle-shut", delay_seconds=100, reason="Wait idle")

    assert wake.schedule_id in manager.scheduled_wakes._tasks
    # No agent tasks in manager
    assert len([s.agent_task for s in manager._conversations.values() if s.agent_task]) == 0

    await manager.shutdown()

    # The wake timer task was cancelled cleanly
    assert len(manager.scheduled_wakes._tasks) == 0


@pytest.mark.asyncio
async def test_rate_limited_wake_re_arms_timer_and_preserves_sidecar(config, mock_manager):
    """When a wake turn is rate-limited, the schedule is re-armed and retained in sidecar (#1119 review)."""
    rate_limited_fut = asyncio.get_running_loop().create_future()
    rate_limited_fut.set_result(None)
    rate_limited_fut.rate_limited = True
    mock_manager.enqueue_turn.return_value = rate_limited_fut

    wake_mgr = ScheduledWakeManager(config, manager=mock_manager)
    wake = await wake_mgr.schedule(conv_id="conv-rate-limit", delay_seconds=1, reason="Check build")

    # Let timer fire
    task = wake_mgr._tasks.get(wake.schedule_id)
    assert task is not None
    await task

    # The schedule must still be in the sidecar!
    schedules = load_scheduled_wakes(config, "conv-rate-limit")
    assert len(schedules) == 1
    assert schedules[0].schedule_id == wake.schedule_id

    # And a new timer task was re-armed
    assert wake.schedule_id in wake_mgr._tasks

    # Clean up
    await wake_mgr.shutdown()


@pytest.mark.asyncio
async def test_shutdown_rejects_new_schedules(config, mock_manager):
    """Scheduling a new timer after shutdown begins must be rejected cleanly (#1119 review)."""
    wake_mgr = ScheduledWakeManager(config, manager=mock_manager)
    await wake_mgr.shutdown()

    with pytest.raises(RuntimeError, match="shutting down"):
        await wake_mgr.schedule(conv_id="conv-reject", delay_seconds=10, reason="Too late")


@pytest.mark.asyncio
async def test_composed_context_setup_preserves_media_handler(config, mock_manager):
    """Composed context_setup preserves inherited handler and sets routing (#1119 review)."""
    wake_mgr = ScheduledWakeManager(config, manager=mock_manager)
    wake = await wake_mgr.schedule(
        conv_id="conv-media-preserve",
        delay_seconds=1,
        reason="Media check",
        channel_id="ch-orig-123",
        thread_id="th-orig-456",
        transport="mattermost",
    )

    # Attach a state with inherited last_context_setup to mock_manager
    fake_state = MagicMock()
    mock_manager.get_state.return_value = fake_state

    handler_sentinel = object()

    def fake_base_setup(ctx):
        ctx.media_handler = handler_sentinel

    fake_state.last_context_setup = fake_base_setup

    # Fire wake
    task = wake_mgr._tasks.get(wake.schedule_id)
    assert task is not None
    await task

    # Assert context_setup was called and preserved media_handler
    mock_manager.enqueue_turn.assert_awaited_once()
    context_setup = mock_manager.enqueue_turn.await_args.kwargs["context_setup"]
    assert context_setup is not None

    test_ctx = MagicMock()
    test_ctx.media_handler = None
    context_setup(test_ctx)
    assert test_ctx.media_handler is handler_sentinel
    assert test_ctx.channel_id == "ch-orig-123"
    assert test_ctx.thread_id == "th-orig-456"
    assert test_ctx.transport == "mattermost"


@pytest.mark.asyncio
async def test_metadata_cannot_override_scheduler_routing_fields(config, mock_manager):
    """User-supplied metadata must not overwrite scheduler-owned routing fields (#1119 review)."""
    wake_mgr = ScheduledWakeManager(config, manager=mock_manager)
    wake = await wake_mgr.schedule(
        conv_id="conv-meta-guard",
        delay_seconds=1,
        reason="Meta test",
        channel_id="ch-legit-1",
        thread_id="th-legit-2",
        transport="mattermost",
        metadata={"channel_id": "malicious-ch", "transport": "fake-transport"},
    )

    task = wake_mgr._tasks.get(wake.schedule_id)
    assert task is not None
    await task

    mock_manager.enqueue_turn.assert_awaited_once()
    meta = mock_manager.enqueue_turn.await_args.kwargs["metadata"]
    assert meta["channel_id"] == "ch-legit-1"
    assert meta["thread_id"] == "th-legit-2"
    assert meta["transport"] == "mattermost"


@pytest.mark.asyncio
async def test_cancel_queued_wake_removes_inbox_entry_and_schedule(config):
    """Cancelling a queued wake removes both the schedule record and the inbox entry (#1119 review)."""
    from decafclaw.conversation_manager import ConversationManager
    from decafclaw.inbox import _append_inbox, _read_inbox

    manager = ConversationManager(config, None)
    conv_id = "c-queued-cancel"

    # Schedule a wake and seed it as "queued"
    wake = await manager.scheduled_wakes.schedule(conv_id=conv_id, delay_seconds=100, reason="To queue")
    schedules = load_scheduled_wakes(config, conv_id)
    schedules[0].status = "queued"
    save_scheduled_wakes(config, conv_id, schedules)

    # Put a queued turn into the inbox
    _append_inbox(
        config,
        conv_id,
        {
            "turn_id": "tid-1",
            "kind": "wake",
            "text": "test",
            "metadata": {"schedule_id": wake.schedule_id},
        },
    )
    assert len(_read_inbox(config, conv_id)) == 1

    # Now cancel the schedule
    cancelled = await manager.scheduled_wakes.cancel(conv_id, wake.schedule_id)
    assert cancelled is True

    # Both schedule record and inbox entry are gone!
    assert load_scheduled_wakes(config, conv_id) == []
    assert len(_read_inbox(config, conv_id)) == 0
