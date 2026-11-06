"""Tests for the WebSocket CANCEL_TURN handler (#1115 subagent cancel)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.web.message_types import WSMessageType
from decafclaw.web.websocket import _handle_cancel_turn


def _index_with(convs: dict):
    index = MagicMock()
    index.get = lambda conv_id: convs.get(conv_id)
    return index


@pytest.mark.asyncio
async def test_cancel_delegated_child_requires_parent_ownership():
    """Cancelling a `{parent}--child-{hex}` conversation requires the parent."""
    parent_meta = MagicMock(user_id="alice")
    index = _index_with({"web-alice-root": parent_meta})
    ws_send = AsyncMock()
    manager = MagicMock()
    manager.cancel_turn = AsyncMock()
    state = {"manager": manager}

    # Another user's child: refused, no cancel.
    await _handle_cancel_turn(ws_send, index, "mallory", {"conv_id": "web-alice-root--child-abcdef12"}, state)
    manager.cancel_turn.assert_not_awaited()
    ws_send.assert_awaited_once_with({"type": WSMessageType.ERROR, "message": "Conversation not found"})

    # The owner's child: cancel proceeds.
    ws_send.reset_mock()
    await _handle_cancel_turn(ws_send, index, "alice", {"conv_id": "web-alice-root--child-abcdef12"}, state)
    manager.cancel_turn.assert_awaited_once_with("web-alice-root--child-abcdef12")
    ws_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_child_of_unknown_root_is_refused():
    """A child whose parent has no index entry is refused (not attributable)."""
    index = _index_with({})
    ws_send = AsyncMock()
    manager = MagicMock()
    manager.cancel_turn = AsyncMock()
    state = {"manager": manager}

    await _handle_cancel_turn(ws_send, index, "alice", {"conv_id": "web-alice-root--child-abcdef12"}, state)
    manager.cancel_turn.assert_not_awaited()
    ws_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_non_child_keeps_preexisting_unguarded_behavior():
    """System conversations keep the pre-existing no-ownership-check behavior.

    The broader ownership hole for non-child conv ids is pre-existing and
    tracked for follow-up; this pins the current scope of the child check.
    """
    index = _index_with({})  # no index entry for the conv id at all
    ws_send = AsyncMock()
    manager = MagicMock()
    manager.cancel_turn = AsyncMock()
    state = {"manager": manager}

    await _handle_cancel_turn(ws_send, index, "alice", {"conv_id": "some-system-conv"}, state)
    manager.cancel_turn.assert_awaited_once_with("some-system-conv")
    ws_send.assert_not_awaited()
