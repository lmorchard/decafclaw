"""Unit tests for RestartCoordinator (#1533)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.events import EventBus
from decafclaw.restart import RestartCoordinator


@pytest.mark.asyncio
async def test_immediate_restart_signals_shutdown_instantly():
    shutdown_event = asyncio.Event()
    bus = EventBus()
    events = []
    bus.subscribe(lambda e: events.append(e))

    coord = RestartCoordinator(shutdown_event, event_bus=bus)
    result = await coord.request_restart(reason="urgent maintenance", on_idle=False)

    assert result is True
    assert shutdown_event.is_set()
    assert coord.is_restart_pending() is True
    assert any(e.get("type") == "restart_scheduled" and e.get("immediate") is True for e in events)


@pytest.mark.asyncio
async def test_on_idle_restart_when_already_idle():
    shutdown_event = asyncio.Event()
    manager = MagicMock()
    manager._conversations = {}

    coord = RestartCoordinator(shutdown_event, manager=manager)
    result = await coord.request_restart(reason="idle test", on_idle=True, poll_interval=0.01)

    assert result is True
    # Allow background drain task to check idle
    await asyncio.sleep(0.05)
    assert shutdown_event.is_set()


@pytest.mark.asyncio
async def test_on_idle_restart_drains_and_restarts():
    shutdown_event = asyncio.Event()

    state = MagicMock()
    state.busy = True
    state.agent_task = None

    manager = MagicMock()
    manager._conversations = {"c1234567890": state}

    coord = RestartCoordinator(shutdown_event, manager=manager)
    await coord.request_restart(reason="config reload", on_idle=True, poll_interval=0.02)

    # Active turn in progress: shutdown_event should NOT be set yet
    await asyncio.sleep(0.05)
    assert not shutdown_event.is_set()
    assert coord.is_restart_pending() is True

    # Turn completes
    state.busy = False
    await asyncio.sleep(0.08)
    assert shutdown_event.is_set()


@pytest.mark.asyncio
async def test_on_idle_restart_timeout_forces_restart():
    shutdown_event = asyncio.Event()

    state = MagicMock()
    state.busy = True
    state.agent_task = None

    manager = MagicMock()
    manager._conversations = {"c1234567890": state}

    coord = RestartCoordinator(shutdown_event, manager=manager)
    # Set tiny timeout of 0.05s
    await coord.request_restart(
        reason="timeout test",
        on_idle=True,
        max_wait_seconds=0.05,
        poll_interval=0.01,
    )

    await asyncio.sleep(0.1)
    assert shutdown_event.is_set()


@pytest.mark.asyncio
async def test_cancel_restart():
    shutdown_event = asyncio.Event()
    bus = EventBus()
    events = []
    bus.subscribe(lambda e: events.append(e))

    state = MagicMock()
    state.busy = True
    manager = MagicMock()
    manager._conversations = {"c1": state}

    coord = RestartCoordinator(shutdown_event, manager=manager, event_bus=bus)
    await coord.request_restart(reason="test", on_idle=True, poll_interval=0.01)
    assert coord.is_restart_pending() is True

    cancelled = await coord.cancel_restart()
    assert cancelled is True
    assert coord.is_restart_pending() is False
    assert any(e.get("type") == "restart_cancelled" for e in events)

    # Wait to make sure shutdown_event was NOT set
    state.busy = False
    await asyncio.sleep(0.05)
    assert not shutdown_event.is_set()
