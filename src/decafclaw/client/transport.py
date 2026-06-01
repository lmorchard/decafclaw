"""Thin network transport for the decafclaw client.

Wraps the websockets client and an httpx call to POST /api/conversations,
using the same cookie auth the browser and TUI use
(`Cookie: decafclaw_session=<token>`). Exposes a minimal interface that the
orchestrator in run.py drives: `connect()`, `create_conversation()`, `send()`,
`events()` (async iterator of parsed dicts), and `close()`.

Network failures are surfaced as TransportError so the orchestrator can map
them to exit code 4.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import websockets
from websockets.asyncio.client import ClientConnection


class TransportError(Exception):
    """Connection / auth / protocol failure talking to the server."""


class WSTransport:
    def __init__(self, host: str, token: str) -> None:
        self._host = host.rstrip("/")
        self._token = token
        self._ws_url = self._host.replace("http", "ws", 1) + "/ws/chat"
        self._ws: ClientConnection | None = None

    async def connect(self) -> None:
        try:
            self._ws = await websockets.connect(
                self._ws_url,
                additional_headers={
                    "Cookie": f"decafclaw_session={self._token}"},
            )
        except Exception as exc:
            raise TransportError(f"WebSocket connect failed: {exc}") from exc

    async def create_conversation(self, title: str = "decafclaw-client") -> str:
        url = f"{self._host}/api/conversations"
        cookies = {"decafclaw_session": self._token}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json={"title": title},
                                         cookies=cookies, timeout=30.0)
        except Exception as exc:
            raise TransportError(f"create conversation failed: {exc}") from exc
        if resp.status_code != 201:
            raise TransportError(
                f"create conversation: HTTP {resp.status_code} {resp.text}")
        conv_id = resp.json().get("conv_id", "")
        if not conv_id:
            raise TransportError("create conversation: no conv_id in response")
        return conv_id

    async def send(self, msg: dict) -> None:
        if self._ws is None:
            raise TransportError("send before connect")
        await self._ws.send(json.dumps(msg))

    async def events(self) -> AsyncIterator[dict]:
        if self._ws is None:
            raise TransportError("events before connect")
        try:
            async for raw in self._ws:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict) and isinstance(parsed.get("type"), str):
                    yield parsed
        except websockets.ConnectionClosed:
            return  # socket dropped; the orchestrator treats this as a disconnect

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
