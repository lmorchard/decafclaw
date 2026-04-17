"""Tests for MCP client — config parsing, namespacing, and tool conversion."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.mcp_client import (
    MCPRegistry,
    MCPServerConfig,
    MCPServerState,
    _convert_mcp_response,
    _convert_prompt_response,
    _convert_resource_response,
    _convert_tool_definition,
    _expand_env,
    _namespace_tool,
    _parse_namespace,
    _validate_server_name,
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


def test_namespace_tool_normalizes_hyphens_to_underscores():
    """Hyphens in server names are normalized to underscores in the
    advertised tool name (workaround for Gemini's hyphen-to-underscore
    serialization of function-call identifiers)."""
    assert _namespace_tool("my-server", "get_data") == "mcp__my_server__get_data"


def test_namespace_tool_underscore_unchanged():
    """Server names already using underscores round-trip unchanged."""
    assert _namespace_tool("my_server", "get_data") == "mcp__my_server__get_data"


def test_parse_namespace_returns_normalized_segment():
    """Parsed server segment is the normalized form, not the original MCP
    server name. Callers that need the original must look it up via the
    registry."""
    namespaced = _namespace_tool("my-server", "get_data")
    result = _parse_namespace(namespaced)
    assert result == ("my_server", "get_data")


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
    # Server name hyphens are normalized to underscores in the advertised
    # tool identifier (Gemini function-call compatibility).
    assert result["function"]["name"] == "mcp__weather_server__get_weather"
    assert result["function"]["description"] == "Get weather for a location"
    assert result["function"]["parameters"]["required"] == ["location"]


def test_convert_tool_definition_object():
    """Handles SDK Tool objects with attribute access."""
    class FakeTool:
        name = "search"
        description = "Search things"
        inputSchema = {"type": "object", "properties": {}}

    result = _convert_tool_definition("my-server", FakeTool())
    assert result["function"]["name"] == "mcp__my_server__search"


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
    assert "file attached" in tr.text
    assert "image/png" in tr.text
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
    assert "file attached" in tr.text
    assert "audio/wav" in tr.text
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
    assert "file attached" in tr.text
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


def _make_fake_resource(uri, name, description="", mime_type="text/plain"):
    """Create a fake MCP resource object."""
    res = MagicMock()
    res.uri = uri
    res.name = name
    res.description = description
    res.mimeType = mime_type
    return res


def _make_fake_resource_template(uri_template, name, description=""):
    """Create a fake MCP resource template object."""
    tmpl = MagicMock()
    tmpl.uriTemplate = uri_template
    tmpl.name = name
    tmpl.description = description
    return tmpl


def _make_fake_prompt(name, description="", arguments=None):
    """Create a fake MCP prompt object."""
    prompt = MagicMock()
    prompt.name = name
    prompt.description = description
    prompt.arguments = arguments or []
    return prompt


def _make_mock_session(tools=None, capabilities=None, resources=None,
                       resource_templates=None, prompts=None):
    """Create a mock ClientSession that returns given tools/resources/prompts."""
    session = AsyncMock()
    tools_result = MagicMock()
    tools_result.tools = tools or []
    session.list_tools = AsyncMock(return_value=tools_result)
    session.initialize = AsyncMock()

    # Capabilities
    if capabilities is None:
        capabilities = MagicMock()
        capabilities.resources = None
        capabilities.prompts = None
    session.get_server_capabilities = MagicMock(return_value=capabilities)

    # Resources
    res_result = MagicMock()
    res_result.resources = resources or []
    session.list_resources = AsyncMock(return_value=res_result)
    tmpl_result = MagicMock()
    tmpl_result.resourceTemplates = resource_templates or []
    session.list_resource_templates = AsyncMock(return_value=tmpl_result)

    # Prompts
    prompts_result = MagicMock()
    prompts_result.prompts = prompts or []
    session.list_prompts = AsyncMock(return_value=prompts_result)

    return session


@pytest.mark.asyncio
async def test_connect_server_success():
    """Successful connection populates state correctly."""
    fake_tool = _make_fake_tool("get_strategy", "Get a strategy")
    mock_session = _make_mock_session([fake_tool])

    registry = MCPRegistry()

    # Patch _connect_stdio to return our mock session
    async def fake_connect_stdio(exit_stack, server_config, message_handler=None):
        return mock_session

    registry._connect_stdio = fake_connect_stdio

    cfg = MCPServerConfig(name="test-server", type="stdio", command="echo")
    await registry.connect_server(cfg)

    state = registry.servers["test-server"]
    assert state.status == "connected"
    # Server name hyphens are normalized in the advertised tool name.
    assert "mcp__test_server__get_strategy" in state.tools
    assert len(state.tool_definitions) == 1


@pytest.mark.asyncio
async def test_connect_server_failure():
    """Failed connection sets status to 'failed' without raising."""
    registry = MCPRegistry()

    async def failing_connect(exit_stack, server_config, message_handler=None):
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

    async def failing_connect(exit_stack, server_config, message_handler=None):
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
    from decafclaw.skills.mcp.tools import tool_mcp_status

    monkeypatch.setattr(mcp_client, "_registry", None)
    result = await tool_mcp_status(ctx)
    assert "No MCP servers configured" in result


@pytest.mark.asyncio
async def test_mcp_status_shows_servers(ctx, monkeypatch):
    """Status shows connected servers and their tools."""
    from decafclaw import mcp_client
    from decafclaw.skills.mcp.tools import tool_mcp_status

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
    assert "2 tool(s)" in result


# -- capability-aware discovery tests --


@pytest.mark.asyncio
async def test_connect_discovers_resources_when_capable():
    """Server with resource capabilities gets resources discovered."""
    fake_tool = _make_fake_tool("t1")
    fake_res = _make_fake_resource("file:///a.txt", "a.txt")
    fake_tmpl = _make_fake_resource_template("file:///{path}", "files")
    caps = MagicMock()
    caps.resources = MagicMock()  # truthy = has resources
    caps.prompts = None
    mock_session = _make_mock_session(
        tools=[fake_tool], capabilities=caps,
        resources=[fake_res], resource_templates=[fake_tmpl],
    )

    registry = MCPRegistry()
    registry._connect_stdio = AsyncMock(return_value=mock_session)

    cfg = MCPServerConfig(name="res-server", type="stdio", command="echo")
    await registry.connect_server(cfg)

    state = registry.servers["res-server"]
    assert state.status == "connected"
    assert len(state.resources) == 1
    assert len(state.resource_templates) == 1
    assert state.resources[0].uri == "file:///a.txt"


@pytest.mark.asyncio
async def test_connect_skips_resources_when_not_capable():
    """Server without resource capabilities gets empty resources."""
    fake_tool = _make_fake_tool("t1")
    caps = MagicMock()
    caps.resources = None
    caps.prompts = None
    mock_session = _make_mock_session(tools=[fake_tool], capabilities=caps)

    registry = MCPRegistry()
    registry._connect_stdio = AsyncMock(return_value=mock_session)

    cfg = MCPServerConfig(name="no-res", type="stdio", command="echo")
    await registry.connect_server(cfg)

    state = registry.servers["no-res"]
    assert state.status == "connected"
    assert state.resources == []
    assert state.resource_templates == []
    # list_resources should not have been called
    mock_session.list_resources.assert_not_called()


@pytest.mark.asyncio
async def test_connect_discovers_prompts_when_capable():
    """Server with prompt capabilities gets prompts discovered."""
    fake_tool = _make_fake_tool("t1")
    fake_prompt = _make_fake_prompt("summarize", "Summarize text")
    caps = MagicMock()
    caps.resources = None
    caps.prompts = MagicMock()  # truthy = has prompts
    mock_session = _make_mock_session(
        tools=[fake_tool], capabilities=caps, prompts=[fake_prompt],
    )

    registry = MCPRegistry()
    registry._connect_stdio = AsyncMock(return_value=mock_session)

    cfg = MCPServerConfig(name="prompt-server", type="stdio", command="echo")
    await registry.connect_server(cfg)

    state = registry.servers["prompt-server"]
    assert state.status == "connected"
    assert len(state.prompts) == 1
    assert state.prompts[0].name == "summarize"


@pytest.mark.asyncio
async def test_connect_skips_prompts_when_not_capable():
    """Server without prompt capabilities gets empty prompts."""
    fake_tool = _make_fake_tool("t1")
    caps = MagicMock()
    caps.resources = None
    caps.prompts = None
    mock_session = _make_mock_session(tools=[fake_tool], capabilities=caps)

    registry = MCPRegistry()
    registry._connect_stdio = AsyncMock(return_value=mock_session)

    cfg = MCPServerConfig(name="no-prompts", type="stdio", command="echo")
    await registry.connect_server(cfg)

    state = registry.servers["no-prompts"]
    assert state.prompts == []
    mock_session.list_prompts.assert_not_called()


# -- registry accessor tests --


@pytest.mark.asyncio
async def test_get_resources_only_connected():
    """get_resources returns resources only from connected servers."""
    registry = MCPRegistry()
    res1 = _make_fake_resource("file:///a.txt", "a")
    res2 = _make_fake_resource("file:///b.txt", "b")

    registry.servers["good"] = MCPServerState(
        config=MCPServerConfig(name="good", type="stdio"),
        status="connected", resources=[res1],
    )
    registry.servers["bad"] = MCPServerState(
        config=MCPServerConfig(name="bad", type="stdio"),
        status="failed", resources=[res2],
    )

    results = registry.get_resources()
    assert len(results) == 1
    assert results[0] == ("good", res1)


@pytest.mark.asyncio
async def test_get_prompts_only_connected():
    """get_prompts returns prompts only from connected servers."""
    registry = MCPRegistry()
    p1 = _make_fake_prompt("summarize")
    p2 = _make_fake_prompt("translate")

    registry.servers["good"] = MCPServerState(
        config=MCPServerConfig(name="good", type="stdio"),
        status="connected", prompts=[p1],
    )
    registry.servers["bad"] = MCPServerState(
        config=MCPServerConfig(name="bad", type="stdio"),
        status="failed", prompts=[p2],
    )

    results = registry.get_prompts()
    assert len(results) == 1
    assert results[0] == ("good", p1)


# -- refresh methods tests --


@pytest.mark.asyncio
async def test_refresh_tools():
    """refresh_tools updates tool list from server."""
    old_tool = _make_fake_tool("old_tool")
    new_tool = _make_fake_tool("new_tool")
    mock_session = _make_mock_session(tools=[old_tool])

    registry = MCPRegistry()
    registry._connect_stdio = AsyncMock(return_value=mock_session)

    cfg = MCPServerConfig(name="srv", type="stdio", command="echo")
    await registry.connect_server(cfg)
    assert "mcp__srv__old_tool" in registry.servers["srv"].tools

    # Change what list_tools returns and refresh
    new_result = MagicMock()
    new_result.tools = [new_tool]
    mock_session.list_tools = AsyncMock(return_value=new_result)

    await registry.refresh_tools("srv")
    assert "mcp__srv__new_tool" in registry.servers["srv"].tools
    assert "mcp__srv__old_tool" not in registry.servers["srv"].tools


@pytest.mark.asyncio
async def test_refresh_resources():
    """refresh_resources updates resource list from server."""
    fake_tool = _make_fake_tool("t1")
    res1 = _make_fake_resource("file:///a.txt", "a")
    res2 = _make_fake_resource("file:///b.txt", "b")
    caps = MagicMock()
    caps.resources = MagicMock()
    caps.prompts = None
    mock_session = _make_mock_session(
        tools=[fake_tool], capabilities=caps, resources=[res1],
    )

    registry = MCPRegistry()
    registry._connect_stdio = AsyncMock(return_value=mock_session)

    cfg = MCPServerConfig(name="srv", type="stdio", command="echo")
    await registry.connect_server(cfg)
    assert len(registry.servers["srv"].resources) == 1

    # Change what list_resources returns and refresh
    new_res_result = MagicMock()
    new_res_result.resources = [res1, res2]
    mock_session.list_resources = AsyncMock(return_value=new_res_result)
    new_tmpl_result = MagicMock()
    new_tmpl_result.resourceTemplates = []
    mock_session.list_resource_templates = AsyncMock(return_value=new_tmpl_result)

    await registry.refresh_resources("srv")
    assert len(registry.servers["srv"].resources) == 2


@pytest.mark.asyncio
async def test_refresh_prompts():
    """refresh_prompts updates prompt list from server."""
    fake_tool = _make_fake_tool("t1")
    p1 = _make_fake_prompt("p1")
    p2 = _make_fake_prompt("p2")
    caps = MagicMock()
    caps.resources = None
    caps.prompts = MagicMock()
    mock_session = _make_mock_session(
        tools=[fake_tool], capabilities=caps, prompts=[p1],
    )

    registry = MCPRegistry()
    registry._connect_stdio = AsyncMock(return_value=mock_session)

    cfg = MCPServerConfig(name="srv", type="stdio", command="echo")
    await registry.connect_server(cfg)
    assert len(registry.servers["srv"].prompts) == 1

    # Change what list_prompts returns and refresh
    new_prompts_result = MagicMock()
    new_prompts_result.prompts = [p1, p2]
    mock_session.list_prompts = AsyncMock(return_value=new_prompts_result)

    await registry.refresh_prompts("srv")
    assert len(registry.servers["srv"].prompts) == 2


# -- notification handler tests --


@pytest.mark.asyncio
async def test_notification_handler_tools_changed():
    """ToolListChangedNotification triggers refresh_tools."""
    from mcp import types as mcp_types

    registry = MCPRegistry()
    registry.refresh_tools = AsyncMock()

    handler = registry._make_notification_handler("test-srv")

    # Simulate a ToolListChangedNotification
    notification = MagicMock(spec=mcp_types.ServerNotification)
    notification.root = mcp_types.ToolListChangedNotification()
    await handler(notification)

    registry.refresh_tools.assert_awaited_once_with("test-srv")


@pytest.mark.asyncio
async def test_notification_handler_resources_changed():
    """ResourceListChangedNotification triggers refresh_resources."""
    from mcp import types as mcp_types

    registry = MCPRegistry()
    registry.refresh_resources = AsyncMock()

    handler = registry._make_notification_handler("test-srv")

    notification = MagicMock(spec=mcp_types.ServerNotification)
    notification.root = mcp_types.ResourceListChangedNotification()
    await handler(notification)

    registry.refresh_resources.assert_awaited_once_with("test-srv")


@pytest.mark.asyncio
async def test_notification_handler_prompts_changed():
    """PromptListChangedNotification triggers refresh_prompts."""
    from mcp import types as mcp_types

    registry = MCPRegistry()
    registry.refresh_prompts = AsyncMock()

    handler = registry._make_notification_handler("test-srv")

    notification = MagicMock(spec=mcp_types.ServerNotification)
    notification.root = mcp_types.PromptListChangedNotification()
    await handler(notification)

    registry.refresh_prompts.assert_awaited_once_with("test-srv")


@pytest.mark.asyncio
async def test_notification_handler_ignores_non_notifications():
    """Handler ignores non-ServerNotification messages."""
    registry = MCPRegistry()
    registry.refresh_tools = AsyncMock()

    handler = registry._make_notification_handler("test-srv")

    # Pass a non-notification (e.g., an exception)
    await handler(RuntimeError("test"))
    registry.refresh_tools.assert_not_awaited()


@pytest.mark.asyncio
async def test_notification_handler_error_is_logged_not_raised():
    """Errors in notification handler are caught, not raised."""
    from mcp import types as mcp_types

    registry = MCPRegistry()
    registry.refresh_tools = AsyncMock(side_effect=RuntimeError("boom"))

    handler = registry._make_notification_handler("test-srv")

    notification = MagicMock(spec=mcp_types.ServerNotification)
    notification.root = mcp_types.ToolListChangedNotification()
    # Should not raise
    await handler(notification)


# -- resource response conversion tests --


def test_convert_resource_response_text():
    """Text resource content is returned as text."""
    result = MagicMock()
    item = MagicMock()
    item.text = "Hello from resource"
    item.blob = None
    item.uri = "file:///test.txt"
    item.mimeType = "text/plain"
    result.contents = [item]

    tr = _convert_resource_response(result)
    assert "Hello from resource" in tr.text
    assert tr.media == []


def test_convert_resource_response_blob():
    """Blob resource content is returned as media attachment."""
    import base64
    result = MagicMock()
    item = MagicMock()
    item.text = None
    item.blob = base64.b64encode(b"fake-png").decode()
    item.uri = "file:///image.png"
    item.mimeType = "image/png"
    result.contents = [item]

    tr = _convert_resource_response(result)
    assert "file attached" in tr.text
    assert "image/png" in tr.text
    assert len(tr.media) == 1
    assert tr.media[0]["data"] == b"fake-png"
    assert tr.media[0]["content_type"] == "image/png"


def test_convert_resource_response_empty():
    """Empty resource returns no-content message."""
    result = MagicMock()
    result.contents = []
    tr = _convert_resource_response(result)
    assert tr.text == "(no content)"


# -- prompt response conversion tests --


def test_convert_prompt_response_text_messages():
    """Prompt messages are converted to role-prefixed text."""
    result = MagicMock()
    msg1 = MagicMock()
    msg1.role = "user"
    msg1.content = MagicMock()
    msg1.content.text = "Summarize this"
    msg2 = MagicMock()
    msg2.role = "assistant"
    msg2.content = MagicMock()
    msg2.content.text = "Here is the summary"
    result.messages = [msg1, msg2]

    text = _convert_prompt_response(result)
    assert "[user]: Summarize this" in text
    assert "[assistant]: Here is the summary" in text


def test_convert_prompt_response_empty():
    """Empty prompt returns no-messages message."""
    result = MagicMock()
    result.messages = []
    text = _convert_prompt_response(result)
    assert text == "(no messages)"


# -- resource tool tests --


@pytest.mark.asyncio
async def test_mcp_list_resources_shows_resources(ctx, monkeypatch):
    """mcp_list_resources returns formatted resource list."""
    from decafclaw import mcp_client
    from decafclaw.skills.mcp.tools import tool_mcp_list_resources

    registry = MCPRegistry()
    res = _make_fake_resource("file:///data.csv", "data.csv", "A CSV file", "text/csv")
    cfg = MCPServerConfig(name="data-srv", type="stdio")
    registry.servers["data-srv"] = MCPServerState(
        config=cfg, status="connected", resources=[res],
    )
    monkeypatch.setattr(mcp_client, "_registry", registry)

    result = await tool_mcp_list_resources(ctx)
    assert "data-srv" in result
    assert "file:///data.csv" in result
    assert "text/csv" in result


@pytest.mark.asyncio
async def test_mcp_list_resources_empty(ctx, monkeypatch):
    """mcp_list_resources with no resources returns appropriate message."""
    from decafclaw import mcp_client
    from decafclaw.skills.mcp.tools import tool_mcp_list_resources

    registry = MCPRegistry()
    cfg = MCPServerConfig(name="empty-srv", type="stdio")
    registry.servers["empty-srv"] = MCPServerState(
        config=cfg, status="connected",
    )
    monkeypatch.setattr(mcp_client, "_registry", registry)

    result = await tool_mcp_list_resources(ctx)
    assert "No MCP resources" in result


@pytest.mark.asyncio
async def test_mcp_read_resource_success(ctx, monkeypatch):
    """mcp_read_resource reads and converts resource content."""
    from decafclaw import mcp_client
    from decafclaw.skills.mcp.tools import tool_mcp_read_resource

    mock_session = AsyncMock()
    read_result = MagicMock()
    item = MagicMock()
    item.text = "file contents here"
    item.blob = None
    item.uri = "file:///test.txt"
    item.mimeType = "text/plain"
    read_result.contents = [item]
    mock_session.read_resource = AsyncMock(return_value=read_result)

    registry = MCPRegistry()
    cfg = MCPServerConfig(name="test-srv", type="stdio")
    registry.servers["test-srv"] = MCPServerState(
        config=cfg, status="connected", session=mock_session,
    )
    monkeypatch.setattr(mcp_client, "_registry", registry)

    result = await tool_mcp_read_resource(ctx, server="test-srv", uri="file:///test.txt")
    assert "file contents here" in result.text


@pytest.mark.asyncio
async def test_mcp_read_resource_missing_params(ctx, monkeypatch):
    """mcp_read_resource returns error when params missing."""
    from decafclaw.skills.mcp.tools import tool_mcp_read_resource

    result = await tool_mcp_read_resource(ctx, server="", uri="")
    assert "[error:" in result.text


# -- prompt tool tests --


@pytest.mark.asyncio
async def test_mcp_list_prompts_shows_prompts(ctx, monkeypatch):
    """mcp_list_prompts returns formatted prompt list."""
    from decafclaw import mcp_client
    from decafclaw.skills.mcp.tools import tool_mcp_list_prompts

    arg = MagicMock()
    arg.name = "text"
    arg.description = "Text to summarize"
    arg.required = True

    registry = MCPRegistry()
    prompt = _make_fake_prompt("summarize", "Summarize text", [arg])
    cfg = MCPServerConfig(name="ai-srv", type="stdio")
    registry.servers["ai-srv"] = MCPServerState(
        config=cfg, status="connected", prompts=[prompt],
    )
    monkeypatch.setattr(mcp_client, "_registry", registry)

    result = await tool_mcp_list_prompts(ctx)
    assert "ai-srv" in result
    assert "summarize" in result
    assert "text" in result
    assert "required" in result


@pytest.mark.asyncio
async def test_mcp_get_prompt_success(ctx, monkeypatch):
    """mcp_get_prompt gets and converts prompt messages."""
    from decafclaw import mcp_client
    from decafclaw.skills.mcp.tools import tool_mcp_get_prompt

    mock_session = AsyncMock()
    prompt_result = MagicMock()
    msg = MagicMock()
    msg.role = "user"
    msg.content = MagicMock()
    msg.content.text = "Please summarize the following"
    prompt_result.messages = [msg]
    mock_session.get_prompt = AsyncMock(return_value=prompt_result)

    registry = MCPRegistry()
    cfg = MCPServerConfig(name="ai-srv", type="stdio")
    registry.servers["ai-srv"] = MCPServerState(
        config=cfg, status="connected", session=mock_session,
    )
    monkeypatch.setattr(mcp_client, "_registry", registry)

    result = await tool_mcp_get_prompt(ctx, server="ai-srv", name="summarize")
    assert "Please summarize the following" in result.text


@pytest.mark.asyncio
async def test_mcp_get_prompt_missing_params(ctx, monkeypatch):
    """mcp_get_prompt returns error when params missing."""
    from decafclaw.skills.mcp.tools import tool_mcp_get_prompt

    result = await tool_mcp_get_prompt(ctx, server="", name="")
    assert "[error:" in result.text


# -- MCP prompt command tests --


@pytest.mark.asyncio
async def test_dispatch_mcp_prompt_command_success(ctx, monkeypatch):
    """MCP prompt command dispatches and returns inline result."""
    from decafclaw import mcp_client
    from decafclaw.commands import dispatch_command

    mock_session = AsyncMock()
    prompt_result = MagicMock()
    msg = MagicMock()
    msg.role = "user"
    msg.content = MagicMock()
    msg.content.text = "Summarize this text"
    prompt_result.messages = [msg]
    mock_session.get_prompt = AsyncMock(return_value=prompt_result)

    arg = MagicMock()
    arg.name = "text"
    arg.required = False
    arg.description = ""
    prompt = _make_fake_prompt("summarize", "Summarize", [arg])

    registry = MCPRegistry()
    cfg = MCPServerConfig(name="ai-srv", type="stdio")
    registry.servers["ai-srv"] = MCPServerState(
        config=cfg, status="connected", session=mock_session,
        prompts=[prompt],
    )
    monkeypatch.setattr(mcp_client, "_registry", registry)

    result = await dispatch_command(ctx, "!mcp__ai-srv__summarize hello world")
    assert result.mode == "inline"
    assert "Summarize this text" in result.text
    assert "invoked MCP prompt" in result.text


@pytest.mark.asyncio
async def test_dispatch_mcp_prompt_command_missing_required_arg(ctx, monkeypatch):
    """MCP prompt command returns error when required args missing."""
    from decafclaw import mcp_client
    from decafclaw.commands import dispatch_command

    arg = MagicMock()
    arg.name = "text"
    arg.required = True
    arg.description = "Text to summarize"
    prompt = _make_fake_prompt("summarize", "Summarize", [arg])

    registry = MCPRegistry()
    cfg = MCPServerConfig(name="ai-srv", type="stdio")
    registry.servers["ai-srv"] = MCPServerState(
        config=cfg, status="connected", session=AsyncMock(),
        prompts=[prompt],
    )
    monkeypatch.setattr(mcp_client, "_registry", registry)

    result = await dispatch_command(ctx, "!mcp__ai-srv__summarize")
    assert result.mode == "error"
    assert "Missing required" in result.text
    assert "text" in result.text


@pytest.mark.asyncio
async def test_dispatch_mcp_prompt_command_unknown_server(ctx, monkeypatch):
    """MCP prompt command returns error for unknown server."""
    from decafclaw import mcp_client
    from decafclaw.commands import dispatch_command

    registry = MCPRegistry()
    monkeypatch.setattr(mcp_client, "_registry", registry)

    result = await dispatch_command(ctx, "!mcp__nonexistent__prompt")
    assert result.mode == "error"
    assert "not connected" in result.text


@pytest.mark.asyncio
async def test_dispatch_mcp_prompt_command_unknown_prompt(ctx, monkeypatch):
    """MCP prompt command returns error for unknown prompt name."""
    from decafclaw import mcp_client
    from decafclaw.commands import dispatch_command

    registry = MCPRegistry()
    cfg = MCPServerConfig(name="ai-srv", type="stdio")
    registry.servers["ai-srv"] = MCPServerState(
        config=cfg, status="connected", session=AsyncMock(),
        prompts=[],
    )
    monkeypatch.setattr(mcp_client, "_registry", registry)

    result = await dispatch_command(ctx, "!mcp__ai-srv__nonexistent")
    assert result.mode == "error"
    assert "not found" in result.text


def test_format_help_includes_mcp_prompts(monkeypatch):
    """format_help includes MCP prompt commands."""
    from decafclaw import mcp_client
    from decafclaw.commands import format_help

    arg = MagicMock()
    arg.name = "text"
    arg.required = True
    arg.description = ""
    prompt = _make_fake_prompt("summarize", "Summarize text", [arg])

    registry = MCPRegistry()
    cfg = MCPServerConfig(name="ai-srv", type="stdio")
    registry.servers["ai-srv"] = MCPServerState(
        config=cfg, status="connected", prompts=[prompt],
    )
    monkeypatch.setattr(mcp_client, "_registry", registry)

    text = format_help([], prefix="!")
    assert "mcp__ai-srv__summarize" in text
    assert "<text>" in text


def test_parse_positional_args_quoted():
    """Quoted strings are parsed as single arguments."""
    from decafclaw.commands import _parse_positional_args

    result = _parse_positional_args('hello "world foo" bar')
    assert result == ["hello", "world foo", "bar"]


def test_parse_positional_args_empty():
    """Empty string returns empty list."""
    from decafclaw.commands import _parse_positional_args

    assert _parse_positional_args("") == []
