"""MCP client — connect to external MCP servers as tool providers."""

import asyncio
import base64
import contextlib
import json
import logging
import mimetypes
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _ga(obj, key, default=None):
    """Get attribute from dict or object uniformly."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _extract_list(result, attr: str) -> list:
    """Extract a list from an MCP SDK response (handles dict, object, or raw list)."""
    if isinstance(result, list):
        return result
    value = _ga(result, attr, [])
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return []


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


def _normalize_server_segment(server_name: str) -> str:
    """Normalize a server name for use in namespaced tool identifiers.

    Hyphens are replaced with underscores. This is a workaround for
    Gemini, which normalizes hyphens to underscores at the function-call
    serialization layer — a tool registered as ``mcp__foo-bar__baz``
    would be emitted as ``mcp__foo_bar__baz`` and fail to route. By
    advertising underscored names up-front, every provider can
    round-trip the identifier.

    The *actual* MCP SDK server name (used for calls via
    ``session.call_tool``) is preserved separately — this only affects
    the identifier seen by the LLM.
    """
    return server_name.replace("-", "_")


def _namespace_tool(server_name: str, tool_name: str) -> str:
    """Create a namespaced tool name: mcp__<server>__<tool>.

    The server segment is normalized (hyphens → underscores) for
    provider-compatibility; see ``_normalize_server_segment``.
    """
    return f"mcp__{_normalize_server_segment(server_name)}__{tool_name}"


def _parse_namespace(namespaced: str) -> tuple[str, str] | None:
    """Parse a namespaced MCP tool name into (server_segment, tool_name).

    Note: the returned ``server_segment`` is the normalized form
    (hyphens replaced with underscores). It will not match an original
    MCP server name if the server had hyphens. Callers that need the
    original server name should look it up via the registry.

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
    name = _ga(mcp_tool, "name", "")
    description = _ga(mcp_tool, "description", "")
    input_schema = _ga(mcp_tool, "inputSchema", {"type": "object", "properties": {}})

    return {
        "type": "function",
        "function": {
            "name": _namespace_tool(server_name, name),
            "description": description or f"MCP tool from {server_name}",
            "parameters": input_schema,
        },
    }


def _decode_media(item, label: str, count: int, default_mime: str, default_ext: str):
    """Decode a base64 media item into a media attachment dict and text placeholder.

    Returns (media_dict, placeholder_text).
    """
    data_str = _ga(item, "data", "")
    mime_type = _ga(item, "mimeType", "") or default_mime
    ext = mimetypes.guess_extension(mime_type) or default_ext

    try:
        data = base64.b64decode(data_str)
    except Exception:
        data = data_str.encode() if isinstance(data_str, str) else data_str

    filename = f"mcp-{label}-{count}{ext}"
    attachment = {
        "type": "file",
        "filename": filename,
        "data": data,
        "content_type": mime_type,
    }
    placeholder = f"[file attached: {filename} ({mime_type}) — will appear as an attachment on your reply]"
    return attachment, placeholder


def _convert_mcp_response(result):
    """Convert an MCP tools/call response to a ToolResult.

    Handles text, image/audio media, and errors.
    Returns a ToolResult with text and optional media attachments.
    """
    from .media import ToolResult

    is_error = _ga(result, "isError", False)
    content = _ga(result, "content", [])

    parts = []
    media = []
    img_count = 0
    audio_count = 0

    for item in content:
        item_type = _ga(item, "type", "")
        text = _ga(item, "text", "")

        if item_type == "text":
            parts.append(text)
        elif item_type == "image":
            img_count += 1
            attachment, placeholder = _decode_media(item, "image", img_count, "image/png", ".png")
            media.append(attachment)
            parts.append(placeholder)
        elif item_type == "audio":
            audio_count += 1
            attachment, placeholder = _decode_media(item, "audio", audio_count, "audio/wav", ".wav")
            media.append(attachment)
            parts.append(placeholder)
        else:
            parts.append(f"[{item_type}: unsupported content type]")

    text = "\n".join(parts) if parts else "(no content)"

    if is_error:
        return ToolResult(text=f"[error: {text}]", media=media)
    return ToolResult(text=text, media=media)


def _convert_resource_response(result):
    """Convert an MCP resources/read response to a ToolResult.

    Handles TextResourceContents and BlobResourceContents.
    Returns a ToolResult with text and optional media attachments.
    """
    from .media import ToolResult

    contents = _ga(result, "contents", [])

    parts = []
    media = []
    blob_count = 0

    for item in contents:
        item_text = _ga(item, "text")
        item_blob = _ga(item, "blob")
        item_uri = _ga(item, "uri", "")
        if not isinstance(item_uri, str):
            item_uri = str(item_uri)
        item_mime = _ga(item, "mimeType", "") or ""

        if item_text is not None:
            parts.append(item_text)
        elif item_blob is not None:
            mime_type = item_mime or "application/octet-stream"
            ext = mimetypes.guess_extension(mime_type) or ".bin"
            try:
                data = base64.b64decode(item_blob)
            except Exception:
                data = item_blob.encode() if isinstance(item_blob, str) else item_blob

            blob_count += 1
            filename = f"mcp-resource-{blob_count}{ext}"
            media.append({
                "type": "file",
                "filename": filename,
                "data": data,
                "content_type": mime_type,
            })
            parts.append(f"[file attached: {filename} ({mime_type}) — will appear as an attachment on your reply]")
        else:
            parts.append(f"[unsupported resource content from {item_uri}]")

    text = "\n".join(parts) if parts else "(no content)"
    return ToolResult(text=text, media=media)


def _convert_prompt_response(result):
    """Convert an MCP prompts/get response to a text string.

    The result contains .messages — a list of PromptMessage objects
    with .role and .content. Converts to a readable text block.
    """
    messages = _ga(result, "messages", [])

    parts = []
    for msg in messages:
        role = _ga(msg, "role", "unknown")
        content = _ga(msg, "content", {})

        # Content can be TextContent, ImageContent, EmbeddedResource, or a dict
        if isinstance(content, dict):
            text = content.get("text", str(content))
        elif hasattr(content, "text"):
            text = content.text
        elif isinstance(content, str):
            text = content
        else:
            content_type = getattr(content, "type", type(content).__name__)
            text = f"({content_type} content)"

        parts.append(f"[{role}]: {text}")

    return "\n".join(parts) if parts else "(no messages)"


# -- MCP Registry --------------------------------------------------------------


@dataclass
class MCPServerState:
    """Runtime state for a single MCP server connection."""

    config: MCPServerConfig
    status: str = "disconnected"  # connected, disconnected, failed
    session: Any = None  # ClientSession when connected
    capabilities: Any = None  # ServerCapabilities from initialize()
    tools: dict = field(default_factory=dict)  # namespaced_name -> callable
    tool_definitions: list = field(default_factory=list)  # OpenAI-style defs
    resources: list = field(default_factory=list)  # from list_resources()
    resource_templates: list = field(default_factory=list)  # from list_resource_templates()
    prompts: list = field(default_factory=list)  # from list_prompts()
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

    def get_resources(self) -> list[tuple[str, Any]]:
        """Return all MCP resources as (server_name, resource) tuples."""
        results = []
        for name, state in self.servers.items():
            if state.status == "connected":
                for res in state.resources:
                    results.append((name, res))
        return results

    def get_resource_templates(self) -> list[tuple[str, Any]]:
        """Return all MCP resource templates as (server_name, template) tuples."""
        results = []
        for name, state in self.servers.items():
            if state.status == "connected":
                for tmpl in state.resource_templates:
                    results.append((name, tmpl))
        return results

    def get_prompts(self) -> list[tuple[str, Any]]:
        """Return all MCP prompts as (server_name, prompt) tuples."""
        results = []
        for name, state in self.servers.items():
            if state.status == "connected":
                for prompt in state.prompts:
                    results.append((name, prompt))
        return results

    async def refresh_tools(self, server_name: str):
        """Re-discover tools for a connected server."""
        state = self.servers.get(server_name)
        if not state or state.status != "connected" or not state.session:
            return

        try:
            tools_result = await state.session.list_tools()
            tools_list = tools_result.tools if hasattr(tools_result, "tools") else tools_result

            new_tools = {}
            new_defs = []
            for mcp_tool in tools_list:
                tool_def = _convert_tool_definition(server_name, mcp_tool)
                namespaced = tool_def["function"]["name"]
                tool_name = mcp_tool.name if hasattr(mcp_tool, "name") else mcp_tool["name"]
                new_tools[namespaced] = self._make_tool_caller(
                    server_name, tool_name, state.config.timeout)
                new_defs.append(tool_def)

            # Atomic swap
            state.tools = new_tools
            state.tool_definitions = new_defs
            log.info(f"MCP server {server_name!r} tools refreshed: {len(new_tools)} tool(s)")
        except Exception as e:
            log.warning(f"MCP server {server_name!r}: failed to refresh tools: {e}")

    async def refresh_resources(self, server_name: str):
        """Re-discover resources for a connected server."""
        state = self.servers.get(server_name)
        if not state or state.status != "connected" or not state.session:
            return
        if not state.capabilities or not getattr(state.capabilities, "resources", None):
            return

        try:
            res_result = await state.session.list_resources()
            state.resources = _extract_list(res_result, "resources")
            tmpl_result = await state.session.list_resource_templates()
            state.resource_templates = _extract_list(tmpl_result, "resourceTemplates")
            log.info(f"MCP server {server_name!r} resources refreshed: "
                     f"{len(state.resources)} resource(s), {len(state.resource_templates)} template(s)")
        except Exception as e:
            log.warning(f"MCP server {server_name!r}: failed to refresh resources: {e}")

    async def refresh_prompts(self, server_name: str):
        """Re-discover prompts for a connected server."""
        state = self.servers.get(server_name)
        if not state or state.status != "connected" or not state.session:
            return
        if not state.capabilities or not getattr(state.capabilities, "prompts", None):
            return

        try:
            prompts_result = await state.session.list_prompts()
            state.prompts = _extract_list(prompts_result, "prompts")
            log.info(f"MCP server {server_name!r} prompts refreshed: {len(state.prompts)} prompt(s)")
        except Exception as e:
            log.warning(f"MCP server {server_name!r}: failed to refresh prompts: {e}")

    def _make_notification_handler(self, server_name: str):
        """Create a message_handler callback for a ClientSession.

        Handles ToolListChanged, ResourceListChanged, and PromptListChanged
        notifications by re-discovering the corresponding primitives.
        """
        async def handler(message):
            from mcp import types as _mcp_types

            # Only handle ServerNotification, ignore requests and exceptions
            if not isinstance(message, _mcp_types.ServerNotification):
                return

            try:
                notification = message.root
                if isinstance(notification, _mcp_types.ToolListChangedNotification):
                    await self.refresh_tools(server_name)
                elif isinstance(notification, _mcp_types.ResourceListChangedNotification):
                    await self.refresh_resources(server_name)
                elif isinstance(notification, _mcp_types.PromptListChangedNotification):
                    await self.refresh_prompts(server_name)
                else:
                    log.debug(f"MCP server {server_name!r}: unhandled notification {type(notification).__name__}")
            except Exception as e:
                log.warning(f"MCP server {server_name!r}: notification handler error: {e}")

        return handler

    async def connect_server(self, server_config: MCPServerConfig):
        """Connect to a single MCP server and discover its tools."""
        name = server_config.name
        state = MCPServerState(config=server_config)
        self.servers[name] = state

        try:
            exit_stack = contextlib.AsyncExitStack()
            state._exit_stack = exit_stack
            await exit_stack.__aenter__()

            message_handler = self._make_notification_handler(name)

            if server_config.type == "stdio":
                session = await self._connect_stdio(exit_stack, server_config, message_handler)
            elif server_config.type == "http":
                session = await self._connect_http(exit_stack, server_config, message_handler)
            else:
                log.warning(f"MCP server {name!r}: unknown type {server_config.type!r}")
                state.status = "failed"
                return

            state.session = session

            # Store server capabilities for capability-aware discovery
            state.capabilities = session.get_server_capabilities()

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

            # Discover resources if server supports them
            if state.capabilities and getattr(state.capabilities, "resources", None):
                try:
                    res_result = await session.list_resources()
                    state.resources = _extract_list(res_result, "resources")
                    tmpl_result = await session.list_resource_templates()
                    state.resource_templates = _extract_list(tmpl_result, "resourceTemplates")
                except Exception as e:
                    log.warning(f"MCP server {name!r}: failed to discover resources: {e}")

            # Discover prompts if server supports them
            if state.capabilities and getattr(state.capabilities, "prompts", None):
                try:
                    prompts_result = await session.list_prompts()
                    state.prompts = _extract_list(prompts_result, "prompts")
                except Exception as e:
                    log.warning(f"MCP server {name!r}: failed to discover prompts: {e}")

            state.status = "connected"
            state.retry_count = 0
            log.info(f"MCP server {name!r} connected: {len(state.tools)} tool(s), "
                     f"{len(state.resources)} resource(s), {len(state.prompts)} prompt(s)")

        except BaseException as e:
            log.warning(f"MCP server {name!r} failed to connect: {e}")
            state.status = "failed"
            # Clean up the exit stack on failure
            if state._exit_stack:
                try:
                    await state._exit_stack.__aexit__(None, None, None)
                except Exception as exc:
                    log.debug("MCP server %r exit-stack cleanup failed: %s", name, exc)
                state._exit_stack = None

    async def _connect_stdio(self, exit_stack, server_config, message_handler=None):
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
        kwargs = {}
        if message_handler:
            kwargs["message_handler"] = message_handler
        session = await exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream, **kwargs)
        )
        await session.initialize()
        return session

    async def _connect_http(self, exit_stack, server_config, message_handler=None):
        """Connect to an HTTP MCP server. Override in tests."""
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        http_kwargs: dict = {"url": server_config.url}
        if server_config.headers:
            http_client = httpx.AsyncClient(headers=server_config.headers)
            http_kwargs["http_client"] = http_client

        read_stream, write_stream, _ = await exit_stack.enter_async_context(
            streamable_http_client(**http_kwargs)
        )
        session_kwargs = {}
        if message_handler:
            session_kwargs["message_handler"] = message_handler
        session = await exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream, **session_kwargs)
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
        """Connect to all configured MCP servers (in parallel)."""
        await asyncio.gather(
            *(self.connect_server(cfg) for cfg in configs),
            return_exceptions=True,
        )

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
            except asyncio.CancelledError:
                log.debug(f"MCP server {name!r} disconnect cancelled")
            except Exception as e:
                log.debug(f"Error closing MCP server {name!r}: {e}")
            state._exit_stack = None

        state.session = None
        state.capabilities = None
        state.tools.clear()
        state.tool_definitions.clear()
        state.resources.clear()
        state.resource_templates.clear()
        state.prompts.clear()
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
        except asyncio.CancelledError:
            log.debug("MCP disconnect cancelled during shutdown")

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
