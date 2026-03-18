"""MCP client — connect to external MCP servers as tool providers."""

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Server name validation: lowercase alphanumeric + hyphens
_VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Env var expansion: ${VAR} or ${VAR:-default}
_ENV_VAR_RE = re.compile(r"\$\{([^}:]+?)(?::-([^}]*))?\}")


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    type: str  # "stdio" or "http"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    timeout: int = 30000  # ms


def _expand_env(value: str) -> str:
    """Expand ${VAR} and ${VAR:-default} in a string from os.environ."""
    def _replace(match):
        var_name = match.group(1)
        default = match.group(2)
        result = os.environ.get(var_name)
        if result is not None:
            return result
        if default is not None:
            return default
        log.warning(f"Environment variable ${{{var_name}}} not set and no default")
        return ""

    return _ENV_VAR_RE.sub(_replace, value)


def _expand_config(obj):
    """Recursively expand env vars in strings, lists, and dicts."""
    if isinstance(obj, str):
        return _expand_env(obj)
    if isinstance(obj, list):
        return [_expand_config(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _expand_config(v) for k, v in obj.items()}
    return obj


def _validate_server_name(name: str) -> bool:
    """Check that a server name is lowercase alphanumeric + hyphens."""
    return bool(_VALID_NAME_RE.match(name))


def load_mcp_config(config) -> list[MCPServerConfig]:
    """Load MCP server configurations from mcp_servers.json.

    Returns empty list if file is missing (not an error).
    """
    path = config.agent_path / "mcp_servers.json"
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Could not read MCP config: {e}")
        return []

    servers_data = data.get("mcpServers", {})
    if not isinstance(servers_data, dict):
        log.warning("mcpServers must be a JSON object")
        return []

    configs = []
    for name, server_data in servers_data.items():
        if not _validate_server_name(name):
            log.warning(f"Skipping MCP server with invalid name: {name!r}")
            continue

        if not isinstance(server_data, dict):
            log.warning(f"Skipping MCP server {name!r}: config must be an object")
            continue

        # Expand env vars in all string values
        server_data: dict = _expand_config(server_data)  # type: ignore[assignment]

        server_type = server_data.get("type", "stdio")
        configs.append(MCPServerConfig(
            name=name,
            type=server_type,
            command=server_data.get("command", ""),
            args=server_data.get("args", []),
            env=server_data.get("env", {}),
            url=server_data.get("url", ""),
            headers=server_data.get("headers", {}),
            timeout=server_data.get("timeout", 30000),
        ))

    log.info(f"Loaded {len(configs)} MCP server config(s): {[c.name for c in configs]}")
    return configs


# -- Tool namespacing ----------------------------------------------------------


def _namespace_tool(server_name: str, tool_name: str) -> str:
    """Create a namespaced tool name: mcp__<server>__<tool>."""
    return f"mcp__{server_name}__{tool_name}"


def _parse_namespace(namespaced: str) -> tuple[str, str] | None:
    """Parse a namespaced MCP tool name into (server_name, tool_name).

    Returns None if the name doesn't match the mcp__<server>__<tool> pattern.
    """
    if not namespaced.startswith("mcp__"):
        return None
    rest = namespaced[5:]  # after "mcp__"
    parts = rest.split("__", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def _convert_tool_definition(server_name: str, mcp_tool) -> dict:
    """Convert an MCP tool definition to OpenAI-style tool definition.

    mcp_tool can be a dict or an SDK Tool object (has .name, .description, .inputSchema).
    """
    if isinstance(mcp_tool, dict):
        name = mcp_tool.get("name", "")
        description = mcp_tool.get("description", "")
        input_schema = mcp_tool.get("inputSchema", {"type": "object", "properties": {}})
    else:
        name = getattr(mcp_tool, "name", "")
        description = getattr(mcp_tool, "description", "")
        input_schema = getattr(mcp_tool, "inputSchema", {"type": "object", "properties": {}})

    return {
        "type": "function",
        "function": {
            "name": _namespace_tool(server_name, name),
            "description": description or f"MCP tool from {server_name}",
            "parameters": input_schema,
        },
    }


def _convert_mcp_response(result):
    """Convert an MCP tools/call response to a ToolResult.

    Handles text, image/audio media, and errors.
    Returns a ToolResult with text and optional media attachments.
    """
    import base64
    import mimetypes as _mimetypes

    from .media import ToolResult

    # Handle dict or SDK result object
    if isinstance(result, dict):
        is_error = result.get("isError", False)
        content = result.get("content", [])
    else:
        is_error = getattr(result, "isError", False)
        content = getattr(result, "content", [])

    parts = []
    media = []
    img_count = 0
    audio_count = 0

    for item in content:
        if isinstance(item, dict):
            item_type = item.get("type", "")
            text = item.get("text", "")
        else:
            item_type = getattr(item, "type", "")
            text = getattr(item, "text", "")

        if item_type == "text":
            parts.append(text)
        elif item_type == "image":
            data_str = item.get("data", "") if isinstance(item, dict) else getattr(item, "data", "")
            mime_type = (item.get("mimeType", "") if isinstance(item, dict)
                        else getattr(item, "mimeType", "")) or "image/png"
            ext = _mimetypes.guess_extension(mime_type) or ".png"

            try:
                data = base64.b64decode(data_str)
            except Exception:
                data = data_str.encode() if isinstance(data_str, str) else data_str

            img_count += 1
            media.append({
                "type": "file",
                "filename": f"mcp-image-{img_count}{ext}",
                "data": data,
                "content_type": mime_type,
            })
            parts.append("Image attached.")

        elif item_type == "audio":
            data_str = item.get("data", "") if isinstance(item, dict) else getattr(item, "data", "")
            mime_type = (item.get("mimeType", "") if isinstance(item, dict)
                        else getattr(item, "mimeType", "")) or "audio/wav"
            ext = _mimetypes.guess_extension(mime_type) or ".wav"

            try:
                data = base64.b64decode(data_str)
            except Exception:
                data = data_str.encode() if isinstance(data_str, str) else data_str

            audio_count += 1
            media.append({
                "type": "file",
                "filename": f"mcp-audio-{audio_count}{ext}",
                "data": data,
                "content_type": mime_type,
            })
            parts.append("Audio attached.")

        else:
            parts.append(f"[{item_type}: unsupported content type]")

    text = "\n".join(parts) if parts else "(no content)"

    if is_error:
        return ToolResult(text=f"[error: {text}]", media=media)
    return ToolResult(text=text, media=media)


# -- MCP Registry --------------------------------------------------------------


@dataclass
class MCPServerState:
    """Runtime state for a single MCP server connection."""

    config: MCPServerConfig
    status: str = "disconnected"  # connected, disconnected, failed
    session: Any = None  # ClientSession when connected
    tools: dict = field(default_factory=dict)  # namespaced_name -> callable
    tool_definitions: list = field(default_factory=list)  # OpenAI-style defs
    retry_count: int = 0
    last_retry_time: float = 0.0
    _exit_stack: Any = None  # AsyncExitStack for managing context lifetimes


class MCPRegistry:
    """Registry of MCP server connections and their tools."""

    def __init__(self):
        self.servers: dict[str, MCPServerState] = {}

    def get_tools(self) -> dict:
        """Return all MCP tools as {namespaced_name: async_callable}."""
        tools = {}
        for state in self.servers.values():
            if state.status == "connected":
                tools.update(state.tools)
        return tools

    def get_tool_definitions(self) -> list[dict]:
        """Return all MCP tool definitions in OpenAI format."""
        defs = []
        for state in self.servers.values():
            if state.status == "connected":
                defs.extend(state.tool_definitions)
        return defs

    async def connect_server(self, server_config: MCPServerConfig):
        """Connect to a single MCP server and discover its tools."""
        name = server_config.name
        state = MCPServerState(config=server_config)
        self.servers[name] = state

        try:
            exit_stack = contextlib.AsyncExitStack()
            state._exit_stack = exit_stack
            await exit_stack.__aenter__()

            if server_config.type == "stdio":
                session = await self._connect_stdio(exit_stack, server_config)
            elif server_config.type == "http":
                session = await self._connect_http(exit_stack, server_config)
            else:
                log.warning(f"MCP server {name!r}: unknown type {server_config.type!r}")
                state.status = "failed"
                return

            state.session = session

            # Discover tools
            tools_result = await session.list_tools()
            tools_list = tools_result.tools if hasattr(tools_result, "tools") else tools_result

            for mcp_tool in tools_list:
                tool_def = _convert_tool_definition(name, mcp_tool)
                namespaced = tool_def["function"]["name"]
                tool_name = mcp_tool.name if hasattr(mcp_tool, "name") else mcp_tool["name"]

                # Build async callable wrapper (uses state.session, supports reconnect)
                state.tools[namespaced] = self._make_tool_caller(name, tool_name, server_config.timeout)
                state.tool_definitions.append(tool_def)

            state.status = "connected"
            state.retry_count = 0
            log.info(f"MCP server {name!r} connected: {len(state.tools)} tool(s)")

        except BaseException as e:
            log.warning(f"MCP server {name!r} failed to connect: {e}")
            state.status = "failed"
            # Clean up the exit stack on failure
            if state._exit_stack:
                try:
                    await state._exit_stack.__aexit__(None, None, None)
                except Exception:
                    pass
                state._exit_stack = None

    async def _connect_stdio(self, exit_stack, server_config):
        """Connect to a stdio MCP server. Override in tests."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=server_config.command,
            args=server_config.args,
            env={**os.environ, **server_config.env} if server_config.env else None,
        )
        read_stream, write_stream = await exit_stack.enter_async_context(
            stdio_client(params)
        )
        session = await exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()
        return session

    async def _connect_http(self, exit_stack, server_config):
        """Connect to an HTTP MCP server. Override in tests."""
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        kwargs = {"url": server_config.url}
        if server_config.headers:
            kwargs["headers"] = server_config.headers

        read_stream, write_stream, _ = await exit_stack.enter_async_context(
            streamable_http_client(**kwargs)
        )
        session = await exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()
        return session

    def _make_tool_caller(self, server_name, tool_name, timeout_ms):
        """Create an async callable that invokes an MCP tool.

        Uses the server state's current session, supporting auto-reconnect.
        """
        timeout_s = timeout_ms / 1000

        async def call_tool(arguments):
            # Try auto-reconnect if server is down
            if not await self._maybe_reconnect(server_name):
                return f"[error: MCP server {server_name} is not available]"

            state = self.servers.get(server_name)
            if not state or not state.session:
                return f"[error: MCP server {server_name} has no active session]"

            try:
                result = await asyncio.wait_for(
                    state.session.call_tool(tool_name, arguments),
                    timeout=timeout_s,
                )
                return _convert_mcp_response(result)
            except asyncio.TimeoutError:
                return f"[error: MCP tool {tool_name} on {server_name} timed out after {timeout_s}s]"
            except Exception as e:
                # Mark server as failed for auto-restart on next call
                if state:
                    state.status = "failed"
                return f"[error: MCP tool {tool_name} on {server_name} failed: {e}]"

        return call_tool

    async def _maybe_reconnect(self, server_name: str) -> bool:
        """Check if a server needs reconnection and attempt it.

        Returns True if the server is connected (or was reconnected).
        """
        state = self.servers.get(server_name)
        if not state:
            return False

        if state.status == "connected":
            return True

        # Give up after max retries
        max_retries = 3
        if state.retry_count >= max_retries:
            return False

        # Check backoff timing
        backoff = min(2 ** state.retry_count, 8)
        now = time.monotonic()
        if now - state.last_retry_time < backoff:
            return False

        # Attempt reconnection
        log.info(f"Attempting reconnection for MCP server {server_name!r} "
                 f"(attempt {state.retry_count + 1}/{max_retries})")
        state.last_retry_time = now

        # Disconnect first if there's stale state
        await self.disconnect_server(server_name)

        # Reconnect
        await self.connect_server(state.config)

        new_state = self.servers.get(server_name)
        if new_state and new_state.status == "connected":
            log.info(f"MCP server {server_name!r} reconnected successfully")
            return True

        # Increment retry count (connect_server resets it on success,
        # so we only get here on failure)
        if new_state:
            new_state.retry_count = state.retry_count + 1
        return False

    async def connect_all(self, configs: list[MCPServerConfig]):
        """Connect to all configured MCP servers."""
        for cfg in configs:
            await self.connect_server(cfg)

        connected = sum(1 for s in self.servers.values() if s.status == "connected")
        failed = sum(1 for s in self.servers.values() if s.status == "failed")
        total_tools = sum(len(s.tools) for s in self.servers.values())
        log.info(f"MCP: {connected} connected, {failed} failed, {total_tools} tool(s)")

    async def disconnect_server(self, name: str):
        """Disconnect a single MCP server."""
        state = self.servers.get(name)
        if not state:
            return

        if state._exit_stack:
            try:
                await state._exit_stack.__aexit__(None, None, None)
            except Exception as e:
                log.debug(f"Error closing MCP server {name!r}: {e}")
            state._exit_stack = None

        state.session = None
        state.tools.clear()
        state.tool_definitions.clear()
        state.status = "disconnected"
        log.info(f"MCP server {name!r} disconnected")

    async def disconnect_all(self):
        """Disconnect all MCP servers with a timeout."""
        try:
            await asyncio.wait_for(
                self._disconnect_all_inner(),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            log.warning("MCP disconnect timed out after 5s")

    async def _disconnect_all_inner(self):
        for name in list(self.servers.keys()):
            await self.disconnect_server(name)


# Module-level global registry
_registry: MCPRegistry | None = None


def get_registry() -> MCPRegistry | None:
    """Return the global MCP registry, or None if not initialized."""
    return _registry


async def init_mcp(config):
    """Initialize the global MCP registry and connect all servers."""
    global _registry
    configs = load_mcp_config(config)
    if not configs:
        log.info("No MCP servers configured")
        return

    _registry = MCPRegistry()
    await _registry.connect_all(configs)


async def shutdown_mcp():
    """Shut down the global MCP registry."""
    global _registry
    if _registry:
        await _registry.disconnect_all()
        _registry = None
