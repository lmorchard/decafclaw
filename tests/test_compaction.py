"""Tests for conversation compaction."""

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.archive import append_message, write_compacted_history
from decafclaw.compaction import (
    SUMMARY_PREFIX,
    _extract_previous_summary,
    _run_memory_sweep,
    _split_into_turns,
    _turn_has_protected_tool,
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


class TestTurnHasProtectedTool:
    def test_detects_activate_skill(self):
        turn = [
            {"role": "user", "content": "activate tabstack"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc1", "function": {"name": "activate_skill", "arguments": '{"name": "tabstack"}'}}
            ]},
            {"role": "tool", "tool_call_id": "tc1", "content": "skill body here"},
        ]
        assert _turn_has_protected_tool(turn) is True

    def test_ignores_regular_tools(self):
        turn = [
            {"role": "user", "content": "run ls"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc1", "function": {"name": "shell", "arguments": '{"command": "ls"}'}}
            ]},
            {"role": "tool", "tool_call_id": "tc1", "content": "file1.txt"},
        ]
        assert _turn_has_protected_tool(turn) is False

    def test_handles_none_tool_calls(self):
        """tool_calls: None is a valid LLM response shape — should not crash."""
        turn = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi", "tool_calls": None},
        ]
        assert _turn_has_protected_tool(turn) is False

    def test_handles_no_tool_calls(self):
        turn = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        assert _turn_has_protected_tool(turn) is False


class TestSkillProtectionDuringCompaction:
    @pytest.fixture
    def archive_with_skill(self, config):
        """Create an archive with a skill activation in turn 1 (of 8)."""
        conv_id = "test-conv"
        # Turn 0: regular
        append_message(config, conv_id, {"role": "user", "content": "hello"})
        append_message(config, conv_id, {"role": "assistant", "content": "hi"})
        # Turn 1: skill activation
        append_message(config, conv_id, {"role": "user", "content": "activate tabstack"})
        append_message(config, conv_id, {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "tc1", "function": {
                "name": "activate_skill", "arguments": '{"name": "tabstack"}'
            }}],
        })
        append_message(config, conv_id, {
            "role": "tool", "tool_call_id": "tc1",
            "content": "# Tabstack Skill\nFull skill body with instructions...",
        })
        append_message(config, conv_id, {
            "role": "assistant", "content": "Tabstack is now active.",
        })
        # Turns 2-7: regular
        for i in range(2, 8):
            append_message(config, conv_id, {"role": "user", "content": f"message {i}"})
            append_message(config, conv_id, {"role": "assistant", "content": f"response {i}"})
        return conv_id

    @pytest.mark.asyncio
    async def test_skill_turn_preserved(self, ctx, archive_with_skill):
        """Skill activation turn should survive compaction."""
        ctx.config.compaction.preserve_turns = 5
        history = [{"role": "user", "content": "placeholder"}]

        mock_response = {
            "content": "Summary of non-skill turns.",
            "tool_calls": None, "role": "assistant", "usage": None,
        }

        with patch("decafclaw.compaction.call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await compact_history(ctx, history)

        assert result is True
        # History should be: summary + protected skill turn + 5 recent turns
        assert history[0]["content"].startswith(SUMMARY_PREFIX)
        # Find the skill content in the protected messages
        skill_content = [m for m in history if (m.get("content") or "").startswith("# Tabstack Skill")]
        assert len(skill_content) == 1, "Skill body should be preserved verbatim"

    @pytest.mark.asyncio
    async def test_all_old_turns_protected_skips(self, ctx, config):
        """If every old turn is a skill activation, compaction should skip."""
        conv_id = "test-conv"
        # Turn 0: skill activation
        append_message(config, conv_id, {"role": "user", "content": "activate tabstack"})
        append_message(config, conv_id, {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "tc1", "function": {
                "name": "activate_skill", "arguments": '{"name": "tabstack"}'
            }}],
        })
        append_message(config, conv_id, {
            "role": "tool", "tool_call_id": "tc1", "content": "skill body",
        })
        append_message(config, conv_id, {"role": "assistant", "content": "activated"})
        # Turns 1-5: regular (these will be the "recent" window)
        for i in range(1, 6):
            append_message(config, conv_id, {"role": "user", "content": f"message {i}"})
            append_message(config, conv_id, {"role": "assistant", "content": f"response {i}"})

        ctx.config.compaction.preserve_turns = 5
        history = [{"role": "user", "content": "placeholder"}]

        result = await compact_history(ctx, history)
        assert result is False


class TestMemorySweep:
    @pytest.fixture
    def populated_archive(self, config):
        """Create an archive with 8 turns."""
        conv_id = "test-conv"
        for i in range(8):
            append_message(config, conv_id, {"role": "user", "content": f"message {i}"})
            append_message(config, conv_id, {"role": "assistant", "content": f"response {i}"})
        return conv_id

    @pytest.mark.asyncio
    async def test_sweep_runs_during_compaction(self, ctx, populated_archive):
        """Memory sweep should be fired as a background task during compaction."""
        import asyncio

        ctx.config.compaction.preserve_turns = 5
        ctx.config.compaction.memory_sweep_enabled = True
        history = [{"role": "user", "content": f"message {i}"} for i in range(8)]

        mock_response = {
            "content": "Summary.", "tool_calls": None,
            "role": "assistant", "usage": None,
        }

        created_tasks = []
        original_create_task = asyncio.create_task

        def capture_create_task(coro, **kwargs):
            task = original_create_task(coro, **kwargs)
            created_tasks.append(task)
            return task

        with (
            patch("decafclaw.compaction.call_llm", new_callable=AsyncMock, return_value=mock_response),
            patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock,
                  return_value=AsyncMock(text="sweep done")),
            patch("decafclaw.compaction.asyncio.create_task", side_effect=capture_create_task),
        ):
            result = await compact_history(ctx, history)

        assert result is True
        assert len(created_tasks) == 1  # sweep task was created
        await asyncio.gather(*created_tasks, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_sweep_skipped_when_disabled(self, ctx, populated_archive):
        """Memory sweep should not run when disabled."""
        ctx.config.compaction.preserve_turns = 5
        ctx.config.compaction.memory_sweep_enabled = False
        history = [{"role": "user", "content": f"message {i}"} for i in range(8)]

        mock_response = {
            "content": "Summary.", "tool_calls": None,
            "role": "assistant", "usage": None,
        }

        with (
            patch("decafclaw.compaction.call_llm", new_callable=AsyncMock, return_value=mock_response),
            patch("decafclaw.compaction.asyncio.create_task") as mock_ct,
        ):
            result = await compact_history(ctx, history)

        assert result is True
        mock_ct.assert_not_called()

    @pytest.mark.asyncio
    async def test_sweep_receives_vault_tools(self, ctx):
        """The sweep child context should have vault tools available."""
        from decafclaw.media import ToolResult

        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]

        mock_result = ToolResult(text="Nothing noteworthy.")

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock,
                    return_value=mock_result) as mock_turn:
            await _run_memory_sweep(ctx, messages)

        mock_turn.assert_called_once()
        child_ctx = mock_turn.call_args[0][0]
        # Child should have vault tools in its allowed set
        assert "vault_write" in child_ctx.tools.allowed
        assert "vault_journal_append" in child_ctx.tools.allowed
        assert "vault_search" in child_ctx.tools.allowed
        # Vault tools should be preapproved
        assert "vault_write" in child_ctx.tools.preapproved
        # Sweep must not archive into the parent conversation
        assert child_ctx.skip_archive is True

    @pytest.mark.asyncio
    async def test_sweep_error_is_logged_not_raised(self, ctx):
        """Sweep errors are caught and logged, not propagated."""
        messages = [{"role": "user", "content": "hello"}]

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock,
                    side_effect=Exception("LLM exploded")):
            # Should not raise
            await _run_memory_sweep(ctx, messages)


# -- Decision-slice integration (#302) ----------------------------------------


class TestDecisionSliceIntegration:
    """End-to-end exercise of the structured slice through compact_history."""

    @pytest.fixture
    def populated_archive(self, config):
        # Use the same conv_id the ctx fixture exposes so compact_history
        # finds this archive when called with the standard ctx.
        conv_id = "test-conv"
        for i in range(8):
            append_message(config, conv_id, {"role": "user", "content": f"u{i}"})
            append_message(config, conv_id, {"role": "assistant", "content": f"a{i}"})
        return conv_id

    @pytest.mark.asyncio
    async def test_first_compaction_persists_slice_and_renders_prefix(
        self, ctx, populated_archive,
    ):
        """A successful parse persists the slice to the sidecar and
        prepends the rendered slice to the prose summary."""
        from decafclaw.compaction_decisions import load_slice

        ctx.config.compaction.preserve_turns = 5
        ctx.config.compaction.decisions_enabled = True
        history = [{"role": "user", "content": "u0"}]

        prose = "Earlier turns covered some setup work."
        json_block = (
            "```json\n"
            "{\n"
            '  "decisions": ["use vertex by default"],\n'
            '  "open_questions": ["when to add openai?"],\n'
            '  "artifacts": ["vault://decisions/llm"]\n'
            "}\n"
            "```"
        )
        mock_response = {
            "content": f"{prose}\n\n{json_block}",
            "tool_calls": None,
            "role": "assistant",
            "usage": None,
        }

        with patch("decafclaw.compaction.call_llm", new_callable=AsyncMock,
                   return_value=mock_response):
            result = await compact_history(ctx, history)

        assert result is True
        # Slice persisted to sidecar — verify via load_slice (the
        # _slice_path helper is internal; load_slice is the public API).
        loaded = load_slice(ctx.config, ctx.conv_id or "test-conv")
        assert [e.text for e in loaded.decisions] == ["use vertex by default"]
        assert [e.text for e in loaded.open_questions] == ["when to add openai?"]
        assert [e.text for e in loaded.artifacts] == ["vault://decisions/llm"]
        # Summary message has the slice prefix above the prose.
        summary = history[0]["content"]
        assert "<decision_slice>" in summary
        assert "</decision_slice>" in summary
        assert "### Decisions" in summary
        assert "use vertex by default" in summary
        assert prose in summary
        # JSON block stripped from prose half (we still see the slice
        # heading, but no fenced ```json).
        assert "```json" not in summary

    @pytest.mark.asyncio
    async def test_second_compaction_carries_existing_entries_verbatim(
        self, ctx, populated_archive,
    ):
        """If the LLM emits a partially-overlapping slice, the merge
        keeps existing entries' created_at and adds new ones with the
        current time."""
        from decafclaw.compaction_decisions import (
            DecisionEntry,
            DecisionSlice,
            load_slice,
            save_slice,
        )

        ctx.config.compaction.preserve_turns = 5
        ctx.config.compaction.decisions_enabled = True

        # Seed an existing slice on disk first.
        existing = DecisionSlice(
            decisions=[DecisionEntry(text="use vertex", created_at="2026-01-01T00:00:00Z")],
            open_questions=[DecisionEntry(text="prune skills?", created_at="2026-01-02T00:00:00Z")],
        )
        save_slice(ctx.config, ctx.conv_id or "test-conv", existing)

        history = [{"role": "user", "content": "u0"}]

        # LLM emits the existing decision verbatim + a new artifact;
        # drops the open_question (signaling it's resolved).
        mock_response = {
            "content": (
                "More work happened.\n\n"
                "```json\n"
                "{\n"
                '  "decisions": ["use vertex"],\n'
                '  "open_questions": [],\n'
                '  "artifacts": ["docs/llm.md"]\n'
                "}\n"
                "```"
            ),
            "tool_calls": None,
            "role": "assistant",
            "usage": None,
        }

        with patch("decafclaw.compaction.call_llm", new_callable=AsyncMock,
                   return_value=mock_response):
            await compact_history(ctx, history)

        merged = load_slice(ctx.config, ctx.conv_id or "test-conv")
        # Existing entry kept verbatim with its original timestamp.
        assert len(merged.decisions) == 1
        assert merged.decisions[0].text == "use vertex"
        assert merged.decisions[0].created_at == "2026-01-01T00:00:00Z"
        # Open question dropped.
        assert merged.open_questions == []
        # New artifact added.
        assert [e.text for e in merged.artifacts] == ["docs/llm.md"]

    @pytest.mark.asyncio
    async def test_missing_json_block_falls_through(
        self, ctx, populated_archive,
    ):
        """When the LLM forgets the JSON block, prose-only fallback
        still works — slice file is not touched."""
        from decafclaw.compaction_decisions import _slice_path

        ctx.config.compaction.preserve_turns = 5
        ctx.config.compaction.decisions_enabled = True
        history = [{"role": "user", "content": "u0"}]

        mock_response = {
            "content": "Just a prose summary, no json.",
            "tool_calls": None,
            "role": "assistant",
            "usage": None,
        }

        with patch("decafclaw.compaction.call_llm", new_callable=AsyncMock,
                   return_value=mock_response):
            result = await compact_history(ctx, history)

        assert result is True
        # No sidecar created (would have been empty anyway).
        path = _slice_path(ctx.config, ctx.conv_id or "test-conv")
        assert not path.exists()
        # Summary message is just the prose, no slice block.
        summary = history[0]["content"]
        assert "Just a prose summary" in summary
        assert "<decision_slice>" not in summary

    @pytest.mark.asyncio
    async def test_disabled_skips_feature_entirely(
        self, ctx, populated_archive,
    ):
        """With decisions_enabled=False, no slice operations even if
        the LLM happens to emit a JSON block."""
        from decafclaw.compaction_decisions import _slice_path

        ctx.config.compaction.preserve_turns = 5
        ctx.config.compaction.decisions_enabled = False
        history = [{"role": "user", "content": "u0"}]

        mock_response = {
            "content": (
                "Prose.\n\n```json\n"
                '{"decisions": ["x"], "open_questions": [], "artifacts": []}\n'
                "```"
            ),
            "tool_calls": None,
            "role": "assistant",
            "usage": None,
        }

        with patch("decafclaw.compaction.call_llm", new_callable=AsyncMock,
                   return_value=mock_response):
            await compact_history(ctx, history)

        # Sidecar untouched.
        path = _slice_path(ctx.config, ctx.conv_id or "test-conv")
        assert not path.exists()
        # Summary message keeps the JSON block (no stripping when
        # feature is off — we don't post-process the response at all).
        summary = history[0]["content"]
        assert "```json" in summary
        assert "<decision_slice>" not in summary
