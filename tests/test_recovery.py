"""#1144 — bounded recovery policy tests."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.agent import TurnRunner, _Continue, _Final
from decafclaw.media import ToolResult
from decafclaw.recovery import (
    RECOVERY_CONTINUE,
    RECOVERY_RETRY,
    RECOVERY_STOP,
    RecoveryState,
    decide_recovery_retry,
)

# -- Policy unit tests ---------------------------------------------------------


def test_recovery_retry_when_nothing_committed():
    assert decide_recovery_retry(RecoveryState(phase="pre_output")) == RECOVERY_RETRY


def test_recovery_continue_when_partial_text_committed():
    assert decide_recovery_retry(RecoveryState(phase="partial_text", committed_output=True)) == RECOVERY_CONTINUE


def test_recovery_stop_when_uncertain():
    assert decide_recovery_retry(RecoveryState(phase="uncertain")) == RECOVERY_STOP


def test_recovery_stop_when_cancelled_even_with_partial_output():
    assert (
        decide_recovery_retry(RecoveryState(phase="partial_text", committed_output=True), cancelled=True)
        == RECOVERY_STOP
    )


def test_recovery_stop_when_partial_text_not_committed():
    assert decide_recovery_retry(RecoveryState(phase="partial_text", committed_output=False)) == RECOVERY_STOP


# -- Agent loop integration -----------------------------------------------------


@pytest.mark.asyncio
async def test_agent_recovery_continues_on_committed_partial_output(ctx, config, monkeypatch):
    """A stream failure with committed partial text resumes via _Continue
    (bounded continuation) instead of blindly replaying the request."""
    history = [{"role": "user", "content": "hello"}]
    runner = TurnRunner(
        ctx=ctx,
        config=config,
        history=history,
        user_message="hello",
        archive_text="",
        attachments=None,
    )
    runner._compose = AsyncMock()
    runner.composed = MagicMock()
    runner.composed.total_tokens_estimated = 0
    runner.messages = history.copy()
    runner.history = history
    runner.composer = MagicMock()
    runner.accumulated_text_parts = ["partial answer"]

    async def mock_call_llm_with_events(*args, **kwargs):
        raise RuntimeError("stream exploded mid-text")

    monkeypatch.setattr("decafclaw.agent._call_llm_with_events", mock_call_llm_with_events)

    outcome = await runner._run_iteration(0)
    assert isinstance(outcome, _Continue)


@pytest.mark.asyncio
async def test_agent_recovery_stops_when_cancelled(ctx, config, monkeypatch):
    """User cancellation during the failed LLM call settles as _Final, not a
    later automatic resume."""
    history = [{"role": "user", "content": "hello"}]
    runner = TurnRunner(
        ctx=ctx,
        config=config,
        history=history,
        user_message="hello",
        archive_text="",
        attachments=None,
    )
    runner._compose = AsyncMock()
    runner.composed = MagicMock()
    runner.composed.total_tokens_estimated = 0
    runner.messages = history.copy()
    runner.history = history
    runner.composer = MagicMock()
    runner.accumulated_text_parts = ["partial answer"]
    ctx.cancelled = asyncio.Event()
    ctx.cancelled.set()

    async def mock_call_llm_with_events(*args, **kwargs):
        raise RuntimeError("stream exploded mid-text")

    monkeypatch.setattr("decafclaw.agent._call_llm_with_events", mock_call_llm_with_events)

    outcome = await runner._run_iteration(0)
    assert isinstance(outcome, _Final)
    assert isinstance(outcome.result, ToolResult)
