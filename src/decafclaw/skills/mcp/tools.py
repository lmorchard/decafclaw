"""MCP management tools — status, resources, prompts, restart."""

import logging

from decafclaw.media import ToolResult

log = logging.getLogger(__name__)


async def tool_mcp_status(ctx, action: str = "status", server: str = "") -> str | ToolResult:
    """Show MCP server status or restart servers."""
    from decafclaw.mcp_client import get_registry

    log.info(f"[tool:mcp_status] action={action} server={server}")

    registry = get_registry()

    if action == "restart":
        return await _restart(ctx, registry, server)

    # Default: status
    if not registry or not registry.servers:
        return "No MCP servers configured."

    lines = ["MCP Server Status:\n"]
    for name, state in registry.servers.items():
        tool_count = len(state.tools)
        resource_count = len(state.resources)
        template_count = len(state.resource_templates)
        prompt_count = len(state.prompts)

        parts = [f"{tool_count} tool(s)"]
        if resource_count or template_count:
            parts.append(f"{resource_count} resource(s)")
            if template_count:
                parts.append(f"{template_count} template(s)")
        if prompt_count:
            parts.append(f"{prompt_count} prompt(s)")
        counts_str = ", ".join(parts)

        lines.append(f"- **{name}** ({state.config.type}, {state.status}): {counts_str}")

    return "\n".join(lines)


async def _restart(ctx, registry, server_name) -> str | ToolResult:
    """Restart MCP servers by scheduling a restart and reporting status.

    Due to anyio/asyncio cancel scope incompatibilities, MCP servers
    cannot be safely disconnected from within a tool call. Instead,
    we mark servers for reconnection — the auto-restart mechanism
    will reconnect them on the next tool call.

    For a full reload (all servers), we connect new servers without
    disconnecting old ones first, then swap the registry.
    """
    import decafclaw.mcp_client as _mcp
    from decafclaw.mcp_client import MCPRegistry, load_mcp_config

    try:
        if server_name:
            if not registry or server_name not in registry.servers:
                return ToolResult(text=f"[error: MCP server '{server_name}' not found]")

            # Mark server as failed — auto-restart will reconnect on next call
            state = registry.servers.get(server_name)
            if state:
                state.status = "failed"
                state.retry_count = 0  # reset so auto-restart kicks in
            return f"MCP server '{server_name}' marked for reconnection. It will reconnect on next use."

        else:
            # Full reload: create a fresh registry and connect
            configs = load_mcp_config(ctx.config)
            if not configs:
                return "MCP servers reloaded: no servers configured."

            new_registry = MCPRegistry()
            await new_registry.connect_all(configs)

            # Swap the global registry (old one's connections will be GC'd)
            _mcp._registry = new_registry

            connected = sum(1 for s in new_registry.servers.values() if s.status == "connected")
            total_tools = sum(len(s.tools) for s in new_registry.servers.values())
            return f"MCP servers reloaded: {connected} connected, {total_tools} tool(s)"

    except BaseException as e:
        log.error(f"MCP restart failed: {e}", exc_info=True)
        return ToolResult(text=f"[error: MCP restart failed: {e}]")


async def tool_mcp_list_resources(ctx) -> str | ToolResult:
    """List all MCP resources and resource templates."""
    from decafclaw.mcp_client import get_registry

    registry = get_registry()
    if not registry:
        return "No MCP servers configured."

    resources = registry.get_resources()
    templates = registry.get_resource_templates()

    if not resources and not templates:
        return "No MCP resources available."

    lines = ["**MCP Resources:**\n"]

    if resources:
        for server_name, res in resources:
            uri = str(getattr(res, "uri", ""))
            name = getattr(res, "name", uri)
            desc = getattr(res, "description", "")
            mime = getattr(res, "mimeType", "")
            parts = [f"- **{server_name}** / `{uri}`"]
            if name and name != uri:
                parts.append(f" — {name}")
            if desc:
                parts.append(f": {desc}")
            if mime:
                parts.append(f" [{mime}]")
            lines.append("".join(parts))

    if templates:
        lines.append("\n**Resource Templates:**\n")
        for server_name, tmpl in templates:
            uri_tmpl = str(getattr(tmpl, "uriTemplate", ""))
            name = getattr(tmpl, "name", uri_tmpl)
            desc = getattr(tmpl, "description", "")
            parts = [f"- **{server_name}** / `{uri_tmpl}` (template)"]
            if name and name != uri_tmpl:
                parts.append(f" — {name}")
            if desc:
                parts.append(f": {desc}")
            lines.append("".join(parts))

    return "\n".join(lines)


async def tool_mcp_read_resource(ctx, server: str = "", uri: str = "") -> str | ToolResult:
    """Read a resource from an MCP server by URI."""
    import asyncio

    from decafclaw.mcp_client import _convert_resource_response, get_registry

    if not server or not uri:
        return ToolResult(text="[error: both 'server' and 'uri' parameters are required]")

    registry = get_registry()
    if not registry:
        return ToolResult(text="[error: no MCP servers configured]")

    state = registry.servers.get(server)
    if not state:
        return ToolResult(text=f"[error: MCP server '{server}' not found]")
    if state.status != "connected" or not state.session:
        return ToolResult(text=f"[error: MCP server '{server}' is not connected]")

    try:
        from pydantic import AnyUrl
        timeout_s = state.config.timeout / 1000
        result = await asyncio.wait_for(
            state.session.read_resource(AnyUrl(uri)),
            timeout=timeout_s,
        )
        return _convert_resource_response(result)
    except asyncio.TimeoutError:
        return ToolResult(text=f"[error: reading resource timed out after {timeout_s}s]")
    except Exception as e:
        return ToolResult(text=f"[error: failed to read resource: {e}]")


async def tool_mcp_list_prompts(ctx) -> str | ToolResult:
    """List all MCP prompts from connected servers."""
    from decafclaw.mcp_client import get_registry

    registry = get_registry()
    if not registry:
        return "No MCP servers configured."

    prompts = registry.get_prompts()
    if not prompts:
        return "No MCP prompts available."

    lines = ["**MCP Prompts:**\n"]
    for server_name, prompt in prompts:
        name = getattr(prompt, "name", "")
        desc = getattr(prompt, "description", "")
        args = getattr(prompt, "arguments", []) or []

        line = f"- **{server_name}** / `{name}`"
        if desc:
            line += f" — {desc}"
        lines.append(line)

        for arg in args:
            arg_name = getattr(arg, "name", "") if not isinstance(arg, dict) else arg.get("name", "")
            arg_desc = getattr(arg, "description", "") if not isinstance(arg, dict) else arg.get("description", "")
            arg_req = getattr(arg, "required", False) if not isinstance(arg, dict) else arg.get("required", False)
            req_str = "required" if arg_req else "optional"
            line = f"  - `{arg_name}` ({req_str})"
            if arg_desc:
                line += f": {arg_desc}"
            lines.append(line)

    return "\n".join(lines)


async def tool_mcp_get_prompt(ctx, server: str = "", name: str = "",
                               arguments: str = "{}") -> str | ToolResult:
    """Get a prompt from an MCP server and return its messages."""
    import asyncio
    import json as _json

    from decafclaw.mcp_client import _convert_prompt_response, get_registry

    if not server or not name:
        return ToolResult(text="[error: both 'server' and 'name' parameters are required]")

    registry = get_registry()
    if not registry:
        return ToolResult(text="[error: no MCP servers configured]")

    state = registry.servers.get(server)
    if not state:
        return ToolResult(text=f"[error: MCP server '{server}' not found]")
    if state.status != "connected" or not state.session:
        return ToolResult(text=f"[error: MCP server '{server}' is not connected]")

    try:
        args_dict = _json.loads(arguments) if arguments else {}
    except _json.JSONDecodeError as e:
        return ToolResult(text=f"[error: invalid JSON arguments: {e}]")

    try:
        timeout_s = state.config.timeout / 1000
        result = await asyncio.wait_for(
            state.session.get_prompt(name, args_dict or None),
            timeout=timeout_s,
        )
        text = _convert_prompt_response(result)
        return ToolResult(text=text)
    except asyncio.TimeoutError:
        return ToolResult(text=f"[error: getting prompt timed out after {timeout_s}s]")
    except Exception as e:
        return ToolResult(text=f"[error: failed to get prompt: {e}]")


# -- Registration -------------------------------------------------------------

TOOLS = {
    "mcp_status": tool_mcp_status,
    "mcp_list_resources": tool_mcp_list_resources,
    "mcp_read_resource": tool_mcp_read_resource,
    "mcp_list_prompts": tool_mcp_list_prompts,
    "mcp_get_prompt": tool_mcp_get_prompt,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "mcp_status",
            "description": (
                "Show status of MCP servers or restart them. "
                "Use 'status' (default) to see connected servers and their tools, resources, and prompts. "
                "Use 'restart' to reconnect a specific server or reload all config. "
                "Restart also re-reads the MCP config file to pick up changes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "restart"],
                        "description": "Action to perform (default: status)",
                    },
                    "server": {
                        "type": "string",
                        "description": "Server name for targeted restart. Omit to restart all.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_list_resources",
            "description": (
                "List all resources and resource templates from connected MCP servers. "
                "Shows URI, name, description, and MIME type for each resource."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_read_resource",
            "description": (
                "Read a resource from an MCP server by URI. "
                "Returns text content or binary attachments."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {
                        "type": "string",
                        "description": "MCP server name",
                    },
                    "uri": {
                        "type": "string",
                        "description": "Resource URI to read",
                    },
                },
                "required": ["server", "uri"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_list_prompts",
            "description": (
                "List all prompts from connected MCP servers. "
                "Shows name, description, and arguments for each prompt."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_get_prompt",
            "description": (
                "Get a prompt from an MCP server and return its messages. "
                "The prompt may include templated content based on the arguments."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {
                        "type": "string",
                        "description": "MCP server name",
                    },
                    "name": {
                        "type": "string",
                        "description": "Prompt name",
                    },
                    "arguments": {
                        "type": "string",
                        "description": "JSON object of prompt arguments (default: '{}')",
                    },
                },
                "required": ["server", "name"],
            },
        },
    },
]
