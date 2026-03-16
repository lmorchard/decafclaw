"""Tests for Context creation and forking."""

import asyncio

import pytest

from decafclaw.config import Config
from decafclaw.context import Context
from decafclaw.events import EventBus


@pytest.fixture
def bus():
    return EventBus()


def test_context_creates_with_config_and_bus(config, bus):
    ctx = Context(config=config, event_bus=bus)
    assert ctx.config is config
    assert ctx.event_bus is bus


def test_context_gets_unique_id(config, bus):
    ctx1 = Context(config=config, event_bus=bus)
    ctx2 = Context(config=config, event_bus=bus)
    assert ctx1.context_id != ctx2.context_id
    assert len(ctx1.context_id) == 12  # uuid4().hex[:12]


def test_context_accepts_custom_id(config, bus):
    ctx = Context(config=config, event_bus=bus, context_id="custom-123")
    assert ctx.context_id == "custom-123"


def test_fork_creates_child_with_new_id(ctx):
    child = ctx.fork()
    assert child.context_id != ctx.context_id


def test_fork_shares_event_bus(ctx):
    child = ctx.fork()
    assert child.event_bus is ctx.event_bus


def test_fork_shares_config_by_default(ctx):
    child = ctx.fork()
    assert child.config is ctx.config


def test_fork_accepts_overrides(ctx):
    child = ctx.fork(user_id="new-user", channel_id="new-channel")
    assert child.user_id == "new-user"
    assert child.channel_id == "new-channel"


def test_fork_can_override_config(ctx):
    new_config = Config(data_home="/tmp/other", agent_id="other-agent")
    child = ctx.fork(config=new_config)
    assert child.config is new_config
    assert child.config is not ctx.config


@pytest.mark.asyncio
async def test_publish_includes_context_id(ctx):
    received = []

    async def callback(event):
        received.append(event)

    ctx.event_bus.subscribe(callback)
    await ctx.publish("test_event", foo="bar")

    assert len(received) == 1
    assert received[0]["type"] == "test_event"
    assert received[0]["context_id"] == ctx.context_id
    assert received[0]["foo"] == "bar"


@pytest.mark.asyncio
async def test_forked_context_publishes_independently(ctx):
    received = []

    async def callback(event):
        received.append(event)

    ctx.event_bus.subscribe(callback)

    child = ctx.fork()
    await ctx.publish("parent_event")
    await child.publish("child_event")

    assert len(received) == 2
    assert received[0]["context_id"] == ctx.context_id
    assert received[1]["context_id"] == child.context_id
    assert received[0]["context_id"] != received[1]["context_id"]
