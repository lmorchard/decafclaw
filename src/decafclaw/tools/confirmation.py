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

    Times out after `timeout` seconds, returning {"approved": False}.
    """
    confirm_event = asyncio.Event()
    result = {"approved": False}

    def on_confirm(event):
        if (event.get("type") == "tool_confirm_response"
                and event.get("context_id") == ctx.context_id
                and event.get("tool") == tool_name):
            result.update(event)
            confirm_event.set()

    sub_id = ctx.event_bus.subscribe(on_confirm)
    try:
        await ctx.publish(
            "tool_confirm_request",
            tool=tool_name,
            command=command,
            message=message,
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
