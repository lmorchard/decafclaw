"""Input-widget pause/resume support.

Phase 2 (#256): input widgets (``accepts_input=True``) pause the agent
turn via the existing confirmation infra. An in-memory callback map
lets tools provide a custom ``on_response`` callback that runs when
the user submits. The callback returns a string that's injected as a
synthetic user message so the next LLM iteration sees the choice.

This module provides:

- ``pending_callbacks``: the in-memory map keyed by ``tool_call_id``.
- ``WidgetResponseHandler``: the default confirmation handler for
  ``WIDGET_RESPONSE`` actions. Used only by the recovery path
  (``ConversationManager.recover_confirmation``) when a response
  arrives for a conversation with no running agent loop. Writes a
  synthetic user message directly to the archive so the next turn's
  LLM call sees the answer.
- ``register_widget_handler(registry)``: wires the handler into a
  ``ConfirmationRegistry`` instance at startup.
"""

import logging
from collections.abc import Callable
from typing import Any

from .confirmations import (
    ConfirmationAction,
    ConfirmationRegistry,
    ConfirmationRequest,
    ConfirmationResponse,
)

log = logging.getLogger(__name__)


# In-memory callback map: tool_call_id -> on_response callable.
# Populated by the agent loop when it promotes an input widget into
# a WidgetInputPause signal; consumed once the user submits. Cleared
# in a finally block so crashes don't leak entries.
pending_callbacks: dict[str, Callable[[dict], str]] = {}


def default_inject_message(response_data: dict) -> str:
    """Fallback inject-string used when no ``on_response`` callback is
    registered (e.g., after server restart)."""
    return f"User responded with: {response_data}"


class WidgetResponseHandler:
    """Confirmation handler for ``WIDGET_RESPONSE`` actions.

    Invoked only by the recovery path. Writes a synthetic user message
    to the archive carrying the response data so the next turn's LLM
    call sees the user's choice.

    The live path (agent loop awaiting ``ctx.request_confirmation``)
    handles responses directly without dispatching to this handler; the
    handler's job here is strictly recovery when no loop is running.
    """

    async def on_approve(self, ctx: Any, request: ConfirmationRequest,
                         response: ConfirmationResponse) -> dict:
        return await self._inject(ctx, request, response)

    async def on_deny(self, ctx: Any, request: ConfirmationRequest,
                      response: ConfirmationResponse) -> dict:
        # Widget submits are always "approved" in the confirmation sense
        # — there's no deny path. If we see a denial, something odd
        # happened (timeout maybe). Still inject a best-effort message
        # so the conversation stays coherent.
        log.warning(
            "WIDGET_RESPONSE denied for tool_call_id=%s — unusual; "
            "injecting a terse placeholder",
            request.tool_call_id,
        )
        return await self._inject(ctx, request, response)

    async def _inject(self, ctx: Any, request: ConfirmationRequest,
                      response: ConfirmationResponse) -> dict:
        # Consume callback if present; recovery path won't have one.
        callback = pending_callbacks.pop(request.tool_call_id, None)
        if callback is not None:
            try:
                content = callback(response.data)
            except Exception as exc:
                log.warning(
                    "widget on_response callback raised for %s: %s",
                    request.tool_call_id, exc)
                content = default_inject_message(response.data)
        else:
            content = default_inject_message(response.data)

        # Write the synthetic user message so a subsequent turn sees it.
        if ctx is not None and ctx.conv_id:
            from .archive import append_message
            append_message(ctx.config, ctx.conv_id, {
                "role": "user",
                "source": "widget_response",
                "content": content,
            })
        return {"inject_message": content, "continue_loop": False}


def register_widget_handler(registry: ConfirmationRegistry) -> None:
    """Register the default WIDGET_RESPONSE handler on a registry."""
    registry.register(ConfirmationAction.WIDGET_RESPONSE,
                      WidgetResponseHandler())
