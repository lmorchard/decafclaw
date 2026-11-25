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
from enum import Enum
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


_MISSING = object()


def _snake_case(name: str) -> str:
    """Convert a camelCase SDK field name to its snake_case attribute name."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _ga(obj, key, default=None) -> Any:
    """Get a field from a dict or SDK object uniformly.

    SDK v2 models use snake_case attributes with camelCase JSON aliases; callers
    pass the JSON spelling. Try the exact key first (dicts and v1-style mocks),
    then the snake_case attribute.
    """
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        snake = _snake_case(key)
        if snake in obj:
            return obj[snake]
        return default
    value = getattr(obj, key, _MISSING)
    if value is not _MISSING:
        return value
    return getattr(obj, _snake_case(key), default)


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

# MCP protocol revision policy. "auto" keeps the legacy initialize handshake and
# steps up to 2026-07-28 only when an HTTP server says it is modern-only.
PROTOCOL_AUTO = "auto"
PROTOCOL_LEGACY = "2024-11-05"
PROTOCOL_MODERN = "2026-07-28"
_VALID_PROTOCOLS = frozenset({PROTOCOL_AUTO, PROTOCOL_LEGACY, PROTOCOL_MODERN})


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
    protocol: str = PROTOCOL_AUTO


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
        protocol = server_data.get("protocol", PROTOCOL_AUTO)
        if protocol not in _VALID_PROTOCOLS:
            log.warning(
                f"MCP server {name!r}: unknown protocol {protocol!r}; "
                f"expected one of {sorted(_VALID_PROTOCOLS)}. Using {PROTOCOL_AUTO!r}."
            )
            protocol = PROTOCOL_AUTO
        configs.append(
            MCPServerConfig(
                name=name,
                type=server_type,
                command=server_data.get("command", ""),
                args=server_data.get("args", []),
                env=server_data.get("env", {}),
                url=server_data.get("url", ""),
                headers=server_data.get("headers", {}),
                timeout=server_data.get("timeout", 30000),
                protocol=protocol,
            )
        )

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


def parse_mcp_tool_name(name: str) -> tuple[str, str] | None:
    """Parse an ``mcp__<server>__<tool>`` name into (server, tool).

    The returned server segment is the normalized form
    (hyphens replaced with underscores) as advertised by
    ``_namespace_tool``. It will not match an original MCP server name if
    the server had hyphens; callers that need the original server name
    should look it up via the registry.

    Returns None if the name doesn't match the mcp__<server>__<tool>
    pattern (non-MCP names, or missing/empty segments). This is the single
    parse helper for MCP tool identifiers — use it instead of ad-hoc
    ``name.split("__", 2)``.
    """
    if not name.startswith("mcp__"):
        return None
    rest = name[5:]  # after "mcp__"
    parts = rest.split("__", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def _convert_tool_definition(server_name: str, mcp_tool) -> dict:
    """Convert an MCP tool definition to OpenAI-style tool definition.

    mcp_tool can be a dict or an SDK Tool object (has .name, .description,
    .input_schema / .inputSchema).
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
    if not parts:
        structured = _ga(result, "structuredContent")
        if structured is not None:
            text = json.dumps(structured, indent=2, default=str)

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
            media.append(
                {
                    "type": "file",
                    "filename": filename,
                    "data": data,
                    "content_type": mime_type,
                }
            )
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


# -- Exceptions ----------------------------------------------------------------


class MCPReadinessError(RuntimeError):
    """Base error for MCP readiness policy failures."""


class MCPTimeoutError(MCPReadinessError):
    """Raised when waiting for required MCP servers times out."""


class MCPConnectionError(MCPReadinessError):
    """Raised when a required MCP server fails to connect."""


def _find_mcp_error(exc: BaseException) -> BaseException | None:
    """Find an MCPError inside the task-group ExceptionGroups the SDK raises."""
    from mcp.shared.exceptions import MCPError

    if isinstance(exc, MCPError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            found = _find_mcp_error(sub)
            if found is not None:
                return found
    return None


def _is_modern_only_error(exc: BaseException) -> bool:
    """Whether an initialize failure is positive evidence of a modern-only server.

    MCP revision 2026-07-28 servers reject the legacy handshake with -32022
    and a ``supported`` list. Only that signal triggers the step-up; method
    errors, timeouts, and transport failures keep the legacy verdict.
    """
    from mcp.shared.exceptions import MCPError
    from mcp.types import UNSUPPORTED_PROTOCOL_VERSION, UnsupportedProtocolVersionErrorData
    from mcp.types.version import MODERN_PROTOCOL_VERSIONS
    from pydantic import ValidationError

    if not isinstance(exc, MCPError) or exc.code != UNSUPPORTED_PROTOCOL_VERSION:
        return False

    data = getattr(getattr(exc, "error", None), "data", None)
    try:
        supported = UnsupportedProtocolVersionErrorData.model_validate(data).supported
    except (TypeError, ValidationError):
        # -32022 without a parsable version list still names a revision we do
        # not speak; let the modern probe settle it.
        return True
    return any(version in MODERN_PROTOCOL_VERSIONS for version in supported)


# -- MCP Registry --------------------------------------------------------------


class ConnState(Enum):
    """Connection lifecycle state for an MCP server."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    FAILED = "failed"


@dataclass
class MCPServerState:
    """Runtime state for a single MCP server connection."""

    config: MCPServerConfig
    status: ConnState = ConnState.DISCONNECTED
    session: Any = None  # ClientSession when connected
    capabilities: Any = None  # ServerCapabilities from initialize()
    tools: dict = field(default_factory=dict)  # namespaced_name -> callable
    tool_definitions: list = field(default_factory=list)  # OpenAI-style defs
    resources: list = field(default_factory=list)  # from list_resources()
    resource_templates: list = field(default_factory=list)  # from list_resource_templates()
    prompts: list = field(default_factory=list)  # from list_prompts()
    retry_count: int = 0
    last_retry_time: float = 0.0
    error: str | None = None
    settled_event: asyncio.Event = field(default_factory=asyncio.Event)
    _generation: int = 0
    _exit_stack: Any = None  # AsyncExitStack for managing context lifetimes

    @property
    def is_ready(self) -> bool:
        """Check if server is ready/connected."""
        return self.status == ConnState.CONNECTED


@contextlib.asynccontextmanager
async def _open_stdio_session(server_config: MCPServerConfig, message_handler, *, modern: bool):
    """Open a stdio ClientSession and complete the configured handshake."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=server_config.command,
        args=server_config.args,
        env={**os.environ, **server_config.env} if server_config.env else None,
    )
    session_kwargs = {"message_handler": message_handler} if message_handler else {}
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream, **session_kwargs) as session:
            if modern:
                await session.discover()
            else:
                await session.initialize()
            yield session


@contextlib.asynccontextmanager
async def _open_http_session(server_config: MCPServerConfig, message_handler, *, modern: bool):
    """Open a Streamable HTTP ClientSession and complete the configured handshake."""
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    http_client = (
        # Mirror the SDK's transport defaults: short connect/write/pool waits,
        # long read waits for server-held response streams.
        httpx2.AsyncClient(headers=server_config.headers, timeout=httpx2.Timeout(30.0, read=300.0))
        if server_config.headers
        else None
    )
    session_kwargs = {"message_handler": message_handler} if message_handler else {}
    try:
        async with streamable_http_client(server_config.url, http_client=http_client) as (
            read_stream,
            write_stream,
        ):
            async with ClientSession(read_stream, write_stream, **session_kwargs) as session:
                if modern:
                    await session.discover()
                else:
                    await session.initialize()
                yield session
    finally:
        if http_client is not None:
            await http_client.aclose()


class MCPRegistry:
    """Registry of MCP server connections and their tools."""

    def __init__(self, event_bus=None):
        self.servers: dict[str, MCPServerState] = {}
        self.event_bus = event_bus
        self._closed: bool = False
        self._connection_tasks: dict[str, asyncio.Task] = {}

    def get_tools(self) -> dict:
        """Return all MCP tools as {namespaced_name: async_callable}."""
        tools = {}
        for state in self.servers.values():
            if state.status == ConnState.CONNECTED:
                tools.update(state.tools)
        return tools

    def get_tool_definitions(self) -> list[dict]:
        """Return all MCP tool definitions in OpenAI format."""
        defs = []
        for state in self.servers.values():
            if state.status == ConnState.CONNECTED:
                defs.extend(state.tool_definitions)
        return defs

    def get_resources(self) -> list[tuple[str, Any]]:
        """Return all MCP resources as (server_name, resource) tuples."""
        results = []
        for name, state in self.servers.items():
            if state.status == ConnState.CONNECTED:
                for res in state.resources:
                    results.append((name, res))
        return results

    def get_resource_templates(self) -> list[tuple[str, Any]]:
        """Return all MCP resource templates as (server_name, template) tuples."""
        results = []
        for name, state in self.servers.items():
            if state.status == ConnState.CONNECTED:
                for tmpl in state.resource_templates:
                    results.append((name, tmpl))
        return results

    def get_prompts(self) -> list[tuple[str, Any]]:
        """Return all MCP prompts as (server_name, prompt) tuples."""
        results = []
        for name, state in self.servers.items():
            if state.status == ConnState.CONNECTED:
                for prompt in state.prompts:
                    results.append((name, prompt))
        return results

    async def refresh_tools(self, server_name: str):
        """Re-discover tools for a connected server."""
        state = self.servers.get(server_name)
        if not state or state.status != ConnState.CONNECTED or not state.session:
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
                new_tools[namespaced] = self._make_tool_caller(server_name, tool_name, state.config.timeout)
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
        if not state or state.status != ConnState.CONNECTED or not state.session:
            return
        if not state.capabilities or not getattr(state.capabilities, "resources", None):
            return

        try:
            res_result = await state.session.list_resources()
            state.resources = _extract_list(res_result, "resources")
            tmpl_result = await state.session.list_resource_templates()
            state.resource_templates = _extract_list(tmpl_result, "resourceTemplates")
            log.info(
                f"MCP server {server_name!r} resources refreshed: "
                f"{len(state.resources)} resource(s), {len(state.resource_templates)} template(s)"
            )
        except Exception as e:
            log.warning(f"MCP server {server_name!r}: failed to refresh resources: {e}")

    async def refresh_prompts(self, server_name: str):
        """Re-discover prompts for a connected server."""
        state = self.servers.get(server_name)
        if not state or state.status != ConnState.CONNECTED or not state.session:
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

            # Stream errors arrive as Exception items; only notifications matter.
            if isinstance(message, Exception):
                return

            try:
                if isinstance(message, _mcp_types.ToolListChangedNotification):
                    await self.refresh_tools(server_name)
                elif isinstance(message, _mcp_types.ResourceListChangedNotification):
                    await self.refresh_resources(server_name)
                elif isinstance(message, _mcp_types.PromptListChangedNotification):
                    await self.refresh_prompts(server_name)
                else:
                    log.debug(f"MCP server {server_name!r}: unhandled notification {type(message).__name__}")
            except Exception as e:
                log.warning(f"MCP server {server_name!r}: notification handler error: {e}")

        return handler

    def start_connect_server(self, server_config: MCPServerConfig) -> asyncio.Task:
        """Start connecting to a single MCP server in the background."""
        name = server_config.name
        state = self.servers.get(name)
        if state is None:
            state = MCPServerState(config=server_config, status=ConnState.CONNECTING)
            self.servers[name] = state
        else:
            state.config = server_config
            state.status = ConnState.CONNECTING
            state.error = None
            state.settled_event.clear()

        # Increment generation counter so old in-flight attempts cannot clobber replacement
        state._generation += 1
        gen = state._generation

        # Cancel existing task for this server if one is still running
        existing_task = self._connection_tasks.get(name)
        if existing_task and not existing_task.done():
            existing_task.cancel()

        task = asyncio.create_task(
            self._connect_server_wrapper(server_config, gen),
            name=f"mcp-connect-{name}",
        )
        self._connection_tasks[name] = task

        def _cleanup(t):
            if self._connection_tasks.get(name) is t:
                self._connection_tasks.pop(name, None)

        task.add_done_callback(_cleanup)
        return task

    async def _connect_server_wrapper(self, server_config: MCPServerConfig, gen: int):
        try:
            await self.connect_server(server_config, generation=gen)
        except asyncio.CancelledError:
            log.debug("MCP connection task for %r cancelled", server_config.name)
            raise

    def start_connect_all(self, configs: list[MCPServerConfig]) -> list[asyncio.Task]:
        """Start connecting all configured MCP servers concurrently in the background."""
        if self._closed:
            log.debug("MCPRegistry is closed; not starting connections")
            return []
        return [self.start_connect_server(cfg) for cfg in configs]

    async def connect_server(self, server_config: MCPServerConfig, generation: int | None = None):
        """Connect to a single MCP server and discover its tools."""
        name = server_config.name
        state = self.servers.get(name)
        if self._closed:
            log.debug("MCPRegistry is closed; skipping connection to %r", name)
            if state:
                state.status = ConnState.DISCONNECTED
                state.settled_event.set()
            return

        if state is None:
            state = MCPServerState(config=server_config, status=ConnState.CONNECTING)
            self.servers[name] = state
        else:
            state.config = server_config
            state.status = ConnState.CONNECTING
            state.error = None
            state.settled_event.clear()

        if generation is None:
            state._generation += 1
            gen = state._generation
        else:
            gen = generation

        if self.event_bus:
            try:
                await self.event_bus.publish({"type": "mcp_server_connecting", "server": name})
            except Exception as e:
                log.debug("failed to publish mcp_server_connecting: %s", e)

        exit_stack = contextlib.AsyncExitStack()
        try:
            state._exit_stack = exit_stack
            await exit_stack.__aenter__()

            if self._closed or state._generation != gen:
                await exit_stack.__aexit__(None, None, None)
                if state._generation == gen:
                    state._exit_stack = None
                    state.status = ConnState.DISCONNECTED
                    state.settled_event.set()
                return

            message_handler = self._make_notification_handler(name)

            if server_config.type == "stdio":
                session = await self._connect_stdio(exit_stack, server_config, message_handler)
            elif server_config.type == "http":
                session = await self._connect_http(exit_stack, server_config, message_handler)
            else:
                log.warning(f"MCP server {name!r}: unknown type {server_config.type!r}")
                await exit_stack.__aexit__(None, None, None)
                if state._generation == gen:
                    state.status = ConnState.FAILED
                    state.error = f"unknown server type {server_config.type!r}"
                    state._exit_stack = None
                    state.settled_event.set()
                if self.event_bus:
                    try:
                        await self.event_bus.publish(
                            {"type": "mcp_server_failed", "server": name, "error": state.error or "unknown type"}
                        )
                    except Exception as exc:
                        log.debug("failed to publish mcp_server_failed: %s", exc)
                return

            if self._closed or state._generation != gen:
                await exit_stack.__aexit__(None, None, None)
                if state._generation == gen:
                    state._exit_stack = None
                    state.status = ConnState.DISCONNECTED
                    state.settled_event.set()
                return

            state.session = session

            # Store server capabilities for capability-aware discovery
            state.capabilities = session.server_capabilities

            # Discover tools
            tools_result = await session.list_tools()
            tools_list = tools_result.tools if hasattr(tools_result, "tools") else tools_result

            new_tools = {}
            new_defs = []
            for mcp_tool in tools_list:
                tool_def = _convert_tool_definition(name, mcp_tool)
                namespaced = tool_def["function"]["name"]
                tool_name = mcp_tool.name if hasattr(mcp_tool, "name") else mcp_tool["name"]

                # Build async callable wrapper (uses state.session, supports reconnect)
                new_tools[namespaced] = self._make_tool_caller(name, tool_name, server_config.timeout)
                new_defs.append(tool_def)

            state.tools = new_tools
            state.tool_definitions = new_defs

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

            if self._closed or state._generation != gen:
                await exit_stack.__aexit__(None, None, None)
                if state._generation == gen:
                    state._exit_stack = None
                    state.tools.clear()
                    state.tool_definitions.clear()
                    state.resources.clear()
                    state.resource_templates.clear()
                    state.prompts.clear()
                    state.status = ConnState.DISCONNECTED
                    state.settled_event.set()
                return

            state.status = ConnState.CONNECTED
            state.retry_count = 0
            state.error = None
            state.settled_event.set()

            if self.event_bus:
                try:
                    await self.event_bus.publish({"type": "mcp_server_connected", "server": name})
                except Exception as e:
                    log.debug("failed to publish mcp_server_connected: %s", e)

            log.info(
                f"MCP server {name!r} connected: {len(state.tools)} tool(s), "
                f"{len(state.resources)} resource(s), {len(state.prompts)} prompt(s)"
            )

        except asyncio.CancelledError:
            log.debug(f"MCP server {name!r} connection cancelled")
            try:
                await exit_stack.__aexit__(None, None, None)
            except Exception as exc:
                log.debug("MCP server %r exit-stack cleanup failed: %s", name, exc)
            if state._generation == gen:
                if state._exit_stack is exit_stack:
                    state._exit_stack = None
                state.status = ConnState.DISCONNECTED
                state.settled_event.set()
            raise
        except BaseException as e:
            log.warning(f"MCP server {name!r} failed to connect: {e}")
            try:
                await exit_stack.__aexit__(None, None, None)
            except Exception as exc:
                log.debug("MCP server %r exit-stack cleanup failed: %s", name, exc)
            if state._generation == gen:
                state.status = ConnState.FAILED
                state.error = str(e)
                state.settled_event.set()
                if state._exit_stack is exit_stack:
                    state._exit_stack = None
            if self.event_bus:
                try:
                    await self.event_bus.publish({"type": "mcp_server_failed", "server": name, "error": str(e)})
                except Exception as exc:
                    log.debug("failed to publish mcp_server_failed: %s", exc)

    async def _connect_stdio(self, exit_stack, server_config, message_handler=None):
        """Connect to a stdio MCP server. Override in tests."""
        modern = server_config.protocol == PROTOCOL_MODERN
        return await exit_stack.enter_async_context(_open_stdio_session(server_config, message_handler, modern=modern))

    async def _connect_http(self, exit_stack, server_config, message_handler=None):
        """Connect to an HTTP MCP server. Override in tests.

        ``auto`` tries the legacy initialize handshake first, because the
        existing server fleet is legacy-only and a few servers mishandle an
        unknown ``server/discover``. A -32022 that names a modern revision is
        positive evidence of a modern-only server, so the connection is retried
        with discovery.
        """
        if server_config.protocol == PROTOCOL_MODERN:
            return await exit_stack.enter_async_context(_open_http_session(server_config, message_handler, modern=True))
        if server_config.protocol == PROTOCOL_LEGACY:
            return await exit_stack.enter_async_context(
                _open_http_session(server_config, message_handler, modern=False)
            )

        try:
            return await exit_stack.enter_async_context(
                _open_http_session(server_config, message_handler, modern=False)
            )
        except Exception as exc:
            mcp_error = _find_mcp_error(exc)
            if mcp_error is None or not _is_modern_only_error(mcp_error):
                raise
            log.info(
                "MCP server %r is modern-only; retrying with protocol %s",
                server_config.name,
                PROTOCOL_MODERN,
            )

        return await exit_stack.enter_async_context(_open_http_session(server_config, message_handler, modern=True))

    def _make_tool_caller(self, server_name, tool_name, timeout_ms):
        """Create an async callable that invokes an MCP tool.

        Uses the server state's current session, supporting auto-reconnect.
        """
        timeout_s = timeout_ms / 1000

        async def call_tool(arguments):
            from .media import ToolResult

            # Try auto-reconnect if server is down
            if not await self._maybe_reconnect(server_name):
                return ToolResult(text=f"[error: MCP server {server_name} is not available]")

            state = self.servers.get(server_name)
            if not state or not state.session:
                return ToolResult(text=f"[error: MCP server {server_name} has no active session]")

            try:
                result = await asyncio.wait_for(
                    state.session.call_tool(tool_name, arguments),
                    timeout=timeout_s,
                )
                return _convert_mcp_response(result)
            except asyncio.TimeoutError:
                return ToolResult(text=f"[error: MCP tool {tool_name} on {server_name} timed out after {timeout_s}s]")
            except Exception as e:
                # Mark server as failed for auto-restart on next call
                if state:
                    state.status = ConnState.FAILED
                return ToolResult(text=f"[error: MCP tool {tool_name} on {server_name} failed: {e}]")

        return call_tool

    async def _maybe_reconnect(self, server_name: str) -> bool:
        """Check if a server needs reconnection and attempt it.

        Returns True if the server is connected (or was reconnected).
        """
        state = self.servers.get(server_name)
        if not state:
            return False

        if state.status == ConnState.CONNECTED:
            return True

        # Give up after max retries
        max_retries = 3
        if state.retry_count >= max_retries:
            return False

        # Check backoff timing
        backoff = min(2**state.retry_count, 8)
        now = time.monotonic()
        if now - state.last_retry_time < backoff:
            return False

        # Attempt reconnection
        log.info(
            f"Attempting reconnection for MCP server {server_name!r} (attempt {state.retry_count + 1}/{max_retries})"
        )
        state.last_retry_time = now

        # Disconnect first if there's stale state
        await self.disconnect_server(server_name)

        # Reconnect
        await self.connect_server(state.config)

        new_state = self.servers.get(server_name)
        if new_state and new_state.status == ConnState.CONNECTED:
            log.info(f"MCP server {server_name!r} reconnected successfully")
            return True

        # Increment retry count (connect_server resets it on success,
        # so we only get here on failure)
        if new_state:
            new_state.retry_count = state.retry_count + 1
        return False

    async def connect_all(self, configs: list[MCPServerConfig], wait: bool = True):
        """Connect to all configured MCP servers (in parallel)."""
        tasks = self.start_connect_all(configs)
        if wait and tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        connected = sum(1 for s in self.servers.values() if s.status == ConnState.CONNECTED)
        failed = sum(1 for s in self.servers.values() if s.status == ConnState.FAILED)
        connecting = sum(1 for s in self.servers.values() if s.status == ConnState.CONNECTING)
        total_tools = sum(len(s.tools) for s in self.servers.values())
        log.info(f"MCP: {connected} connected, {connecting} connecting, {failed} failed, {total_tools} tool(s)")

    async def wait_for_server(self, name: str, timeout: float | None = 30.0) -> bool:
        """Wait for a single MCP server to connect or fail within deadline.

        Raises:
            MCPReadinessError: If the server is not configured.
            MCPTimeoutError: If the server does not settle before timeout.
            MCPConnectionError: If the server fails to connect.
        """
        state = self.servers.get(name)
        if not state:
            raise MCPReadinessError(f"Required MCP server {name!r} is not configured")

        if state.status == ConnState.CONNECTED:
            return True
        if state.status == ConnState.FAILED:
            raise MCPConnectionError(
                f"Required MCP server {name!r} failed to connect: {state.error or 'connection failed'}"
            )

        try:
            if timeout is not None:
                await asyncio.wait_for(state.settled_event.wait(), timeout=timeout)
            else:
                await state.settled_event.wait()
        except asyncio.TimeoutError:
            raise MCPTimeoutError(f"Required MCP server {name!r} timed out waiting for connection after {timeout}s")

        if state.status == ConnState.CONNECTED:
            return True
        raise MCPConnectionError(
            f"Required MCP server {name!r} failed to connect: {state.error or 'connection failed'}"
        )

    async def wait_for_servers(self, names: list[str] | set[str], timeout: float = 30.0) -> dict[str, bool]:
        """Wait for declared server dependencies concurrently within deadline.

        Does not wait on unmentioned slow servers.
        Raises MCPReadinessError/MCPTimeoutError/MCPConnectionError on failure.
        """
        if not names:
            return {}

        results = await asyncio.gather(
            *(self.wait_for_server(name, timeout=timeout) for name in names),
            return_exceptions=False,
        )
        return dict(zip(names, results))

    async def disconnect_server(self, name: str):
        """Disconnect a single MCP server."""
        task = self._connection_tasks.pop(name, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

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
        state.status = ConnState.DISCONNECTED
        state.settled_event.set()
        log.info(f"MCP server {name!r} disconnected")
        if self.event_bus:
            try:
                await self.event_bus.publish({"type": "mcp_server_disconnected", "server": name})
            except Exception as exc:
                log.debug("failed to publish mcp_server_disconnected: %s", exc)

    async def disconnect_all(self):
        """Cancel in-flight connection tasks and disconnect all servers with a timeout."""
        self._closed = True

        # 1. Cancel and drain all in-flight connection tasks
        active_tasks = [t for t in self._connection_tasks.values() if not t.done()]
        for t in active_tasks:
            t.cancel()
        if active_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*active_tasks, return_exceptions=True),
                    timeout=5.0,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        self._connection_tasks.clear()

        # 2. Disconnect any connected servers
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


async def init_mcp(config, event_bus=None, wait: bool = False) -> MCPRegistry | None:
    """Initialize the global MCP registry and start connecting servers."""
    global _registry
    configs = load_mcp_config(config)
    if not configs:
        log.info("No MCP servers configured")
        return None

    _registry = MCPRegistry(event_bus=event_bus)
    if wait:
        await _registry.connect_all(configs, wait=True)
    else:
        _registry.start_connect_all(configs)
    return _registry


async def shutdown_mcp():
    """Shut down the global MCP registry."""
    global _registry
    if _registry:
        reg = _registry
        _registry = None
        await reg.disconnect_all()
