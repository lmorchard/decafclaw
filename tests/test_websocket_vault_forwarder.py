"""Tests for _make_vault_change_forwarder in web/websocket.py.

The forwarder is the per-connection bridge between the global EventBus and
a single WebSocket: it filters bus events down to ``vault_changed`` and
emits the matching WS wire shape on the socket.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from decafclaw.web.message_types import WSMessageType
from decafclaw.web.websocket import _make_vault_change_forwarder


class TestVaultChangeForwarder:
    @pytest.mark.asyncio
    async def test_forwards_matching_event(self):
        ws_send = AsyncMock()
        forward = _make_vault_change_forwarder(ws_send)
        await forward({
            "type": "vault_changed",
            "kind": "create",
            "path": "creative/foo.md",
        })
        ws_send.assert_awaited_once_with({
            "type": WSMessageType.VAULT_CHANGED,
            "path": "creative/foo.md",
            "kind": "create",
        })

    @pytest.mark.asyncio
    async def test_ignores_non_matching_event(self):
        ws_send = AsyncMock()
        forward = _make_vault_change_forwarder(ws_send)
        await forward({"type": "tool_status", "tool_call_id": "x"})
        await forward({"type": "notification_created", "record": {}, "unread_count": 0})
        ws_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_missing_fields_gracefully(self):
        ws_send = AsyncMock()
        forward = _make_vault_change_forwarder(ws_send)
        await forward({"type": "vault_changed"})
        ws_send.assert_awaited_once()
        sent = ws_send.await_args[0][0]
        assert sent["path"] == ""
        assert sent["kind"] == ""

    @pytest.mark.asyncio
    async def test_coerces_null_path_and_kind_to_empty_string(self):
        # If a publisher sends explicit None for path/kind, the wire-types
        # contract still requires strings. Forwarder must coerce.
        ws_send = AsyncMock()
        forward = _make_vault_change_forwarder(ws_send)
        await forward({"type": "vault_changed", "path": None, "kind": None})
        ws_send.assert_awaited_once()
        sent = ws_send.await_args[0][0]
        assert sent["path"] == ""
        assert sent["kind"] == ""
