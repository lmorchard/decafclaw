"""Tests for the conversation manager."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from decafclaw.confirmations import (
    ConfirmationAction,
    ConfirmationRegistry,
    ConfirmationRequest,
    ConfirmationResponse,
)
from decafclaw.conversation_manager import ConversationManager, ConversationState
from decafclaw.events import EventBus


@pytest.fixture
def manager(config):
    bus = EventBus()
    return ConversationManager(config, bus)


# -- State management ---------------------------------------------------------

def test_get_or_create(manager):
    state = manager._get_or_create("conv-1")
    assert state.conv_id == "conv-1"
    assert state is manager._get_or_create("conv-1")  # same instance


def test_get_state_returns_none_for_unknown(manager):
    assert manager.get_state("nonexistent") is None


# -- Subscription --------------------------------------------------------------

def test_subscribe_and_unsubscribe(manager):
    cb = MagicMock()
    sub_id = manager.subscribe("conv-1", cb)
    state = manager.get_state("conv-1")
    assert state is not None
    assert sub_id in state.subscribers

    manager.unsubscribe("conv-1", sub_id)
    assert sub_id not in state.subscribers


@pytest.mark.asyncio
async def test_emit_calls_subscribers(manager):
    received = []

    async def cb(event):
        received.append(event)

    manager.subscribe("conv-1", cb)
    await manager.emit("conv-1", {"type": "test", "data": 42})

    assert len(received) == 1
    assert received[0]["type"] == "test"
    assert received[0]["conv_id"] == "conv-1"


@pytest.mark.asyncio
async def test_emit_subscriber_error_doesnt_break_others(manager):
    received = []

    def bad_cb(event):
        raise RuntimeError("boom")

    async def good_cb(event):
        received.append(event)

    manager.subscribe("conv-1", bad_cb)
    manager.subscribe("conv-1", good_cb)
    await manager.emit("conv-1", {"type": "test"})

    assert len(received) == 1  # good_cb still called


# -- History -------------------------------------------------------------------

def test_load_history_empty(manager):
    history = manager.load_history("new-conv")
    assert history == []


def test_load_history_cached(manager):
    state = manager._get_or_create("conv-1")
    state.history = [{"role": "user", "content": "hello"}]
    assert manager.load_history("conv-1") == state.history


# -- Confirmation request/response --------------------------------------------

@pytest.mark.asyncio
async def test_request_confirmation_approved(manager):
    conv_id = "conv-1"
    manager._get_or_create(conv_id)

    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="Allow shell command?",
        timeout=2.0,
    )

    # Approve after a short delay
    async def approve():
        await asyncio.sleep(0.05)
        await manager.respond_to_confirmation(
            conv_id, request.confirmation_id, approved=True)

    asyncio.create_task(approve())
    response = await manager.request_confirmation(conv_id, request)

    assert response.approved is True
    assert response.confirmation_id == request.confirmation_id
    # Pending state should be cleared
    state = manager.get_state(conv_id)
    assert state.pending_confirmation is None


@pytest.mark.asyncio
async def test_request_confirmation_denied(manager):
    conv_id = "conv-1"
    manager._get_or_create(conv_id)

    request = ConfirmationRequest(
        action_type=ConfirmationAction.ACTIVATE_SKILL,
        action_data={"skill_name": "test"},
        message="Activate skill?",
        timeout=2.0,
    )

    async def deny():
        await asyncio.sleep(0.05)
        await manager.respond_to_confirmation(
            conv_id, request.confirmation_id, approved=False)

    asyncio.create_task(deny())
    response = await manager.request_confirmation(conv_id, request)

    assert response.approved is False


@pytest.mark.asyncio
async def test_request_confirmation_timeout(manager):
    conv_id = "conv-1"
    manager._get_or_create(conv_id)

    request = ConfirmationRequest(
        action_type=ConfirmationAction.CONTINUE_TURN,
        message="Continue?",
        timeout=0.1,
    )

    response = await manager.request_confirmation(conv_id, request)
    assert response.approved is False


@pytest.mark.asyncio
async def test_confirmation_emits_request_event(manager):
    conv_id = "conv-1"
    events = []

    async def cb(event):
        events.append(event)

    manager.subscribe(conv_id, cb)

    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="Allow?",
        timeout=0.1,
    )

    await manager.request_confirmation(conv_id, request)

    # Should have emitted confirmation_request
    req_events = [e for e in events if e["type"] == "confirmation_request"]
    assert len(req_events) == 1
    assert req_events[0]["confirmation_id"] == request.confirmation_id
    assert req_events[0]["message"] == "Allow?"


@pytest.mark.asyncio
async def test_confirmation_persisted_to_archive(manager):
    from decafclaw.archive import read_archive

    conv_id = "conv-persist"
    manager._get_or_create(conv_id)

    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="Allow?",
        timeout=2.0,
    )

    async def approve():
        await asyncio.sleep(0.05)
        await manager.respond_to_confirmation(
            conv_id, request.confirmation_id, approved=True)

    asyncio.create_task(approve())
    await manager.request_confirmation(conv_id, request)

    # Check archive has both request and response
    messages = read_archive(manager.config, conv_id)
    roles = [m["role"] for m in messages]
    assert "confirmation_request" in roles
    assert "confirmation_response" in roles


@pytest.mark.asyncio
async def test_respond_wrong_id_ignored(manager):
    conv_id = "conv-1"
    state = manager._get_or_create(conv_id)
    state.pending_confirmation = ConfirmationRequest(
        action_type=ConfirmationAction.CONTINUE_TURN,
        message="test",
        confirmation_id="correct-id",
    )
    state.confirmation_event = asyncio.Event()

    await manager.respond_to_confirmation(
        conv_id, "wrong-id", approved=True)

    # Event should NOT be set
    assert not state.confirmation_event.is_set()


# -- Message queueing ---------------------------------------------------------

@pytest.mark.asyncio
async def test_message_queued_when_busy(manager):
    state = manager._get_or_create("conv-1")
    state.busy = True

    await manager.send_message("conv-1", "queued msg", user_id="user")

    assert len(state.pending_messages) == 1
    assert state.pending_messages[0]["text"] == "queued msg"


# -- Cancel turn ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_turn_sets_event(manager):
    state = manager._get_or_create("conv-1")
    state.cancel_event = asyncio.Event()

    async def fake_task():
        await asyncio.sleep(10)

    state.agent_task = asyncio.create_task(fake_task())

    await manager.cancel_turn("conv-1")
    assert state.cancel_event.is_set()
    # Give the task a moment to be cancelled
    await asyncio.sleep(0.05)
    assert state.agent_task.cancelled()


# -- Send message with mocked agent turn ---------------------------------------

@pytest.mark.asyncio
async def test_send_message_queues_multiple_when_busy(manager):
    """Multiple messages while busy all get queued."""
    state = manager._get_or_create("conv-1")
    state.busy = True

    await manager.send_message("conv-1", "msg 1", user_id="user")
    await manager.send_message("conv-1", "msg 2", user_id="user")
    await manager.send_message("conv-1", "msg 3", user_id="user")

    assert len(state.pending_messages) == 3
    assert [m["text"] for m in state.pending_messages] == ["msg 1", "msg 2", "msg 3"]


@pytest.mark.asyncio
async def test_always_field_in_confirmation_response(manager):
    conv_id = "conv-1"
    manager._get_or_create(conv_id)

    request = ConfirmationRequest(
        action_type=ConfirmationAction.ACTIVATE_SKILL,
        action_data={"skill_name": "test"},
        message="Activate?",
        timeout=2.0,
    )

    async def approve_always():
        await asyncio.sleep(0.05)
        await manager.respond_to_confirmation(
            conv_id, request.confirmation_id, approved=True, always=True)

    asyncio.create_task(approve_always())
    response = await manager.request_confirmation(conv_id, request)

    assert response.approved is True
    assert response.always is True


# -- Startup recovery ----------------------------------------------------------

@pytest.mark.asyncio
async def test_startup_scan_finds_pending_confirmation(manager):
    """Startup scan should find conversations with unresolved confirmation requests."""
    from decafclaw.archive import append_message

    conv_id = "conv-recovery"
    # Write a confirmation request to the archive (no response)
    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls -la"},
        message="Allow shell command?",
    )
    append_message(manager.config, conv_id, request.to_archive_message())

    recovered = await manager.startup_scan()
    assert recovered == 1

    state = manager.get_state(conv_id)
    assert state is not None
    assert state.pending_confirmation is not None
    assert state.pending_confirmation.confirmation_id == request.confirmation_id
    assert state.pending_confirmation.action_type == ConfirmationAction.RUN_SHELL_COMMAND


@pytest.mark.asyncio
async def test_startup_scan_ignores_resolved_confirmations(manager):
    """Startup scan should not recover confirmations that have a response."""
    from decafclaw.archive import append_message

    conv_id = "conv-resolved"
    request = ConfirmationRequest(
        action_type=ConfirmationAction.ACTIVATE_SKILL,
        action_data={"skill_name": "test"},
        message="Activate?",
    )
    response = ConfirmationResponse(
        confirmation_id=request.confirmation_id,
        approved=True,
    )
    append_message(manager.config, conv_id, request.to_archive_message())
    append_message(manager.config, conv_id, response.to_archive_message())

    recovered = await manager.startup_scan()
    assert recovered == 0


@pytest.mark.asyncio
async def test_startup_scan_ignores_stale_confirmations(manager):
    """Confirmations older than 24 hours should be ignored."""
    from decafclaw.archive import append_message

    conv_id = "conv-stale"
    request = ConfirmationRequest(
        action_type=ConfirmationAction.CONTINUE_TURN,
        message="Continue?",
        timestamp="2020-01-01T00:00:00",  # very old
    )
    append_message(manager.config, conv_id, request.to_archive_message())

    recovered = await manager.startup_scan()
    assert recovered == 0


@pytest.mark.asyncio
async def test_startup_scan_empty_archive(manager):
    """Startup scan with no archives should recover nothing."""
    recovered = await manager.startup_scan()
    assert recovered == 0


@pytest.mark.asyncio
async def test_respond_to_recovered_confirmation(manager):
    """Responding to a recovered confirmation (no running loop) dispatches recovery."""
    from decafclaw.archive import append_message

    conv_id = "conv-recover-respond"
    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="Allow?",
    )
    append_message(manager.config, conv_id, request.to_archive_message())

    await manager.startup_scan()

    # Respond — should dispatch recovery (no running loop)
    await manager.respond_to_confirmation(
        conv_id, request.confirmation_id, approved=True)

    # Pending confirmation should be cleared
    state = manager.get_state(conv_id)
    assert state.pending_confirmation is None
