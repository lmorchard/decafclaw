"""Tests for tool_search — keyword search and exact selection."""

import pytest

from decafclaw.tools.search_tools import tool_search
from decafclaw.tools.tool_registry import get_fetched_tools


def _make_tool_def(name, description="A tool."):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


SAMPLE_POOL = [
    _make_tool_def("vault_read", "Read an entire markdown file as text."),
    _make_tool_def("vault_show", "Show a section's content or document outline."),
    _make_tool_def("vault_items", "List checklist items with their indices."),
    _make_tool_def("workspace_edit", "Edit a file by exact string replacement."),
    _make_tool_def("workspace_search", "Search for a regex pattern across files."),
    _make_tool_def("mcp__playwright__click", "Click an element on the page."),
    _make_tool_def("mcp__playwright__navigate", "Navigate to a URL."),
    _make_tool_def("todo_add", "Add an item to the to-do list."),
]


@pytest.fixture
def search_ctx(ctx):
    """Ctx with a deferred pool set."""
    ctx.deferred_tool_pool = list(SAMPLE_POOL)
    return ctx


class TestKeywordSearch:
    def test_matches_name(self, search_ctx):
        result = tool_search(search_ctx, "vault")
        assert "vault_read" in result.text
        assert "vault_show" in result.text
        assert "vault_items" in result.text

    def test_matches_description(self, search_ctx):
        result = tool_search(search_ctx, "regex")
        assert "workspace_search" in result.text

    def test_case_insensitive(self, search_ctx):
        result = tool_search(search_ctx, "VAULT")
        assert "vault_read" in result.text

    def test_respects_max_results(self, search_ctx):
        result = tool_search(search_ctx, "vault", max_results=1)
        # Should have exactly 1 tool loaded
        assert "1 tool(s) loaded" in result.text

    def test_no_matches(self, search_ctx):
        result = tool_search(search_ctx, "xyznonexistent")
        assert "No tools found" in result.text

    def test_fetched_tools_updated(self, search_ctx):
        tool_search(search_ctx, "vault")
        fetched = get_fetched_tools(search_ctx)
        assert "vault_read" in fetched
        assert "vault_show" in fetched
        assert "vault_items" in fetched


class TestExactSelection:
    def test_select_by_name(self, search_ctx):
        result = tool_search(search_ctx, "select:vault_read,todo_add")
        assert "vault_read" in result.text
        assert "todo_add" in result.text
        assert "2 tool(s) loaded" in result.text

    def test_select_single(self, search_ctx):
        result = tool_search(search_ctx, "select:workspace_edit")
        assert "workspace_edit" in result.text
        assert "1 tool(s) loaded" in result.text

    def test_select_unknown_name(self, search_ctx):
        result = tool_search(search_ctx, "select:vault_read,nonexistent_tool")
        assert "vault_read" in result.text
        assert "Not found: nonexistent_tool" in result.text

    def test_select_all_unknown(self, search_ctx):
        result = tool_search(search_ctx, "select:fake1,fake2")
        assert "No tools found" in result.text

    def test_fetched_tools_updated(self, search_ctx):
        tool_search(search_ctx, "select:mcp__playwright__click")
        fetched = get_fetched_tools(search_ctx)
        assert "mcp__playwright__click" in fetched


class TestEmptyPool:
    def test_no_pool(self, ctx):
        result = tool_search(ctx, "anything")
        assert "No deferred tools" in result.text

    def test_empty_pool(self, ctx):
        ctx.deferred_tool_pool = []
        result = tool_search(ctx, "anything")
        assert "No deferred tools" in result.text
