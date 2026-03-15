"""Tests for core tools and tool execution."""

import pytest

from decafclaw.tools import execute_tool
from decafclaw.tools.core import tool_think


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
    assert result.text == "mock result: hello"


@pytest.mark.asyncio
async def test_execute_tool_extra_tools_not_present(ctx):
    """execute_tool works when ctx has no extra_tools (backward compat)."""
    result = await execute_tool(ctx, "think", {"content": "test"})
    assert result.text == "OK"


@pytest.mark.asyncio
async def test_execute_tool_unknown(ctx):
    """Unknown tool returns error message."""
    result = await execute_tool(ctx, "nonexistent_tool", {})
    assert "[error: unknown tool:" in result.text


@pytest.mark.asyncio
async def test_execute_tool_mcp_routes_to_registry(ctx, monkeypatch):
    """MCP-namespaced tools route to the MCP registry."""
    from unittest.mock import AsyncMock, MagicMock

    from decafclaw import mcp_client

    mock_fn = AsyncMock(return_value="mcp result")
    mock_registry = MagicMock()
    mock_registry.get_tools.return_value = {"mcp__test__my_tool": mock_fn}

    monkeypatch.setattr(mcp_client, "_registry", mock_registry)
    result = await execute_tool(ctx, "mcp__test__my_tool", {"arg": "val"})
    assert result.text == "mcp result"
    mock_fn.assert_called_once_with({"arg": "val"})


@pytest.mark.asyncio
async def test_execute_tool_mcp_no_registry(ctx, monkeypatch):
    """MCP tool with no registry returns error."""
    from decafclaw import mcp_client
    monkeypatch.setattr(mcp_client, "_registry", None)
    result = await execute_tool(ctx, "mcp__test__my_tool", {})
    assert "[error: MCP tool" in result.text


@pytest.mark.asyncio
async def test_execute_tool_mcp_tool_not_found(ctx, monkeypatch):
    """MCP tool not in registry returns error."""
    from unittest.mock import MagicMock

    from decafclaw import mcp_client

    mock_registry = MagicMock()
    mock_registry.get_tools.return_value = {}

    monkeypatch.setattr(mcp_client, "_registry", mock_registry)
    result = await execute_tool(ctx, "mcp__test__missing", {})
    assert "[error: MCP tool" in result.text


@pytest.mark.asyncio
async def test_execute_tool_returns_tool_result(ctx):
    """execute_tool always returns a ToolResult."""
    from decafclaw.media import ToolResult
    result = await execute_tool(ctx, "think", {"content": "test"})
    assert isinstance(result, ToolResult)
    assert result.media == []


def test_context_stats(ctx):
    """context_stats returns a formatted stats report."""
    from decafclaw.tools.core import tool_context_stats
    # Set up minimal context state
    ctx.messages = [
        {"role": "system", "content": "You are a test agent."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    ctx.total_prompt_tokens = 100
    ctx.total_completion_tokens = 20

    result = tool_context_stats(ctx)
    assert "Context Stats" in result
    assert "System prompt" in result
    assert "Tool definitions" in result
    assert "Conversation history" in result
    assert "100" in result  # prompt tokens
    assert "user" in result
    assert "assistant" in result
