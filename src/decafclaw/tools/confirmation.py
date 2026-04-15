"""Shared confirmation request helper for tools that need user approval.

When the context has a ``request_confirmation`` callable (set by the
ConversationManager), confirmations route through the manager for
persistence and per-conversation scoping. Otherwise, falls back to the
legacy event-bus pattern.
"""

import asyncio
import logging

log = logging.getLogger(__name__)

# Maps tool_name to ConfirmationAction for the manager bridge.
# Imported lazily to avoid circular imports at module level.
_TOOL_ACTION_MAP: dict | None = None


def _get_tool_action_map():
    global _TOOL_ACTION_MAP
    if _TOOL_ACTION_MAP is None:
        from ..confirmations import ConfirmationAction
        _TOOL_ACTION_MAP = {
            "shell": ConfirmationAction.RUN_SHELL_COMMAND,
            "shell_background_start": ConfirmationAction.RUN_SHELL_COMMAND,
            "activate_skill": ConfirmationAction.ACTIVATE_SKILL,
            "end_turn_confirm": ConfirmationAction.CONTINUE_TURN,
        }
    return _TOOL_ACTION_MAP


async def _request_via_manager(ctx, tool_name, command, message, timeout,
                               **extra_event_fields) -> dict:
    """Bridge to the ConversationManager's confirmation flow."""
    from ..confirmations import ConfirmationAction, ConfirmationRequest

    action_map = _get_tool_action_map()
    action_type = action_map.get(tool_name, ConfirmationAction.CONTINUE_TURN)

    # Build action_data from the tool-specific parameters
    action_data: dict = {"command": command}
    for key in ("suggested_pattern", "skill_name"):
        if key in extra_event_fields:
            action_data[key] = extra_event_fields[key]

    request = ConfirmationRequest(
        action_type=action_type,
        action_data=action_data,
        message=message,
        tool_call_id=ctx.tools.current_call_id,
        timeout=timeout,
        approve_label=extra_event_fields.get("approve_label", "Approve"),
        deny_label=extra_event_fields.get("deny_label", "Deny"),
    )

    response = await ctx.request_confirmation(request)

    # Convert ConfirmationResponse to the dict format tools expect
    result: dict = {"approved": response.approved}
    if response.always:
        result["always"] = True
    if response.add_pattern:
        result["add_pattern"] = True
    return result


async def _request_via_event_bus(ctx, tool_name, command, message, timeout,
                                 **extra_event_fields) -> dict:
    """Legacy event-bus confirmation flow."""
    confirm_event = asyncio.Event()
    result = {"approved": False}
    tool_call_id = ctx.tools.current_call_id
    match_context_id = ctx.event_context_id or ctx.context_id

    def on_confirm(event):
        if (event.get("type") == "tool_confirm_response"
                and event.get("context_id") == match_context_id
                and event.get("tool") == tool_name):
            resp_id = event.get("tool_call_id", "")
            if tool_call_id and resp_id and resp_id != tool_call_id:
                return
            result.update(event)
            confirm_event.set()

    sub_id = ctx.event_bus.subscribe(on_confirm)
    try:
        await ctx.publish(
            "tool_confirm_request",
            tool=tool_name,
            command=command,
            message=message,
            tool_call_id=tool_call_id,
            **extra_event_fields,
        )
        try:
            await asyncio.wait_for(confirm_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            log.info(f"Confirmation timed out for {tool_name}: {command}")
            return {"approved": False}
    finally:
        ctx.event_bus.unsubscribe(sub_id)

    return result


async def request_confirmation(
    ctx,
    tool_name: str,
    command: str,
    message: str,
    timeout: float = 60,
    **extra_event_fields,
) -> dict:
    """Request user confirmation.

    Routes through the ConversationManager when available (persisted,
    per-conversation scoped). Falls back to the legacy event-bus pattern
    for transports not yet migrated.

    Returns a dict with at least ``"approved"`` (bool). May also contain
    ``"always"``, ``"add_pattern"``, etc.
    """
    # Check command pre-approval before prompting
    if tool_name in ctx.tools.preapproved:
        log.info(f"Confirmation pre-approved for {tool_name}")
        return {"approved": True}

    # Route through manager
    if ctx.request_confirmation is not None:
        return await _request_via_manager(
            ctx, tool_name, command, message, timeout, **extra_event_fields)

    # Fallback for contexts not managed by ConversationManager
    # (heartbeat, scheduled tasks). These should auto-approve via
    # preapproved checks above, so hitting this path is unexpected.
    log.warning("No ConversationManager for confirmation request "
                "(tool=%s, user=%s) — using legacy event bus",
                tool_name, getattr(ctx, "user_id", "?"))
    return await _request_via_event_bus(
        ctx, tool_name, command, message, timeout, **extra_event_fields)
