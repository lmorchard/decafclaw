"""Tests for the sticky_set/sticky_clear WS forwarder in websocket.py."""

import pytest

from decafclaw.web import websocket as ws_mod
from decafclaw.web.message_types import WSMessageType


@pytest.mark.asyncio
async def test_sticky_set_event_forwarded():
    sent = []

    async def ws_send(payload):
        sent.append(payload)

    state = {"ws_send": ws_send, "config": None}
    callback = ws_mod._make_sticky_forwarder(state, conv_id="conv-x")
    await callback({
        "type": "sticky_set", "conv_id": "conv-x",
        "widget_type": "markdown_document", "data": {"content": "# hi"},
    })
    assert sent[0]["type"] == WSMessageType.STICKY_SET
    assert sent[0]["conv_id"] == "conv-x"
    assert sent[0]["widget_type"] == "markdown_document"
    assert sent[0]["data"] == {"content": "# hi"}


@pytest.mark.asyncio
async def test_sticky_clear_event_forwarded():
    sent = []

    async def ws_send(payload):
        sent.append(payload)

    state = {"ws_send": ws_send, "config": None}
    callback = ws_mod._make_sticky_forwarder(state, conv_id="conv-x")
    await callback({"type": "sticky_clear", "conv_id": "conv-x"})
    assert sent[0]["type"] == WSMessageType.STICKY_CLEAR
    assert sent[0]["conv_id"] == "conv-x"


@pytest.mark.asyncio
async def test_sticky_event_skipped_for_other_conv():
    sent = []

    async def ws_send(payload):
        sent.append(payload)

    state = {"ws_send": ws_send, "config": None}
    callback = ws_mod._make_sticky_forwarder(state, conv_id="conv-x")
    await callback({
        "type": "sticky_set", "conv_id": "other",
        "widget_type": "x", "data": {},
    })
    assert sent == []


@pytest.mark.asyncio
async def test_sticky_event_ignores_unrelated_type():
    sent = []

    async def ws_send(payload):
        sent.append(payload)

    state = {"ws_send": ws_send, "config": None}
    callback = ws_mod._make_sticky_forwarder(state, conv_id="conv-x")
    await callback({"type": "canvas_update", "conv_id": "conv-x"})
    assert sent == []
