"""Tests for the `/ws/terminal/{conv_id}/{tab_id}` WebSocket route."""

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from decafclaw.terminals import TerminalSession
from decafclaw.web.auth import create_token


@pytest.fixture
def authed_client(app, http_config):
    """TestClient with a valid `decafclaw_session` cookie set before connecting.

    httpx's cookie jar is applied to the internal websocket handshake
    request, so setting the cookie on the client (rather than reinventing
    ASGI middleware) authenticates via the real `get_current_user` path.
    """
    client = TestClient(app)
    client.cookies.set("decafclaw_session", create_token(http_config, "testuser"))
    return client


@pytest.fixture
def authed_client_with_session(app, http_config, authed_client):
    def _make(buffer: bytes = b""):
        session = TerminalSession(
            conv_id="c1", tab_id="canvas_1", session_id="s1",
            cwd="/tmp", shell="/bin/sh", pid=123, fd=9,
            buffer=bytearray(buffer),
        )
        app.state.terminal_registry._sessions[("c1", "canvas_1")] = session
        return authed_client
    return _make


def test_terminal_ws_rejects_unauthenticated(app):
    client = TestClient(app)  # no cookie set
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/terminal/c1/canvas_1"):
            pass  # server closes 4001 before accept()
    assert exc_info.value.code == 4001


def test_terminal_ws_session_not_found_sends_ended(authed_client):
    with authed_client.websocket_connect("/ws/terminal/c1/canvas_1") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "session_ended"


def test_terminal_ws_replays_buffer_then_done(authed_client_with_session):
    client = authed_client_with_session(buffer=b"hello\n")
    with client.websocket_connect("/ws/terminal/c1/canvas_1") as ws:
        assert ws.receive_bytes() == b"hello\n"
        assert ws.receive_json()["type"] == "buffer_replay_done"


def test_terminal_ws_tolerates_malformed_control_frames(authed_client_with_session):
    """A bad frame (invalid JSON, or missing a required key) must not crash
    the connection — the handler should log and keep the loop alive so
    subsequent valid frames still work."""
    client = authed_client_with_session(buffer=b"")
    with client.websocket_connect("/ws/terminal/c1/canvas_1") as ws:
        assert ws.receive_json()["type"] == "buffer_replay_done"

        # Non-JSON text frame.
        ws.send_text("not json{{{")

        # Well-formed JSON but missing a required key for its type.
        ws.send_text('{"type": "resize"}')

        # Connection must still be alive: a valid resize frame is processed
        # and answered normally.
        ws.send_text('{"type": "resize", "cols": 80, "rows": 24}')
        msg = ws.receive_json()
        assert msg == {"type": "size_changed", "cols": 80, "rows": 24}
