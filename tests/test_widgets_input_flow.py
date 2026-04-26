"""Integration tests for the full input-widget pause/resume flow.

Exercises _handle_widget_input_pause with a real ConversationManager,
verifying the round-trip from WidgetInputPause → request_confirmation
→ respond_to_confirmation → on_response callback → inject-string.
"""

import asyncio
from types import SimpleNamespace

import pytest

from decafclaw import widget_input as widget_input_module
from decafclaw.agent import _handle_widget_input_pause
from decafclaw.conversation_manager import ConversationManager
from decafclaw.events import EventBus
from decafclaw.media import WidgetInputPause
from decafclaw.widget_input import register_widget_handler


def _make_manager(config):
    bus = EventBus()
    manager = ConversationManager(config, bus)
    register_widget_handler(manager.confirmation_registry)
    return manager, bus


def _make_ctx(manager, conv_id):
    """Build a minimal ctx that has the manager-bound
    request_confirmation closure, mirroring how the ConversationManager
    wires it in production — including the ``ctx.manager`` reference
    that the cancel/cleanup path reads. ``cancelled`` defaults to None
    to match the real ``Context`` initial state."""
    async def request_confirmation(request):
        return await manager.request_confirmation(conv_id, request)

    return SimpleNamespace(
        request_confirmation=request_confirmation,
        cancelled=None,
        manager=manager,
        conv_id=conv_id,
    )


@pytest.mark.asyncio
async def test_live_pause_invokes_callback_and_injects(
        config, monkeypatch):
    """With a callback registered, the live path awaits the submit,
    calls the callback with the response data, and returns the
    callback's inject-string."""
    manager, _bus = _make_manager(config)
    conv_id = "conv-live-cb"
    manager._get_or_create(conv_id)

    # Register a callback that captures its arg + formats an answer.
    seen: list[dict] = []

    def on_response(data: dict) -> str:
        seen.append(data)
        return f"You picked {data['selected']}!"

    monkeypatch.setattr(widget_input_module, "pending_callbacks",
                        {"tc-1": on_response})

    signal = WidgetInputPause(
        tool_call_id="tc-1",
        widget_payload={
            "widget_type": "multiple_choice",
            "target": "inline",
            "data": {"prompt": "which?", "options": []},
        })

    ctx = _make_ctx(manager, conv_id)

    # Resolve the confirmation shortly after the pause begins.
    async def respond_soon():
        await asyncio.sleep(0.05)
        # Resolver needs the confirmation_id; read from state.
        state = manager.get_state(conv_id)
        assert state.pending_confirmation is not None
        await manager.respond_to_confirmation(
            conv_id, state.pending_confirmation.confirmation_id,
            approved=True, data={"selected": "red"})

    asyncio.create_task(respond_soon())
    inject = await _handle_widget_input_pause(ctx, signal)

    assert inject == "You picked red!"
    assert seen == [{"selected": "red"}]
    # Callback was popped.
    assert "tc-1" not in widget_input_module.pending_callbacks
    # Pending confirmation cleared on the manager side.
    assert manager.get_state(conv_id).pending_confirmation is None


@pytest.mark.asyncio
async def test_live_pause_without_callback_uses_default(
        config, monkeypatch):
    """No callback registered → default inject ('User responded with:')."""
    manager, _bus = _make_manager(config)
    conv_id = "conv-live-default"
    manager._get_or_create(conv_id)

    monkeypatch.setattr(widget_input_module, "pending_callbacks", {})

    signal = WidgetInputPause(
        tool_call_id="tc-no-cb",
        widget_payload={
            "widget_type": "multiple_choice",
            "target": "inline",
            "data": {"prompt": "which?", "options": []},
        })
    ctx = _make_ctx(manager, conv_id)

    async def respond_soon():
        await asyncio.sleep(0.05)
        state = manager.get_state(conv_id)
        await manager.respond_to_confirmation(
            conv_id, state.pending_confirmation.confirmation_id,
            approved=True, data={"selected": "blue"})

    asyncio.create_task(respond_soon())
    inject = await _handle_widget_input_pause(ctx, signal)

    assert "User responded with" in inject
    assert "blue" in inject


@pytest.mark.asyncio
async def test_live_pause_callback_exception_falls_back_to_default(
        config, monkeypatch, caplog):
    manager, _bus = _make_manager(config)
    conv_id = "conv-live-raises"
    manager._get_or_create(conv_id)

    def raising_cb(_data):
        raise RuntimeError("oops")

    monkeypatch.setattr(widget_input_module, "pending_callbacks",
                        {"tc-raises": raising_cb})

    signal = WidgetInputPause(
        tool_call_id="tc-raises",
        widget_payload={
            "widget_type": "multiple_choice",
            "target": "inline",
            "data": {"prompt": "?", "options": []},
        })
    ctx = _make_ctx(manager, conv_id)

    async def respond_soon():
        await asyncio.sleep(0.05)
        state = manager.get_state(conv_id)
        await manager.respond_to_confirmation(
            conv_id, state.pending_confirmation.confirmation_id,
            approved=True, data={"selected": "x"})

    asyncio.create_task(respond_soon())
    inject = await _handle_widget_input_pause(ctx, signal)

    assert "User responded with" in inject
    assert any("callback raised" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_ctx_without_request_confirmation_ends_turn(caplog):
    """If ctx doesn't have request_confirmation (e.g., manager not
    wired), the pause logic logs + returns None so the outer loop
    ends the turn gracefully."""
    signal = WidgetInputPause(
        tool_call_id="tc-no-mgr",
        widget_payload={})
    ctx = SimpleNamespace(request_confirmation=None)  # mirrors Context default

    inject = await _handle_widget_input_pause(ctx, signal)
    assert inject is None
    assert any("no request_confirmation" in r.message
               for r in caplog.records)


@pytest.mark.asyncio
async def test_cancel_during_pause_unblocks_loop(
        config, monkeypatch):
    """User cancels the turn while the widget is pending → the await
    races against ctx.cancelled and returns None, so the outer loop
    can end the turn cleanly. The pending confirmation is cleared in
    the manager so reload doesn't show a stale pending widget."""
    manager, _bus = _make_manager(config)
    conv_id = "conv-cancel-during-pause"
    state = manager._get_or_create(conv_id)
    state.cancel_event = asyncio.Event()

    cb_marker = {"called": False}

    def on_response(_data):
        cb_marker["called"] = True
        return "should not happen"

    monkeypatch.setattr(widget_input_module, "pending_callbacks",
                        {"tc-cancel": on_response})

    signal = WidgetInputPause(
        tool_call_id="tc-cancel",
        widget_payload={
            "widget_type": "multiple_choice",
            "target": "inline",
            "data": {"prompt": "?", "options": []},
        })

    # Build a ctx with the cancel event + manager reference so the
    # helper can clear the pending confirmation on cancel.
    async def request_confirmation(request):
        return await manager.request_confirmation(conv_id, request)

    ctx = SimpleNamespace(
        request_confirmation=request_confirmation,
        cancelled=state.cancel_event,
        manager=manager,
        conv_id=conv_id,
    )

    # Fire the cancel after the pause begins.
    async def cancel_soon():
        await asyncio.sleep(0.05)
        state.cancel_event.set()

    asyncio.create_task(cancel_soon())
    inject = await _handle_widget_input_pause(ctx, signal)

    assert inject is None
    assert cb_marker["called"] is False
    # Callback was cleared, manager state cleared.
    assert "tc-cancel" not in widget_input_module.pending_callbacks
    assert manager.get_state(conv_id).pending_confirmation is None


@pytest.mark.asyncio
async def test_live_pause_clears_callback_even_on_exception(
        config, monkeypatch):
    """If request_confirmation raises, the try/finally still clears
    the pending callback entry."""
    manager, _bus = _make_manager(config)
    conv_id = "conv-live-raise-await"
    manager._get_or_create(conv_id)

    monkeypatch.setattr(widget_input_module, "pending_callbacks",
                        {"tc-leak": lambda d: "unreached"})

    class _FailingCtx:
        cancelled = None

        async def request_confirmation(self, _req):
            raise asyncio.CancelledError()

    signal = WidgetInputPause(
        tool_call_id="tc-leak",
        widget_payload={})

    with pytest.raises(asyncio.CancelledError):
        await _handle_widget_input_pause(_FailingCtx(), signal)

    assert "tc-leak" not in widget_input_module.pending_callbacks
