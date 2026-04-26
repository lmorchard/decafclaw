"""Tests for WidgetInputPause + default WIDGET_RESPONSE recovery handler."""

import pytest

from decafclaw.archive import read_archive
from decafclaw.confirmations import (
    ConfirmationAction,
    ConfirmationRegistry,
    ConfirmationRequest,
    ConfirmationResponse,
)
from decafclaw.conversation_manager import _RecoveryContext
from decafclaw.media import WidgetInputPause
from decafclaw.widget_input import (
    WidgetResponseHandler,
    pending_callbacks,
    register_widget_handler,
)


def test_widget_input_pause_construction():
    p = WidgetInputPause(
        tool_call_id="tc-1",
        widget_payload={"widget_type": "multiple_choice",
                        "target": "inline",
                        "data": {"prompt": "?", "options": []}})
    assert p.tool_call_id == "tc-1"
    assert p.widget_payload["widget_type"] == "multiple_choice"


def test_register_widget_handler():
    registry = ConfirmationRegistry()
    register_widget_handler(registry)
    handler = registry.get_handler(ConfirmationAction.WIDGET_RESPONSE)
    assert isinstance(handler, WidgetResponseHandler)


@pytest.mark.asyncio
async def test_recovery_handler_with_callback_writes_user_message(
        config, tmp_path, monkeypatch):
    """When a callback is registered for a pending widget, the recovery
    handler invokes it and writes the returned string to the archive."""
    monkeypatch.setattr("decafclaw.widget_input.pending_callbacks",
                        {"tc-1": lambda data: f"Picked: {data['selected']}"})
    handler = WidgetResponseHandler()
    request = ConfirmationRequest(
        action_type=ConfirmationAction.WIDGET_RESPONSE,
        action_data={"widget_type": "multiple_choice"},
        tool_call_id="tc-1",
    )
    response = ConfirmationResponse(
        confirmation_id=request.confirmation_id,
        approved=True,
        data={"selected": "production"},
    )
    ctx = _RecoveryContext(config=config, conv_id="conv-recovery")

    result = await handler.on_approve(ctx, request, response)

    assert result["inject_message"] == "Picked: production"

    archived = read_archive(config, "conv-recovery")
    user_msgs = [m for m in archived
                 if m.get("role") == "user"
                 and m.get("source") == "widget_response"]
    assert len(user_msgs) == 1
    assert user_msgs[0]["content"] == "Picked: production"


@pytest.mark.asyncio
async def test_recovery_handler_without_callback_uses_default(
        config, monkeypatch):
    """No registered callback → handler writes a default 'User responded
    with: X' message."""
    monkeypatch.setattr("decafclaw.widget_input.pending_callbacks", {})
    handler = WidgetResponseHandler()
    request = ConfirmationRequest(
        action_type=ConfirmationAction.WIDGET_RESPONSE,
        action_data={"widget_type": "multiple_choice"},
        tool_call_id="tc-default",
    )
    response = ConfirmationResponse(
        confirmation_id=request.confirmation_id,
        approved=True,
        data={"selected": "staging"},
    )
    ctx = _RecoveryContext(config=config, conv_id="conv-default")

    result = await handler.on_approve(ctx, request, response)

    assert "User responded with" in result["inject_message"]
    assert "staging" in result["inject_message"]

    archived = read_archive(config, "conv-default")
    user_msgs = [m for m in archived if m.get("role") == "user"]
    assert len(user_msgs) == 1
    assert "staging" in user_msgs[0]["content"]


@pytest.mark.asyncio
async def test_recovery_handler_callback_raises_falls_back_to_default(
        config, monkeypatch, caplog):
    def boom(_data):
        raise RuntimeError("nope")

    monkeypatch.setattr("decafclaw.widget_input.pending_callbacks",
                        {"tc-boom": boom})
    handler = WidgetResponseHandler()
    request = ConfirmationRequest(
        action_type=ConfirmationAction.WIDGET_RESPONSE,
        tool_call_id="tc-boom",
    )
    response = ConfirmationResponse(
        confirmation_id=request.confirmation_id,
        approved=True,
        data={"selected": "x"},
    )
    ctx = _RecoveryContext(config=config, conv_id="conv-boom")

    result = await handler.on_approve(ctx, request, response)
    assert "User responded with" in result["inject_message"]
    assert any("callback raised" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_recovery_handler_consumes_callback(config, monkeypatch):
    """After handling, the callback is popped from the map (so it can't
    fire twice)."""
    called = {"count": 0}

    def cb(_data):
        called["count"] += 1
        return "one-shot"

    cb_map = {"tc-pop": cb}
    monkeypatch.setattr("decafclaw.widget_input.pending_callbacks", cb_map)
    handler = WidgetResponseHandler()
    request = ConfirmationRequest(
        action_type=ConfirmationAction.WIDGET_RESPONSE,
        tool_call_id="tc-pop",
    )
    response = ConfirmationResponse(
        confirmation_id=request.confirmation_id,
        approved=True,
        data={},
    )
    ctx = _RecoveryContext(config=config, conv_id="conv-pop")

    await handler.on_approve(ctx, request, response)
    assert called["count"] == 1
    assert "tc-pop" not in cb_map


@pytest.mark.asyncio
async def test_recovery_via_manager_dispatches_to_handler(config):
    """Go through the manager's recover_confirmation dispatch path end
    to end: register handler, seed a pending confirmation, respond."""
    from decafclaw.conversation_manager import ConversationManager
    from decafclaw.events import EventBus

    bus = EventBus()
    manager = ConversationManager(config, bus)
    register_widget_handler(manager.confirmation_registry)

    conv_id = "conv-manager-recovery"
    state = manager._get_or_create(conv_id)
    state.pending_confirmation = ConfirmationRequest(
        action_type=ConfirmationAction.WIDGET_RESPONSE,
        action_data={"widget_type": "multiple_choice"},
        tool_call_id="tc-mgr",
        confirmation_id="cfx-mgr",
    )
    # No confirmation_event → manager will dispatch recovery.

    await manager.respond_to_confirmation(
        conv_id, "cfx-mgr", approved=True,
        data={"selected": "yes"})

    archived = read_archive(config, conv_id)
    user_msgs = [m for m in archived
                 if m.get("role") == "user"
                 and m.get("source") == "widget_response"]
    assert len(user_msgs) == 1
    assert "yes" in user_msgs[0]["content"]
