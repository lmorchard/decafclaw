# MCP Resources, Prompts & Notifications

## Session Goals

Address two GitHub issues in one session:

- **#38**: MCP resources and prompts support
- **#34**: `notifications/tools/list_changed` handling

## Overview

Extend the MCP client to support the full MCP primitive set (tools, resources, prompts) and react to server-side `list_changed` notifications for all three.

## Issue #34: List Changed Notifications

### Behavior

When an MCP server sends `notifications/tools/list_changed`, `notifications/resources/list_changed`, or `notifications/prompts/list_changed`, the client re-discovers the corresponding primitives by calling `list_tools()`, `list_resources()` / `list_resource_templates()`, or `list_prompts()` on that server's session.

### Implementation

- Pass a `message_handler` callback to `ClientSession` during connection.
- The callback inspects incoming `ServerNotification` instances:
  - `ToolListChangedNotification` → re-call `list_tools()`, update `MCPServerState.tools` and `.tool_definitions`
  - `ResourceListChangedNotification` → re-call `list_resources()` + `list_resource_templates()`, update state
  - `PromptListChangedNotification` → re-call `list_prompts()`, update state
- **Capability-aware**: Only re-discover primitives the server advertises support for. Store server capabilities in `MCPServerState` at connection time.
- Changes take effect on the **next conversation turn** — no mid-turn disruption.
- **Concurrency**: Notification-triggered re-discovery updates the cached lists atomically (replace the entire list). In-flight tool calls that already resolved their callable are unaffected. No locking needed — the list swap is a single reference assignment.
- Errors during re-discovery are logged but don't crash the server connection.

## Issue #38: Resources Support

### Discovery

- On initial connection, **check server capabilities first**. Only call `list_resources()` and `list_resource_templates()` if the server's `capabilities.resources` is present.
- Cache results in `MCPServerState.resources` and `MCPServerState.resource_templates`.
- Re-discover on `ResourceListChangedNotification`.
- Servers that don't advertise resource capabilities get empty lists — no errors.

### Agent Tools (deferred)

Two new tools, deferred behind `tool_search`:

- **`mcp_list_resources`** — Returns the cached list of resources and resource templates across all connected servers. Shows URI, name, description, and MIME type for each. Resource templates are listed separately with their URI template pattern, marked as informational (agent must construct concrete URIs from templates to read them).
- **`mcp_read_resource(server, uri)`** — Reads a resource by URI from a specific server. Only supports concrete URIs, not URI templates. Returns content as `ToolResult` — text as `.text`, binary (images, etc.) as `.media` attachments. Follows the same pattern as `_convert_mcp_response`.

### Response Conversion

Resource read results contain `TextResourceContents` and/or `BlobResourceContents`. Convert to `ToolResult`:
- Text content → `ToolResult.text`
- Blob content (base64) → decode and attach as `ToolResult.media` with appropriate MIME type

## Issue #38: Prompts Support

### Discovery

- On initial connection, **check server capabilities first**. Only call `list_prompts()` if the server's `capabilities.prompts` is present.
- Cache results in `MCPServerState.prompts`.
- Re-discover on `PromptListChangedNotification`.
- Servers that don't advertise prompt capabilities get empty lists — no errors.

### Agent Tools (deferred)

Two new tools, deferred behind `tool_search`:

- **`mcp_list_prompts`** — Returns the cached list of prompts across all connected servers. Shows name, description, and arguments (with required/optional and descriptions).
- **`mcp_get_prompt(server, name, arguments)`** — Calls `get_prompt()` on the server and returns the resulting messages as text content in a `ToolResult`.

### User-Invokable Commands

MCP prompts are auto-registered as user-invokable commands:

- **Command name**: `mcp__<servername>__<promptname>` (matching tool namespace convention)
- **Argument passing**: Positional, space-separated. `$0`, `$1`, etc. map to the prompt's declared arguments in order. Multi-word arguments must be quoted.
- **Missing required arguments**: Return a helpful error listing expected arguments with their descriptions.
- **Result injection**: The returned prompt messages are injected as a user message with a note: "The user invoked MCP prompt `X` which returned:" followed by the content. The agent then responds normally.
- **`!help`** lists MCP prompt commands alongside other commands.
- **Dynamic registration**: Prompt commands are derived from the live registry state, not statically registered at startup. If a server reconnects or sends `PromptListChangedNotification`, the available commands update automatically. The command system queries the MCP registry at invocation time.

### Prompt Message Handling

`get_prompt()` returns a list of `PromptMessage` objects with `role` and `content`. Convert to a single text block:
- Prefix each message with its role for transparency
- Concatenate all messages with newlines

## Data Model Changes

### MCPServerState additions

```python
@dataclass
class MCPServerState:
    # ... existing fields ...
    capabilities: ServerCapabilities | None = None          # from initialize() result
    resources: list = field(default_factory=list)           # from list_resources()
    resource_templates: list = field(default_factory=list)  # from list_resource_templates()
    prompts: list = field(default_factory=list)             # from list_prompts()
```

### MCPRegistry additions

- `get_resources() -> list` — All resources from connected servers (with server name attached)
- `get_resource_templates() -> list` — All resource templates from connected servers
- `get_prompts() -> list` — All prompts from connected servers (with server name attached)
- `refresh_tools(server_name)` — Re-discover tools for a server
- `refresh_resources(server_name)` — Re-discover resources for a server
- `refresh_prompts(server_name)` — Re-discover prompts for a server

## Tool Definitions

All new tools are **deferred** (not always-loaded).

## Status Tool Update

`mcp_status` should include resource and prompt counts in its output alongside tool counts.

## Follow-up Issues to File

- **Tab-complete for web UI**: Autocomplete for `!` commands and `@` resources (workspace files + MCP resources)

## Error Handling

- All notification handlers are fail-open: log errors, don't crash.
- Resource read errors return `ToolResult(text="[error: ...]")`.
- Prompt get errors return `ToolResult(text="[error: ...]")`.
- Missing required prompt arguments return a descriptive error listing expected args.
