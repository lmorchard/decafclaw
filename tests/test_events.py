"""Tests for EventBus pub/sub."""

import asyncio

import pytest

from decafclaw.events import EventBus


@pytest.fixture
def bus():
    return EventBus()


@pytest.mark.asyncio
async def test_subscribe_receives_events(bus):
    received = []

    async def callback(event):
        received.append(event)

    bus.subscribe(callback)
    await bus.publish({"type": "test", "data": 42})

    assert len(received) == 1
    assert received[0]["type"] == "test"
    assert received[0]["data"] == 42


@pytest.mark.asyncio
async def test_unsubscribe_stops_receiving(bus):
    received = []

    async def callback(event):
        received.append(event)

    sub_id = bus.subscribe(callback)
    await bus.publish({"type": "first"})
    bus.unsubscribe(sub_id)
    await bus.publish({"type": "second"})

    assert len(received) == 1
    assert received[0]["type"] == "first"


@pytest.mark.asyncio
async def test_multiple_subscribers(bus):
    received_a = []
    received_b = []

    async def callback_a(event):
        received_a.append(event)

    async def callback_b(event):
        received_b.append(event)

    bus.subscribe(callback_a)
    bus.subscribe(callback_b)
    await bus.publish({"type": "test"})

    assert len(received_a) == 1
    assert len(received_b) == 1


@pytest.mark.asyncio
async def test_sync_subscriber(bus):
    received = []

    def callback(event):
        received.append(event)

    bus.subscribe(callback)
    await bus.publish({"type": "sync_test"})

    assert len(received) == 1
    assert received[0]["type"] == "sync_test"


@pytest.mark.asyncio
async def test_async_subscriber(bus):
    received = []

    async def callback(event):
        await asyncio.sleep(0)  # actually async
        received.append(event)

    bus.subscribe(callback)
    await bus.publish({"type": "async_test"})

    assert len(received) == 1


@pytest.mark.asyncio
async def test_error_in_subscriber_does_not_affect_others(bus):
    received = []

    async def bad_callback(event):
        raise ValueError("boom")

    async def good_callback(event):
        received.append(event)

    bus.subscribe(bad_callback)
    bus.subscribe(good_callback)
    await bus.publish({"type": "test"})

    assert len(received) == 1


@pytest.mark.asyncio
async def test_publish_with_no_subscribers(bus):
    # Should not raise
    await bus.publish({"type": "orphan"})


@pytest.mark.asyncio
async def test_unsubscribe_nonexistent_id(bus):
    # Should not raise
    bus.unsubscribe("nonexistent-id")
