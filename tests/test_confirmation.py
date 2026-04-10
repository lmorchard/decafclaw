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


# -- tool_call_id matching tests -----------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_id_both_match(ctx):
    """When both request and response have tool_call_id, matching succeeds."""
    ctx.tools.current_call_id = "call_abc"

    async def approve():
        await asyncio.sleep(0.05)
        await ctx.event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": ctx.context_id,
            "tool": "test_tool",
            "tool_call_id": "call_abc",
            "approved": True,
        })

    asyncio.create_task(approve())
    result = await request_confirmation(
        ctx, tool_name="test_tool", command="do thing", message="Confirm?",
    )
    assert result["approved"] is True


@pytest.mark.asyncio
async def test_tool_call_id_both_mismatch(ctx):
    """When both have tool_call_id but they differ, response is ignored."""
    ctx.tools.current_call_id = "call_abc"

    async def approve_wrong_id():
        await asyncio.sleep(0.05)
        await ctx.event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": ctx.context_id,
            "tool": "test_tool",
            "tool_call_id": "call_xyz",
            "approved": True,
        })

    asyncio.create_task(approve_wrong_id())
    result = await request_confirmation(
        ctx, tool_name="test_tool", command="do thing", message="Confirm?",
        timeout=0.2,
    )
    assert result["approved"] is False  # timed out — wrong ID ignored


@pytest.mark.asyncio
async def test_tool_call_id_response_omits(ctx):
    """When request has tool_call_id but response omits it, still accepted (backward compat)."""
    ctx.tools.current_call_id = "call_abc"

    async def approve_no_id():
        await asyncio.sleep(0.05)
        await ctx.event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": ctx.context_id,
            "tool": "test_tool",
            "approved": True,
            # no tool_call_id
        })

    asyncio.create_task(approve_no_id())
    result = await request_confirmation(
        ctx, tool_name="test_tool", command="do thing", message="Confirm?",
    )
    assert result["approved"] is True


@pytest.mark.asyncio
async def test_tool_call_id_request_omits(ctx):
    """When request has no tool_call_id, any response matches (original behavior)."""
    ctx.tools.current_call_id = ""

    async def approve_with_id():
        await asyncio.sleep(0.05)
        await ctx.event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": ctx.context_id,
            "tool": "test_tool",
            "tool_call_id": "call_xyz",
            "approved": True,
        })

    asyncio.create_task(approve_with_id())
    result = await request_confirmation(
        ctx, tool_name="test_tool", command="do thing", message="Confirm?",
    )
    assert result["approved"] is True


@pytest.mark.asyncio
async def test_event_context_id_used_for_matching(ctx):
    """Child agents with event_context_id match on that, not context_id."""
    ctx.event_context_id = "parent-ctx-id"
    # ctx.context_id is the child's own ID, different from event_context_id

    async def approve_with_parent_id():
        await asyncio.sleep(0.05)
        await ctx.event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": "parent-ctx-id",  # matches event_context_id
            "tool": "test_tool",
            "approved": True,
        })

    asyncio.create_task(approve_with_parent_id())
    result = await request_confirmation(
        ctx, tool_name="test_tool", command="do thing", message="Confirm?",
    )
    assert result["approved"] is True


@pytest.mark.asyncio
async def test_event_context_id_rejects_child_id(ctx):
    """When event_context_id is set, responses with the child's own context_id don't match."""
    ctx.event_context_id = "parent-ctx-id"

    async def approve_with_child_id():
        await asyncio.sleep(0.05)
        await ctx.event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": ctx.context_id,  # child's own ID, not event_context_id
            "tool": "test_tool",
            "approved": True,
        })

    asyncio.create_task(approve_with_child_id())
    result = await request_confirmation(
        ctx, tool_name="test_tool", command="do thing", message="Confirm?",
        timeout=0.2,
    )
    assert result["approved"] is False  # timed out


@pytest.mark.asyncio
async def test_concurrent_confirmations_independent(ctx):
    """Two concurrent confirmations for the same tool match independently by tool_call_id."""
    results = {}

    async def run_confirm(tool_call_id):
        fork = ctx.fork_for_tool_call(tool_call_id)
        r = await request_confirmation(
            fork, tool_name="shell", command=f"cmd-{tool_call_id}",
            message="Confirm?", timeout=1.0,
        )
        results[tool_call_id] = r["approved"]

    async def approve_second_only():
        await asyncio.sleep(0.05)
        # Only approve call_2
        await ctx.event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": ctx.context_id,
            "tool": "shell",
            "tool_call_id": "call_2",
            "approved": True,
        })

    asyncio.create_task(approve_second_only())
    # Run two confirmations concurrently
    await asyncio.gather(
        run_confirm("call_1"),
        run_confirm("call_2"),
    )

    assert results["call_2"] is True  # approved
    assert results["call_1"] is False  # timed out — only call_2 was approved


# -- pending_confirmation lifecycle tests --------------------------------------


@pytest.mark.asyncio
async def test_pending_confirmation_set_during_wait(ctx):
    """ctx.pending_confirmation is set while waiting for response."""
    ctx.conv_id = "test-conv"

    async def check_and_approve():
        await asyncio.sleep(0.05)
        # Should be set while waiting
        assert ctx.pending_confirmation is not None
        assert ctx.pending_confirmation.tool_name == "test_tool"
        assert ctx.pending_confirmation.conv_id == "test-conv"
        assert ctx.pending_confirmation.context_id == ctx.context_id
        await ctx.event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": ctx.context_id,
            "tool": "test_tool",
            "approved": True,
        })

    asyncio.create_task(check_and_approve())
    result = await request_confirmation(
        ctx, tool_name="test_tool", command="do thing", message="Confirm?",
    )
    assert result["approved"] is True
    # Should be cleared after completion
    assert ctx.pending_confirmation is None


@pytest.mark.asyncio
async def test_pending_confirmation_cleared_on_timeout(ctx):
    """ctx.pending_confirmation is cleared after timeout."""
    ctx.conv_id = "test-conv"
    await request_confirmation(
        ctx, tool_name="test_tool", command="do thing", message="Confirm?",
        timeout=0.1,
    )
    assert ctx.pending_confirmation is None


@pytest.mark.asyncio
async def test_pending_confirmation_cleared_on_denial(ctx):
    """ctx.pending_confirmation is cleared after denial."""
    ctx.conv_id = "test-conv"

    async def deny():
        await asyncio.sleep(0.05)
        await ctx.event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": ctx.context_id,
            "tool": "test_tool",
            "approved": False,
        })

    asyncio.create_task(deny())
    result = await request_confirmation(
        ctx, tool_name="test_tool", command="do thing", message="Confirm?",
    )
    assert result["approved"] is False
    assert ctx.pending_confirmation is None


@pytest.mark.asyncio
async def test_pending_confirmation_stores_labels(ctx):
    """approve_label and deny_label from extra_event_fields are stored on PendingConfirmation."""
    ctx.conv_id = "test-conv"

    async def approve():
        await asyncio.sleep(0.05)
        # Check labels while pending
        assert ctx.pending_confirmation.approve_label == "Ship it"
        assert ctx.pending_confirmation.deny_label == "Needs work"
        await ctx.event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": ctx.context_id,
            "tool": "test_tool",
            "approved": True,
        })

    asyncio.create_task(approve())
    await request_confirmation(
        ctx, tool_name="test_tool", command="review", message="Review?",
        approve_label="Ship it", deny_label="Needs work",
    )


@pytest.mark.asyncio
async def test_published_event_includes_conv_id(ctx):
    """The tool_confirm_request event includes conv_id."""
    ctx.conv_id = "test-conv-123"
    captured = []

    def capture(event):
        if event.get("type") == "tool_confirm_request":
            captured.append(event)

    sub_id = ctx.event_bus.subscribe(capture)
    try:
        # Use short timeout so we don't wait long
        await request_confirmation(
            ctx, tool_name="test_tool", command="cmd", message="Msg?",
            timeout=0.1,
        )
    finally:
        ctx.event_bus.unsubscribe(sub_id)

    assert len(captured) == 1
    assert captured[0]["conv_id"] == "test-conv-123"


@pytest.mark.asyncio
async def test_preapproved_skips_pending(ctx):
    """Pre-approved tools don't set pending_confirmation."""
    ctx.tools.preapproved = {"test_tool"}
    ctx.conv_id = "test-conv"
    result = await request_confirmation(
        ctx, tool_name="test_tool", command="cmd", message="Msg?",
    )
    assert result["approved"] is True
    assert ctx.pending_confirmation is None
