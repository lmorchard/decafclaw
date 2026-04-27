"""Tests for canvas REST endpoints and WebSocket event projection."""

import pytest

from decafclaw.web import websocket as ws_mod


@pytest.mark.asyncio
async def test_canvas_update_event_projected_to_client():
    """The on_conv_event callback forwards canvas_update events to the WS."""
    sent = []

    async def ws_send(payload):
        sent.append(payload)

    state = {"ws_send": ws_send, "config": None}
    callback = ws_mod._make_canvas_update_forwarder(state, conv_id="conv-x")

    await callback({
        "type": "canvas_update",
        "conv_id": "conv-x",
        "kind": "set",
        "active_tab": "canvas_1",
        "tab": {"id": "canvas_1", "label": "L",
                "widget_type": "markdown_document",
                "data": {"content": "x"}},
    })

    assert sent == [{
        "type": "canvas_update",
        "conv_id": "conv-x",
        "kind": "set",
        "active_tab": "canvas_1",
        "tab": {"id": "canvas_1", "label": "L",
                "widget_type": "markdown_document",
                "data": {"content": "x"}},
    }]


@pytest.mark.asyncio
async def test_canvas_update_event_skipped_for_other_conv():
    """A canvas_update for a different conv_id is ignored by this socket."""
    sent = []

    async def ws_send(payload):
        sent.append(payload)

    state = {"ws_send": ws_send, "config": None}
    callback = ws_mod._make_canvas_update_forwarder(state, conv_id="conv-x")
    await callback({"type": "canvas_update", "conv_id": "OTHER",
                    "kind": "set", "active_tab": None, "tab": None})
    assert sent == []
