import pytest

from decafclaw.agent import TurnRunner
from decafclaw.context import Context, TurnLifecycle


@pytest.mark.asyncio
async def test_before_llm_call_hook_mutates_messages(ctx, config, monkeypatch):
    """Test that a BEFORE_LLM_CALL hook can successfully mutate the messages list in-place."""
    called_messages = []

    async def mock_call_llm(ctx, config, messages, tools, **kwargs):
        called_messages.extend(messages)
        return {"content": "OK", "usage": {}}

    monkeypatch.setattr("decafclaw.agent._call_llm_with_events", mock_call_llm)

    def mutate_messages_hook(ctx, messages, tools):
        messages.append({"role": "system", "content": "mutated_by_hook"})

    ctx.add_interceptor(TurnLifecycle.BEFORE_LLM_CALL, mutate_messages_hook)

    runner = TurnRunner(
        ctx=ctx,
        config=config,
        history=[],
        user_message="Hello",
        archive_text="",
        attachments=None,
    )

    await runner.run()

    # Verify the mutation happened.
    assert any(
        msg.get("role") == "system" and msg.get("content") == "mutated_by_hook"
        for msg in called_messages
    )


@pytest.mark.asyncio
async def test_interceptors_execute_in_order(ctx, config, monkeypatch):
    """Test that multiple hooks execute in the order they were registered."""
    async def mock_call_llm(ctx, config, messages, tools, **kwargs):
        return {"content": "OK", "usage": {}}

    monkeypatch.setattr("decafclaw.agent._call_llm_with_events", mock_call_llm)

    execution_order = []

    def hook1(ctx, messages, tools):
        execution_order.append("hook1")

    def hook2(ctx, messages, tools):
        execution_order.append("hook2")

    ctx.add_interceptor(TurnLifecycle.BEFORE_LLM_CALL, hook1)
    ctx.add_interceptor(TurnLifecycle.BEFORE_LLM_CALL, hook2)

    runner = TurnRunner(
        ctx=ctx,
        config=config,
        history=[],
        user_message="Hello",
        archive_text="",
        attachments=None,
    )

    await runner.run()

    assert execution_order == ["hook1", "hook2"]
