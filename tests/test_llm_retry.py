"""Tests for the shared LLM retry primitives in llm/retry.py (#1311, #1313)."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.llm.retry import MAX_RETRIES, RetryableError, cancellable_sleep, run_with_retries
from decafclaw.llm.types import LLMRetryExhaustedError


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


# -- Shared streaming retry helper (#1313) -----------------------------------


class _FakeState:
    """Minimal stand-in for a provider stream state."""

    def __init__(self, has_output: bool = False):
        self.has_output = has_output
        self.finish_reason = None


def _scripted_attempt(outcomes: list):
    """Build an attempt callable playing a scripted sequence of outcomes.

    Each entry is None (success) or an Exception instance to raise; the last
    outcome repeats for any further attempts. Returns (attempt, calls).
    """

    calls = []

    async def attempt():
        index = min(len(calls), len(outcomes) - 1)
        calls.append(1)
        outcome = outcomes[index]
        if outcome is not None:
            raise outcome

    return attempt, calls


async def test_run_with_retries_succeeds_without_retrying():
    """A clean first attempt returns without retry or backoff."""
    attempt, calls = _scripted_attempt([None])
    with patch("decafclaw.llm.retry.cancellable_sleep", new_callable=AsyncMock) as sleep:
        await run_with_retries(attempt)
    assert len(calls) == 1
    sleep.assert_not_awaited()


async def test_run_with_retries_backs_off_then_succeeds():
    """A 429 with retry-after backs off for that many seconds, then retries."""
    attempt, calls = _scripted_attempt([RetryableError(429, "slow down", retry_after="7"), None])
    with patch("decafclaw.llm.retry.cancellable_sleep", new_callable=AsyncMock) as sleep:
        await run_with_retries(attempt)
    assert len(calls) == 2
    sleep.assert_awaited_once_with(7, None)


async def test_run_with_retries_exponential_backoff_without_retry_after():
    """Without retry-after, backoff is exponential: 2**attempt seconds."""
    attempt, calls = _scripted_attempt([RetryableError(503, "a"), RetryableError(503, "b"), None])
    with patch("decafclaw.llm.retry.cancellable_sleep", new_callable=AsyncMock) as sleep:
        await run_with_retries(attempt)
    assert len(calls) == 3
    assert [c.args[0] for c in sleep.await_args_list] == [1, 2]


async def test_run_with_retries_backoff_capped_at_30s():
    """A large retry-after is capped at the 30s backoff bound."""
    attempt, calls = _scripted_attempt([RetryableError(429, "x", retry_after="120"), None])
    with patch("decafclaw.llm.retry.cancellable_sleep", new_callable=AsyncMock) as sleep:
        await run_with_retries(attempt)
    assert len(calls) == 2
    sleep.assert_awaited_once_with(30, None)


async def test_run_with_retries_exhaustion_raises_llm_retry_exhausted():
    """Persistent retryable failures exhaust the budget and raise LLMRetryExhaustedError."""
    attempt, calls = _scripted_attempt([RetryableError(503, "unavailable", retry_after="2")] * 10)
    with patch("decafclaw.llm.retry.cancellable_sleep", new_callable=AsyncMock):
        with pytest.raises(LLMRetryExhaustedError) as exc_info:
            await run_with_retries(attempt, max_retries=2)
    assert len(calls) == 3  # initial attempt + 2 retries
    err = exc_info.value
    assert err.status == 503
    assert err.body == "unavailable"
    assert err.retry_after == "2"


async def test_run_with_retries_exhaustion_message_uses_label():
    """The exhaustion message carries the provider label."""
    attempt, _ = _scripted_attempt([RetryableError(503, "x")] * 10)
    with patch("decafclaw.llm.retry.cancellable_sleep", new_callable=AsyncMock):
        with pytest.raises(LLMRetryExhaustedError, match="Vertex failed after 1 retries"):
            await run_with_retries(attempt, max_retries=1, label="Vertex")


async def test_run_with_retries_reraises_non_retryable_without_output():
    """A non-retryable error with no accumulated output re-raises unchanged."""
    attempt, calls = _scripted_attempt([RuntimeError("connection dropped")])
    state = _FakeState(has_output=False)
    with pytest.raises(RuntimeError, match="connection dropped"):
        await run_with_retries(attempt, has_output=lambda s: s.has_output, state=state)
    assert len(calls) == 1


async def test_run_with_retries_without_state_reraises_non_retryable():
    """Without a state/normalizer, non-retryable errors always re-raise."""
    attempt, calls = _scripted_attempt([RuntimeError("boom")])
    with pytest.raises(RuntimeError, match="boom"):
        await run_with_retries(attempt)
    assert len(calls) == 1


async def test_run_with_retries_keeps_partial_output_and_marks_state():
    """A non-retryable error with accumulated output stops the stream, keeps
    the state, and calls on_partial_error to mark it — without retrying."""
    attempt, calls = _scripted_attempt([RuntimeError("stream interrupted")])
    state = _FakeState(has_output=True)
    marked = []

    await run_with_retries(
        attempt,
        has_output=lambda s: s.has_output,
        state=state,
        on_partial_error=lambda s: marked.append(s),
    )

    assert len(calls) == 1
    assert marked == [state]


async def test_run_with_retries_cancellation_during_backoff_propagates():
    """A cancel during the backoff sleep aborts before the next attempt."""
    cancel = asyncio.Event()
    cancel.set()
    attempt, calls = _scripted_attempt([RetryableError(429, "x"), None])
    with pytest.raises(asyncio.CancelledError):
        await run_with_retries(attempt, cancel_event=cancel)
    assert len(calls) == 1
