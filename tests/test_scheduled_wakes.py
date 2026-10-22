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

    await tool_schedule_turn(ctx, delay_seconds=60, reason="First wake")
    await tool_schedule_turn(ctx, delay_seconds=120, reason="Second wake")

    list_res = tool_schedule_list(ctx)
    assert "Found 2 pending scheduled wake turn(s):" in list_res.text
    assert len(list_res.data["schedules"]) == 2
    assert list_res.data["schedules"][0]["reason"] == "First wake"
    assert list_res.data["schedules"][1]["reason"] == "Second wake"


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
