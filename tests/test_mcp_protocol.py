"""Protocol-revision negotiation tests for HTTP MCP servers (#1805).

These run a tiny fake Streamable HTTP server so the real SDK transport, the
``MCP-Protocol-Version`` / ``Mcp-Method`` / ``Mcp-Name`` headers, and the
``_meta`` envelope are exercised end to end.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from decafclaw.mcp_client import (
    PROTOCOL_AUTO,
    PROTOCOL_LEGACY,
    PROTOCOL_MODERN,
    ConnState,
    MCPRegistry,
    MCPServerConfig,
)


class _FakeMCPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # Set by the server fixture.
    mode = "legacy"  # "legacy" or "modern-only"
    requests: list[dict] = []

    def log_message(self, fmt, *args):
        pass

    def _send(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _empty(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        # No server-initiated stream in these fixtures.
        self._empty(405)

    def do_DELETE(self):
        self._empty(204)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        headers = {key.lower(): value for key, value in self.headers.items()}
        method = body.get("method")
        params = body.get("params") or {}
        type(self).requests.append({"method": method, "headers": headers, "body": body})

        if method == "initialize":
            if self.mode == "modern-only":
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": body["id"],
                        "error": {
                            "code": -32022,
                            "message": "Unsupported protocol version",
                            "data": {
                                "supported": [PROTOCOL_MODERN],
                                "requested": params.get("protocolVersion"),
                            },
                        },
                    }
                )
            else:
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": body["id"],
                        "result": {
                            "protocolVersion": "2025-11-25",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "fake-legacy", "version": "1.0"},
                        },
                    }
                )
            return

        if method == "notifications/initialized":
            self._empty(202)
            return

        if method == "server/discover":
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "supportedVersions": [PROTOCOL_MODERN],
                        "capabilities": {"tools": {}},
                        "resultType": "complete",
                    },
                }
            )
            return

        if method == "tools/list":
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "tools": [
                            {
                                "name": "echo",
                                "description": "Echo text",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"text": {"type": "string"}},
                                },
                            }
                        ],
                        "ttlMs": 0,
                        "cacheScope": "private",
                        "resultType": "complete",
                    },
                }
            )
            return

        if method == "tools/call":
            text = (params.get("arguments") or {}).get("text", "")
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "content": [{"type": "text", "text": f"echo:{text}"}],
                        "ttlMs": 0,
                        "cacheScope": "private",
                        "resultType": "complete",
                    },
                }
            )
            return

        self._send({"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32601, "message": "Method not found"}})


@pytest.fixture
def fake_server(request):
    mode = getattr(request, "param", "legacy")
    handler = type("Handler", (_FakeMCPHandler,), {"mode": mode, "requests": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/mcp", handler.requests
    finally:
        server.shutdown()
        server.server_close()


def _cfg(url, protocol=PROTOCOL_AUTO, name="fake"):
    return MCPServerConfig(name=name, type="http", url=url, protocol=protocol, timeout=5000)


def _methods(requests):
    return [entry["method"] for entry in requests]


def _assert_modern_envelope(requests, method):
    entry = next(item for item in requests if item["method"] == method)
    assert entry["headers"]["mcp-protocol-version"] == PROTOCOL_MODERN
    assert entry["headers"]["mcp-method"] == method
    meta = (entry["body"].get("params") or {}).get("_meta") or {}
    assert meta["io.modelcontextprotocol/protocolVersion"] == PROTOCOL_MODERN
    assert meta["io.modelcontextprotocol/clientCapabilities"] == {}
    return entry


@pytest.mark.asyncio
@pytest.mark.parametrize("fake_server", ["legacy"], indirect=True)
async def test_auto_http_keeps_legacy_handshake(fake_server):
    """auto must not probe server/discover against a legacy-only fleet server."""
    url, requests = fake_server
    registry = MCPRegistry()

    await registry.connect_server(_cfg(url))

    state = registry.servers["fake"]
    assert state.status == ConnState.CONNECTED
    assert "mcp__fake__echo" in state.tools
    assert _methods(requests) == ["initialize", "notifications/initialized", "tools/list"]

    result = await state.tools["mcp__fake__echo"]({"text": "hi"})
    assert result.text == "echo:hi"
    await registry.disconnect_all()


@pytest.mark.asyncio
@pytest.mark.parametrize("fake_server", ["modern-only"], indirect=True)
async def test_auto_http_steps_up_to_modern_server(fake_server):
    """auto retries with discovery when initialize reports a modern-only server."""
    url, requests = fake_server
    registry = MCPRegistry()

    await registry.connect_server(_cfg(url))

    state = registry.servers["fake"]
    assert state.status == ConnState.CONNECTED
    assert _methods(requests)[:2] == ["initialize", "server/discover"]
    assert "mcp__fake__echo" in state.tools

    _assert_modern_envelope(requests, "server/discover")
    _assert_modern_envelope(requests, "tools/list")

    result = await state.tools["mcp__fake__echo"]({"text": "hi"})
    assert result.text == "echo:hi"
    call = _assert_modern_envelope(requests, "tools/call")
    assert call["headers"]["mcp-name"] == "echo"
    await registry.disconnect_all()


@pytest.mark.asyncio
@pytest.mark.parametrize("fake_server", ["legacy"], indirect=True)
async def test_http_headers_are_sent(fake_server):
    """Configured headers ride on every request, including the handshake."""
    url, requests = fake_server
    registry = MCPRegistry()
    cfg = _cfg(url)
    cfg.headers = {"X-Test-Header": "present"}

    await registry.connect_server(cfg)

    assert registry.servers["fake"].status == ConnState.CONNECTED
    assert all(entry["headers"]["x-test-header"] == "present" for entry in requests)
    await registry.disconnect_all()


@pytest.mark.asyncio
@pytest.mark.parametrize("fake_server", ["modern-only"], indirect=True)
async def test_explicit_modern_skips_legacy_initialize(fake_server):
    """An explicit 2026-07-28 pin starts with discovery."""
    url, requests = fake_server
    registry = MCPRegistry()

    await registry.connect_server(_cfg(url, protocol=PROTOCOL_MODERN))

    state = registry.servers["fake"]
    assert state.status == ConnState.CONNECTED
    assert _methods(requests)[0] == "server/discover"
    assert "initialize" not in _methods(requests)
    await registry.disconnect_all()


@pytest.mark.asyncio
@pytest.mark.parametrize("fake_server", ["modern-only"], indirect=True)
async def test_explicit_legacy_does_not_step_up(fake_server):
    """An explicit 2024-11-05 pin keeps the failed legacy verdict."""
    url, requests = fake_server
    registry = MCPRegistry()

    await registry.connect_server(_cfg(url, protocol=PROTOCOL_LEGACY))

    state = registry.servers["fake"]
    assert state.status == ConnState.FAILED
    assert "server/discover" not in _methods(requests)
    await registry.disconnect_all()


@pytest.mark.asyncio
async def test_auto_stdio_stays_legacy(monkeypatch):
    """auto on stdio keeps the initialize handshake without a modern probe."""
    import contextlib

    from decafclaw import mcp_client

    seen = {}

    @contextlib.asynccontextmanager
    async def fake_open(config, handler, *, modern):
        seen["modern"] = modern
        yield object()

    monkeypatch.setattr(mcp_client, "_open_stdio_session", fake_open)
    registry = MCPRegistry()
    cfg = MCPServerConfig(name="s", type="stdio", command="true")

    session = await registry._connect_stdio(contextlib.AsyncExitStack(), cfg)

    assert session is not None
    assert seen == {"modern": False}
