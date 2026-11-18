import asyncio

import pytest

from decafclaw.agent import TurnRunner
from decafclaw.context import Context, TurnLifecycle
from decafclaw.context_composer import ContextComposer
from decafclaw.events import EventBus
from decafclaw.extensions import BaseExtension, ExtensionManager


@pytest.mark.asyncio
async def test_pre_compose_hook_injection(ctx, config, monkeypatch):
    """WHEN an async collector hook is registered for TurnLifecycle.PRE_COMPOSE,
    ContextComposer SHALL await registered hooks concurrently with a configurable timeout
    and inject returned text into a bounded tail envelope without mutating static prompt cache prefixes.
    """
    config.system_prompt = "<soul>Static prefix</soul>"
    system_prompt_before = str(config.system_prompt)

    async def mock_call_llm(ctx, config, messages, tools, **kwargs):
        return {"content": "Understood.", "usage": {}}

    monkeypatch.setattr("decafclaw.agent._call_llm_with_events", mock_call_llm)

    async def sample_pre_compose_hook(context, user_msg):
        return "User prefers dark mode and espresso."

    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, sample_pre_compose_hook)

    runner = TurnRunner(
        ctx=ctx,
        config=config,
        history=[],
        user_message="What should I have?",
        archive_text="",
        attachments=None,
    )

    await runner.run()

    # Assert static system prompt was NEVER mutated
    assert config.system_prompt == system_prompt_before

    # Assert that <recalled_facts> was injected in history right before user message
    recalled_msgs = [m for m in ctx.history if "<recalled_facts>" in str(m.get("content", ""))]
    assert len(recalled_msgs) == 1
    assert "User prefers dark mode and espresso." in recalled_msgs[0]["content"]

    # Verify that the message sent to the LLM had the facts mapped
    llm_recalled = [m for m in runner.messages if "<recalled_facts>" in str(m.get("content", ""))]
    assert len(llm_recalled) == 1
    assert llm_recalled[0]["role"] == "user"


@pytest.mark.asyncio
async def test_recalled_facts_attach_to_archived_assistant_row(ctx, config, monkeypatch):
    """The delivered assistant row carries the turn's atomic recalled facts (#1514).

    ``conv_history`` reloads this row, so the disclosure must survive a
    reload without re-deriving it from the hidden prompt envelope.
    """

    async def mock_call_llm(ctx, config, messages, tools, **kwargs):
        return {"content": "Here is your answer.", "usage": {}}

    monkeypatch.setattr("decafclaw.agent._call_llm_with_events", mock_call_llm)

    async def sample_pre_compose_hook(context, user_msg):
        return ["User prefers espresso.", "User is in Pacific time."]

    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, sample_pre_compose_hook)

    runner = TurnRunner(
        ctx=ctx,
        config=config,
        history=[],
        user_message="What should I drink?",
        archive_text="",
        attachments=None,
    )
    await runner.run()

    from decafclaw.archive import read_archive

    archived = read_archive(config, ctx.conv_id)
    assistant_rows = [m for m in archived if m.get("role") == "assistant"]
    assert assistant_rows
    assert assistant_rows[-1]["recalled_facts"] == [
        "User prefers espresso.",
        "User is in Pacific time.",
    ]


@pytest.mark.asyncio
async def test_recalled_facts_absent_from_archive_without_a_hit(ctx, config, monkeypatch):
    """A turn with no memory hit archives no recalled_facts key (#1514)."""

    async def mock_call_llm(ctx, config, messages, tools, **kwargs):
        return {"content": "No memory needed.", "usage": {}}

    monkeypatch.setattr("decafclaw.agent._call_llm_with_events", mock_call_llm)

    async def empty_hook(context, user_msg):
        return None

    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, empty_hook)

    runner = TurnRunner(
        ctx=ctx,
        config=config,
        history=[],
        user_message="Hello",
        archive_text="",
        attachments=None,
    )
    await runner.run()

    from decafclaw.archive import read_archive

    archived = read_archive(config, ctx.conv_id)
    assistant_rows = [m for m in archived if m.get("role") == "assistant"]
    assert assistant_rows
    assert "recalled_facts" not in assistant_rows[-1]


@pytest.mark.asyncio
async def test_pre_compose_hook_timeout_and_fail_open(ctx, config):
    """WHEN a PRE_COMPOSE hook raises an exception or exceeds its execution timeout,
    ContextComposer SHALL log a warning and complete turn composition without failing the turn.
    """
    config.lifecycle.pre_compose_timeout = 0.05  # 50ms fast timeout for test

    async def failing_hook(context, user_msg):
        raise RuntimeError("Database connection exploded")

    async def timing_out_hook(context, user_msg):
        await asyncio.sleep(0.5)  # 500ms > 50ms
        return "Slow fact that should be dropped"

    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, failing_hook)
    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, timing_out_hook)

    composer = ContextComposer(state=ctx.composer)
    # Compose should complete without raising any exception (fail open)
    composed = await composer.compose(
        ctx=ctx,
        user_message="Hello there",
        history=[],
    )

    assert composed is not None
    # No recalled_facts envelope should be present
    recalled_msgs = [m for m in composed.messages if "<recalled_facts>" in str(m.get("content", ""))]
    assert len(recalled_msgs) == 0


@pytest.mark.asyncio
async def test_turn_settle_dispatches_background_task(ctx, config, monkeypatch):
    """WHEN an agent turn completes and settles in TurnRunner.run(),
    TurnRunner SHALL dispatch an ON_TURN_SETTLE event with the turn's transcript slice
    to all registered async listeners as unawaited background tasks.
    """
    settled_event = asyncio.Event()
    received_slice = []

    async def mock_call_llm(ctx, config, messages, tools, **kwargs):
        return {"content": "Turn response content", "usage": {}}

    monkeypatch.setattr("decafclaw.agent._call_llm_with_events", mock_call_llm)

    async def settle_listener(context, transcript):
        received_slice.extend(transcript)
        settled_event.set()

    ctx.add_interceptor(TurnLifecycle.ON_TURN_SETTLE, settle_listener)

    runner = TurnRunner(
        ctx=ctx,
        config=config,
        history=[],
        user_message="Test message",
        archive_text="",
        attachments=None,
    )

    await runner.run()

    # Wait for the unawaited background task to complete
    await asyncio.wait_for(settled_event.wait(), timeout=1.0)

    assert len(received_slice) > 0
    # The transcript slice must include the user message and assistant message
    roles = [m.get("role") for m in received_slice]
    assert "user" in roles
    assert "assistant" in roles


@pytest.mark.asyncio
async def test_dynamic_extension_disable(ctx, config, monkeypatch):
    """WHEN an extension is dynamically disabled via its lifecycle handle,
    all registered hooks SHALL be unsubscribed from EventBus,
    and subsequent turns SHALL run without invoking the detached hooks.
    """

    async def mock_call_llm(ctx, config, messages, tools, **kwargs):
        return {"content": "Response", "usage": {}}

    monkeypatch.setattr("decafclaw.agent._call_llm_with_events", mock_call_llm)
    ctx.event_bus = EventBus()

    call_count = 0

    class CountingExtension(BaseExtension):
        def __init__(self):
            super().__init__(name="counting")

        def enable(self, bus: EventBus):
            async def hook(context, user_msg):
                nonlocal call_count
                call_count += 1
                return f"Count is {call_count}"

            sub_id = bus.subscribe(TurnLifecycle.PRE_COMPOSE, hook)
            self.subscription_ids = [sub_id]
            return self.subscription_ids

    ext = CountingExtension()
    manager = ExtensionManager(ctx.event_bus, config)
    manager.register("counting", ext)

    # Turn 1 with extension enabled
    manager.enable("counting")
    assert manager.is_enabled("counting")

    runner1 = TurnRunner(
        ctx=ctx,
        config=config,
        history=[],
        user_message="Turn 1",
        archive_text="",
        attachments=None,
    )
    await runner1.run()
    assert call_count == 1

    # Now dynamically disable the extension
    manager.disable("counting")
    assert not manager.is_enabled("counting")
    assert len(ctx.event_bus.get_lifecycle_hooks(TurnLifecycle.PRE_COMPOSE)) == 0

    # Turn 2: Extension hook should NOT be invoked
    runner2 = TurnRunner(
        ctx=ctx,
        config=config,
        history=ctx.history,
        user_message="Turn 2",
        archive_text="",
        attachments=None,
    )
    await runner2.run()
    assert call_count == 1  # Unchanged!
