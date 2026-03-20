"""Tests for the health_status diagnostic tool."""

import dataclasses
import time
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


@pytest.mark.asyncio
async def test_health_heartbeat_disabled(ctx):
    """Heartbeat section shows disabled when no interval."""
    ctx.config = dataclasses.replace(
        ctx.config,
        heartbeat=dataclasses.replace(ctx.config.heartbeat, interval=""),
    )
    result = await tool_health_status(ctx)
    assert "disabled" in result.lower()


@pytest.mark.asyncio
async def test_health_heartbeat_enabled(ctx):
    """Heartbeat section shows timing info when enabled."""
    ctx.config = dataclasses.replace(
        ctx.config,
        heartbeat=dataclasses.replace(ctx.config.heartbeat, interval="30m"),
    )
    # Write a fake last-run timestamp
    ts_path = ctx.config.workspace_path / ".heartbeat_last_run"
    ts_path.parent.mkdir(parents=True, exist_ok=True)
    ts_path.write_text(str(time.time() - 300))  # 5 min ago

    result = await tool_health_status(ctx)
    assert "30m" in result
    assert "ago" in result


@pytest.mark.asyncio
async def test_health_tools_section(ctx):
    """Tools section shows active/deferred counts."""
    result = await tool_health_status(ctx)
    assert "### Tools" in result
    assert "Active:" in result


@pytest.mark.asyncio
async def test_health_embeddings_no_db(ctx):
    """Embeddings section handles missing database."""
    result = await tool_health_status(ctx)
    assert "No embedding" in result or "Embeddings" in result


@pytest.mark.asyncio
async def test_health_embeddings_with_data(ctx):
    """Embeddings section shows counts when DB exists."""
    import sqlite3

    db_path = ctx.config.workspace_path / "embeddings.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE memory_embeddings (
        id INTEGER PRIMARY KEY, file_path TEXT, entry_hash TEXT UNIQUE,
        entry_text TEXT, embedding BLOB, source_type TEXT DEFAULT 'memory',
        created_at TEXT)""")
    conn.execute(
        "INSERT INTO memory_embeddings VALUES (1,'f','h1','t',X'00','memory','2024-01-01')"
    )
    conn.execute(
        "INSERT INTO memory_embeddings VALUES (2,'f','h2','t',X'00','conversation','2024-01-01')"
    )
    conn.commit()
    conn.close()

    result = await tool_health_status(ctx)
    assert "Memory:" in result
    assert "Conversation:" in result
