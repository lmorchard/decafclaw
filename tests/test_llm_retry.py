"""Tests for the shared LLM retry primitives in llm/retry.py (#1311)."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.llm.retry import MAX_RETRIES, RetryableError, cancellable_sleep


def test_retry_error_shape():
    err = RetryableError(429, "rate limited", retry_after="5")
    assert err.status == 429
    assert err.body == "rate limited"
    assert err.retry_after == "5"
    # retry_after is optional
    assert RetryableError(503, "unavailable").retry_after is None


def test_openai_compat_keeps_private_aliases():
    """vertex.py and openai_responses.py still import the private names from
    openai_compat until #1312 (3.2) moves them to ..retry directly."""
    from decafclaw.llm.providers import openai_compat

    assert openai_compat._MAX_RETRIES == MAX_RETRIES
    assert openai_compat._RetryableError is RetryableError
    assert openai_compat._cancellable_sleep is cancellable_sleep


async def test_cancellable_sleep_raises_when_cancel_event_set():
    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises(asyncio.CancelledError):
        await cancellable_sleep(10.0, cancel)


async def test_cancellable_sleep_without_cancel_event():
    with patch("decafclaw.llm.retry.asyncio.sleep", new_callable=AsyncMock) as sleep:
        await cancellable_sleep(2.5, None)
    sleep.assert_awaited_once_with(2.5)
