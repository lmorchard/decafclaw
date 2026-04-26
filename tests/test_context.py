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


def test_fork_for_tool_call_propagates_all_fields(ctx):
    """Every flat field on the parent must reach the child fork.

    Iterates ``vars(ctx)`` rather than a hand-maintained allowlist, so a new
    field added to ``Context.__init__`` is automatically covered. Sub-objects
    that are intentionally fresh or replaced on the child are listed in
    ``INTENTIONALLY_DIFFERENT`` and verified separately below.
    """
    # Set every public flat field to a non-default value so a missed copy
    # would surface as a value mismatch rather than a coincidence of
    # equal defaults.
    ctx.user_id = "test-user"
    ctx.channel_id = "test-channel"
    ctx.channel_name = "test-channel-name"
    ctx.thread_id = "test-thread"
    ctx.conv_id = "test-conv"
    ctx.history = [{"role": "user", "content": "hi"}]
    ctx.messages = [{"role": "system", "content": "sys"}]
    ctx.cancelled = asyncio.Event()
    ctx.media_handler = "fake-handler"
    ctx.on_stream_chunk = lambda chunk: None
    ctx.event_context_id = "parent-event-ctx"
    ctx._current_iteration = 7
    ctx.is_child = True
    ctx.skip_reflection = True
    ctx.skip_vault_retrieval = True
    ctx.skip_archive = True
    ctx.wiki_page = "[[Test]]"
    ctx.active_model = "test-model"
    ctx.task_mode = "scheduled"
    ctx.request_confirmation = lambda req: None
    ctx.manager = object()  # stand-in for ConversationManager instance
    ctx.tools.extra = {"vault_read": lambda: None}
    ctx.tools.extra_definitions = [{"function": {"name": "vault_read"}}]
    ctx.tools.allowed = {"shell", "memory_search"}
    ctx.skills.activated = {"tabstack"}
    ctx.skills.data = {"vault_base_path": "obsidian/main"}

    forked = ctx.fork_for_tool_call("call_new")

    # Overridden field
    assert forked.tools.current_call_id == "call_new"

    # Preserved identity
    assert forked.context_id == ctx.context_id
    assert forked.event_bus is ctx.event_bus
    assert forked.config is ctx.config

    # Sub-objects with intentionally different state on the child;
    # checked separately below.
    INTENTIONALLY_DIFFERENT = {"tokens", "tools"}

    parent_attrs = vars(ctx)
    child_attrs = vars(forked)
    assert parent_attrs.keys() == child_attrs.keys(), (
        "Parent and child have different attribute sets — copy.copy missed something"
    )

    for name, parent_val in parent_attrs.items():
        if name in INTENTIONALLY_DIFFERENT:
            continue
        child_val = child_attrs[name]
        assert child_val is parent_val or child_val == parent_val, (
            f"fork_for_tool_call did not propagate '{name}': "
            f"parent={parent_val!r}, child={child_val!r}"
        )

    # Sub-objects: explicit checks
    assert forked.tokens is not ctx.tokens, "tokens must be a fresh instance"
    assert forked.tokens.total_prompt == 0
    assert forked.tools is not ctx.tools, "tools must be a fresh ToolState"
    # Inner containers shared via dataclasses.replace
    assert forked.tools.extra is ctx.tools.extra
    assert forked.tools.extra_definitions is ctx.tools.extra_definitions
    assert forked.tools.allowed is ctx.tools.allowed
    assert forked.skills is ctx.skills
    assert forked.composer is ctx.composer


def test_fork_for_tool_call_propagates_task_mode(ctx):
    """Regression: task_mode must reach tool calls so newsletter_publish (and
    any other tool reading ``ctx.task_mode``) takes the scheduled-delivery
    path during scheduled runs instead of the interactive short-circuit."""
    ctx.task_mode = "scheduled"
    forked = ctx.fork_for_tool_call("call_x")
    assert forked.task_mode == "scheduled"


def test_fork_for_tool_call_fresh_token_counters(ctx):
    """Token counters should be fresh on forked ctx."""
    ctx.tokens.total_prompt = 100
    ctx.tokens.total_completion = 50
    forked = ctx.fork_for_tool_call("call_1")
    assert forked.tokens.total_prompt == 0
    assert forked.tokens.total_completion == 0
