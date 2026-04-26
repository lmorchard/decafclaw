"""Tests for the shared confirmation request helper."""

import asyncio
import dataclasses

import pytest

from decafclaw.confirmations import (
    ConfirmationAction,
    ConfirmationRequest,
    ConfirmationResponse,
)
from decafclaw.tools.confirmation import request_confirmation

# -- Archive round-trip -------------------------------------------------------


class TestConfirmationRequestArchive:
    def test_roundtrip_preserves_all_fields(self):
        req = ConfirmationRequest(
            action_type=ConfirmationAction.RUN_SHELL_COMMAND,
            action_data={"command": "ls"},
            message="Run this?",
            approve_label="Yes",
            deny_label="No",
            tool_call_id="call_123",
            timeout=60.0,
            confirmation_id="conf_abc",
            timestamp="2026-04-25T00:00:00",
        )
        roundtripped = ConfirmationRequest.from_archive_message(
            req.to_archive_message())
        assert roundtripped == req

    def test_to_archive_emits_all_dataclass_fields(self):
        """Future fields on ConfirmationRequest must show up in the archive
        dict automatically. Per CLAUDE.md, serialization iterates fields
        rather than enumerating them by name."""
        req = ConfirmationRequest(
            action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        )
        msg = req.to_archive_message()
        expected = {f.name for f in dataclasses.fields(ConfirmationRequest)}
        # Plus the non-field "role" tag.
        assert expected <= set(msg.keys())
        assert msg["role"] == "confirmation_request"

    def test_action_type_serialized_as_string(self):
        """JSON serialization stays explicit regardless of encoder."""
        req = ConfirmationRequest(
            action_type=ConfirmationAction.ACTIVATE_SKILL,
        )
        msg = req.to_archive_message()
        assert msg["action_type"] == "activate_skill"
        assert isinstance(msg["action_type"], str)

    def test_from_archive_ignores_unknown_keys(self):
        """Forward compat: future agent versions may write keys this build
        doesn't recognize yet."""
        req = ConfirmationRequest.from_archive_message({
            "role": "confirmation_request",
            "action_type": "run_shell_command",
            "confirmation_id": "x",
            "future_field": "ignored",
        })
        assert req.action_type is ConfirmationAction.RUN_SHELL_COMMAND
        assert req.confirmation_id == "x"


class TestConfirmationResponseArchive:
    def test_roundtrip_preserves_all_fields(self):
        resp = ConfirmationResponse(
            confirmation_id="conf_abc",
            approved=True,
            always=True,
            add_pattern=True,
            timestamp="2026-04-25T00:00:00",
        )
        roundtripped = ConfirmationResponse.from_archive_message(
            resp.to_archive_message())
        assert roundtripped == resp

    def test_falsy_optional_flags_dropped_from_archive(self):
        """Lean archive: omit always/add_pattern when False. Round-trip
        still works because from_archive falls back to dataclass defaults."""
        resp = ConfirmationResponse(
            confirmation_id="conf_abc",
            approved=False,
        )
        msg = resp.to_archive_message()
        assert "always" not in msg
        assert "add_pattern" not in msg
        roundtripped = ConfirmationResponse.from_archive_message(msg)
        assert roundtripped == resp

    def test_to_archive_emits_all_dataclass_fields_when_truthy(self):
        """When all optional flags are True the archive contains every
        declared field. Serves as the iterate-over-fields check —
        a new dataclass field that to_archive forgets would surface here."""
        resp = ConfirmationResponse(
            confirmation_id="conf_abc",
            approved=True,
            always=True,
            add_pattern=True,
        )
        msg = resp.to_archive_message()
        expected = {f.name for f in dataclasses.fields(ConfirmationResponse)}
        assert expected <= set(msg.keys())
        assert msg["role"] == "confirmation_response"


# -- Helper tests --------------------------------------------------------------


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
            message="Confirm?", timeout=0.2,
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
