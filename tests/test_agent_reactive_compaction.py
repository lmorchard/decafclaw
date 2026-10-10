import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from decafclaw.agent import TurnRunner, _Continue, _Final
from decafclaw.conversation_manager import ConversationManager, TurnKind
from decafclaw.events import EventBus
from decafclaw.llm.types import ContextLengthExceededError


@pytest.fixture
def manager(config):
    bus = EventBus()
    return ConversationManager(config, bus)


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

    # The shared config must remain unchanged
    assert config.compaction.max_tokens == 10000

    # The runner's local config should have the dynamically lowered threshold
    assert runner.config.compaction.max_tokens < 10000
    assert runner.ctx.config.compaction.max_tokens < 10000


@pytest.mark.asyncio
async def test_reactive_overflow_does_not_affect_second_conversation(manager, monkeypatch):
    manager.config.compaction.max_tokens = 10000

    mock_compact = AsyncMock()
    monkeypatch.setattr("decafclaw.compaction.compact_history", mock_compact)

    call_count = 0

    async def mock_call_llm_with_events(ctx, config, messages, tools, **kwargs):
        nonlocal call_count
        call_count += 1
        if ctx.conv_id == "c1" and call_count == 1:
            req = httpx.Request("POST", "http://test")
            resp = httpx.Response(400, request=req, content=b"context_length_exceeded")
            raise httpx.HTTPStatusError("error", request=req, response=resp)
        return {"content": f"Hello from {ctx.conv_id}", "role": "assistant"}

    monkeypatch.setattr("decafclaw.agent._call_llm_with_events", mock_call_llm_with_events)

    events_c1 = []
    events_c2 = []
    manager.subscribe("c1", lambda e: events_c1.append(e))
    manager.subscribe("c2", lambda e: events_c2.append(e))

    fut1 = await manager.enqueue_turn(conv_id="c1", kind=TurnKind.USER, prompt="hi c1", history=[])
    await asyncio.wait_for(fut1, timeout=5.0)

    # After c1's reactive overflow, manager's shared config must still be intact
    assert manager.config.compaction.max_tokens == 10000

    fut2 = await manager.enqueue_turn(conv_id="c2", kind=TurnKind.USER, prompt="hi c2", history=[])
    await asyncio.wait_for(fut2, timeout=5.0)

    assert manager.config.compaction.max_tokens == 10000

    complete_c1 = [e for e in events_c1 if e.get("type") == "message_complete"]
    assert complete_c1
    assert complete_c1[-1].get("context_limit") == 10000

    complete_c2 = [e for e in events_c2 if e.get("type") == "message_complete"]
    assert complete_c2
    assert complete_c2[-1].get("context_limit") == 10000
