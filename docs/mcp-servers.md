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

Server names must be lowercase alphanumeric with hyphens or underscores (e.g., `my-server` or `my_server`). They become part of the tool namespace — **hyphens are normalized to underscores** in the identifier the LLM sees (see [Tool namespacing](#tool-namespacing) below).

## How it works

1. On startup, DecafClaw reads `mcp_servers.json` and connects to each server
2. Server capabilities are checked — tools, resources, and prompts are discovered based on what the server supports
3. Tools are registered with namespaced names: `mcp__<server>__<tool>`
4. Resources and prompts are cached for on-demand access via agent tools
5. The agent sees MCP tools alongside built-in tools and can call them directly
6. No activation or confirmation needed — MCP tools are immediately available

## Tool namespacing

All MCP tools are prefixed with the server name to prevent collisions.

**Hyphens in server names are normalized to underscores in the namespaced tool identifier.** For a server configured as `oblique-strategies`, the agent sees:

```
mcp__oblique_strategies__get_strategy
mcp__oblique_strategies__search_strategies
mcp__oblique_strategies__list_editions
```

### Why normalize?

Gemini normalizes hyphens to underscores in tool identifiers at the function-call serialization layer — a tool registered as `mcp__oblique-strategies__get_strategy` gets emitted as `mcp__oblique_strategies__get_strategy` and fails to route. The model literally cannot produce a hyphen inside a tool identifier, even when it knows the correct name. By advertising underscored names up-front, every provider round-trips the identifier reliably.

This is a DecafClaw-layer convention — the actual MCP SDK call preserves the original server name (including hyphens), so protocol interop is unaffected. Only the identifier the LLM sees changes.

### Consequences

- **Config server names can use hyphens or underscores** — e.g., `my-api-server` or `my_api_server`. Both produce the same advertised tool names (`mcp__my_api_server__...`). Collisions would be rare but possible; avoid configuring two servers whose names differ only in separator.
- **User-invokable prompt commands still use the raw server name** — if you named a server `oblique-strategies` you invoke prompts as `!mcp__oblique-strategies__promptname` (humans can type hyphens fine). This asymmetry is small and targeted; we may unify later.
- **The agent calls tools by the normalized name.** The prefix is stripped and the *original* server name is used to route to the correct MCP session.

## Resources

MCP servers can expose resources — data the agent can read on demand (files, database records, API responses, etc.).

- **`mcp_list_resources`** — Lists all resources and resource templates from connected servers
- **`mcp_read_resource(server, uri)`** — Reads a resource by URI, returning text content or binary attachments

Both tools are deferred (loaded via `tool_search`). Resources are discovered automatically on connection for servers that advertise resource capabilities.

Resource templates show URI patterns (e.g., `file:///{path}`) — the agent constructs concrete URIs from these to read specific resources.

## Prompts

MCP servers can provide prompt templates — pre-built interactions with arguments.

### As agent tools (deferred)

- **`mcp_list_prompts`** — Lists all prompts from connected servers with their arguments
- **`mcp_get_prompt(server, name, arguments)`** — Gets a prompt's messages from the server

### As user-invokable commands

MCP prompts are automatically available as commands:

```
!mcp__server__promptname arg1 "multi word arg2"
```

Arguments are positional, mapping to the prompt's declared arguments in order. Multi-word arguments must be quoted. Missing required arguments produce a helpful error with usage information.

The returned prompt content is injected as a user message for the agent to respond to.

Use `!help` to see available MCP prompt commands.

## Management

The `mcp_status` tool provides visibility and control:

- **Status**: shows all servers, their connection state, tool/resource/prompt counts
- **Restart**: reconnects a specific server or reloads all config

The agent can use this tool, or you can ask it: "show me the MCP server status" or "restart the MCP servers."

Restart also re-reads `mcp_servers.json`, so you can add/remove/modify servers without restarting DecafClaw.

## Auto-restart

If a stdio server crashes, DecafClaw automatically attempts to reconnect on the next tool call:

- Exponential backoff: 1s, 2s, 4s, 8s between attempts
- Maximum 3 retries, then gives up
- Retry counter resets on successful reconnection
- Use `mcp_status(action="restart")` to manually trigger reconnection

## List changed notifications

When an MCP server sends `notifications/tools/list_changed`, `notifications/resources/list_changed`, or `notifications/prompts/list_changed`, DecafClaw automatically re-discovers the corresponding primitives. Changes take effect on the next conversation turn.

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
