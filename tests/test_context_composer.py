"""Tests for the context composer module."""

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.context_composer import (
    ComposedContext,
    ComposerMode,
    ComposerState,
    ContextComposer,
    SourceEntry,
)

# -- Dataclass construction ---------------------------------------------------


class TestSourceEntry:
    def test_construction(self):
        entry = SourceEntry(source="memory", tokens_estimated=100, items_included=3)
        assert entry.source == "memory"
        assert entry.tokens_estimated == 100
        assert entry.items_included == 3
        assert entry.items_truncated == 0
        assert entry.details == {}

    def test_with_details(self):
        entry = SourceEntry(
            source="tools", tokens_estimated=500, items_included=10,
            items_truncated=5, details={"deferred_mode": True},
        )
        assert entry.items_truncated == 5
        assert entry.details["deferred_mode"] is True


class TestComposedContext:
    def test_construction(self):
        ctx = ComposedContext(
            messages=[{"role": "system", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "test"}}],
            deferred_tools=[],
            total_tokens_estimated=100,
            sources=[],
        )
        assert len(ctx.messages) == 1
        assert len(ctx.tools) == 1
        assert ctx.total_tokens_estimated == 100
        assert ctx.retrieved_context_text == ""

    def test_with_retrieved_context(self):
        ctx = ComposedContext(
            messages=[], tools=[], deferred_tools=[],
            total_tokens_estimated=0, sources=[],
            retrieved_context_text="some memory context",
        )
        assert ctx.retrieved_context_text == "some memory context"


# -- ComposerMode -------------------------------------------------------------


class TestComposerMode:
    def test_all_modes_exist(self):
        assert ComposerMode.INTERACTIVE.value == "interactive"
        assert ComposerMode.HEARTBEAT.value == "heartbeat"
        assert ComposerMode.SCHEDULED.value == "scheduled"
        assert ComposerMode.CHILD_AGENT.value == "child_agent"

    def test_mode_count(self):
        assert len(ComposerMode) == 4


# -- ComposerState ------------------------------------------------------------


class TestComposerState:
    def test_defaults(self):
        state = ComposerState()
        assert state.last_sources == []
        assert state.last_total_tokens_estimated == 0
        assert state.last_prompt_tokens_actual == 0
        assert state.last_completion_tokens_actual == 0
        assert state.injected_paths == set()


# -- ContextComposer ----------------------------------------------------------


class TestContextComposer:
    def test_creates_default_state(self):
        composer = ContextComposer()
        assert isinstance(composer.state, ComposerState)

    def test_uses_provided_state(self):
        state = ComposerState(last_total_tokens_estimated=42)
        composer = ContextComposer(state=state)
        assert composer.state.last_total_tokens_estimated == 42

    def test_record_actuals(self):
        composer = ContextComposer()
        composer.record_actuals(prompt_tokens=1500, completion_tokens=300)
        assert composer.state.last_prompt_tokens_actual == 1500
        assert composer.state.last_completion_tokens_actual == 300


# -- Context integration -------------------------------------------------------


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


class TestGetContextWindowSize:
    def test_prefers_context_window_size(self, config):
        config.llm.context_window_size = 200000
        config.compaction.max_tokens = 100000
        composer = ContextComposer()
        assert composer._get_context_window_size(config) == 200000

    def test_falls_back_to_compaction_max_tokens(self, config):
        config.llm.context_window_size = 0
        config.compaction.max_tokens = 100000
        composer = ContextComposer()
        assert composer._get_context_window_size(config) == 100000


# -- Memory context ------------------------------------------------------------


class TestComposeMemoryContext:
    @pytest.mark.asyncio
    async def test_skips_when_skip_vault_retrieval(self, ctx, config):
        ctx.skip_vault_retrieval = True
        composer = ContextComposer()
        msgs, text, raw, entry = await composer._compose_vault_retrieval(
            ctx, config, "hello", ComposerMode.INTERACTIVE,
        )
        assert msgs == []
        assert text == ""
        assert raw == []
        assert entry is None

    @pytest.mark.asyncio
    async def test_skips_for_heartbeat_mode(self, ctx, config):
        composer = ContextComposer()
        msgs, text, raw, entry = await composer._compose_vault_retrieval(
            ctx, config, "hello", ComposerMode.HEARTBEAT,
        )
        assert msgs == []
        assert entry is None

    @pytest.mark.asyncio
    async def test_skips_for_child_agent_mode(self, ctx, config):
        composer = ContextComposer()
        msgs, text, raw, entry = await composer._compose_vault_retrieval(
            ctx, config, "hello", ComposerMode.CHILD_AGENT,
        )
        assert msgs == []
        assert entry is None

    @pytest.mark.asyncio
    async def test_returns_results_when_available(self, ctx, config):
        mock_results = [
            {"entry_text": "some memory", "source_type": "page", "similarity": 0.8},
        ]
        with (
            patch("decafclaw.memory_context.retrieve_memory_context",
                  new_callable=AsyncMock, return_value=mock_results),
            patch("decafclaw.memory_context.format_memory_context",
                  return_value="formatted memory"),
        ):
            composer = ContextComposer()
            msgs, text, raw, entry = await composer._compose_vault_retrieval(
                ctx, config, "hello", ComposerMode.INTERACTIVE,
            )
            assert len(msgs) == 1
            assert msgs[0]["role"] == "vault_retrieval"
            assert text == "formatted memory"
            assert len(raw) == 1
            assert entry is not None
            assert entry.source == "memory"
            assert entry.items_included == 1

    @pytest.mark.asyncio
    async def test_fail_open_on_error(self, ctx, config):
        with patch("decafclaw.memory_context.retrieve_memory_context",
                   new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            composer = ContextComposer()
            msgs, text, raw, entry = await composer._compose_vault_retrieval(
                ctx, config, "hello", ComposerMode.INTERACTIVE,
            )
            assert msgs == []
            assert text == ""
            assert raw == []
            assert entry is None

    @pytest.mark.asyncio
    async def test_tracks_injected_paths(self, ctx, config):
        mock_results = [
            {"entry_text": "first", "source_type": "page", "similarity": 0.9,
             "file_path": "pages/first.md", "modified_at": "", "importance": 0.5},
            {"entry_text": "second", "source_type": "journal", "similarity": 0.85,
             "file_path": "journal/second.md", "modified_at": "", "importance": 0.5},
        ]
        with (
            patch("decafclaw.memory_context.retrieve_memory_context",
                  new_callable=AsyncMock, return_value=mock_results),
            patch("decafclaw.memory_context.format_memory_context",
                  return_value="formatted"),
        ):
            composer = ContextComposer()
            await composer._compose_vault_retrieval(
                ctx, config, "hello", ComposerMode.INTERACTIVE,
            )
            assert "pages/first.md" in composer.state.injected_paths
            assert "journal/second.md" in composer.state.injected_paths

    @pytest.mark.asyncio
    async def test_suppresses_already_injected(self, ctx, config):
        mock_results = [
            {"entry_text": "first", "source_type": "page", "similarity": 0.8,
             "file_path": "pages/first.md", "modified_at": "", "importance": 0.5},
        ]
        with (
            patch("decafclaw.memory_context.retrieve_memory_context",
                  new_callable=AsyncMock, return_value=mock_results),
            patch("decafclaw.memory_context.format_memory_context",
                  return_value="formatted"),
        ):
            composer = ContextComposer()
            # First turn: injected
            _, _, _, entry1 = await composer._compose_vault_retrieval(
                ctx, config, "hello", ComposerMode.INTERACTIVE,
            )
            assert entry1 is not None
            # Second turn: suppressed (already in context)
            _, _, _, entry2 = await composer._compose_vault_retrieval(
                ctx, config, "hello again", ComposerMode.INTERACTIVE,
            )
            assert entry2 is None  # no results after suppression


# -- Wiki context --------------------------------------------------------------


class TestComposeWikiContext:
    def test_skips_for_heartbeat_mode(self, ctx, config):
        composer = ContextComposer()
        msgs, entry = composer._compose_vault_references(
            ctx, config, "hello", [], ComposerMode.HEARTBEAT,
        )
        assert msgs == []
        assert entry is None

    def test_skips_when_no_vault_dir(self, ctx, config, tmp_path):
        # Point vault to non-existent dir
        config.vault.vault_path = str(tmp_path / "nonexistent")
        composer = ContextComposer()
        msgs, entry = composer._compose_vault_references(
            ctx, config, "hello", [], ComposerMode.INTERACTIVE,
        )
        assert msgs == []
        assert entry is None

    def test_returns_wiki_messages(self, ctx, config, tmp_path):
        # Set up vault with a page
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        config.vault.vault_path = str(vault_dir)
        with patch("decafclaw.agent._parse_wiki_references",
                   return_value=[{"page": "TestPage", "source": "mention"}]):
            with patch("decafclaw.agent._read_wiki_page",
                       return_value="Page content here"):
                composer = ContextComposer()
                msgs, entry = composer._compose_vault_references(
                    ctx, config, "@[[TestPage]]", [], ComposerMode.INTERACTIVE,
                )
                assert len(msgs) == 1
                assert msgs[0]["role"] == "vault_references"
                assert "TestPage" in msgs[0]["content"]
                assert entry is not None
                assert entry.source == "wiki"
                assert entry.items_included == 1

    def test_skips_already_injected_pages(self, ctx, config, tmp_path):
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        config.vault.vault_path = str(vault_dir)
        history = [{"role": "vault_references", "content": "old", "wiki_page": "TestPage"}]
        with patch("decafclaw.agent._parse_wiki_references",
                   return_value=[{"page": "TestPage", "source": "mention"}]):
            composer = ContextComposer()
            msgs, entry = composer._compose_vault_references(
                ctx, config, "@[[TestPage]]", history, ComposerMode.INTERACTIVE,
            )
            assert msgs == []
            # Entry still created to track the skip
            assert entry is not None
            assert entry.items_truncated == 1


# -- Tool assembly -------------------------------------------------------------


def _make_tool_def(name, description="A tool."):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


class TestComposeTools:
    def test_under_budget_no_deferral(self, ctx, config):
        # Set a very large budget so nothing gets deferred
        config.agent.tool_context_budget_pct = 1.0
        config.compaction.max_tokens = 1000000
        small_tools = [_make_tool_def("tool_a"), _make_tool_def("tool_b")]
        with patch("decafclaw.agent._collect_all_tool_defs", return_value=small_tools):
            composer = ContextComposer()
            active, deferred, text, entry = composer._compose_tools(ctx, config)
            assert len(active) == 2
            assert len(deferred) == 0
            assert text is None
            assert entry.source == "tools"
            assert entry.details["deferred_mode"] is False

    def test_over_budget_deferral(self, ctx, config):
        # Set a tiny budget to force deferral
        config.agent.tool_context_budget_pct = 0.001
        config.compaction.max_tokens = 100
        many_tools = [_make_tool_def(f"tool_{i}", "x" * 200) for i in range(20)]
        # Add one always-loaded tool
        many_tools.append(_make_tool_def("think", "Think step by step"))
        with patch("decafclaw.agent._collect_all_tool_defs", return_value=many_tools):
            composer = ContextComposer()
            active, deferred, text, entry = composer._compose_tools(ctx, config)
            assert len(deferred) > 0
            assert text is not None
            assert entry.details["deferred_mode"] is True
            assert entry.items_truncated == len(deferred)
            # "think" should be in active (always-loaded)
            active_names = {t["function"]["name"] for t in active}
            assert "think" in active_names

    def test_allowed_tools_filter(self, ctx, config):
        config.agent.tool_context_budget_pct = 1.0
        config.compaction.max_tokens = 1000000
        tools = [_make_tool_def("allowed_tool"), _make_tool_def("blocked_tool")]
        ctx.tools.allowed = {"allowed_tool"}
        with patch("decafclaw.agent._collect_all_tool_defs", return_value=tools):
            composer = ContextComposer()
            active, deferred, text, entry = composer._compose_tools(ctx, config)
            active_names = {t["function"]["name"] for t in active}
            assert "allowed_tool" in active_names
            assert "blocked_tool" not in active_names


# -- Full compose() ------------------------------------------------------------


class TestCompose:
    @pytest.mark.asyncio
    async def test_produces_valid_composed_context(self, ctx, config):
        config.system_prompt = "You are a test agent."
        config.agent.tool_context_budget_pct = 1.0
        config.compaction.max_tokens = 1000000
        small_tools = [_make_tool_def("tool_a")]
        with (
            patch("decafclaw.agent._collect_all_tool_defs", return_value=small_tools),
            patch("decafclaw.memory_context.retrieve_memory_context",
                  new_callable=AsyncMock, return_value=[]),
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
            patch("decafclaw.agent._collect_all_tool_defs", return_value=many_tools),
            patch("decafclaw.memory_context.retrieve_memory_context",
                  new_callable=AsyncMock, return_value=[]),
        ):
            composer = ContextComposer()
            result = await composer.compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)
            # System prompt first
            assert result.messages[0]["role"] == "system"
            assert result.messages[0]["content"] == "System prompt."
            # Deferred text second (since tools are over budget)
            assert result.messages[1]["role"] == "system"
            assert "tool_search" in result.messages[1]["content"]
            # User message before context status
            user_msgs = [m for m in result.messages if m.get("role") == "user"]
            assert user_msgs[-1]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_truncates_long_user_message(self, ctx, config):
        config.system_prompt = "System."
        config.agent.max_message_length = 50
        config.agent.tool_context_budget_pct = 1.0
        config.compaction.max_tokens = 1000000
        with (
            patch("decafclaw.agent._collect_all_tool_defs", return_value=[]),
            patch("decafclaw.memory_context.retrieve_memory_context",
                  new_callable=AsyncMock, return_value=[]),
        ):
            composer = ContextComposer()
            long_msg = "x" * 200
            result = await composer.compose(ctx, long_msg, [], mode=ComposerMode.INTERACTIVE)
            user_msgs = [m for m in result.messages if m.get("role") == "user"]
            assert "[truncated at" in user_msgs[-1]["content"]

    @pytest.mark.asyncio
    async def test_stores_sources_on_state(self, ctx, config):
        config.system_prompt = "System."
        config.agent.tool_context_budget_pct = 1.0
        config.compaction.max_tokens = 1000000
        with (
            patch("decafclaw.agent._collect_all_tool_defs", return_value=[]),
            patch("decafclaw.memory_context.retrieve_memory_context",
                  new_callable=AsyncMock, return_value=[]),
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
            patch("decafclaw.agent._collect_all_tool_defs", return_value=[]),
            patch("decafclaw.memory_context.retrieve_memory_context",
                  new_callable=AsyncMock, return_value=mock_results),
            patch("decafclaw.memory_context.format_memory_context",
                  return_value="formatted memory"),
        ):
            composer = ContextComposer()
            result = await composer.compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)
            assert result.retrieved_context_text == "formatted memory"


# -- Relevance scoring ---------------------------------------------------------


class TestScoreCandidates:
    def test_produces_composite_scores(self, config):
        candidates = [
            {"similarity": 0.8, "modified_at": "", "importance": 0.5},
            {"similarity": 0.6, "modified_at": "", "importance": 0.5},
        ]
        composer = ContextComposer()
        scored = composer._score_candidates(candidates, config)
        assert all("composite_score" in c for c in scored)

    def test_sorted_by_score_descending(self, config):
        candidates = [
            {"similarity": 0.3, "modified_at": "", "importance": 0.5},
            {"similarity": 0.9, "modified_at": "", "importance": 0.5},
            {"similarity": 0.6, "modified_at": "", "importance": 0.5},
        ]
        composer = ContextComposer()
        scored = composer._score_candidates(candidates, config)
        scores = [c["composite_score"] for c in scored]
        assert scores == sorted(scores, reverse=True)

    def test_recency_favors_recent(self, config):
        from datetime import datetime, timedelta
        now = datetime.now()
        recent = (now - timedelta(hours=1)).isoformat()
        old = (now - timedelta(hours=1000)).isoformat()
        candidates = [
            {"similarity": 0.5, "modified_at": old, "importance": 0.5},
            {"similarity": 0.5, "modified_at": recent, "importance": 0.5},
        ]
        composer = ContextComposer()
        scored = composer._score_candidates(candidates, config)
        # Recent item should score higher
        assert scored[0]["modified_at"] == recent

    def test_importance_influences_score(self, config):
        candidates = [
            {"similarity": 0.5, "modified_at": "", "importance": 0.1},
            {"similarity": 0.5, "modified_at": "", "importance": 0.9},
        ]
        composer = ContextComposer()
        scored = composer._score_candidates(candidates, config)
        # Higher importance should score higher
        assert scored[0]["importance"] == 0.9

    def test_missing_modified_at_defaults_recency(self, config):
        candidates = [
            {"similarity": 0.8, "importance": 0.5},
        ]
        composer = ContextComposer()
        scored = composer._score_candidates(candidates, config)
        assert scored[0]["composite_score"] > 0



# -- Diagnostics and sidecar ---------------------------------------------------


class TestBuildDiagnostics:
    def test_produces_valid_dict(self, config):
        config.system_prompt = "System."
        composed = ComposedContext(
            messages=[{"role": "system", "content": "System."}],
            tools=[], deferred_tools=[],
            total_tokens_estimated=5000,
            sources=[
                SourceEntry(source="system_prompt", tokens_estimated=800, items_included=1),
                SourceEntry(source="history", tokens_estimated=3000, items_included=10),
            ],
            memory_results=[
                {"file_path": "page.md", "source_type": "page",
                 "composite_score": 0.75, "similarity": 0.9,
                 "recency": 0.8, "importance": 0.5,
                 "modified_at": "2026-04-01T12:00:00",
                 "tokens_estimated": 200},
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
            messages=[], tools=[], deferred_tools=[],
            total_tokens_estimated=100, sources=[], memory_results=[],
        )
        composer = ContextComposer()
        diag = composer.build_diagnostics(config, composed)
        assert diag["memory_candidates"] == []

    def test_includes_linked_from(self, config):
        config.system_prompt = "System."
        composed = ComposedContext(
            messages=[], tools=[], deferred_tools=[],
            total_tokens_estimated=100, sources=[],
            memory_results=[
                {"file_path": "linked.md", "source_type": "graph_expansion",
                 "composite_score": 0.6, "similarity": 0.5,
                 "recency": 0.7, "importance": 0.5,
                 "modified_at": "", "tokens_estimated": 100,
                 "linked_from": "parent.md"},
            ],
        )
        composer = ContextComposer()
        diag = composer.build_diagnostics(config, composed)
        assert diag["memory_candidates"][0]["linked_from"] == "parent.md"


class TestContextSidecar:
    def test_write_and_read(self, config):
        from decafclaw.context_composer import read_context_sidecar, write_context_sidecar
        data = {"timestamp": "2026-04-02T12:00:00", "sources": [], "total_tokens_estimated": 100}
        write_context_sidecar(config, "test-conv", data)
        result = read_context_sidecar(config, "test-conv")
        assert result is not None
        assert result["total_tokens_estimated"] == 100

    def test_read_missing_returns_none(self, config):
        from decafclaw.context_composer import read_context_sidecar
        result = read_context_sidecar(config, "nonexistent-conv")
        assert result is None

    def test_write_fail_open(self, config):
        from decafclaw.context_composer import write_context_sidecar
        # Should not raise even with bad data
        with patch("decafclaw.context_composer.Path.write_text", side_effect=OSError("fail")):
            write_context_sidecar(config, "test-conv", {"data": True})
            # No exception raised


class TestContextComposerOnContext:
    def test_ctx_has_composer_state(self, ctx):
        assert isinstance(ctx.composer, ComposerState)

    def test_fork_gets_fresh_composer(self, ctx):
        child = ctx.fork()
        assert child.composer is not ctx.composer

    def test_fork_shares_composer_when_explicit(self, ctx):
        child = ctx.fork(composer=ctx.composer)
        assert child.composer is ctx.composer

    def test_fork_for_tool_call_shares_composer(self, ctx):
        child = ctx.fork_for_tool_call("call-123")
        assert child.composer is ctx.composer


# -- Context status ------------------------------------------------------------


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
            patch("decafclaw.agent._collect_all_tool_defs", return_value=[]),
            patch("decafclaw.memory_context.retrieve_memory_context",
                  new_callable=AsyncMock, return_value=[]),
        ):
            composer = ContextComposer()
            result = await composer.compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)
            # Last message should be the context status
            last = result.messages[-1]
            assert last["role"] == "system"
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
            patch("decafclaw.agent._collect_all_tool_defs", return_value=[]),
            patch("decafclaw.memory_context.retrieve_memory_context",
                  new_callable=AsyncMock, return_value=[]),
        ):
            composer = ContextComposer()
            result = await composer.compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)
            # Last message should be the user message, not context status
            last = result.messages[-1]
            assert last["role"] == "user"
            assert last["content"] == "hello"
