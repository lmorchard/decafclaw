"""Tests for the health_status diagnostic tool."""

import dataclasses
import time
from unittest.mock import MagicMock, patch

import pytest

from decafclaw.mcp_client import ConnState
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
    mock_state.status = ConnState.CONNECTED
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
async def test_health_tools_section_with_fetched_list(ctx):
    """Fetched tools are stored as a JSON list; reporting must not error."""
    ctx.skills.data = {"fetched_tools": ["wait"]}
    result = await tool_health_status(ctx)
    assert "[error" not in result
    assert "### Tools" in result
    assert "Active:" in result


@pytest.mark.asyncio
async def test_health_tools_section_uses_effective_budget(ctx, monkeypatch):
    """Token usage compares the active set with the clamped budget (#1741)."""
    from decafclaw.config_types import ModelConfig
    from decafclaw.tools import TOOL_DEFINITIONS
    from decafclaw.tools.tool_registry import classify_tools, estimate_tool_tokens

    monkeypatch.setattr("decafclaw.mcp_client.get_registry", lambda: None)
    ctx.config.model_configs["tiny"] = ModelConfig(provider="local", model="tiny", context_window_size=2000)
    ctx.active_model = "tiny"

    result = await tool_health_status(ctx)

    all_defs = TOOL_DEFINITIONS + ctx.tools.extra_definitions
    active, _, effective = classify_tools(all_defs, ctx.config, model_name="tiny")
    active_tokens = estimate_tool_tokens(active)
    assert f"- **Token usage:** ~{active_tokens:,} / {effective:,} budget" in result
    # The critical floor exceeds the raw 10% budget, so the denominator is clamped.
    assert effective > ctx.config.tool_context_budget_for("tiny")
    # The numerator is the active set, not every definition (deferred included).
    assert active_tokens < estimate_tool_tokens(all_defs)


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
    conn.execute("INSERT INTO memory_embeddings VALUES (1,'f','h1','t',X'00','journal','2024-01-01')")
    conn.execute("INSERT INTO memory_embeddings VALUES (2,'f','h2','t',X'00','page','2024-01-01')")
    conn.commit()
    conn.close()

    result = await tool_health_status(ctx)
    assert "Journal:" in result
    assert "Page:" in result


@pytest.mark.asyncio
async def test_health_full_report_all_sections(ctx):
    """Full report includes all five section headers."""
    result = await tool_health_status(ctx)
    assert "## Agent Health" in result
    assert "### Process" in result
    assert "### MCP Servers" in result
    assert "### Heartbeat" in result
    assert "### Tools" in result
    assert "### Embeddings" in result


# -- Deploy-grade health payload (#1644) -------------------------------------


@pytest.fixture(autouse=True)
def _clear_commit_sha_cache():
    from decafclaw.tools.health import read_commit_sha

    read_commit_sha.cache_clear()
    yield
    read_commit_sha.cache_clear()


def test_read_commit_sha_from_environment(monkeypatch):
    from decafclaw.tools.health import COMMIT_SHA_ENV, read_commit_sha

    monkeypatch.setenv(COMMIT_SHA_ENV, "abc123")
    assert read_commit_sha() == "abc123"


def test_read_commit_sha_unavailable(monkeypatch):
    import decafclaw.tools.health as health_module

    monkeypatch.delenv(health_module.COMMIT_SHA_ENV, raising=False)
    monkeypatch.setattr(health_module, "_git_commit_sha", lambda: None)
    assert health_module.read_commit_sha() is None


def test_mcp_data_reports_disconnected_server():
    """A DISCONNECTED server is in the configured set, not failed/connected."""
    from decafclaw.tools.health import get_mcp_data

    connected = MagicMock()
    connected.status = ConnState.CONNECTED
    connected.tools = {"mcp__alpha__one": None, "mcp__alpha__two": None}
    connected.retry_count = 0
    disconnected = MagicMock()
    disconnected.status = ConnState.DISCONNECTED
    disconnected.tools = {}
    disconnected.retry_count = 0
    registry = MagicMock()
    registry.servers = {"alpha": connected, "beta": disconnected}

    with patch("decafclaw.mcp_client.get_registry", return_value=registry):
        data = get_mcp_data()

    assert data["configured"] == 2
    assert data["connected"] == 1
    assert data["disconnected"] == 1
    assert data["connected_servers"] == ["alpha"]
    assert data["tool_count"] == 2
    assert data["servers"]["beta"]["status"] == "disconnected"


def test_health_data_includes_commit_and_busy(config, monkeypatch):
    from decafclaw.tools.health import COMMIT_SHA_ENV, get_health_data

    monkeypatch.setenv(COMMIT_SHA_ENV, "deadbeef")
    manager = MagicMock()
    manager.get_busy_conversations.return_value = ["conv-1"]

    data = get_health_data(config, manager=manager)

    assert data["commit"] == "deadbeef"
    assert data["busy"] == {"known": True, "active": True, "conversations": ["conv-1"]}


def test_health_data_busy_unknown_without_manager(config):
    from decafclaw.tools.health import get_health_data

    data = get_health_data(config)
    assert data["busy"] == {"known": False, "active": False, "conversations": []}
