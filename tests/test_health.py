"""Tests for the health_status diagnostic tool."""

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
