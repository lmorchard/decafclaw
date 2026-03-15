"""MCP management tools — status and restart."""

import logging

log = logging.getLogger(__name__)


async def tool_mcp_status(ctx, action: str = "status", server: str = "") -> str:
    """Show MCP server status or restart servers."""
    from ..mcp_client import get_registry, init_mcp, load_mcp_config, shutdown_mcp

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
    """Restart MCP servers by scheduling a restart and reporting status.

    Due to anyio/asyncio cancel scope incompatibilities, MCP servers
    cannot be safely disconnected from within a tool call. Instead,
    we mark servers for reconnection — the auto-restart mechanism
    will reconnect them on the next tool call.

    For a full reload (all servers), we connect new servers without
    disconnecting old ones first, then swap the registry.
    """
    import decafclaw.mcp_client as _mcp

    from ..mcp_client import MCPRegistry, load_mcp_config

    try:
        if server_name:
            if not registry or server_name not in registry.servers:
                return f"[error: MCP server '{server_name}' not found]"

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
        return f"[error: MCP restart failed: {e}]"


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
