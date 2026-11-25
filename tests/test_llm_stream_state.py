"""Tests for the shared LLM streaming state base (StreamState, #1316).

The base owns the pieces every provider needs regardless of wire format:
the on_chunk callback with error swallowing, bounded raw-event recording
for diagnostics, and the empty-response event dump. Provider-specific
accumulation stays in each provider's subclass (openai_compat adopts it
here; vertex/responses follow in #1316 3.4b/3.4c).
"""

import logging

import pytest

from decafclaw.llm.stream_state import MAX_DIAGNOSTIC_EVENTS, StreamState

# -- record_event: bounded diagnostic ring -----------------------------------


def test_record_event_keeps_events_under_cap():
    state = StreamState()
    for i in range(3):
        state.record_event({"i": i})
    assert [e["i"] for e in state._all_events] == [0, 1, 2]


def test_record_event_evicts_oldest_beyond_cap():
    state = StreamState()
    for i in range(MAX_DIAGNOSTIC_EVENTS + 10):
        state.record_event({"i": i})
    assert len(state._all_events) == MAX_DIAGNOSTIC_EVENTS
    # The oldest 10 were evicted; the newest survive in order.
    assert state._all_events[0] == {"i": 10}
    assert state._all_events[-1] == {"i": MAX_DIAGNOSTIC_EVENTS + 9}


# -- emit: callback invocation -------------------------------------------------


@pytest.mark.asyncio
async def test_emit_invokes_async_callback():
    received = []

    async def on_chunk(chunk_type, data):
        received.append((chunk_type, data))

    state = StreamState(on_chunk=on_chunk)
    await state.emit("text", "hello")
    assert received == [("text", "hello")]


@pytest.mark.asyncio
async def test_emit_sync_callback_not_awaited():
    """A sync callback runs; its non-awaitable return is ignored, not raised."""
    received = []

    def on_chunk(chunk_type, data):
        received.append((chunk_type, data))
        return "not-awaitable"

    state = StreamState(on_chunk=on_chunk)
    await state.emit("text", "hello")  # must not raise
    assert received == [("text", "hello")]


@pytest.mark.asyncio
async def test_emit_callback_error_does_not_propagate():
    """Callback errors are swallowed so they never kill the stream."""

    async def bad(chunk_type, data):
        raise RuntimeError("callback broke")

    state = StreamState(on_chunk=bad)
    await state.emit("text", "hello")  # must not raise


@pytest.mark.asyncio
async def test_emit_without_callback_is_noop():
    state = StreamState()
    await state.emit("text", "hello")  # must not raise


# -- _log_empty_dump: diagnostic SSE dump --------------------------------------


def test_empty_dump_logs_recorded_events(caplog):
    state = StreamState()
    state.record_event({"choices": []})
    with caplog.at_level(logging.WARNING):
        state._log_empty_dump(content=None, thinking=None, tool_calls=None)
    assert "returned empty" in caplog.text


def test_empty_dump_skipped_when_content_present(caplog):
    state = StreamState()
    state.record_event({"choices": []})
    with caplog.at_level(logging.WARNING):
        state._log_empty_dump(content="hi", thinking=None, tool_calls=None)
    assert "returned empty" not in caplog.text


def test_empty_dump_skipped_with_no_events(caplog):
    state = StreamState()
    with caplog.at_level(logging.WARNING):
        state._log_empty_dump(content=None, thinking=None, tool_calls=None)
    assert "returned empty" not in caplog.text


# -- Adoption ------------------------------------------------------------------


def test_openai_compat_state_adopts_shared_base():
    """openai_compat's state class subclasses the shared base (#1316 3.4a)."""
    from decafclaw.llm.providers.openai_compat import _StreamState

    assert issubclass(_StreamState, StreamState)
