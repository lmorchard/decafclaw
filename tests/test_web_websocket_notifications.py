"""WebSocket-side forwarding of notification events to connected clients.

Covers the bridge added in #332: the `websocket_chat` handler registers a
subscriber on the global event bus that filters notification events by
type and forwards them to its socket. Complements the REST-side event
tests in `tests/test_web_notifications.py`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.websockets import WebSocketDisconnect

from decafclaw.events import EventBus
from decafclaw.web.websocket import _make_notification_forwarder, websocket_chat

# -- Unit tests on the forwarder itself ---------------------------------------


class TestNotificationForwarder:
    """ws_send is always async in production; these tests mirror that."""

    @staticmethod
    def _capture():
        sent: list[dict] = []

        async def send(msg):
            sent.append(msg)

        return sent, send

    @pytest.mark.asyncio
    async def test_forwards_notification_created(self):
        sent, send = self._capture()
        forward = _make_notification_forwarder(send)
        await forward({
            "type": "notification_created",
            "record": {"id": "abc", "title": "Hi"},
            "unread_count": 3,
        })
        assert sent == [{
            "type": "notification_created",
            "record": {"id": "abc", "title": "Hi"},
            "unread_count": 3,
        }]

    @pytest.mark.asyncio
    async def test_forwards_notification_read(self):
        sent, send = self._capture()
        forward = _make_notification_forwarder(send)
        await forward({
            "type": "notification_read",
            "ids": ["a", "b"],
            "unread_count": 0,
        })
        assert sent == [{
            "type": "notification_read",
            "ids": ["a", "b"],
            "unread_count": 0,
        }]

    @pytest.mark.asyncio
    async def test_ignores_other_event_types(self):
        """Bus events unrelated to notifications do not reach the socket."""
        sent, send = self._capture()
        forward = _make_notification_forwarder(send)
        await forward({"type": "tool_start", "tool_call_id": "x"})
        await forward({"type": "llm_end"})
        await forward({"type": "turn_complete", "conv_id": "y"})
        assert sent == []


# -- Subscription lifecycle inside websocket_chat ------------------------------


@pytest.fixture
def mock_ws():
    """A mock WebSocket that disconnects on first receive_text."""
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.send_json = AsyncMock()
    ws.receive_text = AsyncMock(side_effect=WebSocketDisconnect())
    ws.cookies = {"decafclaw_session": "not-a-real-token"}
    return ws


@pytest.mark.asyncio
async def test_websocket_chat_subscribes_and_unsubscribes(
    config, mock_ws, monkeypatch,
):
    """The notification forwarder subscribes on connect and unsubscribes
    on disconnect — no subscriber leak across connections."""
    from decafclaw.web import auth as auth_mod
    monkeypatch.setattr(auth_mod, "get_current_user", lambda ws, cfg: "testuser")
    bus = EventBus()

    # Register one pre-existing subscriber so we can verify the handler
    # adds exactly one of its own and then removes only that one.
    async def baseline(event):
        return None

    bus.subscribe(baseline)
    assert len(bus._subscribers) == 1

    await websocket_chat(mock_ws, config, bus, MagicMock())

    # After the handler returns, its own subscriber must be gone — we
    # should be back to just `baseline`.
    assert len(bus._subscribers) == 1

    # Confirm accept happened (we got past auth) and cleanup reached finally.
    mock_ws.accept.assert_awaited_once()


@pytest.mark.asyncio
async def test_websocket_chat_forwards_live_bus_events(
    config, mock_ws, monkeypatch,
):
    """While the chat session is running, a bus publish reaches ws.send_json."""
    from decafclaw.web import auth as auth_mod
    monkeypatch.setattr(auth_mod, "get_current_user", lambda ws, cfg: "testuser")
    bus = EventBus()

    # Rig receive_text to publish then disconnect, so we get at least one
    # iteration of the handler's message loop running concurrently with the
    # bus dispatch.
    publish_fired = False

    async def publish_then_disconnect():
        nonlocal publish_fired
        if not publish_fired:
            publish_fired = True
            await bus.publish({
                "type": "notification_created",
                "record": {"id": "xyz", "title": "live"},
                "unread_count": 7,
            })
        raise WebSocketDisconnect()

    mock_ws.receive_text = AsyncMock(side_effect=publish_then_disconnect)

    await websocket_chat(mock_ws, config, bus, MagicMock())

    # At least one send_json call should be the forwarded notification.
    sent_payloads = [call.args[0] for call in mock_ws.send_json.call_args_list]
    notif_payloads = [p for p in sent_payloads if p.get("type") == "notification_created"]
    assert len(notif_payloads) == 1
    assert notif_payloads[0] == {
        "type": "notification_created",
        "record": {"id": "xyz", "title": "live"},
        "unread_count": 7,
    }
