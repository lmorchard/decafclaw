"""Tests for the health_status diagnostic tool."""

from unittest.mock import MagicMock, patch

import pytest

from decafclaw.tools.health import tool_health_status


@pytest.mark.asyncio
async def test_health_process_section(ctx):
    """Process section shows uptime and memory."""
    result = await tool_health_status(ctx)
    assert "## Agent Health" in result
    assert "### Process" in result
    assert "Uptime:" in result
    assert "Memory (RSS):" in result
    assert "MB" in result


@pytest.mark.asyncio
async def test_health_mcp_section_no_servers(ctx):
    """MCP section handles no registry gracefully."""
    with patch("decafclaw.mcp_client.get_registry", return_value=None):
        result = await tool_health_status(ctx)
    assert "No MCP servers configured" in result


@pytest.mark.asyncio
async def test_health_mcp_section_with_servers(ctx):
    """MCP section shows server status table."""
    mock_registry = MagicMock()
    mock_state = MagicMock()
    mock_state.status = "connected"
    mock_state.tools = {"mcp__test__a": None, "mcp__test__b": None}
    mock_state.retry_count = 0
    mock_registry.servers = {"test-server": mock_state}

    with patch("decafclaw.mcp_client.get_registry", return_value=mock_registry):
        result = await tool_health_status(ctx)
    assert "test-server" in result
    assert "connected" in result
