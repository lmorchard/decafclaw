"""Tests for decafclaw.polling."""

import asyncio

import pytest

from decafclaw.polling import build_task_preamble, run_polling_loop


def test_build_task_preamble_heartbeat_keeps_status_token():
    """Heartbeat preamble keeps HEARTBEAT_OK — `is_heartbeat_ok()` in
    `heartbeat.py` consumes it to gate alert-vs-all-clear notifications.
    See #362 for why only the scheduled-task preamble drops it."""
    result = build_task_preamble("heartbeat check")
    assert "heartbeat check" in result
    assert "HEARTBEAT_OK" in result


def test_build_task_preamble_scheduled_requires_narrative_with_marker():
    """Scheduled-task preamble requires a narrative summary AND preserves
    HEARTBEAT_OK as a leading marker for quiet cycles (#362).

    Scheduled tasks also go through `is_heartbeat_ok()` (via
    `schedules.py::run_schedule_task`) for log-line tidiness. The marker
    must still reach the first 300 chars of the response for detection,
    so the preamble instructs the model to put it FIRST when the cycle
    was quiet. Narrative is required either way — this fixes the #362
    symptom of scheduled archives ending with bare tokens."""
    result = build_task_preamble("scheduled task")
    assert "scheduled task" in result
    assert "narrative summary" in result
    # Preserved as quiet-cycle marker — leading position keeps
    # is_heartbeat_ok() detection reliable.
    assert "HEARTBEAT_OK" in result


def test_build_task_preamble_with_task_name():
    result = build_task_preamble("scheduled task", "my-task")
    assert "my-task" in result


@pytest.mark.asyncio
async def test_polling_loop_calls_tick():
    shutdown = asyncio.Event()
    calls = []

    async def tick():
        calls.append(1)
        shutdown.set()

    await run_polling_loop(0.01, shutdown, tick, label="test")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_polling_loop_survives_tick_error():
    shutdown = asyncio.Event()
    calls = []

    async def bad_tick():
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("boom")
        shutdown.set()

    await run_polling_loop(0.01, shutdown, bad_tick, label="test")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_polling_loop_exits_immediately_if_shutdown_set():
    shutdown = asyncio.Event()
    shutdown.set()
    calls = []

    async def tick():
        calls.append(1)

    await run_polling_loop(0.01, shutdown, tick, label="test")
    assert len(calls) == 0
