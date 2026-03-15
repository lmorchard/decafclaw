"""Tests for conversation compaction."""

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.archive import append_message
from decafclaw.compaction import (
    _estimate_tokens,
    _flatten_messages,
    _split_into_turns,
    compact_history,
)


class TestSplitIntoTurns:
    def test_basic_turns(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "how are you"},
            {"role": "assistant", "content": "fine"},
        ]
        turns = _split_into_turns(messages)
        assert len(turns) == 2
        assert turns[0][0]["content"] == "hello"
        assert turns[1][0]["content"] == "how are you"

    def test_turn_with_tool_calls(self):
        messages = [
            {"role": "user", "content": "search for X"},
            {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "shell"}}]},
            {"role": "tool", "tool_call_id": "tc1", "content": "result"},
            {"role": "assistant", "content": "found it"},
            {"role": "user", "content": "thanks"},
            {"role": "assistant", "content": "welcome"},
        ]
        turns = _split_into_turns(messages)
        assert len(turns) == 2
        assert len(turns[0]) == 4  # user + assistant(tool) + tool + assistant
        assert len(turns[1]) == 2  # user + assistant

    def test_single_turn(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        turns = _split_into_turns(messages)
        assert len(turns) == 1

    def test_empty_messages(self):
        assert _split_into_turns([]) == []

    def test_starts_with_non_user(self):
        # e.g., a conversation summary at the start
        messages = [
            {"role": "user", "content": "[Conversation summary]: stuff"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        turns = _split_into_turns(messages)
        assert len(turns) == 2


class TestFlattenMessages:
    def test_basic_conversation(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        result = _flatten_messages(messages)
        assert "User: hello" in result
        assert "Assistant: hi there" in result

    def test_tool_calls_and_results(self):
        messages = [
            {"role": "user", "content": "run ls"},
            {"role": "assistant", "content": "Let me check.", "tool_calls": [
                {"function": {"name": "shell"}}
            ]},
            {"role": "tool", "tool_call_id": "tc1", "content": "file1.txt\nfile2.txt"},
            {"role": "assistant", "content": "Found 2 files."},
        ]
        result = _flatten_messages(messages)
        assert "User: run ls" in result
        assert "Assistant: Let me check." in result
        assert "[called tools: shell]" in result
        assert "Tool result (tc1):" in result
        assert "Assistant: Found 2 files." in result

    def test_truncates_long_tool_results(self):
        messages = [
            {"role": "tool", "tool_call_id": "tc1", "content": "x" * 1000},
        ]
        result = _flatten_messages(messages)
        assert "..." in result
        assert len(result) < 1000


class TestEstimateTokens:
    def test_basic(self):
        assert _estimate_tokens("1234") == 1
        assert _estimate_tokens("12345678") == 2
        assert _estimate_tokens("") == 0


class TestCompactHistory:
    @pytest.fixture
    def populated_archive(self, config):
        """Create an archive with 8 turns."""
        conv_id = "test-conv"
        for i in range(8):
            append_message(config, conv_id, {"role": "user", "content": f"message {i}"})
            append_message(config, conv_id, {"role": "assistant", "content": f"response {i}"})
        return conv_id

    @pytest.mark.asyncio
    async def test_compacts_when_enough_turns(self, ctx, populated_archive):
        """With 8 turns and preserve=5, should compact 3 old turns."""
        ctx.config.compaction_preserve_turns = 5
        history = [
            {"role": "user", "content": f"message {i}"}
            for i in range(8)
        ]

        mock_response = {
            "content": "Summary of earlier conversation.",
            "tool_calls": None,
            "role": "assistant",
            "usage": None,
        }

        with patch("decafclaw.compaction.call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await compact_history(ctx, history)

        assert result is True
        # History should be: 1 summary + 5 recent turns (10 messages)
        assert history[0]["role"] == "user"
        assert "[Conversation summary]" in history[0]["content"]
        assert "Summary of earlier conversation." in history[0]["content"]

    @pytest.mark.asyncio
    async def test_skips_when_too_few_turns(self, ctx, config):
        """With fewer turns than preserve, should skip."""
        conv_id = "test-conv"
        append_message(config, conv_id, {"role": "user", "content": "hello"})
        append_message(config, conv_id, {"role": "assistant", "content": "hi"})

        ctx.config.compaction_preserve_turns = 5
        history = [{"role": "user", "content": "hello"}]

        result = await compact_history(ctx, history)
        assert result is False

    @pytest.mark.asyncio
    async def test_skips_on_empty_archive(self, ctx):
        """With no archive, should skip."""
        history = []
        result = await compact_history(ctx, history)
        assert result is False

    @pytest.mark.asyncio
    async def test_handles_llm_failure(self, ctx, populated_archive):
        """If LLM call fails, should return False and not modify history."""
        ctx.config.compaction_preserve_turns = 5
        history = [{"role": "user", "content": "test"}]
        original_len = len(history)

        with patch("decafclaw.compaction.call_llm", new_callable=AsyncMock, side_effect=Exception("LLM error")):
            result = await compact_history(ctx, history)

        assert result is False
        assert len(history) == original_len

    @pytest.mark.asyncio
    async def test_handles_empty_summary(self, ctx, populated_archive):
        """If LLM returns empty summary, should skip."""
        ctx.config.compaction_preserve_turns = 5
        history = [{"role": "user", "content": "test"}]

        mock_response = {"content": "", "tool_calls": None, "role": "assistant", "usage": None}

        with patch("decafclaw.compaction.call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await compact_history(ctx, history)

        assert result is False
