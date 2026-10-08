"""Tests for forwarding conversation_status events to WebSocket clients."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.websockets import WebSocketDisconnect

from decafclaw.events import EventBus
from decafclaw.web.message_types import WSMessageType
from decafclaw.web.websocket import _make_conversation_status_forwarder, websocket_chat


class TestConversationStatusForwarder:
    @staticmethod
    def _capture():
        sent: list[dict] = []

        async def send(msg):
            sent.append(msg)

        return sent, send

    @pytest.mark.asyncio
    async def test_forwards_conversation_status(self):
        sent, send = self._capture()
        forward = _make_conversation_status_forwarder(send)
        await forward(
            {
                "type": "conversation_status",
                "conv_id": "c1",
                "status": "busy",
            }
        )
        assert sent == [
            {
                "type": WSMessageType.CONVERSATION_STATUS,
                "conv_id": "c1",
                "status": "busy",
            }
        ]

    @pytest.mark.asyncio
    async def test_ignores_other_event_types(self):
        sent, send = self._capture()
        forward = _make_conversation_status_forwarder(send)
        await forward({"type": "notification_created", "conv_id": "c1"})
        await forward({"type": "vault_changed", "path": "p"})
        assert sent == []


@pytest.fixture
def mock_ws():
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.send_json = AsyncMock()
    ws.receive_text = AsyncMock(side_effect=WebSocketDisconnect())
    ws.cookies = {"decafclaw_session": "not-a-real-token"}
    return ws


@pytest.mark.asyncio
async def test_websocket_chat_forwards_status_events(config, mock_ws, monkeypatch):
    from decafclaw.web import auth as auth_mod

    monkeypatch.setattr(auth_mod, "get_current_user", lambda ws, cfg: "testuser")
    bus = EventBus()

    publish_fired = False

    async def publish_then_disconnect():
        nonlocal publish_fired
        if not publish_fired:
            publish_fired = True
            await bus.publish(
                {
                    "type": "conversation_status",
                    "conv_id": "c-bg",
                    "status": "waiting",
                }
            )
        raise WebSocketDisconnect()

    mock_ws.receive_text = AsyncMock(side_effect=publish_then_disconnect)
    await websocket_chat(mock_ws, config, bus, MagicMock())

    sent_payloads = [call.args[0] for call in mock_ws.send_json.call_args_list]
    status_payloads = [p for p in sent_payloads if p.get("type") == "conversation_status"]
    assert len(status_payloads) == 1
    assert status_payloads[0] == {
        "type": WSMessageType.CONVERSATION_STATUS,
        "conv_id": "c-bg",
        "status": "waiting",
    }
