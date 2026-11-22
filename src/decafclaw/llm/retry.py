"""Shared LLM retry primitives.

Retry infrastructure for the streaming providers: a marker exception for
retryable HTTP failures (429/5xx), a cancel-aware backoff sleep, and the
retry cap. Providers import these as public names (#1311).
"""

import asyncio
import logging

log = logging.getLogger(__name__)

#: Maximum retries after the initial attempt (4 attempts total).
MAX_RETRIES = 3


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
