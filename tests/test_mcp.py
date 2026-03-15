"""Tests for MCP client — config parsing, namespacing, and tool conversion."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.mcp_client import (
    MCPRegistry,
    MCPServerConfig,
    MCPServerState,
    _expand_env,
    _validate_server_name,
    _namespace_tool,
    _parse_namespace,
    _convert_tool_definition,
    _convert_mcp_response,
    load_mcp_config,
)


# -- env var expansion tests --


def test_expand_env_simple(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "hello")
    assert _expand_env("${TEST_KEY}") == "hello"


def test_expand_env_with_default(monkeypatch):
    monkeypatch.delenv("MISSING_KEY", raising=False)
    assert _expand_env("${MISSING_KEY:-fallback}") == "fallback"


def test_expand_env_missing_no_default(monkeypatch):
    monkeypatch.delenv("MISSING_KEY", raising=False)
    assert _expand_env("${MISSING_KEY}") == ""


def test_expand_env_in_string(monkeypatch):
    monkeypatch.setenv("TOKEN", "abc123")
    assert _expand_env("Bearer ${TOKEN}") == "Bearer abc123"


def test_expand_env_default_not_used_when_set(monkeypatch):
    monkeypatch.setenv("MY_VAR", "real")
    assert _expand_env("${MY_VAR:-default}") == "real"


# -- server name validation tests --


def test_validate_server_name_valid():
    assert _validate_server_name("my-server") is True
    assert _validate_server_name("server1") is True
    assert _validate_server_name("a") is True


def test_validate_server_name_invalid():
    assert _validate_server_name("MY SERVER") is False
    assert _validate_server_name("foo__bar") is False
    assert _validate_server_name("") is False
    assert _validate_server_name("-leading") is False
    assert _validate_server_name("CamelCase") is False


# -- config loading tests --


def _write_config(config, data):
    """Write an mcp_servers.json in the agent path."""
    path = config.agent_path / "mcp_servers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_load_mcp_config_valid(config):
    _write_config(config, {
        "mcpServers": {
            "test-server": {
                "type": "stdio",
                "command": "echo",
                "args": ["hello"],
                "env": {"FOO": "bar"},
            },
            "remote": {
                "type": "http",
                "url": "https://example.com/mcp",
                "headers": {"X-Key": "val"},
            },
        }
    })
    configs = load_mcp_config(config)
    assert len(configs) == 2
    names = {c.name for c in configs}
    assert "test-server" in names
    assert "remote" in names

    stdio = next(c for c in configs if c.name == "test-server")
    assert stdio.type == "stdio"
    assert stdio.command == "echo"
    assert stdio.args == ["hello"]

    http = next(c for c in configs if c.name == "remote")
    assert http.type == "http"
    assert http.url == "https://example.com/mcp"


def test_load_mcp_config_missing_file(config):
    configs = load_mcp_config(config)
    assert configs == []


def test_load_mcp_config_skips_invalid_names(config):
    _write_config(config, {
        "mcpServers": {
            "valid-name": {"type": "stdio", "command": "echo"},
            "INVALID NAME": {"type": "stdio", "command": "echo"},
        }
    })
    configs = load_mcp_config(config)
    assert len(configs) == 1
    assert configs[0].name == "valid-name"


def test_load_mcp_config_expands_env(config, monkeypatch):
    monkeypatch.setenv("MY_API_KEY", "secret123")
    _write_config(config, {
        "mcpServers": {
            "api-server": {
                "type": "http",
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer ${MY_API_KEY}"},
            }
        }
    })
    configs = load_mcp_config(config)
    assert configs[0].headers["Authorization"] == "Bearer secret123"


def test_load_mcp_config_default_timeout(config):
    _write_config(config, {
        "mcpServers": {
            "server": {"type": "stdio", "command": "echo"}
        }
    })
    configs = load_mcp_config(config)
    assert configs[0].timeout == 30000


def test_load_mcp_config_custom_timeout(config):
    _write_config(config, {
        "mcpServers": {
            "server": {"type": "stdio", "command": "echo", "timeout": 60000}
        }
    })
    configs = load_mcp_config(config)
    assert configs[0].timeout == 60000


# -- namespacing tests --


def test_namespace_tool():
    assert _namespace_tool("my-server", "get_data") == "mcp__my-server__get_data"


def test_parse_namespace_roundtrip():
    namespaced = _namespace_tool("my-server", "get_data")
    result = _parse_namespace(namespaced)
    assert result == ("my-server", "get_data")


def test_parse_namespace_non_mcp():
    assert _parse_namespace("think") is None
    assert _parse_namespace("shell") is None


def test_parse_namespace_malformed():
    assert _parse_namespace("mcp__") is None
    assert _parse_namespace("mcp__server") is None
    assert _parse_namespace("mcp____tool") is None


# -- tool definition conversion tests --


def test_convert_tool_definition_dict():
    mcp_tool = {
        "name": "get_weather",
        "description": "Get weather for a location",
        "inputSchema": {
            "type": "object",
            "properties": {"location": {"type": "string"}},
            "required": ["location"],
        },
    }
    result = _convert_tool_definition("weather-server", mcp_tool)
    assert result["type"] == "function"
    assert result["function"]["name"] == "mcp__weather-server__get_weather"
    assert result["function"]["description"] == "Get weather for a location"
    assert result["function"]["parameters"]["required"] == ["location"]


def test_convert_tool_definition_object():
    """Handles SDK Tool objects with attribute access."""
    class FakeTool:
        name = "search"
        description = "Search things"
        inputSchema = {"type": "object", "properties": {}}

    result = _convert_tool_definition("my-server", FakeTool())
    assert result["function"]["name"] == "mcp__my-server__search"


# -- response conversion tests --


def test_convert_mcp_response_text():
    result = {
        "content": [
            {"type": "text", "text": "Hello"},
            {"type": "text", "text": "World"},
        ],
        "isError": False,
    }
    tr = _convert_mcp_response(result)
    assert tr.text == "Hello\nWorld"
    assert tr.media == []


def test_convert_mcp_response_error():
    result = {
        "content": [{"type": "text", "text": "Something broke"}],
        "isError": True,
    }
    tr = _convert_mcp_response(result)
    assert tr.text == "[error: Something broke]"


def test_convert_mcp_response_image_media():
    import base64
    img_data = base64.b64encode(b"fake-png-data").decode()
    result = {
        "content": [{"type": "image", "data": img_data, "mimeType": "image/png"}],
        "isError": False,
    }
    tr = _convert_mcp_response(result)
    assert "Image attached" in tr.text
    assert len(tr.media) == 1
    assert tr.media[0]["type"] == "file"
    assert tr.media[0]["data"] == b"fake-png-data"
    assert tr.media[0]["content_type"] == "image/png"


def test_convert_mcp_response_audio_media():
    import base64
    audio_data = base64.b64encode(b"fake-wav").decode()
    result = {
        "content": [{"type": "audio", "data": audio_data, "mimeType": "audio/wav"}],
        "isError": False,
    }
    tr = _convert_mcp_response(result)
    assert "Audio attached" in tr.text
    assert len(tr.media) == 1
    assert tr.media[0]["content_type"] == "audio/wav"


def test_convert_mcp_response_mixed_text_and_image():
    import base64
    img_data = base64.b64encode(b"img").decode()
    result = {
        "content": [
            {"type": "text", "text": "Here's your image:"},
            {"type": "image", "data": img_data, "mimeType": "image/jpeg"},
        ],
        "isError": False,
    }
    tr = _convert_mcp_response(result)
    assert "Here's your image:" in tr.text
    assert "Image attached" in tr.text
    assert len(tr.media) == 1


def test_convert_mcp_response_empty():
    result = {"content": [], "isError": False}
    tr = _convert_mcp_response(result)
    assert tr.text == "(no content)"
    assert tr.media == []


# -- connection tests (mocked SDK) --


def _make_fake_tool(name, description="A test tool"):
    """Create a fake MCP tool object."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.inputSchema = {"type": "object", "properties": {}}
    return tool


def _make_mock_session(tools=None):
    """Create a mock ClientSession that returns given tools."""
    session = AsyncMock()
    tools_result = MagicMock()
    tools_result.tools = tools or []
    session.list_tools = AsyncMock(return_value=tools_result)
    session.initialize = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_connect_server_success():
    """Successful connection populates state correctly."""
    fake_tool = _make_fake_tool("get_strategy", "Get a strategy")
    mock_session = _make_mock_session([fake_tool])

    registry = MCPRegistry()

    # Patch _connect_stdio to return our mock session
    async def fake_connect_stdio(exit_stack, server_config):
        return mock_session

    registry._connect_stdio = fake_connect_stdio

    cfg = MCPServerConfig(name="test-server", type="stdio", command="echo")
    await registry.connect_server(cfg)

    state = registry.servers["test-server"]
    assert state.status == "connected"
    assert "mcp__test-server__get_strategy" in state.tools
    assert len(state.tool_definitions) == 1


@pytest.mark.asyncio
async def test_connect_server_failure():
    """Failed connection sets status to 'failed' without raising."""
    registry = MCPRegistry()

    async def failing_connect(exit_stack, server_config):
        raise ConnectionError("boom")

    registry._connect_stdio = failing_connect

    cfg = MCPServerConfig(name="bad-server", type="stdio", command="nonexistent")
    await registry.connect_server(cfg)

    state = registry.servers["bad-server"]
    assert state.status == "failed"
    assert len(state.tools) == 0


@pytest.mark.asyncio
async def test_disconnect_server():
    """Disconnect cleans up state."""
    fake_tool = _make_fake_tool("tool1")
    mock_session = _make_mock_session([fake_tool])

    registry = MCPRegistry()
    registry._connect_stdio = AsyncMock(return_value=mock_session)

    cfg = MCPServerConfig(name="test-server", type="stdio", command="echo")
    await registry.connect_server(cfg)
    assert registry.servers["test-server"].status == "connected"

    await registry.disconnect_server("test-server")
    state = registry.servers["test-server"]
    assert state.status == "disconnected"
    assert len(state.tools) == 0
    assert state.session is None


@pytest.mark.asyncio
async def test_get_tools_only_connected():
    """get_tools only returns tools from connected servers."""
    registry = MCPRegistry()
    registry.servers["good"] = MCPServerState(
        config=MCPServerConfig(name="good", type="stdio"),
        status="connected",
        tools={"mcp__good__tool1": AsyncMock()},
    )
    registry.servers["bad"] = MCPServerState(
        config=MCPServerConfig(name="bad", type="stdio"),
        status="failed",
        tools={"mcp__bad__tool2": AsyncMock()},
    )

    tools = registry.get_tools()
    assert "mcp__good__tool1" in tools
    assert "mcp__bad__tool2" not in tools


@pytest.mark.asyncio
async def test_tool_caller_timeout():
    """Tool call wrapper respects timeout."""
    import asyncio as aio

    registry = MCPRegistry()

    # Create a session that hangs
    slow_session = AsyncMock()

    async def slow_call(name, args):
        await aio.sleep(10)

    slow_session.call_tool = slow_call

    cfg = MCPServerConfig(name="slow", type="stdio", timeout=100)  # 100ms timeout
    # Set up server state so the caller can find the session
    registry.servers["slow"] = MCPServerState(
        config=cfg, status="connected", session=slow_session,
    )
    caller = registry._make_tool_caller("slow", "slow_tool", cfg.timeout)

    result = await caller({"query": "test"})
    assert "timed out" in result


# -- auto-restart tests --


@pytest.mark.asyncio
async def test_maybe_reconnect_connected():
    """Connected server returns True without reconnecting."""
    registry = MCPRegistry()
    cfg = MCPServerConfig(name="ok", type="stdio")
    registry.servers["ok"] = MCPServerState(config=cfg, status="connected")

    assert await registry._maybe_reconnect("ok") is True


@pytest.mark.asyncio
async def test_maybe_reconnect_max_retries():
    """Server with max retries gives up."""
    registry = MCPRegistry()
    cfg = MCPServerConfig(name="dead", type="stdio")
    registry.servers["dead"] = MCPServerState(
        config=cfg, status="failed", retry_count=3,
    )

    assert await registry._maybe_reconnect("dead") is False


@pytest.mark.asyncio
async def test_maybe_reconnect_success():
    """Failed server reconnects successfully."""
    fake_tool = _make_fake_tool("tool1")
    mock_session = _make_mock_session([fake_tool])

    registry = MCPRegistry()
    registry._connect_stdio = AsyncMock(return_value=mock_session)

    cfg = MCPServerConfig(name="flaky", type="stdio", command="echo")
    registry.servers["flaky"] = MCPServerState(
        config=cfg, status="failed", retry_count=0, last_retry_time=0.0,
    )

    assert await registry._maybe_reconnect("flaky") is True
    assert registry.servers["flaky"].status == "connected"


@pytest.mark.asyncio
async def test_maybe_reconnect_increments_retry():
    """Failed reconnection increments retry count."""
    registry = MCPRegistry()

    async def failing_connect(exit_stack, server_config):
        raise ConnectionError("still broken")

    registry._connect_stdio = failing_connect

    cfg = MCPServerConfig(name="broken", type="stdio", command="nope")
    registry.servers["broken"] = MCPServerState(
        config=cfg, status="failed", retry_count=0, last_retry_time=0.0,
    )

    assert await registry._maybe_reconnect("broken") is False
    assert registry.servers["broken"].retry_count == 1


# -- mcp_status tool tests --


@pytest.mark.asyncio
async def test_mcp_status_no_registry(ctx, monkeypatch):
    """Status with no registry returns appropriate message."""
    from decafclaw import mcp_client
    from decafclaw.tools.mcp_tools import tool_mcp_status

    monkeypatch.setattr(mcp_client, "_registry", None)
    result = await tool_mcp_status(ctx)
    assert "No MCP servers configured" in result


@pytest.mark.asyncio
async def test_mcp_status_shows_servers(ctx, monkeypatch):
    """Status shows connected servers and their tools."""
    from decafclaw import mcp_client
    from decafclaw.tools.mcp_tools import tool_mcp_status

    registry = MCPRegistry()
    cfg = MCPServerConfig(name="test-server", type="stdio")
    registry.servers["test-server"] = MCPServerState(
        config=cfg, status="connected",
        tools={"mcp__test-server__tool1": AsyncMock(), "mcp__test-server__tool2": AsyncMock()},
    )
    monkeypatch.setattr(mcp_client, "_registry", registry)

    result = await tool_mcp_status(ctx)
    assert "test-server" in result
    assert "connected" in result
    assert "tool1" in result
    assert "tool2" in result
