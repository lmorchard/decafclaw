# Plan: MCP Resources, Prompts & Notifications

## Phase 1: Data Model & Capability Storage

Extend `MCPServerState` and connection flow to store capabilities and new primitive caches.

### Step 1.1: Extend MCPServerState

**Prompt:**
In `src/decafclaw/mcp_client.py`, add new fields to the `MCPServerState` dataclass:
- `capabilities: Any = None` — stores the `ServerCapabilities` from `session.get_server_capabilities()` after `initialize()`
- `resources: list = field(default_factory=list)` — cached `list_resources()` results
- `resource_templates: list = field(default_factory=list)` — cached `list_resource_templates()` results
- `prompts: list = field(default_factory=list)` — cached `list_prompts()` results

In `connect_server()`, after `await session.initialize()` (which happens inside `_connect_stdio`/`_connect_http`), store the capabilities:
```python
state.capabilities = session.get_server_capabilities()
```

Note: `initialize()` is called inside `_connect_stdio`/`_connect_http`, so `get_server_capabilities()` must be called after those return. Refactor: have `_connect_stdio`/`_connect_http` return `(session, capabilities)`, or just call `session.get_server_capabilities()` after the session is assigned.

After storing capabilities, discover resources and prompts if the server supports them:
- If `state.capabilities.resources` is not None: call `session.list_resources()` and `session.list_resource_templates()`, store results
- If `state.capabilities.prompts` is not None: call `session.list_prompts()`, store results

Log counts for each: "MCP server 'name' connected: N tool(s), N resource(s), N prompt(s)"

Add tests:
- Test that a connected server with resource capabilities has resources populated
- Test that a connected server without resource capabilities has empty resources
- Same for prompts

### Step 1.2: Add Registry Accessor Methods

**Prompt:**
Add methods to `MCPRegistry`:

- `get_resources() -> list[tuple[str, Any]]` — returns `(server_name, resource)` tuples from all connected servers
- `get_resource_templates() -> list[tuple[str, Any]]` — same for resource templates
- `get_prompts() -> list[tuple[str, Any]]` — same for prompts

These mirror the pattern of `get_tools()` and `get_tool_definitions()` but return the cached lists with server names attached.

Add tests for each — connected servers return their items, failed/disconnected servers are excluded.

---

## Phase 2: Notification Handling

Wire up `message_handler` on `ClientSession` to react to list-changed notifications.

### Step 2.1: Add Refresh Methods

**Prompt:**
Add three methods to `MCPRegistry`:

- `async refresh_tools(server_name)` — re-call `session.list_tools()`, rebuild `state.tools` and `state.tool_definitions` (extract the existing tool discovery logic from `connect_server` into a reusable method)
- `async refresh_resources(server_name)` — re-call `session.list_resources()` + `list_resource_templates()`, replace `state.resources` and `state.resource_templates`
- `async refresh_prompts(server_name)` — re-call `session.list_prompts()`, replace `state.prompts`

Each method should:
- Check that the server exists and is connected
- Check capabilities before calling (e.g., don't call `list_resources()` if no resource capability)
- Log the refresh and new counts
- Catch and log exceptions without crashing

Add tests for each — mock a connected server, verify refresh updates the cached state.

### Step 2.2: Wire Up message_handler Callback

**Prompt:**
In `MCPRegistry`, add a method `_make_notification_handler(server_name)` that returns an async callback suitable for `ClientSession(message_handler=...)`.

The callback should:
1. Check if the incoming message is a `ServerNotification` (not a request responder or exception)
2. Match on notification type:
   - `ToolListChangedNotification` → `await self.refresh_tools(server_name)`
   - `ResourceListChangedNotification` → `await self.refresh_resources(server_name)`
   - `PromptListChangedNotification` → `await self.refresh_prompts(server_name)`
3. Ignore all other notification types (log at debug level)
4. Wrap everything in try/except — errors logged, never crash

In `_connect_stdio` and `_connect_http`, pass the handler when creating `ClientSession`:
```python
session = await exit_stack.enter_async_context(
    ClientSession(read_stream, write_stream, message_handler=message_handler)
)
```

This means `_connect_stdio` and `_connect_http` need to accept the `message_handler` parameter. Update their signatures and the call sites in `connect_server`.

Add tests:
- Simulate a `ToolListChangedNotification` → verify `refresh_tools` is called
- Simulate a `ResourceListChangedNotification` → verify `refresh_resources` is called
- Simulate a `PromptListChangedNotification` → verify `refresh_prompts` is called
- Verify unknown notification types don't crash

---

## Phase 3: Resource Tools

### Step 3.1: Resource Response Conversion

**Prompt:**
In `src/decafclaw/mcp_client.py`, add a function `_convert_resource_response(result)` that converts a `ReadResourceResult` to a `ToolResult`.

The result contains a list of `contents` which are either `TextResourceContents` or `BlobResourceContents`:
- `TextResourceContents`: has `.text` and `.uri` — append text to parts
- `BlobResourceContents`: has `.blob` (base64 string), `.uri`, `.mimeType` — decode and attach as media

Follow the same pattern as `_convert_mcp_response` for media handling.

Add tests with mock text and blob resource contents.

### Step 3.2: Resource Tools Implementation

**Prompt:**
Create or extend `src/decafclaw/tools/mcp_tools.py` with two new tools:

**`mcp_list_resources(ctx)`** — returns a formatted string listing all resources and resource templates from the MCP registry. For each resource: server name, URI, name, description, MIME type. For templates: mark as "(template)" with the URI pattern.

**`mcp_read_resource(ctx, server: str, uri: str)`** — reads a resource from a specific server:
1. Find the server in the registry, verify it's connected
2. Call `session.read_resource(AnyUrl(uri))` with a timeout (use server's timeout setting)
3. Convert result via `_convert_resource_response`
4. Return `ToolResult`

Add tool definitions for both. Both are **deferred** — add them to the deferred pool, not `MCP_TOOLS`/`MCP_TOOL_DEFINITIONS`.

Wire into `tools/__init__.py` — add to the deferred definitions list (check how `tool_registry.py` builds the deferred pool).

Add tests for both tools.

---

## Phase 4: Prompt Tools

### Step 4.1: Prompt Response Conversion

**Prompt:**
In `src/decafclaw/mcp_client.py`, add a function `_convert_prompt_response(result)` that converts a `GetPromptResult` to a string.

The result contains `.messages` — a list of `PromptMessage` objects with `.role` and `.content`. Content can be `TextContent`, `ImageContent`, or `EmbeddedResource`.

Convert to a text block:
- For each message, prefix with role: `[role]: content_text`
- For `TextContent`: use `.text`
- For `ImageContent`: note as "(image attached)"
- For `EmbeddedResource`: note as "(embedded resource)"
- Join with newlines

Add tests with mock prompt responses.

### Step 4.2: Prompt Tools Implementation

**Prompt:**
Add two more tools to `src/decafclaw/tools/mcp_tools.py`:

**`mcp_list_prompts(ctx)`** — returns a formatted string listing all prompts from the MCP registry. For each prompt: server name, name, description, and arguments (name, description, required/optional).

**`mcp_get_prompt(ctx, server: str, name: str, arguments: str = "{}")`** — gets a prompt from a specific server:
1. Find the server in the registry, verify it's connected
2. Parse `arguments` as JSON dict
3. Call `session.get_prompt(name, arguments)` with a timeout
4. Convert result via `_convert_prompt_response`
5. Return as `ToolResult`

Add deferred tool definitions for both. Wire into `tools/__init__.py`.

Add tests for both tools.

---

## Phase 5: User-Invokable Prompt Commands

### Step 5.1: Dynamic MCP Prompt Command Registration

**Prompt:**
Extend the command system to dynamically include MCP prompts as commands.

In `src/decafclaw/commands.py`, modify `dispatch_command`:
- After the normal `find_command` lookup returns None (before returning "unknown"), check MCP prompts
- Import `get_registry` from `mcp_client`
- If the command name matches an MCP prompt pattern (`mcp__<server>__<prompt>`), parse out server name and prompt name
- Validate the server exists and is connected, and the prompt exists in its cached prompts
- If valid, execute the prompt (see next step)

Modify `format_help`:
- After listing discovered skill commands, also list MCP prompt commands from the registry
- Format as `!mcp__server__prompt <arg1> [arg2]` — show required/optional args

### Step 5.2: MCP Prompt Command Execution

**Prompt:**
Add an `_execute_mcp_prompt_command` function in `src/decafclaw/commands.py`:

1. Parse the command name to extract server name and prompt name
2. Parse positional arguments from the argument string (respecting quoted multi-word args)
3. Look up the prompt's declared arguments from the cached prompt list
4. Map positional args to declared argument names in order
5. Check required arguments — if missing, return a helpful error listing expected args with descriptions
6. Call `session.get_prompt(name, mapped_args)` with timeout
7. Convert result via `_convert_prompt_response`
8. Return as a `CommandResult` with mode="inline" and text formatted as:
   ```
   The user invoked MCP prompt `mcp__server__prompt` which returned:

   [converted prompt content]
   ```

Wire this into `dispatch_command` so it's called when an MCP prompt command is detected.

Add tests:
- Successful prompt command execution
- Missing required arguments returns helpful error
- Unknown server/prompt returns appropriate error
- Argument parsing with quoted strings

---

## Phase 6: Status Update & Polish

### Step 6.1: Update mcp_status Output

**Prompt:**
Update `tool_mcp_status` in `src/decafclaw/tools/mcp_tools.py` to include resource and prompt counts in the status output.

Change from:
```
- **server** (stdio, connected): tool1, tool2
```
To:
```
- **server** (stdio, connected): 2 tools, 3 resources, 1 prompt
```

Update the existing `test_mcp_status_shows_servers` test to verify the new format.

### Step 6.2: Lint, Test, Documentation

**Prompt:**
- Run `make lint` and fix any issues
- Run `make test` and fix any failures
- Update `CLAUDE.md` key files if needed (mcp_tools.py already listed via tools/)
- Update `docs/` if there's an MCP-specific doc page
- File a GitHub issue for tab-complete: "Web UI: autocomplete for `!` commands and `@` resources (workspace files + MCP resources)"

---

## Review: Critical Gaps

1. **`_connect_stdio`/`_connect_http` signature change**: These are overridden in tests. Need to update test mocks to match new signatures accepting `message_handler`. ✅ Addressed in Step 2.2.

2. **Deferred tool wiring**: Need to verify how the deferred pool is built to make sure new tools land there correctly. Check `tool_registry.py` during Step 3.2.

3. **Argument parsing for quoted strings**: `shlex.split` handles this well but need to handle edge cases (unbalanced quotes). Use `shlex.split` with fallback. Addressed in Step 5.2.

4. **`initialize()` return value**: `_connect_stdio`/`_connect_http` call `session.initialize()` but don't return the result. We use `session.get_server_capabilities()` after the fact, which is fine since the SDK stores it internally. ✅ No issue.
