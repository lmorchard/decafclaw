"""Tests for web UI confirmation round-trip.

Tests the confirm_response websocket handler — verifying that tool_call_id
flows from the browser message to the event bus. This is the path that broke
during the concurrent-tools work when tool_call_id wasn't echoed back.
"""

import asyncio

import pytest

from decafclaw.events import EventBus


@pytest.mark.asyncio
async def test_confirm_response_forwards_tool_call_id():
    """confirm_response message includes tool_call_id in event bus publish."""
    bus = EventBus()
    published = []
    bus.subscribe(lambda e: published.append(e))

    # Simulate what the websocket handler does for confirm_response
    msg = {
        "type": "confirm_response",
        "context_id": "ctx-123",
        "tool": "shell",
        "tool_call_id": "call_abc",
        "approved": True,
    }
    tool_call_id = msg.get("tool_call_id", "")
    await bus.publish({
        "type": "tool_confirm_response",
        "context_id": msg.get("context_id", ""),
        "tool": msg.get("tool", ""),
        "approved": msg.get("approved", False),
        **({"tool_call_id": tool_call_id} if tool_call_id else {}),
    })

    assert len(published) == 1
    event = published[0]
    assert event["type"] == "tool_confirm_response"
    assert event["tool_call_id"] == "call_abc"
    assert event["context_id"] == "ctx-123"
    assert event["tool"] == "shell"
    assert event["approved"] is True


@pytest.mark.asyncio
async def test_confirm_response_without_tool_call_id():
    """confirm_response without tool_call_id doesn't include it in event."""
    bus = EventBus()
    published = []
    bus.subscribe(lambda e: published.append(e))

    msg = {
        "type": "confirm_response",
        "context_id": "ctx-123",
        "tool": "shell",
        "approved": True,
        # no tool_call_id
    }
    tool_call_id = msg.get("tool_call_id", "")
    await bus.publish({
        "type": "tool_confirm_response",
        "context_id": msg.get("context_id", ""),
        "tool": msg.get("tool", ""),
        "approved": msg.get("approved", False),
        **({"tool_call_id": tool_call_id} if tool_call_id else {}),
    })

    assert len(published) == 1
    event = published[0]
    assert "tool_call_id" not in event


@pytest.mark.asyncio
async def test_confirm_round_trip_with_tool_call_id(ctx):
    """Full round-trip: request_confirmation + simulated browser response with tool_call_id."""
    from decafclaw.tools.confirmation import request_confirmation

    ctx.tools.current_call_id = "call_abc"

    # Capture the confirm request event to verify it includes tool_call_id
    request_events = []

    def capture_request(event):
        if event.get("type") == "tool_confirm_request":
            request_events.append(event)

    ctx.event_bus.subscribe(capture_request)

    async def browser_approve():
        # Wait for the request to be published
        await asyncio.sleep(0.05)
        # Simulate browser echoing back tool_call_id
        await ctx.event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": ctx.context_id,
            "tool": "shell",
            "tool_call_id": "call_abc",
            "approved": True,
        })

    asyncio.create_task(browser_approve())
    result = await request_confirmation(
        ctx, tool_name="shell", command="curl example.com", message="Confirm?",
    )

    # Request included tool_call_id
    assert len(request_events) == 1
    assert request_events[0]["tool_call_id"] == "call_abc"

    # Response matched
    assert result["approved"] is True


@pytest.mark.asyncio
async def test_publish_includes_tool_call_id_from_ctx(ctx):
    """ctx.publish auto-includes current_tool_call_id when set."""
    published = []
    ctx.event_bus.subscribe(lambda e: published.append(e))

    ctx.tools.current_call_id = "call_xyz"
    await ctx.publish("tool_status", tool="shell", message="running...")

    assert published[0]["tool_call_id"] == "call_xyz"


@pytest.mark.asyncio
async def test_publish_does_not_override_explicit_tool_call_id(ctx):
    """Explicit tool_call_id in kwargs takes precedence over ctx field."""
    published = []
    ctx.event_bus.subscribe(lambda e: published.append(e))

    ctx.tools.current_call_id = "call_xyz"
    await ctx.publish("tool_status", tool="shell", message="running...",
                      tool_call_id="call_override")

    assert published[0]["tool_call_id"] == "call_override"


@pytest.mark.asyncio
async def test_publish_no_tool_call_id_when_unset(ctx):
    """ctx.publish does not include tool_call_id when not set on ctx."""
    published = []
    ctx.event_bus.subscribe(lambda e: published.append(e))

    ctx.tools.current_call_id = ""
    await ctx.publish("tool_status", tool="shell", message="running...")

    assert "tool_call_id" not in published[0]


# -- approve_label / deny_label defaults --------------------------------------
#
# The web UI gates its third confirmation button ("Allow: <pattern>" for shell,
# otherwise "Always") on `c.approve_label` being falsy — `confirm-view.js`
# treats a non-empty label as a signal that the tool wants a custom UI gate
# and suppresses the extra button. So a truthy default ("Approve" / "Deny")
# coming over the wire silently hides the third button for every shell
# confirmation, even though the rest of the plumbing (suggested_pattern,
# add_pattern, always) is wired up correctly.


def test_confirmation_to_dict_omits_default_labels():
    """Defaulted ConfirmationRequest yields empty labels on the wire so the
    web UI's `c.approve_label ? '' : ...` conditional correctly enables the
    third button (Allow / Always)."""
    from decafclaw.confirmations import ConfirmationAction, ConfirmationRequest
    from decafclaw.web.websocket import _confirmation_to_dict

    req = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "tv list-screens",
                     "suggested_pattern": "tv list-screens *"},
        message="Shell command: `tv list-screens`",
    )
    payload = _confirmation_to_dict(req)
    assert payload["approve_label"] == ""
    assert payload["deny_label"] == ""
    assert payload["suggested_pattern"] == "tv list-screens *"


def test_confirmation_to_dict_preserves_custom_labels():
    """When a tool sets custom labels (EndTurnConfirm flow), they pass through
    unchanged. The web UI's third button stays hidden — the tool is asking
    for a specific UI gate, not a generic shell allow."""
    from decafclaw.confirmations import ConfirmationAction, ConfirmationRequest
    from decafclaw.web.websocket import _confirmation_to_dict

    req = ConfirmationRequest(
        action_type=ConfirmationAction.CONTINUE_TURN,
        message="Approve project state?",
        approve_label="Approve",
        deny_label="Needs Feedback",
    )
    payload = _confirmation_to_dict(req)
    assert payload["approve_label"] == "Approve"
    assert payload["deny_label"] == "Needs Feedback"


def test_request_via_manager_uses_empty_label_defaults():
    """When `request_confirmation` is called without `approve_label` /
    `deny_label` extras (the shell path), the ConfirmationRequest that lands
    at the manager has empty labels so the wire payload preserves them."""
    import asyncio

    from decafclaw.confirmations import (
        ConfirmationAction,
        ConfirmationRequest,
        ConfirmationResponse,
    )

    captured: list[ConfirmationRequest] = []

    async def fake_request_confirmation(request: ConfirmationRequest):
        captured.append(request)
        return ConfirmationResponse(
            confirmation_id=request.confirmation_id, approved=False)

    # Reach into the manager helper directly with a stub ctx — covers the
    # default-label propagation without needing a full ConversationManager.
    from decafclaw.tools.confirmation import _request_via_manager

    class _ToolsState:
        current_call_id = "call_test"

    class _StubCtx:
        tools = _ToolsState()
        request_confirmation = staticmethod(fake_request_confirmation)

    asyncio.run(_request_via_manager(
        _StubCtx(), tool_name="shell", command="tv list-screens",
        message="Shell command: `tv list-screens`", timeout=1.0,
        suggested_pattern="tv list-screens *",
    ))

    assert len(captured) == 1
    req = captured[0]
    assert req.action_type is ConfirmationAction.RUN_SHELL_COMMAND
    assert req.approve_label == ""
    assert req.deny_label == ""
    assert req.action_data["suggested_pattern"] == "tv list-screens *"
