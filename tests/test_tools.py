"""Tests for core tools and tool execution."""

import pytest
from decafclaw.tools.core import tool_think
from decafclaw.tools import execute_tool


def test_think_returns_ok(ctx):
    result = tool_think(ctx, "I should search for cocktails first")
    assert result == "OK"


def test_think_with_empty_content(ctx):
    result = tool_think(ctx, "")
    assert result == "OK"


@pytest.mark.asyncio
async def test_execute_tool_with_extra_tools(ctx):
    """Skill-provided tools on ctx.extra_tools are callable via execute_tool."""
    def mock_tool(ctx, query: str) -> str:
        return f"mock result: {query}"

    ctx.extra_tools = {"mock_search": mock_tool}
    result = await execute_tool(ctx, "mock_search", {"query": "hello"})
    assert result == "mock result: hello"


@pytest.mark.asyncio
async def test_execute_tool_extra_tools_not_present(ctx):
    """execute_tool works when ctx has no extra_tools (backward compat)."""
    result = await execute_tool(ctx, "think", {"content": "test"})
    assert result == "OK"


@pytest.mark.asyncio
async def test_execute_tool_unknown(ctx):
    """Unknown tool returns error message."""
    result = await execute_tool(ctx, "nonexistent_tool", {})
    assert "[error: unknown tool:" in result
