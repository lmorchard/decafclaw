"""Tests for the `/ws/terminal/{conv_id}/{tab_id}` WebSocket route."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from decafclaw.terminals import TerminalRegistry, TerminalSession
from decafclaw.web.auth import create_token
from decafclaw.web.websocket import websocket_terminal


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


@pytest.mark.asyncio
async def test_ws_handler_serves_real_spawned_session(http_config):
    """End-to-end seam the other tests stub around: a REAL spawned PTY session
    (real fd + live reader) served through `websocket_terminal` — its buffer is
    replayed to the socket and a resize round-trips to `size_changed`.

    Driven against the handler directly with a fake WebSocket rather than
    Starlette's TestClient: a live PTY reader is bound to the event loop it was
    spawned on (`loop.add_reader`), and TestClient runs the app on a separate
    portal loop — so a real-spawn-over-TestClient would cross loops. Here the
    spawn, the reader, and the handler all share the one test loop (same shape
    as `tests/test_terminals.py::test_real_pty_echo_and_cleanup`).
    """
    registry = TerminalRegistry(http_config)
    # /bin/cat stays alive (echoes stdin), so the session is still attachable
    # when the handler connects — unlike a one-shot command that exits first.
    session = await registry.spawn("c1", "canvas_1", "s1", cwd="/tmp", shell="/bin/cat")
    try:
        await registry.write_input(session, b"ping-42\n")
        # Wait on the real signal (buffer fills), not a fixed sleep.
        for _ in range(200):
            if b"ping-42" in bytes(session.buffer):
                break
            await asyncio.sleep(0.01)
        assert b"ping-42" in bytes(session.buffer)

        sent_bytes: list[bytes] = []
        sent_json: list[dict] = []
        ws = MagicMock()
        ws.cookies = {"decafclaw_session": create_token(http_config, "testuser")}
        ws.path_params = {"conv_id": "c1", "tab_id": "canvas_1"}
        ws.accept = AsyncMock()
        ws.close = AsyncMock()
        ws.send_bytes = AsyncMock(side_effect=lambda b: sent_bytes.append(bytes(b)))
        ws.send_json = AsyncMock(side_effect=lambda m: sent_json.append(m))
        # One resize frame, then disconnect.
        ws.receive = AsyncMock(side_effect=[
            {"type": "websocket.receive",
             "text": json.dumps({"type": "resize", "cols": 100, "rows": 30})},
            {"type": "websocket.disconnect"},
        ])

        await websocket_terminal(ws, http_config, registry)

        # Replay: the real session's buffer went out as a binary frame...
        assert any(b"ping-42" in chunk for chunk in sent_bytes)
        # ...followed by buffer_replay_done, and the resize round-tripped.
        assert any(m.get("type") == "buffer_replay_done" for m in sent_json)
        assert {"type": "size_changed", "cols": 100, "rows": 30} in sent_json
        # Disconnect cleanup released the viewport (no leak).
        assert session.viewports == {}
    finally:
        # Short grace: cat dies on SIGHUP immediately; no need for the full
        # default 1s SIGHUP→SIGKILL window (keeps this test off the slow list).
        await registry.kill(session, grace=0.1)
