# MCP Server Support — Spec

## Goal

Connect external MCP (Model Context Protocol) servers as tool providers. Discover tools on startup, namespace them, and route calls through the existing `execute_tool` system. Supports both stdio and HTTP transports.

## Design Principles

- **Admin-configured, globally available**: MCP servers are configured in the agent-level directory (outside workspace). No activation or confirmation needed — tools are available immediately on startup.
- **Claude Code compatible config**: JSON config format matches Claude Code's `mcpServers` shape for familiarity.
- **Namespaced tools**: `mcp__<server>__<tool>` naming prevents collisions with built-in tools and between servers.
- **Use the official SDK**: `mcp` Python package for protocol correctness. Fully async — fits DecafClaw's async architecture.
- **Eager startup**: all configured servers are connected and tools discovered at startup.
- **Resilient**: auto-restart crashed servers with backoff, skip servers that fail to start.
- **Separate MCP registry**: MCP tools live in their own registry, merged at call time. Built-in `TOOLS` and `TOOL_DEFINITIONS` stay immutable.

## Configuration

### File: `data/{agent_id}/mcp_servers.json`

```json
{
  "mcpServers": {
    "oblique-strategies": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/lmorchard/oblique-strategies-mcp", "oblique-strategies-mcp"],
      "env": {}
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

- Lives at `data/{agent_id}/mcp_servers.json` — outside workspace, read-only to agent
- Top-level key `mcpServers` matches Claude Code format
- Server name is the key (used in tool namespacing). Must be lowercase alphanumeric + hyphens. Validated on load; invalid names logged and skipped.
- `type`: `"stdio"` or `"http"`
- Optional `timeout` per server (ms) for tool calls. Default: 30000 (30s).
- Stdio: `command`, `args` (list), `env` (optional dict of env vars passed to subprocess)
- HTTP: `url`, `headers` (optional dict)
- **Environment variable expansion**: `${VAR}` and `${VAR:-default}` syntax in `command`, `args`, `env`, `url`, and `headers` values. Expands from the host process environment.
- If file doesn't exist, no MCP servers are loaded (not an error)

## Server Lifecycle

### Startup

1. Read and parse `mcp_servers.json` (skip if missing)
2. Expand `${VAR}` references in config values
3. For each server:
   - **stdio**: launch subprocess via the `mcp` SDK's stdio transport, perform protocol handshake
   - **http**: connect via the `mcp` SDK's HTTP/Streamable HTTP transport
4. Call `tools/list` on each connected server
5. Convert MCP tool definitions to OpenAI-style tool definitions with `mcp__<server>__<tool>` naming
6. Register in a separate MCP tool registry (not the global `TOOLS`/`TOOL_DEFINITIONS`)
7. If a server fails to connect, log a warning and skip it (don't block startup)

The agent loop and `execute_tool` merge the MCP registry with built-in tools at call time, similar to how `extra_tools` works for skills.

### Tool Execution

When `execute_tool` receives a call for `mcp__<server>__<tool>`:
1. Parse the server name and tool name from the namespaced string
2. Look up the MCP client session for that server
3. Call `tools/call` on the server with the original tool name and arguments (with per-server timeout, default 30s)
4. Convert the MCP response:
   - `text` content items → concatenate `.text` fields
   - `image`/`audio` content items → return placeholder `[image: N bytes]` / `[audio: N bytes]` (deferred: render in Mattermost via file upload)
   - `isError: true` → prefix with `[error: ...]`
   - Protocol errors → return `[error: ...]`
5. Return the string result

### Auto-Restart (stdio servers)

- If a stdio server process dies, mark it as disconnected
- On next tool call to that server, attempt reconnection
- Exponential backoff: 1s, 2s, 4s between retries
- Max 3 retry attempts, then give up and log error
- Retry counter resets on successful reconnection
- `mcp_status(action="restart")` also triggers reconnection

### SDK Session Lifecycle

The `mcp` SDK uses nested async context managers: an outer transport context (`stdio_client` or `streamable_http_client`) yields `(read_stream, write_stream)`, and an inner `ClientSession` wraps them. These contexts must remain open for the lifetime of the server connection. The MCP registry manages entering/exiting these contexts on connect/disconnect.

For HTTP servers, the SDK manages session IDs (`Mcp-Session-Id`) internally.

### Graceful Shutdown

- On SIGTERM/SIGINT, close all MCP client sessions
- For stdio servers: send close, then terminate subprocess
- For HTTP servers: close the HTTP client
- Timeout on cleanup (5s) — don't hang forever on unresponsive servers
- Hook into existing graceful shutdown path

## Tool Namespacing

All MCP tools are registered with the format:

```
mcp__<server-name>__<tool-name>
```

Example: server `oblique-strategies` with tool `get_strategy` → `mcp__oblique-strategies__get_strategy`

The LLM sees these in its tool definitions and calls them by the namespaced name. The MCP client strips the namespace before forwarding to the server.

## Management Tool: `mcp_status`

A built-in tool (not from MCP) for visibility and control:

```json
{
  "name": "mcp_status",
  "parameters": {
    "action": "status | restart",
    "server": "(optional) server name for restart"
  }
}
```

### Actions

- **`status`** (default): returns a list of all configured MCP servers, their connection state (connected/disconnected/failed), transport type, and the tools they provide.
- **`restart`**: reconnects a specific server (or all if `server` is omitted). Re-discovers tools after reconnection. Also re-reads `mcp_servers.json` to pick up config changes.

## MCP Tool Definition Conversion

MCP `tools/list` returns:
```json
{
  "name": "get_strategy",
  "description": "Returns a random oblique strategy",
  "inputSchema": { "type": "object", "properties": { ... } }
}
```

Converted to OpenAI-style:
```json
{
  "type": "function",
  "function": {
    "name": "mcp__oblique-strategies__get_strategy",
    "description": "Returns a random oblique strategy",
    "parameters": { "type": "object", "properties": { ... } }
  }
}
```

The `inputSchema` maps directly to `parameters` — both are JSON Schema.

## Deferred Improvements

- **Rich media in Mattermost**: MCP `image`/`audio` content rendered via file upload (depends on Mattermost file upload support in BACKLOG-MATTERMOST.md)
- **`notifications/tools/list_changed`**: re-discover tools when a server signals its tool list changed
- **Tool search / deferred loading**: when many MCP tools would bloat the context, defer discovery like Claude Code does with `MCPSearch`
- **OAuth for HTTP servers**: Claude Code supports OAuth config for HTTP MCP servers
- **SSE transport**: deprecated but some servers still use it
- **Bidirectional MCP**: DecafClaw exposes its own tools as an MCP server

## Testing

1. **Config parsing** — reads valid config, handles missing file, expands `${VAR}` and `${VAR:-default}`
2. **Tool namespacing** — `mcp__server__tool` format, conversion from MCP to OpenAI-style definitions
3. **Tool execution routing** — namespaced tool name parsed correctly, routed to right server
4. **MCP response conversion** — text concatenation, image/audio placeholders, error handling
5. **Auto-restart** — retry logic with backoff, max retries, counter reset
6. **mcp_status tool** — returns server states, restart action works
7. **Graceful shutdown** — all sessions closed, subprocesses terminated
8. **Integration** — oblique-strategies-mcp as a live stdio server test (if available)

## Out of Scope (this session)

- OAuth authentication for HTTP servers
- SSE transport (deprecated)
- Bidirectional MCP (DecafClaw as server)
- `notifications/tools/list_changed` handling
- Tool search / deferred loading for large tool sets
- Rich media rendering in Mattermost
- MCP resources and prompts (tools only)
