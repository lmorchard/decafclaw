"""Tests for the shared confirmation request helper."""

import asyncio

import pytest

from decafclaw.tools.confirmation import request_confirmation


@pytest.mark.asyncio
async def test_approved_confirmation(ctx):
    async def approve_after_delay():
        await asyncio.sleep(0.05)
        await ctx.event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": ctx.context_id,
            "tool": "test_tool",
            "approved": True,
        })

    asyncio.create_task(approve_after_delay())
    result = await request_confirmation(
        ctx, tool_name="test_tool", command="do thing", message="Confirm?",
    )
    assert result["approved"] is True


@pytest.mark.asyncio
async def test_denied_confirmation(ctx):
    async def deny_after_delay():
        await asyncio.sleep(0.05)
        await ctx.event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": ctx.context_id,
            "tool": "test_tool",
            "approved": False,
        })

    asyncio.create_task(deny_after_delay())
    result = await request_confirmation(
        ctx, tool_name="test_tool", command="do thing", message="Confirm?",
    )
    assert result["approved"] is False


@pytest.mark.asyncio
async def test_timeout_returns_not_approved(ctx):
    result = await request_confirmation(
        ctx, tool_name="test_tool", command="do thing", message="Confirm?",
        timeout=0.1,
    )
    assert result["approved"] is False


@pytest.mark.asyncio
async def test_extra_fields_passed_through(ctx):
    async def approve_with_extras():
        await asyncio.sleep(0.05)
        await ctx.event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": ctx.context_id,
            "tool": "test_tool",
            "approved": True,
            "always": True,
            "add_pattern": True,
        })

    asyncio.create_task(approve_with_extras())
    result = await request_confirmation(
        ctx, tool_name="test_tool", command="do thing", message="Confirm?",
    )
    assert result["approved"] is True
    assert result["always"] is True
    assert result["add_pattern"] is True


@pytest.mark.asyncio
async def test_ignores_wrong_context_id(ctx):
    async def wrong_context():
        await asyncio.sleep(0.05)
        # Wrong context_id — should be ignored
        await ctx.event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": "wrong-id",
            "tool": "test_tool",
            "approved": True,
        })

    asyncio.create_task(wrong_context())
    result = await request_confirmation(
        ctx, tool_name="test_tool", command="do thing", message="Confirm?",
        timeout=0.2,
    )
    assert result["approved"] is False  # timed out


@pytest.mark.asyncio
async def test_ignores_wrong_tool_name(ctx):
    async def wrong_tool():
        await asyncio.sleep(0.05)
        await ctx.event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": ctx.context_id,
            "tool": "wrong_tool",
            "approved": True,
        })

    asyncio.create_task(wrong_tool())
    result = await request_confirmation(
        ctx, tool_name="test_tool", command="do thing", message="Confirm?",
        timeout=0.2,
    )
    assert result["approved"] is False  # timed out


@pytest.mark.asyncio
async def test_subscriber_cleaned_up_after_completion(ctx):
    before_count = len(ctx.event_bus._subscribers)

    async def approve():
        await asyncio.sleep(0.05)
        await ctx.event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": ctx.context_id,
            "tool": "test_tool",
            "approved": True,
        })

    asyncio.create_task(approve())
    await request_confirmation(
        ctx, tool_name="test_tool", command="do thing", message="Confirm?",
    )
    assert len(ctx.event_bus._subscribers) == before_count


@pytest.mark.asyncio
async def test_subscriber_cleaned_up_after_timeout(ctx):
    before_count = len(ctx.event_bus._subscribers)
    await request_confirmation(
        ctx, tool_name="test_tool", command="do thing", message="Confirm?",
        timeout=0.1,
    )
    assert len(ctx.event_bus._subscribers) == before_count
