"""Tests for decafclaw.polling."""

import asyncio

import pytest

from decafclaw.polling import build_task_preamble, run_polling_loop


def test_build_task_preamble_heartbeat():
    result = build_task_preamble("heartbeat check")
    assert "heartbeat check" in result
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
