"""Context-composer assembly tests: system prompt, tools, full compose(), history
remapping, diagnostics, context status, background events, and prompt-cache
stability.

Split from the former monolithic ``test_context_composer.py`` by concern (#1420).
These cover how the composer assembles the final message list and token accounting."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.context_composer import ContextComposer
from decafclaw.context_messages import _expand_background_event
from decafclaw.context_types import ComposedContext, ComposerMode, SourceEntry
from decafclaw.util import estimate_tokens

# -- Helpers


def _make_tool_def(name, description="A tool.", priority=None, prompt_guidelines=None):
    td = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }
    if priority is not None:
        td["priority"] = priority
    if prompt_guidelines is not None:
        td["prompt_guidelines"] = prompt_guidelines
    return td


def _make_skill_info(name, description, **kwargs):
    """Build a SkillInfo for tests without touching the filesystem."""
    from pathlib import Path

    from decafclaw.skills import SkillInfo

    return SkillInfo(
        name=name,
        description=description,
        location=Path(f"/fake/skills/{name}"),
        **kwargs,
    )


# -- System prompt


class TestComposeSystemPrompt:
    def test_returns_prompt_and_source(self, config):
        config.system_prompt = "You are a helpful agent."
        composer = ContextComposer()
        text, entry = composer._compose_system_prompt(config)
        assert text == "You are a helpful agent."
        assert entry.source == "system_prompt"
        assert entry.items_included == 1

    def test_token_estimate_positive(self, config):
        config.system_prompt = "A" * 400  # ~100 tokens
        composer = ContextComposer()
        _, entry = composer._compose_system_prompt(config)
        assert entry.tokens_estimated > 0

    def test_empty_prompt(self, config):
        config.system_prompt = ""
        composer = ContextComposer()
        text, entry = composer._compose_system_prompt(config)
        assert text == ""
        assert entry.tokens_estimated == 0


class TestAutoRefreshSkills:
    @pytest.mark.asyncio
    async def test_compose_auto_refreshes_skill_catalog(self, ctx, config):
        ws_skills = config.workspace_path / "skills"
        ws_skills.mkdir(parents=True, exist_ok=True)

        composer = ContextComposer()
        await composer.compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)
        assert "late-skill" not in config.system_prompt

        skill_dir = ws_skills / "late-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("---\nname: late-skill\ndescription: A late skill.\n---\nLate body.")

        await composer.compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)
        assert "late-skill" in config.system_prompt

    @pytest.mark.asyncio
    async def test_compose_skips_refresh_when_disabled(self, ctx, config):
        config.agent.auto_refresh_skills = False
        ws_skills = config.workspace_path / "skills"
        ws_skills.mkdir(parents=True, exist_ok=True)

        composer = ContextComposer()
        await composer.compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)

        skill_dir = ws_skills / "disabled-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("---\nname: disabled-skill\ndescription: Disabled skill.\n---\nBody.")

        await composer.compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)
        assert "disabled-skill" not in config.system_prompt


# -- Tool assembly


class TestComposeTools:
    def test_under_budget_no_deferral(self, ctx, config):
        # Set a very large budget so nothing gets deferred
        config.agent.tool_context_budget_pct = 1.0
        config.compaction.max_tokens = 1000000
        small_tools = [_make_tool_def("tool_a"), _make_tool_def("tool_b")]
        with patch("decafclaw.context_tools.collect_all_tool_defs", return_value=small_tools):
            composer = ContextComposer()
            active, deferred, text, guidance_text, entry = composer._compose_tools(ctx)
            assert len(active) == 2
            assert len(deferred) == 0
            assert text is None
            assert guidance_text is None
            assert entry.source == "tools"
            assert entry.details["deferred_mode"] is False
            assert entry.details["has_guidance"] is False

    def test_over_budget_deferral(self, ctx, config):
        # Set a tiny budget to force deferral
        config.agent.tool_context_budget_pct = 0.001
        config.compaction.max_tokens = 100
        many_tools = [_make_tool_def(f"tool_{i}", "x" * 200) for i in range(20)]
        # Add one critical tool — should survive deferral
        critical_def = _make_tool_def("current_time", "Get current time", priority="critical")
        many_tools.append(critical_def)
        with patch("decafclaw.context_tools.collect_all_tool_defs", return_value=many_tools):
            composer = ContextComposer()
            active, deferred, text, guidance_text, entry = composer._compose_tools(ctx)
            assert len(deferred) > 0
            assert text is not None
            assert entry.details["deferred_mode"] is True
            assert entry.items_truncated == len(deferred)
            # "current_time" should be in active (always-loaded)
            active_names = {t["function"]["name"] for t in active}
            assert "current_time" in active_names

    def test_allowed_tools_filter(self, ctx, config):
        config.agent.tool_context_budget_pct = 1.0
        config.compaction.max_tokens = 1000000
        tools = [_make_tool_def("allowed_tool"), _make_tool_def("blocked_tool")]
        ctx.tools.allowed = {"allowed_tool"}
        with patch("decafclaw.context_tools.collect_all_tool_defs", return_value=tools):
            composer = ContextComposer()
            active, deferred, text, guidance_text, entry = composer._compose_tools(ctx)
            active_names = {t["function"]["name"] for t in active}
            assert "allowed_tool" in active_names
            assert "blocked_tool" not in active_names

    def test_preempt_matches_do_not_change_native_loadout(self, ctx, config):
        """Keyword matching cannot change the cached native-schema prefix."""
        # Tight budget so non-critical tools would otherwise be deferred.
        config.agent.tool_context_budget_pct = 0.001
        config.compaction.max_tokens = 100
        tools = [_make_tool_def(f"tool_{i}", "x" * 200) for i in range(20)]
        # A keyword match must remain deferred until explicitly fetched.
        ctx.tools.preempt_matches = {"tool_7"}
        with patch("decafclaw.context_tools.collect_all_tool_defs", return_value=tools):
            composer = ContextComposer()
            active, deferred, text, guidance_text, entry = composer._compose_tools(ctx)
            active_names = {t["function"]["name"] for t in active}
            assert "tool_7" not in active_names
            assert "tool_7" in {t["function"]["name"] for t in deferred}

    def test_guidance_text_rendered_and_counted_in_entry(self, ctx, config):
        """Active tool with prompt_guidelines produces guidance_text and increments tokens."""
        tool = _make_tool_def("workspace_read", prompt_guidelines=["Guideline line."])
        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[tool]),
            patch("decafclaw.tool_definitions.TOOL_DEFINITIONS", [tool]),
        ):
            composer = ContextComposer()
            active, deferred, text, guidance_text, entry = composer._compose_tools(ctx)
            assert guidance_text is not None
            assert "<tool_guidance>\n- Guideline line.\n</tool_guidance>" in guidance_text
            assert entry.details["has_guidance"] is True

    @pytest.mark.asyncio
    async def test_guidance_message_in_composed_messages(self, ctx, config):
        """Integration: compose() emits <tool_guidance> system message."""
        tool = _make_tool_def("workspace_read", prompt_guidelines=["Read exact lines."])
        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[tool]),
            patch("decafclaw.tool_definitions.TOOL_DEFINITIONS", [tool]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            composer = ContextComposer()
            composed = await composer.compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)
            guidance_msgs = [
                m
                for m in composed.messages
                if m.get("role") == "system" and m.get("content", "").startswith("<tool_guidance>")
            ]
            assert len(guidance_msgs) == 1
            assert "- Read exact lines." in guidance_msgs[0]["content"]


class TestSyncToolMessages:
    def test_insert_both_when_neither_present(self):
        messages = [
            {"role": "system", "content": "base prompt"},
            {"role": "user", "content": "hello"},
        ]
        ContextComposer.sync_tool_messages(
            messages,
            deferred_text="<deferred_tools>\n- tool_x\n</deferred_tools>",
            guidance_text="<tool_guidance>\n- rule_y\n</tool_guidance>",
        )
        assert len(messages) == 4
        assert messages[0]["content"] == "base prompt"
        assert messages[1]["content"] == "<deferred_tools>\n- tool_x\n</deferred_tools>"
        assert messages[2]["content"] == "<tool_guidance>\n- rule_y\n</tool_guidance>"
        assert messages[3]["content"] == "hello"

    def test_insert_guidance_without_deferred(self):
        messages = [
            {"role": "system", "content": "base prompt"},
            {"role": "user", "content": "hello"},
        ]
        ContextComposer.sync_tool_messages(
            messages,
            deferred_text=None,
            guidance_text="<tool_guidance>\n- rule_y\n</tool_guidance>",
        )
        assert len(messages) == 3
        assert messages[0]["content"] == "base prompt"
        assert messages[1]["content"] == "<tool_guidance>\n- rule_y\n</tool_guidance>"
        assert messages[2]["content"] == "hello"

    def test_replace_in_place(self):
        messages = [
            {"role": "system", "content": "base prompt"},
            {"role": "system", "content": "<deferred_tools>\n- old\n</deferred_tools>"},
            {"role": "system", "content": "<tool_guidance>\n- old_g\n</tool_guidance>"},
            {"role": "user", "content": "hello"},
        ]
        ContextComposer.sync_tool_messages(
            messages,
            deferred_text="<deferred_tools>\n- new\n</deferred_tools>",
            guidance_text="<tool_guidance>\n- new_g\n</tool_guidance>",
        )
        assert len(messages) == 4
        assert messages[1]["content"] == "<deferred_tools>\n- new\n</deferred_tools>"
        assert messages[2]["content"] == "<tool_guidance>\n- new_g\n</tool_guidance>"

    def test_remove_when_none(self):
        messages = [
            {"role": "system", "content": "base prompt"},
            {"role": "system", "content": "<deferred_tools>\n- old\n</deferred_tools>"},
            {"role": "system", "content": "<tool_guidance>\n- old_g\n</tool_guidance>"},
            {"role": "user", "content": "hello"},
        ]
        ContextComposer.sync_tool_messages(
            messages,
            deferred_text=None,
            guidance_text=None,
        )
        assert len(messages) == 2
        assert messages[0]["content"] == "base prompt"
        assert messages[1]["content"] == "hello"

    def test_deferred_removed_guidance_kept(self):
        messages = [
            {"role": "system", "content": "base prompt"},
            {"role": "system", "content": "<deferred_tools>\n- old\n</deferred_tools>"},
            {"role": "system", "content": "<tool_guidance>\n- keep_g\n</tool_guidance>"},
            {"role": "user", "content": "hello"},
        ]
        ContextComposer.sync_tool_messages(
            messages,
            deferred_text=None,
            guidance_text="<tool_guidance>\n- keep_g\n</tool_guidance>",
        )
        assert len(messages) == 3
        assert messages[0]["content"] == "base prompt"
        assert messages[1]["content"] == "<tool_guidance>\n- keep_g\n</tool_guidance>"
        assert messages[2]["content"] == "hello"

    def test_sync_tool_messages_preserves_vault_guide_position(self):
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "system", "content": "<vault_guide>guide</vault_guide>"},
            {"role": "user", "content": "hi"},
        ]
        ContextComposer.sync_tool_messages(
            messages,
            deferred_text="<deferred_tools>\n- x\n</deferred_tools>",
            guidance_text="<tool_guidance>\n- y\n</tool_guidance>",
        )
        assert messages[0]["content"] == "system prompt"
        assert messages[1]["content"] == "<vault_guide>guide</vault_guide>"
        assert messages[2]["content"] == "<deferred_tools>\n- x\n</deferred_tools>"
        assert messages[3]["content"] == "<tool_guidance>\n- y\n</tool_guidance>"
        assert messages[4]["content"] == "hi"


# -- Full compose()


class TestCompose:
    @pytest.mark.asyncio
    async def test_preempt_skill_hint_injected_after_user_message(self, ctx, config):
        config.system_prompt = "You are a test agent."
        config.discovered_skills = [_make_skill_info("background", "Run background processes")]
        config.agent.tool_context_budget_pct = 1.0
        config.compaction.max_tokens = 1000000
        composer = ContextComposer()
        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            result = await composer.compose(ctx, "run background job", [], mode=ComposerMode.INTERACTIVE)

        # Preempt skill hint should not be in system prompt prefix messages
        system_msgs = [m for m in result.messages if m.get("role") == "system"]
        assert not any("<preempt_skill_hint>" in m.get("content", "") for m in system_msgs)

        # Hint should be present as an epilogue message after the user message
        user_msgs = [m for m in result.messages if m.get("role") == "user"]
        assert any("<preempt_skill_hint>" in m.get("content", "") for m in user_msgs)

    @pytest.mark.asyncio
    async def test_produces_valid_composed_context(self, ctx, config):
        config.system_prompt = "You are a test agent."
        config.agent.tool_context_budget_pct = 1.0
        config.compaction.max_tokens = 1000000
        small_tools = [_make_tool_def("tool_a")]
        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=small_tools),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            composer = ContextComposer()
            result = await composer.compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)
            assert isinstance(result, ComposedContext)
            assert len(result.messages) >= 2  # system + user
            assert result.messages[0]["role"] == "system"
            assert result.total_tokens_estimated > 0
            assert len(result.sources) >= 3  # system_prompt, history, tools

    @pytest.mark.asyncio
    async def test_message_ordering(self, ctx, config):
        config.system_prompt = "System prompt."
        config.agent.tool_context_budget_pct = 0.001
        config.compaction.max_tokens = 100
        many_tools = [_make_tool_def(f"t_{i}", "x" * 200) for i in range(20)]
        many_tools.append(_make_tool_def("think", "Think"))
        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=many_tools),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            composer = ContextComposer()
            result = await composer.compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)
            # System prompt first
            assert result.messages[0]["role"] == "system"
            assert result.messages[0]["content"] == "System prompt."
            # Deferred text second (since tools are over budget)
            assert result.messages[1]["role"] == "system"
            assert "tool_search" in result.messages[1]["content"]
            # User message before the working-memory block
            user_msgs = [
                m
                for m in result.messages
                if m.get("role") == "user" and not m.get("content", "").startswith("<working_memory>")
            ]
            assert user_msgs[-1]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_truncates_long_user_message(self, ctx, config):
        config.system_prompt = "System."
        config.agent.max_message_length = 50
        config.agent.tool_context_budget_pct = 1.0
        config.compaction.max_tokens = 1000000
        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            composer = ContextComposer()
            long_msg = "x" * 200
            result = await composer.compose(ctx, long_msg, [], mode=ComposerMode.INTERACTIVE)
            user_msgs = [
                m
                for m in result.messages
                if m.get("role") == "user" and not m.get("content", "").startswith("<working_memory>")
            ]
            assert "[truncated at" in user_msgs[-1]["content"]

    @pytest.mark.asyncio
    async def test_stores_sources_on_state(self, ctx, config):
        config.system_prompt = "System."
        config.agent.tool_context_budget_pct = 1.0
        config.compaction.max_tokens = 1000000
        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            composer = ContextComposer()
            await composer.compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)
            assert len(composer.state.last_sources) > 0
            assert composer.state.last_total_tokens_estimated > 0

    @pytest.mark.asyncio
    async def test_includes_retrieved_context_text(self, ctx, config):
        config.system_prompt = "System."
        config.agent.tool_context_budget_pct = 1.0
        config.compaction.max_tokens = 1000000
        mock_results = [
            {"entry_text": "memory entry", "source_type": "page", "similarity": 0.8},
        ]
        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context",
                new_callable=AsyncMock,
                return_value=mock_results,
            ),
            patch("decafclaw.extensions.vault_retrieval.format_memory_context", return_value="formatted memory"),
        ):
            composer = ContextComposer()
            result = await composer.compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)
            assert result.retrieved_context_text == "formatted memory"

    @pytest.mark.asyncio
    async def test_preempt_matchers_ignores_memory_context_history(self, ctx, config):
        """#1049 end-to-end: keywords that reach history via the injected
        memory context — and the prior assistant response echoing them —
        must not drive pre-emptive tool promotion or skill hints for a user
        message that has nothing to do with them. The matchers evaluate the
        raw user message only."""
        config.system_prompt = "You are a test agent."
        config.agent.tool_context_budget_pct = 1.0
        config.compaction.max_tokens = 1000000
        config.discovered_skills = [
            _make_skill_info("meta-ingest", "Run the meta-ingest pipeline for opencode sessions"),
        ]
        tools = [
            _make_tool_def("opencode_send", "Send a prompt to an opencode session"),
            _make_tool_def("unrelated_tool", "Totally unrelated"),
        ]
        history = [
            {"role": "user", "content": "tell me about my pipelines"},
            {
                "role": "vault_retrieval",
                "content": (
                    "Memory context:\n\n## meta-ingest pipeline\n"
                    "The meta-ingest workflow runs opencode sessions over the vault."
                ),
            },
            {
                "role": "assistant",
                "content": "Your meta-ingest pipeline runs opencode sessions over the vault.",
            },
        ]
        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=tools),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            composer = ContextComposer()
            result = await composer.compose(
                ctx,
                "how do I water my plants",
                history,
                mode=ComposerMode.INTERACTIVE,
            )
        sources = {s.source for s in result.sources}
        assert "preempt_matches" not in sources
        assert "preempt_skill_matches" not in sources
        assert ctx.tools.preempt_matches == set()
        assert ctx.skills.preempt_matches == set()
        # No <preempt_skill_hint> system message in the composed prompt.
        assert not any(m.get("content", "").startswith("<preempt_skill_hint>") for m in result.messages)

    @pytest.mark.asyncio
    async def test_compose_memory_budget_capped_by_max_tokens(self, ctx, config):
        """When model context window is large (e.g. 300,000), memory_budget is capped
        at config.vault_retrieval.max_tokens rather than unbounded (#1048)."""
        config.system_prompt = "System."
        config.agent.tool_context_budget_pct = 1.0
        config.compaction.max_tokens = 300000
        config.vault_retrieval.max_tokens = 4000
        composer = ContextComposer()
        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            await composer.compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)
        assert ctx.composer.dynamic_token_budget == 4000

    @pytest.mark.asyncio
    async def test_compose_memory_budget_constrained_by_remaining_space(self, ctx, config):
        """When remaining context space is smaller than max_tokens, memory_budget
        is constrained by remaining_budget (#1048)."""
        config.system_prompt = "System."
        config.agent.tool_context_budget_pct = 1.0
        # Window of 5000: fixed tokens ~4 (system + user msg), response reserve 4096.
        # remaining_budget is roughly 5000 - 4096 - fixed < 1000.
        config.compaction.max_tokens = 5000
        config.vault_retrieval.max_tokens = 4000
        composer = ContextComposer()
        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            await composer.compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)
        assert ctx.composer.dynamic_token_budget is not None
        assert ctx.composer.dynamic_token_budget < 4000
        assert ctx.composer.dynamic_token_budget > 0

    @pytest.mark.asyncio
    async def test_compose_memory_budget_exhausted(self, ctx, config):
        """When fixed costs + response reserve exceed context window, memory_budget is 0."""
        config.system_prompt = "System."
        config.agent.tool_context_budget_pct = 1.0
        # Window of 4000: less than response_reserve 4096 alone!
        config.compaction.max_tokens = 4000
        config.vault_retrieval.max_tokens = 4000
        composer = ContextComposer()
        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            await composer.compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)
        assert ctx.composer.dynamic_token_budget == 0


# -- History and turn markers


class TestHistoryArchivedRemap:
    """Regression coverage for #393 — archived remap-role messages
    must count toward history_entry.tokens_estimated."""

    @pytest.mark.asyncio
    async def test_archived_remap_messages_counted_in_history_entry(
        self,
        ctx,
        config,
    ):
        """An archived vault_retrieval / vault_references / conversation_notes
        message in history (loaded from the archive on a later turn) must
        contribute to history_entry.tokens_estimated."""
        composer = ContextComposer()
        archived_user = {"role": "user", "content": "earlier user message"}
        archived_assistant = {"role": "assistant", "content": "earlier reply"}
        archived_vault_retrieval = {
            "role": "vault_retrieval",
            "content": "AAAA " * 100,  # ~ measurable token weight
        }
        archived_vault_references = {
            "role": "vault_references",
            "content": "BBBB " * 100,
            "wiki_page": "Foo",
        }
        archived_notes = {
            "role": "conversation_notes",
            "content": "CCCC " * 100,
        }
        history = [
            archived_user,
            archived_assistant,
            archived_vault_retrieval,
            archived_vault_references,
            archived_notes,
        ]

        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            composed = await composer.compose(
                ctx,
                "current user message",
                history,
                mode=ComposerMode.INTERACTIVE,
            )

        history_entries = [s for s in composed.sources if s.source == "history"]
        assert len(history_entries) == 1
        history_entry = history_entries[0]

        # Sum of all five archived messages — naive estimate the same way
        # compose() does it, to match the assertion to the implementation.
        expected_min = sum(
            estimate_tokens(str(m.get("content", "")))
            for m in (
                archived_user,
                archived_assistant,
                archived_vault_retrieval,
                archived_vault_references,
                archived_notes,
            )
        )
        assert history_entry.tokens_estimated >= expected_min, (
            f"history_entry.tokens_estimated={history_entry.tokens_estimated} "
            f"expected >= {expected_min} (sum of all five archived messages)"
        )

    @pytest.mark.asyncio
    async def test_archived_compaction_message_ignored_by_composer(
        self,
        ctx,
        config,
    ):
        """#1094 — an archived role: 'compaction' message in history must be ignored
        by compose(): not counted in history tokens and omitted from LLM messages."""
        composer = ContextComposer()
        history = [
            {"role": "user", "content": "earlier user message"},
            {"role": "assistant", "content": "earlier reply"},
            {
                "role": "compaction",
                "content": "Conversation compacted (10s, 10 -> 5 messages)",
                "data": {"before_messages": 10, "after_messages": 5, "success": True},
            },
        ]

        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            composed = await composer.compose(
                ctx,
                "current user message",
                history,
                mode=ComposerMode.INTERACTIVE,
            )

        history_entries = [s for s in composed.sources if s.source == "history"]
        assert len(history_entries) == 1
        # Only the 2 user/assistant messages should be counted in history items_included
        assert history_entries[0].items_included == 2

        # 'compaction' role must not appear in composed messages sent to LLM
        roles = [m.get("role") for m in composed.messages]
        assert "compaction" not in roles
        assert all("Conversation compacted" not in str(m.get("content", "")) for m in composed.messages)

    @pytest.mark.asyncio
    async def test_source_entries_account_for_all_llm_content(
        self,
        ctx,
        config,
    ):
        """The sum of SourceEntry.tokens_estimated across all sources
        should equal the sum of token estimates over llm_history (the
        messages the model actually sees). Catches double-counting and
        silent drops."""
        composer = ContextComposer()
        history = [
            {"role": "user", "content": "prior user " * 50},
            {"role": "assistant", "content": "prior assistant " * 50},
            {"role": "vault_references", "content": "archived ref " * 50, "wiki_page": "Old"},
        ]

        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            composed = await composer.compose(
                ctx,
                "fresh user message " * 20,
                history,
                mode=ComposerMode.INTERACTIVE,
            )

        # Sum of SourceEntry.tokens_estimated for sources that map to
        # entries in composed.messages. Tools are tracked via a SourceEntry
        # but sent via the separate API `tools=` parameter, NOT in
        # composed.messages — they have to be excluded from this sum or
        # the comparison is not apples-to-apples. The test still patches
        # _collect_all_tool_defs to [] to keep tools_entry near-zero, but
        # excluding it explicitly makes the invariant robust to future
        # changes that introduce non-empty default tool fixtures.
        source_total = sum(s.tokens_estimated for s in composed.sources if s.source != "tools")

        # Sum of token estimates over the actual LLM-bound message list.
        # composed.messages includes the system prompt as the first entry;
        # SourceEntry breakdown also includes "system_prompt" as a source,
        # so this comparison is apples-to-apples once tools is excluded.
        llm_total = sum(estimate_tokens(str(m.get("content", ""))) for m in composed.messages)

        # Allow small rounding drift from per-message estimate boundaries
        # (estimate_tokens may use ceiling division). Tighten if the gap
        # is larger than a few percent — that signals a real divergence.
        assert abs(source_total - llm_total) <= max(50, llm_total * 0.05), (
            f"SourceEntry sum {source_total} (excl tools) vs composed.messages "
            f"sum {llm_total} diverge by more than 5% / 50 tokens — token "
            f"accounting is drifting."
        )

    @pytest.mark.asyncio
    async def test_archived_background_event_counted_in_history_entry(
        self,
        ctx,
        config,
    ):
        """Regression coverage for #435 — an archived background_event
        record is expanded into a synthetic assistant tool_call + tool
        result pair the LLM sees, so history_entry.tokens_estimated
        must include the expanded content."""
        composer = ContextComposer()
        # Use large tails so the expansion has measurable token weight
        # and the assertion isn't lost in rounding noise.
        stdout_tail = "expanded stdout " * 50
        stderr_tail = "expanded stderr " * 50
        bg_event = {
            "role": "background_event",
            "job_id": "job-435",
            "command": "run something",
            "status": "completed",
            "exit_code": 0,
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
            "elapsed_ms": 1234,
            "completion_tail_lines": 50,
        }
        history = [
            {"role": "user", "content": "start it"},
            {"role": "assistant", "content": "ok"},
            bg_event,
        ]

        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            composed = await composer.compose(
                ctx,
                "current user message",
                history,
                mode=ComposerMode.INTERACTIVE,
            )

        history_entries = [s for s in composed.sources if s.source == "history"]
        assert len(history_entries) == 1
        history_entry = history_entries[0]

        expanded_msgs = _expand_background_event(bg_event)
        expanded_tokens = sum(estimate_tokens(str(m.get("content", ""))) for m in expanded_msgs)
        # The plain user + assistant messages together are much smaller
        # than the expanded background_event, so any correct accounting
        # of the expansion has to exceed this floor.
        assert history_entry.tokens_estimated >= expanded_tokens, (
            f"history_entry.tokens_estimated={history_entry.tokens_estimated} "
            f"expected >= expanded background_event tokens {expanded_tokens}"
        )
        # ``items_included`` feeds the "N messages" status line and must
        # count LLM-visible messages after expansion. One user +
        # one assistant + two expanded (tool_call + tool result) = 4.
        assert history_entry.items_included == 4, (
            f"history_entry.items_included={history_entry.items_included} "
            f"expected 4 (user + assistant + 2 expanded background_event msgs)"
        )


class TestCancelMarker:
    """The cancel_marker role is archived when a user cancels a turn;
    composer must remap it to user-role so the LLM sees the marker
    between the cancelled-user-turn and the next user message at the
    correct position (issue #491)."""

    @pytest.mark.asyncio
    async def test_cancel_marker_remapped_to_user(self, ctx, config):
        config.system_prompt = "System."
        config.agent.tool_context_budget_pct = 1.0
        config.compaction.max_tokens = 1000000
        history = [
            {"role": "user", "content": "write me a 600-word essay"},
            {"role": "assistant", "content": "Once upon a"},
            {
                "role": "cancel_marker",
                "content": "[User cancelled this turn. Do not retry.]",
            },
        ]
        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            composer = ContextComposer()
            result = await composer.compose(
                ctx,
                "hello",
                history,
                mode=ComposerMode.INTERACTIVE,
            )

        # cancel_marker should appear as a user-role message in the
        # composed messages list, with its content preserved.
        cancel_msgs = [
            m for m in result.messages if m.get("role") == "user" and "cancelled this turn" in m.get("content", "")
        ]
        assert len(cancel_msgs) == 1, (
            f"expected exactly one user-role cancel marker in composed.messages; got {len(cancel_msgs)}"
        )
        # No cancel_marker role should leak through to the LLM.
        assert not any(m.get("role") == "cancel_marker" for m in result.messages)


class TestTurnAbortedMarker:
    """The turn_aborted role is archived when a turn aborts via an
    unexpected exception (issue #517, follow-up to #491). Composer
    remaps it to user-role so the LLM sees the marker between the
    aborted-user-turn and the next user message at the correct
    position."""

    @pytest.mark.asyncio
    async def test_turn_aborted_remapped_to_user(self, ctx, config):
        config.system_prompt = "System."
        config.agent.tool_context_budget_pct = 1.0
        config.compaction.max_tokens = 1000000
        history = [
            {"role": "user", "content": "write me a 600-word essay"},
            {"role": "assistant", "content": "Once upon a"},
            {
                "role": "turn_aborted",
                "content": (
                    "[The previous turn failed unexpectedly. Treat the "
                    "prior request as not fulfilled and wait for the "
                    "user to clarify.]"
                ),
            },
        ]
        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            composer = ContextComposer()
            result = await composer.compose(
                ctx,
                "hello",
                history,
                mode=ComposerMode.INTERACTIVE,
            )

        # turn_aborted should appear as a user-role message with its
        # content preserved.
        aborted_msgs = [
            m for m in result.messages if m.get("role") == "user" and "failed unexpectedly" in m.get("content", "")
        ]
        assert len(aborted_msgs) == 1, (
            f"expected exactly one user-role turn_aborted marker in composed.messages; got {len(aborted_msgs)}"
        )
        # No turn_aborted role should leak through to the LLM.
        assert not any(m.get("role") == "turn_aborted" for m in result.messages)


# -- Diagnostics and sidecars


class TestBuildDiagnostics:
    def test_produces_valid_dict(self, config):
        config.system_prompt = "System."
        composed = ComposedContext(
            messages=[{"role": "system", "content": "System."}],
            tools=[],
            deferred_tools=[],
            total_tokens_estimated=5000,
            sources=[
                SourceEntry(source="system_prompt", tokens_estimated=800, items_included=1),
                SourceEntry(source="history", tokens_estimated=3000, items_included=10),
            ],
            memory_results=[
                {
                    "file_path": "page.md",
                    "source_type": "page",
                    "composite_score": 0.75,
                    "similarity": 0.9,
                    "recency": 0.8,
                    "importance": 0.5,
                    "modified_at": "2026-04-01T12:00:00",
                    "tokens_estimated": 200,
                },
            ],
        )
        composer = ContextComposer()
        composer.record_actuals(5200, 300)
        diag = composer.build_diagnostics(config, composed)
        assert "timestamp" in diag
        assert diag["total_tokens_estimated"] == 5000
        assert diag["total_tokens_actual"] == 5200
        assert len(diag["sources"]) == 2
        assert len(diag["memory_candidates"]) == 1
        assert diag["memory_candidates"][0]["composite_score"] == 0.75
        assert diag["memory_candidates"][0]["recency"] == 0.8

    def test_handles_empty_memory(self, config):
        config.system_prompt = "System."
        composed = ComposedContext(
            messages=[],
            tools=[],
            deferred_tools=[],
            total_tokens_estimated=100,
            sources=[],
            memory_results=[],
        )
        composer = ContextComposer()
        diag = composer.build_diagnostics(config, composed)
        assert diag["memory_candidates"] == []

    def test_includes_linked_from(self, config):
        config.system_prompt = "System."
        composed = ComposedContext(
            messages=[],
            tools=[],
            deferred_tools=[],
            total_tokens_estimated=100,
            sources=[],
            memory_results=[
                {
                    "file_path": "linked.md",
                    "source_type": "graph_expansion",
                    "composite_score": 0.6,
                    "similarity": 0.5,
                    "recency": 0.7,
                    "importance": 0.5,
                    "modified_at": "",
                    "tokens_estimated": 100,
                    "linked_from": "parent.md",
                },
            ],
        )
        composer = ContextComposer()
        diag = composer.build_diagnostics(config, composed)
        assert diag["memory_candidates"][0]["linked_from"] == "parent.md"


class TestContextSidecar:
    def test_write_and_read(self, config):
        from decafclaw.context_sidecars import read_context_sidecar, write_context_sidecar

        data = {"timestamp": "2026-04-02T12:00:00", "sources": [], "total_tokens_estimated": 100}
        write_context_sidecar(config, "test-conv", data)
        result = read_context_sidecar(config, "test-conv")
        assert result is not None
        assert result["total_tokens_estimated"] == 100

    def test_read_missing_returns_none(self, config):
        from decafclaw.context_sidecars import read_context_sidecar

        result = read_context_sidecar(config, "nonexistent-conv")
        assert result is None

    def test_write_fail_open(self, config):
        from decafclaw.context_sidecars import write_context_sidecar

        # Should not raise even with bad data
        with patch("decafclaw.context_composer.Path.write_text", side_effect=OSError("fail")):
            write_context_sidecar(config, "test-conv", {"data": True})
            # No exception raised

    @pytest.mark.asyncio
    async def test_mid_conversation_system_update(self, ctx):
        """WHEN config.system_prompt changes between turns in a single conversation, THE SYSTEM SHALL inject a role: "user" message announcing the update."""
        ctx.config.system_prompt = "Original prompt"
        composer = ContextComposer()
        ctx.composer = composer.state

        # We also need to patch vault retrieval and tools defs for compose() since they are external
        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            # Turn 1
            composed1 = await composer.compose(
                ctx,
                user_message="Hello",
                history=[],
                mode=ComposerMode.INTERACTIVE,
            )
            assert "[System Update:" not in str(composed1.messages)

            # Turn 2 - system prompt changes
            ctx.config.system_prompt = "New prompt with dynamic skills"
            composed2 = await composer.compose(
                ctx,
                user_message="Are skills active?",
                history=[{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi"}],
                mode=ComposerMode.INTERACTIVE,
            )

            # Assert [System Update: ...] message was injected before the new user message
            messages = composed2.messages
            # Find the system update message
            system_update_msgs = [m for m in messages if "[System Update:" in m.get("content", "")]
            assert len(system_update_msgs) == 1
            assert system_update_msgs[0]["role"] == "user"

            # Ensure it's placed before the final user message
            user_msg = next(m for m in reversed(messages) if m.get("content") == "Are skills active?")
            assert messages.index(system_update_msgs[0]) < messages.index(user_msg)

    @pytest.mark.asyncio
    async def test_composer_state_tracks_baseline(self, ctx):
        """The baseline state SHALL be tracked on ComposerState so it is maintained correctly across conversation forks and tool calls."""
        ctx.config.system_prompt = "Initial baseline"
        composer = ContextComposer()
        ctx.composer = composer.state

        assert composer.state.baseline_system_context is None

        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            # Turn 1 sets the baseline
            composed1 = await composer.compose(
                ctx,
                user_message="Turn 1",
                history=[],
            )
            assert composer.state.baseline_system_context == "Initial baseline"
            assert len([m for m in composed1.messages if "[System Update:" in m.get("content", "")]) == 0

            # Turn 2 (no change) - baseline remains the same, no message injected
            composed2 = await composer.compose(
                ctx,
                user_message="Turn 2",
                history=[{"role": "user", "content": "Turn 1"}],
            )
            assert composer.state.baseline_system_context == "Initial baseline"
            assert len([m for m in composed2.messages if "[System Update:" in m.get("content", "")]) == 0

            # Turn 3 (prompt changes) - message injected, baseline updated
            ctx.config.system_prompt = "Changed baseline"
            composed3 = await composer.compose(
                ctx,
                user_message="Turn 3",
                history=[{"role": "user", "content": "Turn 1"}, {"role": "user", "content": "Turn 2"}],
            )
            assert composer.state.baseline_system_context == "Changed baseline"
            assert len([m for m in composed3.messages if "[System Update:" in m.get("content", "")]) == 1


# -- Context status


class TestBuildContextStatus:
    def test_basic_format(self):
        line = ContextComposer._build_context_status(12400, 100000, 5)
        assert line is not None
        assert "12,400" in line
        assert "100,000" in line
        assert "12%" in line
        assert "5 messages" in line

    def test_high_usage_hint(self):
        line = ContextComposer._build_context_status(78000, 100000, 12)
        assert line is not None
        assert "consider being concise" in line

    def test_low_usage_no_hint(self):
        line = ContextComposer._build_context_status(5000, 100000, 2)
        assert line is not None
        assert "consider being concise" not in line

    def test_zero_max_returns_none(self):
        assert ContextComposer._build_context_status(5000, 0, 2) is None

    def test_negative_max_returns_none(self):
        assert ContextComposer._build_context_status(5000, -1, 2) is None


class TestContextStatusInCompose:
    @pytest.mark.asyncio
    async def test_context_status_injected_by_default(self, ctx, config):
        config.system_prompt = "System."
        config.agent.tool_context_budget_pct = 1.0
        config.compaction.max_tokens = 100000
        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            composer = ContextComposer()
            result = await composer.compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)
            # Last message should be the context status (emitted as role: "user" to prevent Vertex pulling it into systemInstruction, #1116)
            last = result.messages[-1]
            assert last["role"] == "user"
            assert "[Context:" in last["content"]
            assert "100,000" in last["content"]
            assert "messages" in last["content"]

    @pytest.mark.asyncio
    async def test_context_status_disabled(self, ctx, config):
        config.system_prompt = "System."
        config.agent.show_context_status = False
        config.agent.tool_context_budget_pct = 1.0
        config.compaction.max_tokens = 100000
        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            composer = ContextComposer()
            result = await composer.compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)
            # Last message should be the user message, not context status
            last = result.messages[-1]
            assert last["role"] == "user"
            assert last["content"] == "hello"


# -- background_event expansion


class TestExpandBackgroundEvent:
    def test_produces_two_messages(self):
        rec = {
            "role": "background_event",
            "job_id": "abc123",
            "command": "echo hello",
            "status": "completed",
            "exit_code": 0,
            "stdout_tail": "hello",
            "stderr_tail": "",
            "elapsed_ms": 1234,
            "completion_tail_lines": 50,
        }
        messages = _expand_background_event(rec)
        assert len(messages) == 2

    def test_first_is_assistant_tool_call(self):
        rec = {
            "job_id": "abc123",
            "command": "echo hello",
            "status": "completed",
            "exit_code": 0,
            "stdout_tail": "hello",
            "stderr_tail": "",
            "elapsed_ms": 1234,
        }
        messages = _expand_background_event(rec)
        msg = messages[0]
        assert msg["role"] == "assistant"
        tool_calls = msg["tool_calls"]
        assert len(tool_calls) == 1
        tc = tool_calls[0]
        assert tc["id"] == "bg-wake-abc123"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "shell_background_status"
        assert json.loads(tc["function"]["arguments"]) == {"job_id": "abc123"}

    def test_second_is_tool_result(self):
        rec = {
            "job_id": "abc123",
            "command": "echo hello",
            "status": "completed",
            "exit_code": 0,
            "stdout_tail": "hello",
            "stderr_tail": "",
            "elapsed_ms": 1234,
        }
        messages = _expand_background_event(rec)
        msg = messages[1]
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "bg-wake-abc123"

    def test_tool_result_content_includes_job_id(self):
        rec = {
            "job_id": "abc123",
            "command": "echo hello",
            "status": "completed",
            "exit_code": 0,
            "stdout_tail": "hello",
            "stderr_tail": "",
            "elapsed_ms": 1234,
        }
        messages = _expand_background_event(rec)
        content = messages[1]["content"]
        assert "Job `abc123`" in content

    def test_tool_result_content_includes_command(self):
        rec = {
            "job_id": "abc123",
            "command": "echo hello",
            "status": "completed",
            "exit_code": 0,
            "stdout_tail": "hello",
            "stderr_tail": "",
            "elapsed_ms": 1234,
        }
        messages = _expand_background_event(rec)
        content = messages[1]["content"]
        assert "echo hello" in content

    def test_tool_result_content_includes_stdout(self):
        rec = {
            "job_id": "abc123",
            "command": "echo hello",
            "status": "completed",
            "exit_code": 0,
            "stdout_tail": "hello",
            "stderr_tail": "",
            "elapsed_ms": 1234,
        }
        messages = _expand_background_event(rec)
        content = messages[1]["content"]
        assert "hello" in content

    def test_tool_result_content_includes_exit_code(self):
        rec = {
            "job_id": "abc123",
            "command": "echo hello",
            "status": "completed",
            "exit_code": 0,
            "stdout_tail": "hello",
            "stderr_tail": "",
            "elapsed_ms": 1234,
        }
        messages = _expand_background_event(rec)
        content = messages[1]["content"]
        # Format is "- **Exit code:** 0"
        assert "Exit code:** 0" in content

    def test_namespaced_call_id(self):
        """Synthetic call IDs use the bg-wake- prefix to avoid collisions."""
        rec = {
            "job_id": "xyz789",
            "command": "ls",
            "status": "completed",
            "exit_code": 0,
            "stdout_tail": "",
            "stderr_tail": "",
            "elapsed_ms": 0,
        }
        messages = _expand_background_event(rec)
        assert messages[0]["tool_calls"][0]["id"] == "bg-wake-xyz789"
        assert messages[1]["tool_call_id"] == "bg-wake-xyz789"

    def test_missing_fields_use_defaults(self):
        """Partial record doesn't crash — missing fields use safe defaults."""
        messages = _expand_background_event({})
        assert len(messages) == 2
        assert messages[0]["role"] == "assistant"
        assert messages[1]["role"] == "tool"


class TestComposeExpandsBackgroundEvent:
    @pytest.mark.asyncio
    async def test_background_event_expanded_in_compose(self, ctx, config):
        """When history contains a background_event record, the composed
        messages include the expanded tool-call pair."""
        config.system_prompt = "System."
        config.agent.tool_context_budget_pct = 1.0
        config.compaction.max_tokens = 1000000
        config.agent.show_context_status = False

        history = [
            {"role": "user", "content": "start a job"},
            {"role": "assistant", "content": "Started job abc123"},
            {
                "role": "background_event",
                "job_id": "abc123",
                "command": "echo hello",
                "status": "completed",
                "exit_code": 0,
                "stdout_tail": "hello",
                "stderr_tail": "",
                "elapsed_ms": 1234,
                "completion_tail_lines": 50,
            },
        ]

        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            composer = ContextComposer()
            result = await composer.compose(
                ctx,
                "follow up",
                history,
                mode=ComposerMode.INTERACTIVE,
            )

        # Extract non-system messages for inspection
        chat_msgs = [m for m in result.messages if m.get("role") != "system"]

        # Assistant tool-call message present
        asst_with_tc = [m for m in chat_msgs if m.get("role") == "assistant" and m.get("tool_calls")]
        assert asst_with_tc, "Expected an assistant tool_calls message"
        tc_names = [m["tool_calls"][0]["function"]["name"] for m in asst_with_tc]
        assert "shell_background_status" in tc_names

        # Tool result message present
        tool_msgs = [
            m for m in chat_msgs if m.get("role") == "tool" and m.get("tool_call_id", "").startswith("bg-wake-")
        ]
        assert tool_msgs, "Expected a bg-wake- tool result message"
        assert "Job `abc123`" in tool_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_background_event_not_in_final_messages(self, ctx, config):
        """background_event records must not appear as raw messages in the
        composed output — only the expanded pair should be present."""
        config.system_prompt = "System."
        config.agent.tool_context_budget_pct = 1.0
        config.compaction.max_tokens = 1000000

        history = [
            {
                "role": "background_event",
                "job_id": "xyz",
                "command": "ls",
                "status": "completed",
                "exit_code": 0,
                "stdout_tail": "",
                "stderr_tail": "",
                "elapsed_ms": 100,
                "completion_tail_lines": 50,
            },
        ]

        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            composer = ContextComposer()
            result = await composer.compose(
                ctx,
                "what happened?",
                history,
                mode=ComposerMode.INTERACTIVE,
            )

        roles = [m.get("role") for m in result.messages]
        assert "background_event" not in roles


# -- Sidecars and prompt-cache stability


class TestLastRequestSidecar:
    def test_read_missing_returns_none(self, config):
        from decafclaw.context_sidecars import read_last_request_sidecar

        assert read_last_request_sidecar(config, "nonexistent-conv") is None
        assert read_last_request_sidecar(config, "") is None


class TestPromptCachePrefixStability:
    @pytest.mark.asyncio
    async def test_composed_messages_prefix_identical_across_turns(self, ctx, config):
        """Between compactions, request N's archived messages (including its tail
        skill hint) must be an exact prefix of request N+1, both from the live
        history and from history rebuilt out of the archive (#1116). The
        per-turn working-memory block is excluded by design (#1574).
        """
        config.system_prompt = "You are a test assistant."
        config.agent.tool_context_budget_pct = 1.0
        config.agent.show_context_status = True
        config.compaction.max_tokens = 100000

        composer = ContextComposer(state=ctx.composer)
        live_history: list[dict] = []
        archived: list[dict] = []
        # Turn 1 matches a skill; turns 2 and 3 do not.
        hints = iter([(None, "<preempt_skill_hint>\ntry: vault\n</preempt_skill_hint>"), (None, None), (None, None)])

        async def run_turn(text: str) -> ComposedContext:
            with (
                patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
                patch(
                    "decafclaw.extensions.vault_retrieval.retrieve_memory_context",
                    new_callable=AsyncMock,
                    return_value=[],
                ),
                patch(
                    "decafclaw.extensions.preempt_hints.compose_preempt_skill_matches",
                    side_effect=lambda *a, **k: next(hints),
                ),
            ):
                result = await composer.compose(ctx, text, live_history, mode=ComposerMode.INTERACTIVE)
            archived.extend(result.messages_to_archive)
            # The agent loop appends its own messages to both.
            turn_tail = [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": f"call-{text}", "function": {"name": "think", "arguments": "{}"}}],
                },
                {"role": "tool", "tool_call_id": f"call-{text}", "content": "ok"},
                {"role": "assistant", "content": f"reply to {text}"},
            ]
            live_history.extend(turn_tail)
            archived.extend(turn_tail)
            return result

        def stable(messages: list[dict]) -> list[dict]:
            return [m for m in messages if not str(m.get("content", "")).startswith("<working_memory>")]

        def assert_prefix(earlier: ComposedContext, later: ComposedContext) -> None:
            expected_msgs = stable(earlier.messages)
            later_msgs = stable(later.messages)
            assert len(expected_msgs) < len(later_msgs)
            for i, expected in enumerate(expected_msgs):
                assert later_msgs[i]["role"] == expected["role"], f"Role mismatch at index {i}"
                assert later_msgs[i]["content"] == expected["content"], f"Content mismatch at index {i}"

        res1 = await run_turn("turn 1")
        assert any("<preempt_skill_hint>" in m["content"] for m in res1.messages)
        assert any("<context_status>" in str(m.get("content", "")) for m in res1.messages)
        res2 = await run_turn("turn 2")
        assert_prefix(res1, res2)
        res3 = await run_turn("turn 3")
        assert_prefix(res2, res3)

        # A restart rebuilds history from the archive; it must match the live view.
        with (
            patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
            patch(
                "decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]
            ),
        ):
            from_archive = await ContextComposer(state=ctx.composer).compose(
                ctx, "turn 4", list(archived), mode=ComposerMode.INTERACTIVE
            )
        assert_prefix(res3, from_archive)


# -- Activated skills block (#1823) -----------------------------------------


class TestActivatedSkillsBlock:
    @pytest.fixture(autouse=True)
    def _no_auto_refresh(self, config):
        # The composer re-discovers skills from disk each turn; these tests
        # use synthetic SkillInfo entries that have no directory.
        config.agent.auto_refresh_skills = False

    @pytest.mark.asyncio
    async def test_renders_bodies_and_is_never_archived(self, ctx):
        info = _make_skill_info("tabstack", "Tabstack", body="# Tabstack\nUse the fetch tool.")
        ctx.config.discovered_skills = [info]
        ctx.skills.activated["tabstack"] = "hash"

        composed = await ContextComposer().compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)

        block = next(
            (m["content"] for m in composed.messages if str(m.get("content", "")).startswith("<activated_skills>")),
            None,
        )
        assert block is not None
        assert "Use the fetch tool." in block
        assert '<skill name="tabstack">' in block
        # Never archived: archiving it would duplicate every body every turn.
        assert not any("<activated_skills>" in str(m.get("content", "")) for m in composed.messages_to_archive)

    @pytest.mark.asyncio
    async def test_expands_skill_dir_placeholder(self, ctx):
        info = _make_skill_info("tabstack", "Tabstack", body="Run $SKILL_DIR/fetch.sh")
        ctx.config.discovered_skills = [info]
        ctx.skills.activated["tabstack"] = "hash"

        composed = await ContextComposer().compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)

        block = next(
            m["content"] for m in composed.messages if str(m.get("content", "")).startswith("<activated_skills>")
        )
        assert f"Run {info.location.resolve()}/fetch.sh" in block
        assert "$SKILL_DIR" not in block

    @pytest.mark.asyncio
    async def test_warns_above_the_ceiling_without_truncating(self, ctx, caplog):
        big_body = "x" * 100_000  # ~25k tokens
        info = _make_skill_info("big-skill", "Big", body=big_body)
        ctx.config.discovered_skills = [info]
        ctx.skills.activated["big-skill"] = "hash"

        with caplog.at_level("WARNING", logger="decafclaw.context_composer"):
            composed = await ContextComposer().compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)

        block = next(
            m["content"] for m in composed.messages if str(m.get("content", "")).startswith("<activated_skills>")
        )
        assert big_body in block  # never truncated
        assert "big-skill" in caplog.text
        assert "ceiling" in caplog.text

    @pytest.mark.asyncio
    async def test_no_block_when_nothing_is_activated(self, ctx):
        composed = await ContextComposer().compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)
        assert not any("<activated_skills>" in str(m.get("content", "")) for m in composed.messages)
