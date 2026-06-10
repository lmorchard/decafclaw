"""Tests for _handle_confirm_response data forwarding.

Verifies that a confirm_response message carrying a `data` dict is
forwarded as-is to manager.respond_to_confirmation, which is the
mechanism the workflow_user_input affordance relies on to deliver the
user's typed answer to the backend handler.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.web.websocket import _handle_confirm_response


@pytest.fixture
def ws_send():
    return AsyncMock()


@pytest.fixture
def ws_state():
    """Minimal WebSocket handler state with a mock manager."""
    manager = MagicMock()
    manager.respond_to_confirmation = AsyncMock()
    return {
        "config": MagicMock(),
        "event_bus": MagicMock(),
        "manager": manager,
    }


@pytest.fixture
def index():
    return MagicMock()


class TestConfirmResponseDataForwarding:
    @pytest.mark.asyncio
    async def test_data_forwarded_to_manager(self, ws_send, index, ws_state):
        """data dict rides through _handle_confirm_response to respond_to_confirmation."""
        msg = {
            "type": "confirm_response",
            "conv_id": "conv-1",
            "confirmation_id": "cfm-abc",
            "approved": True,
            "always": False,
            "add_pattern": False,
            "data": {"value": "tide pools"},
        }

        await _handle_confirm_response(ws_send, index, "testuser", msg, ws_state)

        ws_state["manager"].respond_to_confirmation.assert_awaited_once_with(
            "conv-1",
            "cfm-abc",
            approved=True,
            always=False,
            add_pattern=False,
            data={"value": "tide pools"},
        )

    @pytest.mark.asyncio
    async def test_no_data_passes_none(self, ws_send, index, ws_state):
        """When no data field is present, data=None is forwarded (not missing kwarg)."""
        msg = {
            "type": "confirm_response",
            "conv_id": "conv-1",
            "confirmation_id": "cfm-xyz",
            "approved": True,
            "always": False,
            "add_pattern": False,
        }

        await _handle_confirm_response(ws_send, index, "testuser", msg, ws_state)

        ws_state["manager"].respond_to_confirmation.assert_awaited_once_with(
            "conv-1",
            "cfm-xyz",
            approved=True,
            always=False,
            add_pattern=False,
            data=None,
        )

    @pytest.mark.asyncio
    async def test_non_dict_data_coerced_to_none(self, ws_send, index, ws_state):
        """A non-dict data value (e.g. a string) is coerced to None, not forwarded raw."""
        msg = {
            "type": "confirm_response",
            "conv_id": "conv-1",
            "confirmation_id": "cfm-bad",
            "approved": True,
            "always": False,
            "add_pattern": False,
            "data": "not-a-dict",
        }

        await _handle_confirm_response(ws_send, index, "testuser", msg, ws_state)

        ws_state["manager"].respond_to_confirmation.assert_awaited_once_with(
            "conv-1",
            "cfm-bad",
            approved=True,
            always=False,
            add_pattern=False,
            data=None,
        )
