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
    from decafclaw.config_types import AgentConfig
    new_config = Config(agent=AgentConfig(data_home="/tmp/other", id="other-agent"))
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


# -- fork_for_tool_call --------------------------------------------------------


def test_fork_for_tool_call_copies_all_fields(ctx):
    """fork_for_tool_call should copy all parent fields except the overrides.

    This test catches the "fragile field list" problem — if a new field is
    added to Context and not copied by fork_for_tool_call, this test will
    detect it by comparing all non-default fields.
    """
    # Set every field to a non-default value so we can detect missing copies
    ctx.user_id = "test-user"
    ctx.channel_id = "test-channel"
    ctx.channel_name = "test-channel-name"
    ctx.thread_id = "test-thread"
    ctx.conv_id = "test-conv"
    ctx.history = [{"role": "user", "content": "hi"}]
    ctx.messages = [{"role": "system", "content": "sys"}]
    ctx.cancelled = asyncio.Event()
    ctx.media_handler = "fake-handler"
    ctx.extra_tools = {"vault_read": lambda: None}
    ctx.extra_tool_definitions = [{"function": {"name": "vault_read"}}]
    ctx.activated_skills = {"markdown_vault"}
    ctx.skill_data = {"vault_base_path": "obsidian/main"}
    ctx.allowed_tools = {"shell", "memory_search"}
    ctx.event_context_id = "parent-event-ctx"

    forked = ctx.fork_for_tool_call("call_new")

    # Overridden fields
    assert forked.current_tool_call_id == "call_new"

    # Preserved identity
    assert forked.context_id == ctx.context_id
    assert forked.event_bus is ctx.event_bus
    assert forked.config is ctx.config

    # All other fields should match the parent
    fields_to_check = [
        "user_id", "channel_id", "channel_name", "thread_id", "conv_id",
        "history", "messages", "cancelled", "media_handler",
        "extra_tools", "extra_tool_definitions", "activated_skills",
        "skill_data", "allowed_tools", "event_context_id",
    ]
    for field in fields_to_check:
        parent_val = getattr(ctx, field)
        child_val = getattr(forked, field)
        assert child_val is parent_val or child_val == parent_val, (
            f"fork_for_tool_call didn't copy '{field}': "
            f"parent={parent_val!r}, child={child_val!r}"
        )


def test_fork_for_tool_call_fresh_token_counters(ctx):
    """Token counters should be fresh on forked ctx."""
    ctx.total_prompt_tokens = 100
    ctx.total_completion_tokens = 50
    forked = ctx.fork_for_tool_call("call_1")
    assert forked.total_prompt_tokens == 0
    assert forked.total_completion_tokens == 0
