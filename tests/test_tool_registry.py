"""Tests for tool registry — token estimation, classification, deferred list."""

import pytest

from decafclaw.tools.tool_registry import (
    DEFAULT_ALWAYS_LOADED,
    add_fetched_tools,
    build_deferred_list_text,
    classify_tools,
    estimate_tool_tokens,
    get_always_loaded_names,
    get_description,
    get_fetched_tools,
)


def _make_tool_def(name, description="A tool.", params=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": params or {"type": "object", "properties": {}},
        },
    }


# -- Token estimation ---------------------------------------------------------


class TestEstimateToolTokens:
    def test_empty(self):
        assert estimate_tool_tokens([]) == 0

    def test_single_tool(self):
        td = _make_tool_def("test")
        tokens = estimate_tool_tokens([td])
        assert tokens > 0

    def test_proportional(self):
        small = _make_tool_def("s", "short")
        big = _make_tool_def("b", "a" * 400)
        assert estimate_tool_tokens([big]) > estimate_tool_tokens([small])


# -- Always-loaded names ------------------------------------------------------


class TestGetAlwaysLoadedNames:
    def test_defaults(self, config):
        names = get_always_loaded_names(config)
        assert "current_time" in names
        assert "activate_skill" in names
        assert "shell" in names

    def test_user_override_extends(self, config):
        config.agent.always_loaded_tools = ["my_custom_tool", "another_one"]
        names = get_always_loaded_names(config)
        assert "my_custom_tool" in names
        assert "another_one" in names
        # Defaults still present
        assert "current_time" in names

    def test_empty_override(self, config):
        config.agent.always_loaded_tools = []
        names = get_always_loaded_names(config)
        assert names == DEFAULT_ALWAYS_LOADED


# -- classify_tools ------------------------------------------------------------


class TestClassifyTools:
    def test_under_budget_no_deferral(self, config):
        """When tools fit in budget, all are active, none deferred."""
        tools = [_make_tool_def(f"tool_{i}") for i in range(3)]
        config.compaction.max_tokens = 1000000  # huge budget
        active, deferred = classify_tools(tools, config)
        assert len(active) == 3
        assert len(deferred) == 0

    def test_over_budget_splits(self, config):
        """When tools exceed budget, split into active/deferred."""
        # Create enough tools to exceed a tiny budget
        tools = [_make_tool_def(f"tool_{i}", "x" * 200) for i in range(20)]
        # Add an always-loaded tool
        tools.append(_make_tool_def("current_time", "Get current time"))
        config.compaction.max_tokens = 100  # tiny budget → 10 token budget

        active, deferred = classify_tools(tools, config)
        active_names = {td["function"]["name"] for td in active}
        deferred_names = {td["function"]["name"] for td in deferred}

        assert "current_time" in active_names
        assert len(deferred) > 0
        # Non-always-loaded tools are deferred
        assert "tool_0" in deferred_names

    def test_fetched_tools_in_active(self, config):
        """Fetched tools stay in the active set."""
        tools = [_make_tool_def(f"tool_{i}", "x" * 200) for i in range(20)]
        config.compaction.max_tokens = 100

        active, deferred = classify_tools(tools, config, fetched_names={"tool_5"})
        active_names = {td["function"]["name"] for td in active}

        assert "tool_5" in active_names


# -- get_description -----------------------------------------------------------


class TestGetDescription:
    def test_first_sentence(self):
        td = _make_tool_def("t", "First sentence. Second sentence.")
        assert get_description(td) == "First sentence."

    def test_long_description_truncated(self):
        td = _make_tool_def("t", "a" * 200)
        desc = get_description(td)
        assert len(desc) <= 80

    def test_short_description(self):
        td = _make_tool_def("t", "Short desc")
        assert get_description(td) == "Short desc"


# -- build_deferred_list_text --------------------------------------------------


class TestBuildDeferredListText:
    def test_empty(self):
        assert build_deferred_list_text([]) == ""

    def test_core_tools(self):
        core_names = {"workspace_edit", "checklist_create"}
        tools = [
            _make_tool_def("workspace_edit", "Edit a file"),
            _make_tool_def("checklist_create", "Create a checklist"),
        ]
        text = build_deferred_list_text(tools, core_names=core_names)
        assert "### Core" in text
        assert "workspace_edit" in text
        assert "checklist_create" in text

    def test_mcp_tools_grouped(self):
        tools = [
            _make_tool_def("mcp__playwright__click", "Click element"),
            _make_tool_def("mcp__playwright__navigate", "Navigate to URL"),
            _make_tool_def("mcp__slack__send", "Send a message"),
        ]
        text = build_deferred_list_text(tools, core_names=set())
        assert "### MCP: playwright" in text
        assert "### MCP: slack" in text

    def test_skill_tools(self):
        tools = [
            _make_tool_def("vault_read", "Read a file"),
        ]
        text = build_deferred_list_text(tools, core_names=set())
        assert "### Skills" in text
        assert "vault_read" in text


# -- Fetched tools helpers -----------------------------------------------------


class TestFetchedToolsHelpers:
    def test_get_empty(self, ctx):
        assert get_fetched_tools(ctx) == set()

    def test_add_and_get(self, ctx):
        add_fetched_tools(ctx, {"tool_a", "tool_b"})
        assert get_fetched_tools(ctx) == {"tool_a", "tool_b"}

    def test_add_is_additive(self, ctx):
        add_fetched_tools(ctx, {"tool_a"})
        add_fetched_tools(ctx, {"tool_b"})
        assert get_fetched_tools(ctx) == {"tool_a", "tool_b"}

    def test_stored_as_sorted_list(self, ctx):
        """Stored as sorted list for JSON serialization compatibility."""
        add_fetched_tools(ctx, {"c", "a", "b"})
        raw = ctx.skills.data["fetched_tools"]
        assert isinstance(raw, list)
        assert raw == ["a", "b", "c"]

    def test_handles_existing_list(self, ctx):
        """get_fetched_tools handles list from JSON deserialization."""
        ctx.skills.data = {"fetched_tools": ["tool_a", "tool_b"]}
        assert get_fetched_tools(ctx) == {"tool_a", "tool_b"}
