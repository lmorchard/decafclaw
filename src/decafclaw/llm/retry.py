"""Shared LLM retry primitives.

Retry infrastructure for the streaming providers: a marker exception for
retryable HTTP failures (429/5xx), a cancel-aware backoff sleep, the retry
cap, and the bounded streaming-retry loop shared by the providers (#1311,
#1313).
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable

from .types import LLMRetryExhaustedError

log = logging.getLogger(__name__)

#: Maximum retries after the initial attempt (4 attempts total).
MAX_RETRIES = 3

#: Upper bound for a single backoff delay, in seconds.
_MAX_BACKOFF_DELAY = 30

#: One streaming attempt: a zero-argument callable returning the coroutine
#: for one full try (connect, status check, event consumption). Raises
#: RetryableError on 429/5xx.
Attempt = Callable[[], Awaitable[Any]]

#: Chunk normalizer: reports whether the accumulated streaming state holds
#: output worth preserving when a non-retryable error interrupts the stream.
HasOutput = Callable[[Any], bool]


class RetryableError(Exception):
    """Raised on 429/5xx to trigger retry logic."""

    def __init__(self, status: int, body: str, retry_after: str | None = None):
        self.status = status
        self.body = body
        self.retry_after = retry_after


async def cancellable_sleep(delay: float, cancel_event: asyncio.Event | None):
    """Sleep that can be interrupted by a cancel event."""
    if cancel_event:
        try:
            await asyncio.wait_for(cancel_event.wait(), timeout=delay)
            log.info("LLM retry sleep cancelled by user")
            raise asyncio.CancelledError()
        except asyncio.TimeoutError:
            pass
    else:
        await asyncio.sleep(delay)


async def run_with_retries(
    attempt: Attempt,
    *,
    has_output: HasOutput | None = None,
    state: Any = None,
    cancel_event: asyncio.Event | None = None,
    max_retries: int = MAX_RETRIES,
    label: str = "LLM",
    on_partial_error: Callable[[Any], None] | None = None,
) -> None:
    """Run a streaming attempt with bounded retry on 429/5xx (#1313).

    ``attempt`` performs one full streaming attempt. Retryable failures
    (429/5xx) back off with the server's ``retry-after`` when present,
    otherwise exponential (``2**attempt``), capped at
    :data:`_MAX_BACKOFF_DELAY` seconds via :func:`cancellable_sleep`, so a
    user cancel interrupts the wait; when the budget is exhausted, raises
    :class:`LLMRetryExhaustedError`.

    Non-retryable errors are preserved or re-raised through the chunk
    normalizer: when ``has_output(state)`` reports accumulated output, the
    stream stops with that state (after calling ``on_partial_error(state)``
    so the provider can mark it); otherwise the error propagates.
    """
    for attempt_no in range(max_retries + 1):
        try:
            await attempt()
            return
        except RetryableError as e:
            if attempt_no >= max_retries:
                raise LLMRetryExhaustedError(
                    f"{label} failed after {max_retries} retries (status {e.status}): {e.body[:200]}",
                    status=e.status,
                    body=e.body,
                    retry_after=e.retry_after,
                ) from e
            delay = min(int(e.retry_after or 2**attempt_no), _MAX_BACKOFF_DELAY)
            log.warning(
                "%s %s (%d), retrying in %ds (attempt %d/%d): %s",
                label,
                "rate limited" if e.status == 429 else "server error",
                e.status,
                delay,
                attempt_no + 1,
                max_retries,
                e.body[:200],
            )
            await cancellable_sleep(delay, cancel_event)
        except Exception as e:
            if state is None or has_output is None or not has_output(state):
                raise
            log.error("%s streaming error (partial content kept): %s", label, e)
            if on_partial_error is not None:
                on_partial_error(state)
            return
