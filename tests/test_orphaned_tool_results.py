"""Tests for tool result sanitization (reordering + orphan removal)."""

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.context_composer import (
    ComposerMode,
    ContextComposer,
    _reorder_tool_results,
)

# -- Unit tests for _reorder_tool_results ------------------------------------


class TestReorderToolResults:
    def test_orphaned_tool_results_dropped(self):
        """Tool results with no matching assistant tool_call are removed."""
        messages = [
            {"role": "user", "content": "summary"},
            {"role": "tool", "tool_call_id": "call_orphaned", "content": "npm output"},
            {"role": "assistant", "content": "Done."},
        ]
        result = _reorder_tool_results(messages)
        assert not any(m.get("tool_call_id") == "call_orphaned" for m in result)

    def test_valid_tool_results_preserved(self):
        """Tool results with matching tool_calls are kept."""
        messages = [
            {"role": "user", "content": "go"},
            {
                "role": "assistant", "content": None,
                "tool_calls": [
                    {"id": "call_a", "type": "function", "function": {"name": "foo", "arguments": "{}"}},
                    {"id": "call_b", "type": "function", "function": {"name": "bar", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_a", "content": "a"},
            {"role": "tool", "tool_call_id": "call_b", "content": "b"},
            {"role": "assistant", "content": "done"},
        ]
        result = _reorder_tool_results(messages)
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        assert len(tool_msgs) == 2

    def test_displaced_tool_results_reordered(self):
        """Tool results appearing after a different assistant message are moved back.

        This is the actual bug: two consecutive assistant messages with tool_calls,
        then their results interleaved in the wrong order.
        """
        messages = [
            {"role": "user", "content": "do both"},
            # Assistant A issues call_A
            {
                "role": "assistant", "content": "Installing...",
                "tool_calls": [{"id": "call_A", "type": "function",
                                "function": {"name": "shell", "arguments": "{}"}}],
            },
            # Assistant B issues call_B (e.g. from reflection/compaction race)
            {
                "role": "assistant", "content": "Saving...",
                "tool_calls": [{"id": "call_B", "type": "function",
                                "function": {"name": "vault_write", "arguments": "{}"}}],
            },
            # Results in wrong order: B's result first, then A's
            {"role": "tool", "tool_call_id": "call_B", "content": "page saved"},
            {"role": "tool", "tool_call_id": "call_A", "content": "npm installed"},
        ]
        result = _reorder_tool_results(messages)

        # call_A's result should follow assistant A
        roles_and_ids = [
            (m.get("role"), m.get("tool_call_id", ""), [tc.get("id") for tc in m.get("tool_calls", [])])
            for m in result
        ]
        # Find positions
        asst_a_idx = next(i for i, (r, _, tcs) in enumerate(roles_and_ids) if tcs == ["call_A"])
        asst_b_idx = next(i for i, (r, _, tcs) in enumerate(roles_and_ids) if tcs == ["call_B"])
        tool_a_idx = next(i for i, (r, tc, _) in enumerate(roles_and_ids) if tc == "call_A")
        tool_b_idx = next(i for i, (r, tc, _) in enumerate(roles_and_ids) if tc == "call_B")

        # Each tool result immediately follows its assistant
        assert tool_a_idx == asst_a_idx + 1
        assert tool_b_idx == asst_b_idx + 1

    def test_no_tool_messages_passthrough(self):
        """Messages without any tool results pass through unchanged."""
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        assert _reorder_tool_results(messages) == messages

    def test_multiple_orphans_all_dropped(self):
        """Multiple orphaned tool results are all removed."""
        messages = [
            {"role": "user", "content": "summary"},
            {"role": "tool", "tool_call_id": "gone_1", "content": "r1"},
            {"role": "tool", "tool_call_id": "gone_2", "content": "r2"},
            {"role": "assistant", "content": "ok"},
        ]
        result = _reorder_tool_results(messages)
        assert not any(m.get("role") == "tool" for m in result)


# -- Integration tests via ContextComposer.compose() -------------------------


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
async def test_compose_strips_orphaned_tool_results(ctx, config):
    """Orphaned tool results don't reach the LLM via compose()."""
    history = [
        {"role": "user", "content": "[Conversation summary]: stuff happened"},
        {"role": "tool", "tool_call_id": "call_orphaned", "content": "old result"},
        {"role": "assistant", "content": "Done."},
    ]
    result = await _compose(ctx, config, "hello", history)
    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 0


@pytest.mark.asyncio
async def test_compose_reorders_displaced_tool_results(ctx, config):
    """Displaced tool results are reordered before reaching the LLM."""
    history = [
        {"role": "user", "content": "do it"},
        {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call_A", "type": "function",
                            "function": {"name": "shell", "arguments": "{}"}}],
        },
        {
            "role": "assistant", "content": "saving",
            "tool_calls": [{"id": "call_B", "type": "function",
                            "function": {"name": "vault_write", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call_B", "content": "saved"},
        {"role": "tool", "tool_call_id": "call_A", "content": "installed"},
    ]
    result = await _compose(ctx, config, "next", history)

    # Both tool results should be present
    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 2

    # call_A result should come before call_B result (matching assistant order)
    tool_ids = [m["tool_call_id"] for m in tool_msgs]
    assert tool_ids == ["call_A", "call_B"]
