"""Tests for circuit breaker in ConversationManager."""

import time

import pytest

from decafclaw.conversation_manager import ConversationManager, ConversationState
from decafclaw.events import EventBus


@pytest.fixture
def manager(config):
    bus = EventBus()
    return ConversationManager(config, bus)


def test_conversation_state_defaults():
    conv = ConversationState()
    assert conv.turn_times == []
    assert conv.paused_until == 0
    assert conv.pending_messages == []
    assert conv.busy is False


def test_conversation_state_independent_instances():
    """Verify mutable defaults aren't shared between instances."""
    a = ConversationState()
    b = ConversationState()
    a.pending_messages.append("pending")
    a.turn_times.append(1.0)
    assert b.pending_messages == []
    assert b.turn_times == []


def test_circuit_breaker_not_tripped_under_limit(manager):
    state = manager._get_or_create("conv-1")
    manager._circuit_breaker_record(state)
    manager._circuit_breaker_record(state)
    assert not manager._circuit_breaker_tripped(state)


def test_circuit_breaker_trips_at_limit(manager):
    # Default is 10 turns — set a low limit for testing
    manager._cb_max_turns = 3
    state = manager._get_or_create("conv-1")
    manager._circuit_breaker_record(state)
    manager._circuit_breaker_record(state)
    manager._circuit_breaker_record(state)
    assert manager._circuit_breaker_tripped(state)


def test_circuit_breaker_stays_tripped_during_pause(manager):
    manager._cb_max_turns = 2
    manager._cb_pause_sec = 100
    state = manager._get_or_create("conv-1")
    manager._circuit_breaker_record(state)
    manager._circuit_breaker_record(state)
    assert manager._circuit_breaker_tripped(state)
    assert manager._circuit_breaker_tripped(state)  # still paused


def test_circuit_breaker_recovers_after_pause(manager):
    manager._cb_max_turns = 2
    manager._cb_pause_sec = 0
    state = manager._get_or_create("conv-1")
    manager._circuit_breaker_record(state)
    manager._circuit_breaker_record(state)
    assert manager._circuit_breaker_tripped(state)
    # Clear turn_times to simulate window expiry
    state.turn_times = []
    assert not manager._circuit_breaker_tripped(state)


def test_circuit_breaker_cleans_expired_turns(manager):
    manager._cb_max_turns = 3
    state = manager._get_or_create("conv-1")
    old_time = time.monotonic() - 200
    state.turn_times = [old_time, old_time, old_time]
    assert not manager._circuit_breaker_tripped(state)
    assert len(state.turn_times) == 0


def test_circuit_breaker_record_turn(manager):
    state = manager._get_or_create("conv-1")
    assert len(state.turn_times) == 0
    manager._circuit_breaker_record(state)
    assert len(state.turn_times) == 1
    manager._circuit_breaker_record(state)
    assert len(state.turn_times) == 2


def test_circuit_breaker_sets_paused_until_on_trip(manager):
    manager._cb_max_turns = 1
    manager._cb_pause_sec = 30
    state = manager._get_or_create("conv-1")
    manager._circuit_breaker_record(state)
    before = time.monotonic()
    manager._circuit_breaker_tripped(state)
    after = time.monotonic()
    assert state.paused_until >= before + 30
    assert state.paused_until <= after + 30


@pytest.mark.asyncio
async def test_send_message_blocked_by_circuit_breaker(manager):
    """Messages should be dropped when circuit breaker is tripped."""
    manager._cb_max_turns = 1
    state = manager._get_or_create("conv-1")
    manager._circuit_breaker_record(state)

    # This should be dropped (circuit breaker tripped)
    await manager.send_message("conv-1", "should be dropped", user_id="user")

    # No pending messages or busy state — message was dropped
    assert len(state.pending_messages) == 0
    assert not state.busy
