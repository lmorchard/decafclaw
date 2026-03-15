# MCP Server Support

DecafClaw can connect to external [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) servers as tool providers. This gives the agent access to tools from any MCP-compatible server — file systems, databases, APIs, and more.

## Configuration

Create `data/{agent_id}/mcp_servers.json` with your server definitions. The format matches [Claude Code's MCP config](https://code.claude.com/docs/en/mcp) for familiarity:

```json
{
  "mcpServers": {
    "oblique-strategies": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/lmorchard/oblique-strategies-mcp", "oblique-strategies-mcp"]
    },
    "remote-api": {
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${API_KEY}"
      }
    }
  }
}
```

### Server types

| Type | Description | Fields |
|------|-------------|--------|
| `stdio` | Launches a local subprocess | `command`, `args`, `env` |
| `http` | Connects to an HTTP endpoint | `url`, `headers` |

### Optional fields

- `timeout` — Tool call timeout in milliseconds (default: 30000)
- `env` — Environment variables passed to stdio subprocesses

### Environment variable expansion

Use `${VAR}` or `${VAR:-default}` in any string value. Variables are expanded from the host process environment:

```json
{
  "headers": {
    "Authorization": "Bearer ${MY_API_KEY}"
  },
  "env": {
    "DB_URL": "${DATABASE_URL:-sqlite:///default.db}"
  }
}
```

### Server name requirements

Server names must be lowercase alphanumeric with hyphens (e.g., `my-server`). They become part of the tool namespace.

## How it works

1. On startup, DecafClaw reads `mcp_servers.json` and connects to each server
2. Tools are discovered via `tools/list` and registered with namespaced names: `mcp__<server>__<tool>`
3. The agent sees these tools alongside built-in tools and can call them directly
4. No activation or confirmation needed — MCP tools are immediately available

## Tool namespacing

All MCP tools are prefixed with the server name to prevent collisions:

```
mcp__oblique-strategies__get_strategy
mcp__oblique-strategies__search_strategies
mcp__oblique-strategies__list_editions
```

The agent calls them by the full namespaced name. The prefix is stripped before forwarding to the server.

## Management

The `mcp_status` tool provides visibility and control:

- **Status**: shows all servers, their connection state, and available tools
- **Restart**: reconnects a specific server or reloads all config

The agent can use this tool, or you can ask it: "show me the MCP server status" or "restart the MCP servers."

Restart also re-reads `mcp_servers.json`, so you can add/remove/modify servers without restarting DecafClaw.

## Auto-restart

If a stdio server crashes, DecafClaw automatically attempts to reconnect on the next tool call:

- Exponential backoff: 1s, 2s, 4s, 8s between attempts
- Maximum 3 retries, then gives up
- Retry counter resets on successful reconnection
- Use `mcp_status(action="restart")` to manually trigger reconnection

## Example: oblique-strategies-mcp

A simple test server that serves creative prompt cards:

```json
{
  "mcpServers": {
    "oblique-strategies": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/lmorchard/oblique-strategies-mcp", "oblique-strategies-mcp"]
    }
  }
}
```

This provides three tools: `get_strategy`, `search_strategies`, and `list_editions`.

## Finding MCP servers

- [MCP Server Registry](https://github.com/modelcontextprotocol/servers) — official list of community servers
- Many servers are installable via `npx` or `uvx` without cloning repos
