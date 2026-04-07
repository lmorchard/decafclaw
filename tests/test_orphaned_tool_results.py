"""Tests for orphaned tool result stripping in ContextComposer."""

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.context_composer import ComposerMode, ContextComposer


def _compose(ctx, config, user_message, history):
    """Helper: compose with minimal mocking."""
    config.system_prompt = "System."
    config.agent.tool_context_budget_pct = 1.0
    config.compaction.max_tokens = 1000000
    with (
        patch("decafclaw.agent._collect_all_tool_defs", return_value=[]),
        patch("decafclaw.memory_context.retrieve_memory_context",
              new_callable=AsyncMock, return_value=[]),
    ):
        composer = ContextComposer()
        return composer.compose(ctx, user_message, history,
                                mode=ComposerMode.INTERACTIVE)


@pytest.mark.asyncio
async def test_orphaned_tool_results_stripped(ctx, config):
    """Orphaned tool results (no matching tool_call) are removed."""
    history = [
        # Compaction summary (the assistant with tool_call "call_orphaned" was summarized)
        {"role": "user", "content": "[Conversation summary]: User asked to install deps."},
        {"role": "tool", "tool_call_id": "call_orphaned", "content": "npm install output..."},
        # Complete assistant+tool pair
        {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call_valid", "type": "function",
                            "function": {"name": "vault_write", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call_valid", "content": "Page written."},
        {"role": "assistant", "content": "Done."},
    ]
    result = await _compose(ctx, config, "hello", history)

    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_valid"


@pytest.mark.asyncio
async def test_valid_tool_results_preserved(ctx, config):
    """Tool results with matching tool_calls are kept."""
    history = [
        {"role": "user", "content": "do something"},
        {
            "role": "assistant", "content": None,
            "tool_calls": [
                {"id": "call_a", "type": "function", "function": {"name": "foo", "arguments": "{}"}},
                {"id": "call_b", "type": "function", "function": {"name": "bar", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_a", "content": "result a"},
        {"role": "tool", "tool_call_id": "call_b", "content": "result b"},
        {"role": "assistant", "content": "All done."},
    ]
    result = await _compose(ctx, config, "next", history)

    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 2


@pytest.mark.asyncio
async def test_multiple_orphans_all_stripped(ctx, config):
    """Multiple orphaned tool results are all removed."""
    history = [
        {"role": "user", "content": "[Conversation summary]: stuff happened"},
        {"role": "tool", "tool_call_id": "call_gone_1", "content": "old result 1"},
        {"role": "tool", "tool_call_id": "call_gone_2", "content": "old result 2"},
        {"role": "assistant", "content": "Here's what I found."},
    ]
    result = await _compose(ctx, config, "continue", history)

    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 0
