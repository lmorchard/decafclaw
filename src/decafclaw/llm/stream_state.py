"""Shared streaming state for LLM providers.

Providers accumulate per-stream state while consuming SSE events: content
parts, tool calls, usage, and finish reason. The pieces every provider
needs regardless of wire format — the on_chunk callback with error
swallowing, bounded raw-event recording for diagnostics, and the
empty-response event dump — live here; each provider subclasses with its
own chunk normalization (#1316).
"""

import inspect
import json
import logging

from .types import StreamCallback

log = logging.getLogger(__name__)

#: Maximum raw SSE events kept for the diagnostic dump on empty responses.
MAX_DIAGNOSTIC_EVENTS = 50


class StreamState:
    """Base streaming state shared by LLM providers.

    Owns the on_chunk callback, usage/finish-reason fields, and a bounded
    ring of raw SSE events for diagnostics. Subclasses accumulate
    provider-specific content and implement ``process_chunk`` (chunk
    normalization) and ``finalize`` (response assembly).
    """

    def __init__(self, on_chunk: StreamCallback | None = None):
        self.on_chunk = on_chunk
        self.usage: dict | None = None
        self.finish_reason: str | None = None
        self._all_events: list = []

    def record_event(self, chunk: dict):
        """Record a raw SSE event for diagnostics (bounded ring)."""
        self._all_events.append(chunk)
        if len(self._all_events) > MAX_DIAGNOSTIC_EVENTS:
            self._all_events.pop(0)

    async def emit(self, chunk_type: str, data):
        """Invoke the on_chunk callback; its errors never break the stream.

        Handles both sync and async callbacks — a non-awaitable return is
        simply not awaited.
        """
        if self.on_chunk:
            try:
                result = self.on_chunk(chunk_type, data)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                log.debug("Stream chunk callback error: %s", e)

    def _log_empty_dump(self, *, content, thinking, tool_calls):
        """Dump the recorded SSE events when nothing was produced."""
        if not content and not thinking and not tool_calls and self._all_events:
            log.warning(
                "LLM returned empty — full SSE event dump (%d events):",
                len(self._all_events),
            )
            for i, evt in enumerate(self._all_events):
                log.warning("  event[%d]: %s", i, json.dumps(evt)[:500])
