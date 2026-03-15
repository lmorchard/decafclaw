"""MCP management tools — status and restart."""

import logging

log = logging.getLogger(__name__)


async def tool_mcp_status(ctx, action: str = "status", server: str = "") -> str:
    """Show MCP server status or restart servers."""
    from ..mcp_client import get_registry, load_mcp_config, init_mcp, shutdown_mcp

    log.info(f"[tool:mcp_status] action={action} server={server}")

    registry = get_registry()

    if action == "restart":
        return await _restart(ctx, registry, server)

    # Default: status
    if not registry or not registry.servers:
        return "No MCP servers configured."

    lines = ["MCP Server Status:\n"]
    for name, state in registry.servers.items():
        tool_names = [t.split("__")[-1] for t in state.tools.keys()]
        if tool_names:
            tools_str = ", ".join(tool_names)
        else:
            tools_str = "(no tools)"
        lines.append(f"- **{name}** ({state.config.type}, {state.status}): {tools_str}")

    return "\n".join(lines)


async def _restart(ctx, registry, server_name):
    """Restart MCP servers, re-reading config."""
    from ..mcp_client import get_registry, load_mcp_config, init_mcp, shutdown_mcp

    if server_name:
        # Restart a specific server
        if not registry or server_name not in registry.servers:
            return f"[error: MCP server '{server_name}' not found]"

        # Re-read config to pick up changes
        configs = load_mcp_config(ctx.config)
        new_config = next((c for c in configs if c.name == server_name), None)
        if not new_config:
            return f"[error: MCP server '{server_name}' not found in config]"

        await registry.disconnect_server(server_name)
        await registry.connect_server(new_config)

        state = registry.servers.get(server_name)
        status = state.status if state else "unknown"
        tool_count = len(state.tools) if state else 0
        return f"MCP server '{server_name}' restarted: {status}, {tool_count} tool(s)"

    else:
        # Restart all — full reload
        await shutdown_mcp()
        await init_mcp(ctx.config)

        registry = get_registry()
        if not registry or not registry.servers:
            return "MCP servers reloaded: no servers configured."

        connected = sum(1 for s in registry.servers.values() if s.status == "connected")
        total_tools = sum(len(s.tools) for s in registry.servers.values())
        return f"MCP servers reloaded: {connected} connected, {total_tools} tool(s)"


MCP_TOOLS = {
    "mcp_status": tool_mcp_status,
}

MCP_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "mcp_status",
            "description": (
                "Show status of MCP servers or restart them. "
                "Use 'status' (default) to see connected servers and their tools. "
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
]
