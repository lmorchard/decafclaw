---
name: mcp
description: Admin tools for inspecting and restarting connected MCP servers — status, resources, prompts. Does NOT expose tools provided by MCP servers; those appear as mcp__server__tool and are fetched via tool_search.
auto-approve: true
---

# MCP admin

**This skill is NOT for calling tools that MCP servers provide.** Tools exposed by MCP servers are named `mcp__server__tool` and live in the regular tool catalog. If a tool like `mcp__github__create_issue` isn't immediately available, use `tool_search` to fetch it from the deferred catalog — do NOT activate this skill.

This skill is only for *administering* the MCP layer itself — inspecting server status, listing or reading resources, listing or fetching prompts, or restarting servers.

## Tools

- `mcp_status(action?)` — show connected servers and their tools/resources/prompts, or `action="restart"` to reconnect a server.
- `mcp_list_resources()` — list resources (static) and resource templates (parameterized) from all connected servers.
- `mcp_read_resource(server, uri)` — fetch a specific resource by URI.
- `mcp_list_prompts()` — list prompts from connected servers, including argument schemas.
- `mcp_get_prompt(server, name, arguments?)` — retrieve a specific prompt, optionally with JSON arguments.

## When to use

- A user asks about MCP server status, missing servers, or server errors.
- You need to fetch resource content from an MCP server.
- You need a prompt templated from an MCP server.
- A restart is needed after a config change.

If you're just using a tool exposed by an MCP server (e.g. `mcp__github__create_issue`), you do **not** need this skill — those tools are regular tools in the catalog.
