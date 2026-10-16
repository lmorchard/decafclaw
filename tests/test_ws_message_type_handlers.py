"""Every declared client→server wire type must be dispatchable.

`tests/test_message_types.py::test_all_handler_keys_are_known_types` guards one
direction — no handler keyed on a type the manifest never declared. This guards
the other: no `client_to_server` entry in the manifest without a handler in
`_HANDLERS`.

Declaring the type is the visible half of adding one. It regenerates the enum
and the JS constants, satisfies `make check-message-types`, and satisfies any
client-side assertion that a message's `type` is a registered client→server
type. Wiring the handler is the half nothing checks: `_dispatch` falls through
to `ws: unknown inbound message type` and returns an error frame, so the client
sends a well-formed message that the server drops. A green board and a wire
type that does nothing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

import decafclaw.web
from decafclaw.web.message_types import WSMessageType
from decafclaw.web.websocket import _HANDLERS, _make_ws_send

MANIFEST_PATH = Path(decafclaw.web.__file__).resolve().parent / "message_types.json"


def _client_to_server_types() -> set[str]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {name for name, spec in manifest["messages"].items() if spec["direction"] == "client_to_server"}


def test_manifest_declares_client_to_server_types() -> None:
    """Guard the guard: an empty expected set would make the test below vacuous."""
    assert MANIFEST_PATH.is_file(), f"manifest not found at {MANIFEST_PATH}"
    assert _client_to_server_types(), (
        f"{MANIFEST_PATH} declares no client_to_server messages — either the "
        "manifest shape changed or the `direction` key was renamed"
    )


def test_every_client_to_server_type_has_a_handler() -> None:
    expected = _client_to_server_types()
    # `_HANDLERS` is keyed on `WSMessageType` (a StrEnum). Normalise to the wire
    # strings the manifest uses so the diff below reads in manifest terms.
    handled = {str(key) for key in _HANDLERS}
    missing = sorted(expected - handled)
    assert not missing, (
        "client→server message type(s) declared in "
        f"{MANIFEST_PATH.name} with no entry in websocket._HANDLERS: "
        f"{', '.join(missing)}. The server will answer these with "
        "'unknown inbound message type'. Add a `_handle_*` coroutine and "
        "register it in `_HANDLERS`."
    )


# -- ws_send exception handling (#164) -----------------------------------------
#
# Issue #164 names this file as the home for the ws_send unit tests. The
# per-connection `ws_send` is built by `_make_ws_send`; the tests below drive
# it with minimal fake WebSockets whose `send_json` raises the failure under
# test.


class _RaisingWebSocket:
    """Fake WebSocket whose send_json raises a fixed exception, like the real
    transport does when the client disappears mid-send."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def send_json(self, msg, **kwargs) -> None:
        raise self._exc


class _SerializingWebSocket:
    """Fake WebSocket that serializes the payload with `json.dumps`, exactly
    like starlette's `WebSocket.send_json` — so a non-serializable payload
    raises a real TypeError instead of a faked one."""

    async def send_json(self, msg, **kwargs) -> None:
        json.dumps(msg)


def _ws_send_records(caplog) -> list:
    return [r for r in caplog.records if r.name == "decafclaw.web.websocket"]


@pytest.mark.asyncio
async def test_ws_send_logs_debug_on_websocket_disconnect(caplog) -> None:
    """A WebSocketDisconnect from send_json is an expected client
    disconnect: logged at DEBUG, never as an error (#164)."""
    ws = _RaisingWebSocket(WebSocketDisconnect())
    with caplog.at_level(logging.DEBUG, logger="decafclaw.web.websocket"):
        await _make_ws_send(ws)({"type": WSMessageType.ERROR, "message": "test"})

    records = _ws_send_records(caplog)
    assert any(r.levelno == logging.DEBUG and "client disconnected" in r.getMessage() for r in records)
    assert not [r for r in records if r.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_ws_send_logs_debug_on_runtime_error_disconnect(caplog) -> None:
    """A RuntimeError from send_json (transport closed mid-send) is treated
    as a client disconnect: DEBUG, not ERROR (#164)."""
    ws = _RaisingWebSocket(RuntimeError("connection closed"))
    with caplog.at_level(logging.DEBUG, logger="decafclaw.web.websocket"):
        await _make_ws_send(ws)({"type": WSMessageType.ERROR, "message": "test"})

    records = _ws_send_records(caplog)
    assert any(r.levelno == logging.DEBUG and "client disconnected" in r.getMessage() for r in records)
    assert not [r for r in records if r.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_ws_send_logs_error_with_traceback_on_serialization_failure(caplog) -> None:
    """A payload that cannot be serialized is a server-side bug, not a client
    disconnect: logged at ERROR with a full traceback (#164). Before the fix,
    the broad `except Exception` here reported it as a likely client
    disconnect at DEBUG and hid the bug."""
    ws = _SerializingWebSocket()
    with caplog.at_level(logging.DEBUG, logger="decafclaw.web.websocket"):
        await _make_ws_send(ws)({"type": WSMessageType.ERROR, "message": {1, 2, 3}})

    errors = [r for r in _ws_send_records(caplog) if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "serialize" in errors[0].getMessage()
    # The record must carry the traceback (exc_info), not just the message.
    assert errors[0].exc_info is not None
    assert "not JSON serializable" in caplog.text
