"""Tests for the `/ws/terminal/{conv_id}/{tab_id}` WebSocket route."""

import pytest
from starlette.testclient import TestClient

from decafclaw.terminals import TerminalSession
from decafclaw.web.auth import create_token


class _FixedCookieMiddleware:
    """Test-only ASGI middleware: injects a session cookie on every request.

    A fresh `TestClient(app)` carries no cookies of its own — this bakes a
    valid `decafclaw_session` cookie into the app itself so tests can build
    the client the same way production code does (no auth backdoor; the
    real `get_current_user`/`validate_token` path still runs).
    """

    def __init__(self, app, cookie_header: str):
        self.app = app
        self.cookie_header = cookie_header.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope)
            scope["headers"] = [*scope.get("headers", []), (b"cookie", self.cookie_header)]
        await self.app(scope, receive, send)


@pytest.fixture
def make_app_no_auth_cookie(app):
    def _make():
        return app
    return _make


@pytest.fixture
def make_app_authed(app, http_config):
    def _make():
        token = create_token(http_config, "testuser")
        app.add_middleware(_FixedCookieMiddleware, cookie_header=f"decafclaw_session={token}")
        return app
    return _make


@pytest.fixture
def make_app_authed_with_session(app, http_config):
    def _make(buffer: bytes = b""):
        token = create_token(http_config, "testuser")
        app.add_middleware(_FixedCookieMiddleware, cookie_header=f"decafclaw_session={token}")
        session = TerminalSession(
            conv_id="c1", tab_id="canvas_1", session_id="s1",
            cwd="/tmp", shell="/bin/sh", pid=123, fd=9,
            buffer=bytearray(buffer),
        )
        app.state.terminal_registry._sessions[("c1", "canvas_1")] = session
        return app
    return _make


def test_terminal_ws_rejects_unauthenticated(make_app_no_auth_cookie):
    app = make_app_no_auth_cookie()
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/terminal/c1/canvas_1"):
            pass  # server closes 4001 before accept()


def test_terminal_ws_session_not_found_sends_ended(make_app_authed):
    app = make_app_authed()  # registry empty
    client = TestClient(app)
    with client.websocket_connect("/ws/terminal/c1/canvas_1") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "session_ended"


def test_terminal_ws_replays_buffer_then_done(make_app_authed_with_session):
    app = make_app_authed_with_session(buffer=b"hello\n")
    client = TestClient(app)
    with client.websocket_connect("/ws/terminal/c1/canvas_1") as ws:
        assert ws.receive_bytes() == b"hello\n"
        assert ws.receive_json()["type"] == "buffer_replay_done"
