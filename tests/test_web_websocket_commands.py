"""WebSocket command-dispatch tests: verify cmd_ctx carries the manager.

Regression test for #361 — without the manager attached, bundled skills
with context: fork (dream, garden) fail their !command invocation with
'delegate_task requires a ConversationManager; no manager on parent ctx'.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from decafclaw.commands import CommandResult


@pytest.mark.asyncio
async def test_handle_send_attaches_manager_to_cmd_ctx(monkeypatch, config):
    """When a user sends a message that triggers command dispatch,
    the cmd_ctx passed to dispatch_command MUST have ctx.manager set
    to the conversation manager from state."""
    from decafclaw.web import websocket

    # Capture the ctx passed into dispatch_command so we can assert on it.
    captured = {}

    async def fake_dispatch(ctx, text, **kwargs):
        captured["ctx"] = ctx
        return CommandResult(
            mode="unknown", text="", display_text=text,
            skill=None,
        )

    monkeypatch.setattr(
        "decafclaw.commands.dispatch_command", fake_dispatch,
    )

    # Minimal state: real config + event_bus, sentinel manager.
    from decafclaw.events import EventBus
    bus = EventBus()
    sentinel_manager = MagicMock()
    state = {
        "config": config,
        "event_bus": bus,
        "manager": sentinel_manager,
    }

    # Minimal conversation index with a conv owned by "testuser".
    index = MagicMock()
    conv = MagicMock()
    conv.user_id = "testuser"
    index.get.return_value = conv

    # ws_send must be an awaitable; the test doesn't assert on outbound traffic.
    async def ws_send(_msg):
        pass

    msg = {"conv_id": "conv-1", "text": "!dream"}

    await websocket._handle_send(
        ws_send, index, "testuser", msg, state,
    )

    assert "ctx" in captured, "dispatch_command was not invoked"
    assert captured["ctx"].manager is sentinel_manager
