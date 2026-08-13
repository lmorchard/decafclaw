from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from decafclaw.agent import TurnRunner, _Continue, _Final
from decafclaw.llm.types import ContextLengthExceededError


@pytest.mark.asyncio
async def test_reactive_overflow_compaction(ctx, config, monkeypatch):
    config.compaction.max_tokens = 10000

    history = [{"role": "user", "content": "hello"}]

    runner = TurnRunner(
        ctx=ctx,
        config=config,
        history=history,
        user_message="hello",
        archive_text="",
        attachments=None,
    )

    # Mock compose
    runner._compose = AsyncMock()
    runner.composed = MagicMock()
    runner.composed.total_tokens_estimated = 12000
    runner.messages = history.copy()
    runner.history = history
    runner.composer = MagicMock()

    call_count = 0

    async def mock_call_llm_with_events(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            req = httpx.Request("POST", "http://test")
            resp = httpx.Response(400, request=req, content=b"context_length_exceeded")
            raise httpx.HTTPStatusError("error", request=req, response=resp)
        return {"content": "Hello after compaction", "role": "assistant"}

    monkeypatch.setattr("decafclaw.agent._call_llm_with_events", mock_call_llm_with_events)
    mock_compact = AsyncMock()
    monkeypatch.setattr("decafclaw.compaction.compact_history", mock_compact)

    outcome = await runner._run_iteration(0)

    # The iteration should have continued without crashing
    assert isinstance(outcome, _Continue)
    assert call_count == 1

    # We expect compact_history to have been called
    mock_compact.assert_called_once()

    # We expect the compaction threshold to have been dynamically lowered
    assert config.compaction.max_tokens < 10000
