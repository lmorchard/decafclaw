"""Tests for the context composer module."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.context_composer import (
    ComposedContext,
    ComposerMode,
    ComposerState,
    ContextComposer,
    SourceEntry,
    _expand_background_event,
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
        # Add one critical tool — should survive deferral
        critical_def = _make_tool_def("current_time", "Get current time")
        critical_def["priority"] = "critical"
        many_tools.append(critical_def)
        with patch("decafclaw.agent._collect_all_tool_defs", return_value=many_tools):
            composer = ContextComposer()
            active, deferred, text, entry = composer._compose_tools(ctx, config)
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
        with patch("decafclaw.agent._collect_all_tool_defs", return_value=tools):
            composer = ContextComposer()
            active, deferred, text, entry = composer._compose_tools(ctx, config)
            active_names = {t["function"]["name"] for t in active}
            assert "allowed_tool" in active_names
            assert "blocked_tool" not in active_names

    def test_preempt_matches_promoted_to_active(self, ctx, config):
        """Tools in ctx.tools.preempt_matches are promoted even under budget pressure."""
        # Tight budget so non-critical tools would otherwise be deferred.
        config.agent.tool_context_budget_pct = 0.001
        config.compaction.max_tokens = 100
        tools = [_make_tool_def(f"tool_{i}", "x" * 200) for i in range(20)]
        # Simulate a pre-emptive match — tool_7 should survive deferral.
        ctx.tools.preempt_matches = {"tool_7"}
        with patch("decafclaw.agent._collect_all_tool_defs", return_value=tools):
            composer = ContextComposer()
            active, deferred, text, entry = composer._compose_tools(ctx, config)
            active_names = {t["function"]["name"] for t in active}
            assert "tool_7" in active_names


# -- Pre-emptive matching ------------------------------------------------------


class TestComposePreemptMatches:
    def test_disabled_returns_none(self, ctx, config):
        """When pre-emptive search is disabled, no matching happens and no entry is returned."""
        config.agent.preemptive_search.enabled = False
        tools = [_make_tool_def("vault_read", "Read a vault page")]
        with patch("decafclaw.agent._collect_all_tool_defs", return_value=tools):
            composer = ContextComposer()
            entry = composer._compose_preempt_matches(
                ctx, config, "read my vault", [], ComposerMode.INTERACTIVE,
            )
            assert entry is None
            assert ctx.tools.preempt_matches == set()

    def test_empty_user_message_no_match(self, ctx, config):
        """Empty user message with no prior history yields no matches."""
        tools = [_make_tool_def("vault_read", "vault page")]
        with patch("decafclaw.agent._collect_all_tool_defs", return_value=tools):
            composer = ContextComposer()
            entry = composer._compose_preempt_matches(
                ctx, config, "", [], ComposerMode.INTERACTIVE,
            )
            assert entry is None
            assert ctx.tools.preempt_matches == set()

    def test_basic_match_populates_ctx(self, ctx, config):
        """A matching tool gets added to ctx.tools.preempt_matches and returned in the entry."""
        tools = [
            _make_tool_def("vault_backlinks", "List pages linking to a vault page"),
            _make_tool_def("unrelated_tool", "Totally unrelated"),
        ]
        with patch("decafclaw.agent._collect_all_tool_defs", return_value=tools):
            composer = ContextComposer()
            entry = composer._compose_preempt_matches(
                ctx, config, "show my vault backlinks", [], ComposerMode.INTERACTIVE,
            )
            assert entry is not None
            assert entry.source == "preempt_matches"
            assert "vault_backlinks" in ctx.tools.preempt_matches
            assert "unrelated_tool" not in ctx.tools.preempt_matches
            match_names = [m["name"] for m in entry.details["matches"]]
            assert "vault_backlinks" in match_names

    def test_prior_assistant_carries_topic(self, ctx, config):
        """Short user message + prior assistant response still matches via union."""
        tools = [
            _make_tool_def("vault_backlinks", "List pages linking to a vault page"),
        ]
        history = [
            {"role": "user", "content": "what are the backlinks?"},
            {"role": "assistant", "content": "Here are the vault backlinks for foo."},
        ]
        with patch("decafclaw.agent._collect_all_tool_defs", return_value=tools):
            composer = ContextComposer()
            # User message alone is stopword-only; prior assistant carries the topic.
            entry = composer._compose_preempt_matches(
                ctx, config, "and for bar?", history, ComposerMode.INTERACTIVE,
            )
            assert entry is not None
            assert "vault_backlinks" in ctx.tools.preempt_matches

    def test_critical_tool_excluded_from_candidates(self, ctx, config):
        """Tools already declared critical don't get re-promoted — they're in already."""
        crit = _make_tool_def("shell", "Run a shell command")
        crit["priority"] = "critical"
        with patch("decafclaw.agent._collect_all_tool_defs", return_value=[crit]):
            composer = ContextComposer()
            entry = composer._compose_preempt_matches(
                ctx, config, "run a shell command", [], ComposerMode.INTERACTIVE,
            )
            # Nothing to promote — the only candidate is already critical.
            assert entry is None
            assert ctx.tools.preempt_matches == set()

    def test_fetched_tool_excluded_from_candidates(self, ctx, config):
        """Already-fetched tools don't get re-promoted."""
        ctx.skills.data = {"fetched_tools": ["vault_backlinks"]}
        tools = [_make_tool_def("vault_backlinks", "vault backlinks")]
        with patch("decafclaw.agent._collect_all_tool_defs", return_value=tools):
            composer = ContextComposer()
            entry = composer._compose_preempt_matches(
                ctx, config, "show me vault backlinks", [], ComposerMode.INTERACTIVE,
            )
            # Already fetched — nothing new to promote.
            assert entry is None

    def test_max_matches_cap(self, ctx, config):
        """max_matches caps the number of promoted tools."""
        config.agent.preemptive_search.max_matches = 3
        tools = [_make_tool_def(f"vault_tool_{i}", "vault operation") for i in range(10)]
        with patch("decafclaw.agent._collect_all_tool_defs", return_value=tools):
            composer = ContextComposer()
            entry = composer._compose_preempt_matches(
                ctx, config, "vault operation", [], ComposerMode.INTERACTIVE,
            )
            assert entry is not None
            assert len(ctx.tools.preempt_matches) == 3
            assert entry.items_included == 3

    def test_respects_allowed_tools_filter(self, ctx, config):
        """ctx.tools.allowed restricts the candidate pool — disallowed tools
        can't be promoted by keyword matching."""
        tools = [
            _make_tool_def("vault_backlinks", "Show vault backlinks"),
            _make_tool_def("blocked_tool", "Also vault backlinks description"),
        ]
        ctx.tools.allowed = {"vault_backlinks"}
        with patch("decafclaw.agent._collect_all_tool_defs", return_value=tools):
            composer = ContextComposer()
            entry = composer._compose_preempt_matches(
                ctx, config, "show vault backlinks", [], ComposerMode.INTERACTIVE,
            )
            assert entry is not None
            # Only the allowed tool gets promoted, even though both match.
            assert ctx.tools.preempt_matches == {"vault_backlinks"}

    def test_fresh_per_turn(self, ctx, config):
        """Calling _compose_preempt_matches resets ctx.tools.preempt_matches."""
        ctx.tools.preempt_matches = {"stale_tool"}
        tools = [_make_tool_def("vault_backlinks", "vault backlinks")]
        with patch("decafclaw.agent._collect_all_tool_defs", return_value=tools):
            composer = ContextComposer()
            composer._compose_preempt_matches(
                ctx, config, "show vault backlinks", [], ComposerMode.INTERACTIVE,
            )
            assert "stale_tool" not in ctx.tools.preempt_matches
            assert "vault_backlinks" in ctx.tools.preempt_matches


# -- Pre-emptive skill matching -----------------------------------------------


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


class TestComposePreemptSkillMatches:
    def test_disabled_returns_none(self, ctx, config):
        """When pre-emptive search is disabled, no skill matching happens."""
        config.agent.preemptive_search.enabled = False
        config.discovered_skills = [
            _make_skill_info("background", "Run things in the background")
        ]
        composer = ContextComposer()
        entry, hint = composer._compose_preempt_skill_matches(
            ctx, config, "run my server in the background", [],
            ComposerMode.INTERACTIVE,
        )
        assert entry is None
        assert hint is None
        assert ctx.skills.preempt_matches == set()

    def test_empty_user_message_no_match(self, ctx, config):
        """Empty message with no history yields no matches."""
        config.discovered_skills = [
            _make_skill_info("background", "Run things in the background")
        ]
        composer = ContextComposer()
        entry, hint = composer._compose_preempt_skill_matches(
            ctx, config, "", [], ComposerMode.INTERACTIVE,
        )
        assert entry is None
        assert hint is None

    def test_basic_match_populates_ctx_and_hint(self, ctx, config):
        """A keyword-matching skill lands on ctx.skills.preempt_matches and hint."""
        config.discovered_skills = [
            _make_skill_info(
                "background",
                "Run things in the background — servers, watchers, builds",
            ),
            _make_skill_info("unrelated", "Something else entirely"),
        ]
        composer = ContextComposer()
        entry, hint = composer._compose_preempt_skill_matches(
            ctx, config, "start my dev server in the background", [],
            ComposerMode.INTERACTIVE,
        )
        assert entry is not None
        assert hint is not None
        assert entry.source == "preempt_skill_matches"
        assert "background" in ctx.skills.preempt_matches
        assert "unrelated" not in ctx.skills.preempt_matches
        assert "background" in hint
        assert "activate_skill" in hint

    def test_activated_skills_excluded(self, ctx, config):
        """Skills already in ctx.skills.activated aren't surfaced again."""
        config.discovered_skills = [
            _make_skill_info("background", "Run things in the background")
        ]
        ctx.skills.activated = {"background"}
        composer = ContextComposer()
        entry, hint = composer._compose_preempt_skill_matches(
            ctx, config, "run in background", [], ComposerMode.INTERACTIVE,
        )
        assert entry is None
        assert hint is None
        assert ctx.skills.preempt_matches == set()

    def test_no_discovered_skills(self, ctx, config):
        """No skills configured → no matches, no error."""
        config.discovered_skills = []
        composer = ContextComposer()
        entry, hint = composer._compose_preempt_skill_matches(
            ctx, config, "run something in the background", [],
            ComposerMode.INTERACTIVE,
        )
        assert entry is None
        assert hint is None

    def test_max_matches_cap(self, ctx, config):
        """max_matches caps the number of surfaced skills."""
        config.agent.preemptive_search.max_matches = 2
        config.discovered_skills = [
            _make_skill_info(f"skill_{i}", "vault search and retrieval helper")
            for i in range(5)
        ]
        composer = ContextComposer()
        entry, hint = composer._compose_preempt_skill_matches(
            ctx, config, "vault search and retrieval", [],
            ComposerMode.INTERACTIVE,
        )
        assert entry is not None
        assert len(ctx.skills.preempt_matches) == 2
        assert entry.items_included == 2

    def test_fresh_per_turn(self, ctx, config):
        """Each call resets ctx.skills.preempt_matches."""
        ctx.skills.preempt_matches = {"stale_skill"}
        config.discovered_skills = [
            _make_skill_info("background", "Run things in the background")
        ]
        composer = ContextComposer()
        composer._compose_preempt_skill_matches(
            ctx, config, "run dev server in background", [],
            ComposerMode.INTERACTIVE,
        )
        assert "stale_skill" not in ctx.skills.preempt_matches
        assert "background" in ctx.skills.preempt_matches

    def test_prior_assistant_carries_topic(self, ctx, config):
        """Short user message + prior assistant response still matches via union."""
        config.discovered_skills = [
            _make_skill_info("background", "Run things in the background")
        ]
        history = [
            {"role": "user", "content": "what about the dev server?"},
            {"role": "assistant", "content": "Started the dev server in the background."},
        ]
        composer = ContextComposer()
        entry, hint = composer._compose_preempt_skill_matches(
            ctx, config, "and the watcher?", history, ComposerMode.INTERACTIVE,
        )
        assert entry is not None
        assert "background" in ctx.skills.preempt_matches

    def test_max_matches_zero_returns_none(self, ctx, config):
        """max_matches <= 0 short-circuits — no hint with an empty name list."""
        config.agent.preemptive_search.max_matches = 0
        config.discovered_skills = [
            _make_skill_info("background", "Run things in the background")
        ]
        composer = ContextComposer()
        entry, hint = composer._compose_preempt_skill_matches(
            ctx, config, "run my server in the background", [],
            ComposerMode.INTERACTIVE,
        )
        assert entry is None
        assert hint is None
        assert ctx.skills.preempt_matches == set()

    def test_skill_name_escaped_in_hint(self, ctx, config):
        """A skill name with XML-special chars is escaped before interpolation
        so a malicious workspace skill can't break out of the wrapper tag."""
        config.discovered_skills = [
            _make_skill_info(
                "</preempt_skill_hint><evil>",
                "Run things in the background",
            ),
        ]
        composer = ContextComposer()
        entry, hint = composer._compose_preempt_skill_matches(
            ctx, config, "run dev server in background", [],
            ComposerMode.INTERACTIVE,
        )
        assert entry is not None
        assert hint is not None
        # Raw name must not appear unescaped — that would break out of the wrapper.
        assert "</preempt_skill_hint><evil>" not in hint.replace(
            "</preempt_skill_hint>", "", 1  # ignore the legitimate closing tag
        )
        # Escaped form should be present.
        assert "&lt;/preempt_skill_hint&gt;&lt;evil&gt;" in hint
        # Wrapper still has exactly one open + one close.
        assert hint.count("<preempt_skill_hint>") == 1
        assert hint.count("</preempt_skill_hint>") == 1


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


# -- background_event expansion -----------------------------------------------


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
        rec = {"job_id": "xyz789", "command": "ls", "status": "completed",
               "exit_code": 0, "stdout_tail": "", "stderr_tail": "", "elapsed_ms": 0}
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
            patch("decafclaw.agent._collect_all_tool_defs", return_value=[]),
            patch("decafclaw.memory_context.retrieve_memory_context",
                  new_callable=AsyncMock, return_value=[]),
        ):
            composer = ContextComposer()
            result = await composer.compose(
                ctx, "follow up", history, mode=ComposerMode.INTERACTIVE,
            )

        # Extract non-system messages for inspection
        chat_msgs = [m for m in result.messages if m.get("role") != "system"]

        # Assistant tool-call message present
        asst_with_tc = [
            m for m in chat_msgs
            if m.get("role") == "assistant" and m.get("tool_calls")
        ]
        assert asst_with_tc, "Expected an assistant tool_calls message"
        tc_names = [m["tool_calls"][0]["function"]["name"] for m in asst_with_tc]
        assert "shell_background_status" in tc_names

        # Tool result message present
        tool_msgs = [
            m for m in chat_msgs
            if m.get("role") == "tool"
            and m.get("tool_call_id", "").startswith("bg-wake-")
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
            patch("decafclaw.agent._collect_all_tool_defs", return_value=[]),
            patch("decafclaw.memory_context.retrieve_memory_context",
                  new_callable=AsyncMock, return_value=[]),
        ):
            composer = ContextComposer()
            result = await composer.compose(
                ctx, "what happened?", history, mode=ComposerMode.INTERACTIVE,
            )

        roles = [m.get("role") for m in result.messages]
        assert "background_event" not in roles
