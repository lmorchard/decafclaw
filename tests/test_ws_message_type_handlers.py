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
from pathlib import Path

import decafclaw.web
from decafclaw.web.websocket import _HANDLERS

MANIFEST_PATH = Path(decafclaw.web.__file__).resolve().parent / "message_types.json"


def _client_to_server_types() -> set[str]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {
        name
        for name, spec in manifest["messages"].items()
        if spec["direction"] == "client_to_server"
    }


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
