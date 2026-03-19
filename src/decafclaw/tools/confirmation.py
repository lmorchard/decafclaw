"""Shared confirmation request helper for tools that need user approval."""

import asyncio
import logging

log = logging.getLogger(__name__)


async def request_confirmation(
    ctx,
    tool_name: str,
    command: str,
    message: str,
    timeout: float = 60,
    **extra_event_fields,
) -> dict:
    """Request user confirmation via the event bus.

    Publishes a tool_confirm_request event and waits for a matching
    tool_confirm_response. Returns a dict with at least "approved" (bool).
    May also contain "always", "add_pattern", etc. depending on the
    response.

    Matches responses by context_id + tool_name. When tool_call_id is
    available (via ctx.current_tool_call_id), it's included in the request
    and used for stricter matching — required for concurrent tool calls
    to the same tool.

    Times out after `timeout` seconds, returning {"approved": False}.
    """
    confirm_event = asyncio.Event()
    result = {"approved": False}
    tool_call_id = ctx.current_tool_call_id
    # Match against the context_id used for publishing (event_context_id if set,
    # otherwise context_id). Child agents publish under the parent's event_context_id.
    match_context_id = ctx.event_context_id or ctx.context_id

    def on_confirm(event):
        if (event.get("type") == "tool_confirm_response"
                and event.get("context_id") == match_context_id
                and event.get("tool") == tool_name):
            # When both sides have tool_call_id, require match (concurrent safety).
            # If the response omits it, accept anyway (backward compat with older UIs).
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
