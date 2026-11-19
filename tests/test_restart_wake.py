"""Tests for the single-shot post-restart conversation wake (#1554)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from decafclaw.conversation_manager import ConversationManager, TurnKind
from decafclaw.conversation_paths import restart_wake_path
from decafclaw.events import EventBus
from decafclaw.restart import RestartCoordinator
from decafclaw.restart_wake import (
    RestartWake,
    build_restart_wake_prompt,
    consume_restart_wakes,
    new_restart_wake,
    save_restart_wake,
)


def test_save_and_consume_restart_wake_is_single_shot(config):
    wake = new_restart_wake(
        conv_id="conv-1",
        reason="Apply shell config",
        user_id="user-1",
        channel_id="channel-1",
        thread_id="thread-1",
        transport="mattermost",
    )
    path = save_restart_wake(config, wake)
    assert path.exists()

    records = consume_restart_wakes(config)
    assert records == [wake]
    assert not path.exists()
    assert consume_restart_wakes(config) == []


def test_consume_discards_corrupt_record_and_removes_file(config):
    path = restart_wake_path(config, "conv-bad")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert consume_restart_wakes(config) == []
    assert not path.exists()


def test_consume_skips_record_missing_conv_id(config):
    path = restart_wake_path(config, "conv-missing")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"reason": "no conv"}), encoding="utf-8")

    assert consume_restart_wakes(config) == []
    assert not path.exists()


def test_build_restart_wake_prompt_includes_reason_and_elapsed():
    now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    wake = RestartWake(
        conv_id="conv-1",
        reason="Apply MCP config",
        requested_at=(now - timedelta(seconds=12.5)).isoformat(),
    )

    prompt = build_restart_wake_prompt(wake, now=now)

    assert "Apply MCP config" in prompt
    assert "12.5s" in prompt
    assert "back online" in prompt


@pytest.mark.asyncio
async def test_request_restart_persists_wake_and_cancel_removes_it(config):
    shutdown_event = asyncio.Event()
    state = MagicMock()
    state.busy = True
    state.agent_task = None
    manager = MagicMock()
    manager._conversations = {"conv-1": state}

    coord = RestartCoordinator(shutdown_event, manager=manager, config=config)
    wake = new_restart_wake(conv_id="conv-1", reason="test restart")

    await coord.request_restart(reason="test restart", on_idle=True, poll_interval=0.01, wake=wake)

    path = restart_wake_path(config, "conv-1")
    assert path.exists()
    assert not shutdown_event.is_set()

    assert await coord.cancel_restart() is True
    assert not path.exists()


@pytest.mark.asyncio
async def test_startup_scan_restart_wakes_enqueues_wake(config):
    manager = ConversationManager(config, EventBus())
    save_restart_wake(
        config,
        new_restart_wake(
            conv_id="conv-1",
            reason="Apply config",
            user_id="user-1",
            channel_id="channel-1",
            thread_id="thread-1",
            transport="mattermost",
        ),
    )

    with patch.object(manager, "enqueue_turn", new_callable=AsyncMock) as mock_eq:
        resumed = await manager.startup_scan_restart_wakes()

    assert resumed == 1
    _, kwargs = mock_eq.call_args
    assert kwargs["conv_id"] == "conv-1"
    assert kwargs["kind"] is TurnKind.WAKE
    assert "Apply config" in kwargs["prompt"]
    assert kwargs["metadata"] == {"restart_wake": True}
    assert kwargs["user_id"] == "user-1"

    ctx = MagicMock()
    ctx.media_handler = None
    kwargs["context_setup"](ctx)
    assert ctx.channel_id == "channel-1"
    assert ctx.thread_id == "thread-1"
    assert ctx.transport == "mattermost"

    assert consume_restart_wakes(config) == []

    with patch.object(manager, "enqueue_turn", new_callable=AsyncMock) as mock_eq2:
        assert await manager.startup_scan_restart_wakes() == 0
    mock_eq2.assert_not_awaited()


@pytest.mark.asyncio
async def test_startup_scan_restart_wake_discards_record_when_enqueue_fails(config):
    manager = ConversationManager(config, EventBus())
    save_restart_wake(config, new_restart_wake(conv_id="conv-1", reason="apply"))

    with patch.object(manager, "enqueue_turn", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        resumed = await manager.startup_scan_restart_wakes()

    assert resumed == 0
    # Consumed before enqueue: a failing wake must not replay on the next restart.
    assert not restart_wake_path(config, "conv-1").exists()
