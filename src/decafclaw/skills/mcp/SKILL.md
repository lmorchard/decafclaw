---
name: mcp
description: MCP (Model Context Protocol) admin tools — inspect connected servers, list resources and prompts, restart servers. Use when debugging MCP connectivity or fetching server-provided content.
auto-approve: true
---

# MCP admin

MCP servers expose their own tools (namespaced as `mcp__server__tool`) that show up in the regular tool catalog. This skill is only for *administering* the MCP layer itself — inspecting status, fetching resources and prompts, or restarting servers.

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
