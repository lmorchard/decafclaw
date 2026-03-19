"""Tests for conversation compaction."""

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.archive import append_message, write_compacted_history
from decafclaw.compaction import (
    SUMMARY_PREFIX,
    _extract_previous_summary,
    _split_into_turns,
    compact_history,
    estimate_tokens,
    flatten_messages,
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
        result = flatten_messages(messages)
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
        result = flatten_messages(messages)
        assert "User: run ls" in result
        assert "Assistant: Let me check." in result
        assert "[called tools: shell]" in result
        assert "Tool result (tc1):" in result
        assert "Assistant: Found 2 files." in result

    def test_truncates_long_tool_results(self):
        messages = [
            {"role": "tool", "tool_call_id": "tc1", "content": "x" * 1000},
        ]
        result = flatten_messages(messages)
        assert "..." in result
        assert len(result) < 1000


class TestEstimateTokens:
    def test_basic(self):
        assert estimate_tokens("1234") == 1
        assert estimate_tokens("12345678") == 2
        assert estimate_tokens("") == 0


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
        ctx.config.compaction.preserve_turns = 5
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

        ctx.config.compaction.preserve_turns = 5
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
        ctx.config.compaction.preserve_turns = 5
        history = [{"role": "user", "content": "test"}]
        original_len = len(history)

        with patch("decafclaw.compaction.call_llm", new_callable=AsyncMock, side_effect=Exception("LLM error")):
            result = await compact_history(ctx, history)

        assert result is False
        assert len(history) == original_len

    @pytest.mark.asyncio
    async def test_handles_empty_summary(self, ctx, populated_archive):
        """If LLM returns empty summary, should skip."""
        ctx.config.compaction.preserve_turns = 5
        history = [{"role": "user", "content": "test"}]

        mock_response = {"content": "", "tool_calls": None, "role": "assistant", "usage": None}

        with patch("decafclaw.compaction.call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await compact_history(ctx, history)

        assert result is False

    @pytest.mark.asyncio
    async def test_incremental_compaction(self, ctx, config):
        """Second compaction should only summarize newly-old turns."""
        conv_id = "test-conv"
        # Create 10 turns
        for i in range(10):
            append_message(config, conv_id, {"role": "user", "content": f"message {i}"})
            append_message(config, conv_id, {"role": "assistant", "content": f"response {i}"})

        ctx.config.compaction.preserve_turns = 5

        # Simulate a previous compaction that summarized turns 0-4,
        # preserving turns 5-9 (10 messages) as recent.
        prev_summary = f"{SUMMARY_PREFIX}Summary of turns 0-4."
        prev_recent = []
        for i in range(5, 10):
            prev_recent.append({"role": "user", "content": f"message {i}"})
            prev_recent.append({"role": "assistant", "content": f"response {i}"})
        write_compacted_history(config, conv_id, [
            {"role": "user", "content": prev_summary},
            *prev_recent,
        ])

        # Now add 3 more turns (turns 10-12) to the archive
        for i in range(10, 13):
            append_message(config, conv_id, {"role": "user", "content": f"message {i}"})
            append_message(config, conv_id, {"role": "assistant", "content": f"response {i}"})

        # 13 turns total, preserve 5 → 8 old turns.
        # Previous compaction covered 5 old turns. So 3 newly-old turns.
        history = [{"role": "user", "content": "placeholder"}]

        mock_response = {
            "content": "Updated summary including turns 0-7.",
            "tool_calls": None,
            "role": "assistant",
            "usage": None,
        }

        with patch("decafclaw.compaction.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            result = await compact_history(ctx, history)

        assert result is True
        # Verify the LLM was called with incremental prompt (existing summary + new turns)
        call_args = mock_llm.call_args
        messages = call_args[0][1]  # second positional arg
        user_content = messages[1]["content"]
        assert "Existing summary:" in user_content
        assert "Summary of turns 0-4." in user_content
        assert "New conversation turns to incorporate:" in user_content

    @pytest.mark.asyncio
    async def test_incremental_skips_when_no_new_turns(self, ctx, config):
        """If no turns have aged out since last compaction, skip."""
        conv_id = "test-conv"
        # Create 8 turns
        for i in range(8):
            append_message(config, conv_id, {"role": "user", "content": f"message {i}"})
            append_message(config, conv_id, {"role": "assistant", "content": f"response {i}"})

        ctx.config.compaction.preserve_turns = 5

        # Previous compaction summarized turns 0-2, preserved turns 3-7 (10 msgs).
        prev_recent = []
        for i in range(3, 8):
            prev_recent.append({"role": "user", "content": f"message {i}"})
            prev_recent.append({"role": "assistant", "content": f"response {i}"})
        write_compacted_history(config, conv_id, [
            {"role": "user", "content": f"{SUMMARY_PREFIX}Summary of turns 0-2."},
            *prev_recent,
        ])

        # No new messages added — same 8 turns, same boundary.
        history = [{"role": "user", "content": "placeholder"}]
        result = await compact_history(ctx, history)
        assert result is False
