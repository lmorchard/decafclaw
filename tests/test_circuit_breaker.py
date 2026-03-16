"""Tests for CircuitBreaker and ConversationState."""

import time

from decafclaw.mattermost import CircuitBreaker, ConversationState


def test_conversation_state_defaults():
    conv = ConversationState()
    assert conv.history == []
    assert conv.skill_state is None
    assert conv.pending_msgs == []
    assert conv.debounce_timer is None
    assert conv.last_response_time == 0
    assert conv.busy is False
    assert conv.cancel is None
    assert conv.turn_times == []
    assert conv.paused_until == 0


def test_conversation_state_independent_instances():
    """Verify mutable defaults aren't shared between instances."""
    a = ConversationState()
    b = ConversationState()
    a.history.append("msg")
    a.pending_msgs.append("pending")
    assert b.history == []
    assert b.pending_msgs == []


def test_circuit_breaker_not_tripped_under_limit():
    cb = CircuitBreaker(max_turns=3, window_sec=10, pause_sec=5)
    conv = ConversationState()
    cb.record_turn(conv)
    cb.record_turn(conv)
    assert not cb.is_tripped(conv)


def test_circuit_breaker_trips_at_limit():
    cb = CircuitBreaker(max_turns=3, window_sec=10, pause_sec=5)
    conv = ConversationState()
    cb.record_turn(conv)
    cb.record_turn(conv)
    cb.record_turn(conv)
    assert cb.is_tripped(conv)


def test_circuit_breaker_stays_tripped_during_pause():
    cb = CircuitBreaker(max_turns=2, window_sec=10, pause_sec=100)
    conv = ConversationState()
    cb.record_turn(conv)
    cb.record_turn(conv)
    # First call trips it and sets paused_until
    assert cb.is_tripped(conv)
    # Second call: still paused
    assert cb.is_tripped(conv)


def test_circuit_breaker_recovers_after_pause():
    cb = CircuitBreaker(max_turns=2, window_sec=10, pause_sec=0)
    conv = ConversationState()
    cb.record_turn(conv)
    cb.record_turn(conv)
    # Trip it — but pause_sec=0 so paused_until is now
    assert cb.is_tripped(conv)
    # After trip, turn_times still has 2 entries in window, so still tripped
    # But paused_until is in the past, so it rechecks turn_times
    # We need to clear the turn_times to simulate window expiry
    conv.turn_times = []
    assert not cb.is_tripped(conv)


def test_circuit_breaker_cleans_expired_turns():
    cb = CircuitBreaker(max_turns=3, window_sec=10, pause_sec=5)
    conv = ConversationState()
    # Add turns that are "old" (before the window)
    old_time = time.monotonic() - 20
    conv.turn_times = [old_time, old_time, old_time]
    # Old turns should be cleaned, not counted
    assert not cb.is_tripped(conv)
    assert len(conv.turn_times) == 0


def test_circuit_breaker_record_turn():
    cb = CircuitBreaker(max_turns=10, window_sec=10, pause_sec=5)
    conv = ConversationState()
    assert len(conv.turn_times) == 0
    cb.record_turn(conv)
    assert len(conv.turn_times) == 1
    cb.record_turn(conv)
    assert len(conv.turn_times) == 2


def test_circuit_breaker_sets_paused_until_on_trip():
    cb = CircuitBreaker(max_turns=1, window_sec=10, pause_sec=30)
    conv = ConversationState()
    cb.record_turn(conv)
    before = time.monotonic()
    cb.is_tripped(conv)
    after = time.monotonic()
    # paused_until should be roughly now + 30
    assert conv.paused_until >= before + 30
    assert conv.paused_until <= after + 30
