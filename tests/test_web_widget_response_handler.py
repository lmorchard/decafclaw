"""Tests for the websocket `widget_response` incoming handler + the
`_annotate_widget_responses` helper used on conv_history reload."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from decafclaw.web.websocket import (
    _annotate_widget_responses,
    _handle_widget_response,
)

_HIDDEN = {"effort", "model", "confirmation_request",
           "confirmation_response", "wake_trigger"}


def _ctx(manager):
    """Minimal state shape the handler reads from."""
    return {"manager": manager}


@pytest.mark.asyncio
async def test_widget_response_routes_to_manager():
    respond = AsyncMock()
    manager = SimpleNamespace(respond_to_confirmation=respond)
    ws_send = AsyncMock()
    msg = {
        "type": "widget_response",
        "conv_id": "conv-1",
        "confirmation_id": "cfx-1",
        "tool_call_id": "tc-1",
        "data": {"selected": "production"},
    }

    await _handle_widget_response(
        ws_send, index=None, username="alice",
        msg=msg, state=_ctx(manager))

    respond.assert_awaited_once_with(
        "conv-1", "cfx-1", approved=True,
        data={"selected": "production"})


@pytest.mark.asyncio
async def test_widget_response_missing_data_is_empty_dict():
    """If the client sends no `data` key, the handler still resolves
    the confirmation with an empty dict (approved=True)."""
    respond = AsyncMock()
    manager = SimpleNamespace(respond_to_confirmation=respond)
    msg = {
        "type": "widget_response",
        "conv_id": "conv-1",
        "confirmation_id": "cfx-1",
        # no data field
    }
    await _handle_widget_response(
        AsyncMock(), None, "alice", msg, _ctx(manager))
    respond.assert_awaited_once_with(
        "conv-1", "cfx-1", approved=True, data={})


@pytest.mark.asyncio
async def test_widget_response_missing_manager_noop(caplog):
    """Without a manager in state, the handler logs and drops."""
    msg = {
        "type": "widget_response",
        "conv_id": "conv-1",
        "confirmation_id": "cfx-1",
        "data": {"selected": "x"},
    }
    await _handle_widget_response(
        AsyncMock(), None, "alice", msg, {"manager": None})
    assert any("dropping" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_widget_response_non_dict_data_coerced(caplog):
    """A client sending data: [1,2,3] or similar shouldn't reach
    on_response callbacks as an unexpected type — the handler coerces
    to {} with a warning."""
    respond = AsyncMock()
    manager = SimpleNamespace(respond_to_confirmation=respond)
    msg = {
        "type": "widget_response",
        "conv_id": "conv-1",
        "confirmation_id": "cfx-1",
        "data": [1, 2, 3],  # not a dict
    }
    await _handle_widget_response(
        AsyncMock(), None, "alice", msg, _ctx(manager))
    respond.assert_awaited_once_with(
        "conv-1", "cfx-1", approved=True, data={})
    assert any("not a dict" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_widget_response_missing_confirmation_id_noop(caplog):
    respond = AsyncMock()
    manager = SimpleNamespace(respond_to_confirmation=respond)
    msg = {
        "type": "widget_response",
        "conv_id": "conv-1",
        # no confirmation_id
        "data": {"selected": "x"},
    }
    await _handle_widget_response(
        AsyncMock(), None, "alice", msg, _ctx(manager))
    respond.assert_not_called()
    assert any("dropping" in r.message for r in caplog.records)


# ---------- _annotate_widget_responses ----------


def test_annotate_pairs_widget_response_to_tool():
    """A full widget cycle in the archive: tool (with widget) →
    confirmation_request(widget_response) → confirmation_response(data)
    should flip the tool record to submitted + response."""
    archive = [
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "tc-1",
                         "function": {"name": "ask_user_multiple_choice", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "tc-1", "content": "[awaiting]",
         "widget": {"widget_type": "multiple_choice",
                    "target": "inline", "data": {}}},
        {"role": "confirmation_request",
         "confirmation_id": "cfx-1",
         "action_type": "widget_response",
         "tool_call_id": "tc-1"},
        {"role": "confirmation_response",
         "confirmation_id": "cfx-1",
         "approved": True,
         "data": {"selected": "production"}},
    ]
    out = _annotate_widget_responses(archive, _HIDDEN)
    # Confirmations stripped.
    roles = [m["role"] for m in out]
    assert "confirmation_request" not in roles
    assert "confirmation_response" not in roles
    # Tool record got submitted + response attached.
    tool = next(m for m in out if m["role"] == "tool")
    assert tool["submitted"] is True
    assert tool["response"] == {"selected": "production"}


def test_annotate_empty_data_still_marks_submitted():
    """The presence of a matching confirmation_response — even with
    empty/missing data — is the real submitted signal. Don't leave the
    widget looking live just because the payload was empty."""
    archive = [
        {"role": "tool", "tool_call_id": "tc-1", "content": "ok",
         "widget": {}},
        {"role": "confirmation_request",
         "confirmation_id": "cfx-1",
         "action_type": "widget_response",
         "tool_call_id": "tc-1"},
        {"role": "confirmation_response",
         "confirmation_id": "cfx-1",
         "approved": True,
         "data": {}},  # empty but present
    ]
    out = _annotate_widget_responses(archive, _HIDDEN)
    tool = next(m for m in out if m["role"] == "tool")
    assert tool["submitted"] is True
    assert tool["response"] == {}


def test_annotate_non_dict_data_coerced():
    """Defensive: if data is somehow a non-dict (corrupted archive),
    coerce to {} rather than propagating the type confusion."""
    archive = [
        {"role": "tool", "tool_call_id": "tc-1", "content": "ok",
         "widget": {}},
        {"role": "confirmation_request",
         "confirmation_id": "cfx-1",
         "action_type": "widget_response",
         "tool_call_id": "tc-1"},
        {"role": "confirmation_response",
         "confirmation_id": "cfx-1",
         "approved": True,
         "data": "not a dict"},
    ]
    out = _annotate_widget_responses(archive, _HIDDEN)
    tool = next(m for m in out if m["role"] == "tool")
    assert tool["submitted"] is True
    assert tool["response"] == {}


def test_annotate_pending_widget_no_response():
    """Widget request with no matching response stays unannotated —
    user can still submit live."""
    archive = [
        {"role": "tool", "tool_call_id": "tc-1", "content": "[awaiting]",
         "widget": {"widget_type": "multiple_choice",
                    "target": "inline", "data": {}}},
        {"role": "confirmation_request",
         "confirmation_id": "cfx-1",
         "action_type": "widget_response",
         "tool_call_id": "tc-1"},
    ]
    out = _annotate_widget_responses(archive, _HIDDEN)
    tool = next(m for m in out if m["role"] == "tool")
    assert "submitted" not in tool
    assert "response" not in tool


def test_annotate_ignores_non_widget_confirmation_responses():
    """A regular (non-widget) confirmation_response doesn't attach to
    any tool record — only action_type=widget_response triggers the
    pairing."""
    archive = [
        {"role": "tool", "tool_call_id": "tc-1", "content": "executed"},
        {"role": "confirmation_request",
         "confirmation_id": "cfx-1",
         "action_type": "run_shell_command",  # not widget_response
         "tool_call_id": "tc-1"},
        {"role": "confirmation_response",
         "confirmation_id": "cfx-1",
         "approved": True},
    ]
    out = _annotate_widget_responses(archive, _HIDDEN)
    tool = next(m for m in out if m["role"] == "tool")
    assert "submitted" not in tool


def test_annotate_returns_visible_list_does_not_mutate_input():
    """Input messages list isn't modified; returned list has hidden
    roles stripped and a dict copy for annotated tool records."""
    archive = [
        {"role": "tool", "tool_call_id": "tc-1", "content": "ok",
         "widget": {}},
        {"role": "confirmation_request",
         "confirmation_id": "cfx-1",
         "action_type": "widget_response",
         "tool_call_id": "tc-1"},
        {"role": "confirmation_response",
         "confirmation_id": "cfx-1",
         "data": {"selected": "x"}},
    ]
    original_tool = archive[0]
    out = _annotate_widget_responses(archive, _HIDDEN)
    # Original not mutated.
    assert "submitted" not in original_tool
    # Output tool is a different dict.
    assert out[0] is not original_tool
    assert out[0]["submitted"] is True
