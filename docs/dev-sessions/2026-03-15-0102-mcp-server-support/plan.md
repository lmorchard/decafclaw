# MCP Server Support — Implementation Plan

## Overview

7 phases, each ending with lint + test + commit. Phases build sequentially.

---

## Phase 1: Config Parsing and Environment Variable Expansion

**Goal:** Read `mcp_servers.json`, validate server names, expand `${VAR}` references. No connections yet — just parsing.

**Prompt:**

Create `src/decafclaw/mcp_client.py` with config parsing and env var expansion.

Requirements:
1. Define a `MCPServerConfig` dataclass with fields: `name` (str), `type` (str — "stdio" or "http"), `command` (str, for stdio), `args` (list[str], for stdio), `env` (dict[str, str], for stdio), `url` (str, for http), `headers` (dict[str, str], for http), `timeout` (int, ms, default 30000).
2. Write `_expand_env(value: str) -> str` that expands `${VAR}` and `${VAR:-default}` from `os.environ`. Unexpanded vars with no default raise a warning and expand to empty string.
3. Write `_expand_config(obj)` that recursively expands env vars in strings, lists, and dicts.
4. Write `_validate_server_name(name: str) -> bool` — must be lowercase alphanumeric + hyphens, non-empty.
5. Write `load_mcp_config(config) -> list[MCPServerConfig]` that:
   - Reads `config.agent_path / "mcp_servers.json"`
   - Returns empty list if file missing (not an error)
   - Parses `mcpServers` key
   - Validates each server name (skip invalid with warning)
   - Expands env vars in all string values
   - Returns list of `MCPServerConfig`
6. Create `tests/test_mcp.py` with tests:
   - Parses valid config with stdio and http servers
   - Returns empty list for missing file
   - Validates server names (accepts `my-server`, rejects `MY SERVER`, `foo__bar`)
   - Expands `${VAR}` from env
   - Expands `${VAR:-default}` with fallback
   - Unexpanded var with no default becomes empty string
   - Skips servers with invalid names

Lint and test after.

---

## Phase 2: MCP Registry and Tool Namespacing

**Goal:** Build the registry that holds MCP server connections and their tools. Define the namespacing and conversion logic. No actual connections yet — mock-friendly.

**Prompt:**

Add the MCP registry and tool conversion to `src/decafclaw/mcp_client.py`.

Requirements:
1. Define `MCPServerState` — tracks a single server's runtime state:
   - `config: MCPServerConfig`
   - `status: str` — "connected", "disconnected", "failed"
   - `session: ClientSession | None`
   - `tools: dict[str, Any]` — namespaced_name → MCP tool definition
   - `tool_definitions: list[dict]` — OpenAI-style definitions
   - `retry_count: int` — for auto-restart backoff
   - `_transport_context` and `_session_context` — for managing SDK async context managers

2. Define `MCPRegistry`:
   - `servers: dict[str, MCPServerState]` — name → state
   - `get_tools() -> dict[str, Callable]` — returns dict of namespaced tool name → async callable wrapper
   - `get_tool_definitions() -> list[dict]` — returns all MCP tool definitions in OpenAI format
   - These are what the agent loop and `execute_tool` merge at call time

3. Write `_namespace_tool(server_name: str, tool_name: str) -> str`:
   - Returns `mcp__{server_name}__{tool_name}`

4. Write `_parse_namespace(namespaced: str) -> tuple[str, str] | None`:
   - Returns `(server_name, tool_name)` or None if not an MCP tool name

5. Write `_convert_tool_definition(server_name: str, mcp_tool) -> dict`:
   - Takes an MCP tool object (from `tools/list`) and converts to OpenAI-style
   - Maps `inputSchema` → `parameters`, prefixes name with namespace

6. Write `_convert_mcp_response(result) -> str`:
   - Concatenates text content items
   - Placeholders for image/audio
   - Handles `isError`

7. Tests:
   - `_namespace_tool` produces correct format
   - `_parse_namespace` round-trips correctly, returns None for non-MCP names
   - `_convert_tool_definition` maps fields correctly
   - `_convert_mcp_response` handles text, mixed content, errors

Lint and test after.

---

## Phase 3: Server Connection — stdio and HTTP

**Goal:** Actually connect to MCP servers using the SDK. Discover tools. Populate the registry.

**Prompt:**

Add connection logic to `MCPRegistry` in `src/decafclaw/mcp_client.py`. Add `mcp` as a dependency.

Requirements:
1. Add `mcp` to `pyproject.toml` dependencies.

2. Add `async def connect_server(self, server_config: MCPServerConfig)` to `MCPRegistry`:
   - For **stdio**: use `mcp.client.stdio.stdio_client` with `StdioServerParameters(command, args, env)`. Enter the transport context manager, then create and enter `ClientSession`, call `session.initialize()`, then `session.list_tools()`.
   - For **http**: use `mcp.client.streamable_http.streamable_http_client` with the URL (and headers if present). Same session pattern.
   - Convert discovered tools via `_convert_tool_definition`
   - Build the async callable wrappers for each tool (wrapper calls `session.call_tool(name, arguments)` and runs `_convert_mcp_response`)
   - Store everything on `MCPServerState`
   - Set status to "connected" on success, "failed" on error
   - **Critical**: the transport and session context managers must stay open. Use `contextlib.AsyncExitStack` on each `MCPServerState` to manage their lifetimes.
   - Log errors but don't raise — caller decides what to do

3. Add `async def connect_all(self, configs: list[MCPServerConfig])`:
   - Calls `connect_server` for each config
   - Logs summary: N connected, M failed

4. Add `async def disconnect_server(self, name: str)`:
   - Closes the session and transport via the exit stack
   - Sets status to "disconnected"

5. Add `async def disconnect_all(self)`:
   - Calls `disconnect_server` for each, with a 5s timeout on the whole operation

6. **Testability**: extract the actual SDK calls into thin internal methods (`_connect_stdio`, `_connect_http`) on `MCPRegistry` that can be overridden or patched in tests. This avoids mocking the SDK's nested context manager pattern directly.

7. Tests:
   - Patch `_connect_stdio`/`_connect_http` to return a mock session with canned `list_tools()` response
   - Test that `connect_server` populates state correctly on success
   - Test that failed connections set status to "failed" and don't raise
   - Test that `disconnect_server` cleans up state
   - Test that `disconnect_all` with timeout doesn't hang
   - Optionally: integration test with `oblique-strategies-mcp` if available (mark as `pytest.mark.skipif` if binary not found)

Lint and test after.

---

## Phase 4: Wire MCP Registry into Agent Loop and execute_tool

**Goal:** The agent loop includes MCP tools in LLM calls, and `execute_tool` routes MCP tool calls to the right server.

**Prompt:**

Wire the MCP registry into startup, the agent loop, and tool execution.

Requirements:
1. In `src/decafclaw/mcp_client.py`, add module-level global and accessors:
   - `_registry: MCPRegistry | None = None`
   - `def get_registry() -> MCPRegistry | None` — returns the global registry
   - `async def init_mcp(config)` — loads config, creates registry, calls `connect_all`. Called once at async startup.
   - `async def shutdown_mcp()` — calls `disconnect_all` on the global registry. Called at shutdown.

2. In `src/decafclaw/__init__.py` (`main()`):
   - No MCP setup here — `init_mcp` is async and must run inside `asyncio.run`

3. In `src/decafclaw/agent.py`:
   - Modify `run_agent_turn`: when building `all_tools`, merge MCP tool definitions:
     ```python
     from .mcp_client import get_registry
     mcp_registry = get_registry()
     if mcp_registry:
         all_tools = all_tools + mcp_registry.get_tool_definitions()
     ```
   - Modify `run_interactive`:
     - At startup (before the input loop): `await init_mcp(config)`
     - In the `finally` block: `await shutdown_mcp()`
     - Print MCP server status at startup

4. In `src/decafclaw/tools/__init__.py`, modify `execute_tool`:
   - Before the "unknown tool" fallthrough, check if the name starts with `mcp__`:
     ```python
     if name.startswith("mcp__"):
         from .mcp_client import get_registry
         registry = get_registry()
         if registry:
             mcp_tools = registry.get_tools()
             fn = mcp_tools.get(name)
             if fn:
                 return await fn(arguments)
         return f"[error: MCP tool '{name}' not available]"
     ```

5. In `MattermostClient.run`:
   - After `await self.connect()`, call `await init_mcp(app_ctx.config)`
   - In the `finally` block (before `self.close()`), call `await shutdown_mcp()`

6. Tests:
   - With a mock MCP registry on ctx, verify `execute_tool` routes `mcp__foo__bar` to the registry
   - Verify non-MCP tools still work when registry is present
   - Verify MCP tools not found returns error message

Lint and test after.

---

## Phase 5: Auto-Restart with Backoff

**Goal:** Stdio servers that crash get automatically reconnected on next tool call.

**Prompt:**

Add auto-restart logic to `MCPRegistry` in `src/decafclaw/mcp_client.py`.

Requirements:
1. Add `async def _maybe_reconnect(self, server_name: str) -> bool` to `MCPRegistry`:
   - Check server status — if "connected", return True
   - If "failed" and `retry_count >= 3`, return False (give up)
   - Calculate backoff: `min(2 ** retry_count, 8)` seconds
   - If enough time has passed since last attempt, try `connect_server` again
   - Increment `retry_count` on failure, reset to 0 on success
   - Add `last_retry_time: float` to `MCPServerState` for tracking

2. Update the tool call wrapper (created in Phase 3) to call `_maybe_reconnect` before calling `session.call_tool`. If reconnection fails, return error.

3. Add timeout to tool calls — wrap `session.call_tool` in `asyncio.wait_for` using the server's configured timeout (default 30s).

4. Tests:
   - Server with status "connected" — no reconnect attempted
   - Server with status "failed", retry_count < 3 — reconnect attempted
   - Server with status "failed", retry_count >= 3 — gives up
   - Retry count resets on successful reconnection
   - Tool call timeout returns error message

Lint and test after.

---

## Phase 6: mcp_status Management Tool

**Goal:** Built-in tool for visibility and control over MCP servers.

**Prompt:**

Add `mcp_status` tool to `src/decafclaw/tools/mcp_tools.py`.

Requirements:
1. Create `src/decafclaw/tools/mcp_tools.py` with:
   - `async def tool_mcp_status(ctx, action: str = "status", server: str = "") -> str`
   - **status action**: get registry via `get_registry()`, iterate `.servers`, format each as:
     ```
     - server-name (stdio, connected): tool1, tool2, tool3
     - other-server (http, failed): (no tools)
     ```
   - **restart action**:
     - Re-read `mcp_servers.json` via `load_mcp_config`
     - If `server` specified, disconnect and reconnect just that one
     - If `server` empty, disconnect all, reload config, reconnect all
     - Re-discover tools after reconnection
     - Return summary of what happened
   - If `get_registry()` returns None, return "No MCP servers configured."

2. `MCP_TOOLS` dict and `MCP_TOOL_DEFINITIONS` list.

3. Register in `tools/__init__.py` — import and merge into `TOOLS` and `TOOL_DEFINITIONS`.

4. Tool definition:
   ```json
   {
     "name": "mcp_status",
     "description": "Show status of MCP servers or restart them. Use 'status' to see connected servers and their tools. Use 'restart' to reconnect a server or reload config.",
     "parameters": {
       "action": { "type": "string", "enum": ["status", "restart"], "default": "status" },
       "server": { "type": "string", "description": "Server name (for restart). Omit to restart all." }
     }
   }
   ```

5. Tests:
   - Status with mock registry returns formatted server list
   - Status with no registry returns "No MCP servers configured."
   - Restart triggers disconnect/reconnect on registry

Lint and test after.

---

## Phase 7: Integration, Interactive Mode Polish, and Documentation

**Goal:** End-to-end verification, update interactive mode display, update docs.

**Prompt:**

Final integration and documentation pass.

Requirements:
1. **Interactive mode polish** (`agent.py`):
   - At startup, after connecting MCP servers, print MCP server status alongside tools and skills
   - Example: `MCP: oblique-strategies (3 tools), remote-api (5 tools)`

2. **Manual verification checklist**:
   - Configure `oblique-strategies-mcp` in `mcp_servers.json`
   - Start agent → server connects, tools discovered, shown in tool list
   - Ask agent to "draw an oblique strategy card" → agent calls `mcp__oblique-strategies__get_strategy`
   - `mcp_status` → shows connected servers and tools
   - `mcp_status(action="restart")` → reconnects, re-discovers
   - Kill the MCP server process → next tool call shows error, auto-restart kicks in

3. **Update documentation:**
   - `CLAUDE.md`: add MCP to key files, update conventions
   - `README.md`: add MCP section (config format, setup, how it works)
   - `docs/BACKLOG-DEVINFRA.md`: remove MCP server support item
   - `docs/BACKLOG.md`: add to done (or just remove per Les's preference)

4. **Graceful shutdown verification**: stop the agent with Ctrl-C, verify MCP subprocesses are cleaned up (no orphans).

5. Run full test suite, lint. Commit.

---

## Summary of Phases

| Phase | What | Key Files | Tests |
|-------|------|-----------|-------|
| 1 | Config parsing + env expansion | `mcp_client.py` | ~7 tests |
| 2 | Registry + namespacing + conversion | `mcp_client.py` | ~5 tests |
| 3 | Server connections (stdio + HTTP) | `mcp_client.py`, `pyproject.toml` | ~4 tests |
| 4 | Wire into agent loop + execute_tool | `__init__.py`, `agent.py`, `tools/__init__.py` | ~3 tests |
| 5 | Auto-restart with backoff | `mcp_client.py` | ~5 tests |
| 6 | mcp_status management tool | `tools/mcp_tools.py` | ~3 tests |
| 7 | Integration + docs | docs, agent.py | manual |

## Implementation Notes

- **Module-level global registry**: `_registry` in `mcp_client.py`, accessed via `get_registry()`. MCP is truly global (one per process), like how tabstack's `_client` works. Avoids the config/ctx propagation problem entirely.
- The `mcp` SDK uses nested async context managers. Use `contextlib.AsyncExitStack` per server to manage transport + session lifetimes without deeply nested `async with` blocks.
- MCP tools are global (not per-conversation like skills). The registry is a module-level global, shared across all conversations.
- The MCP registry is separate from `TOOLS`/`TOOL_DEFINITIONS` — those stay immutable. The agent loop and `execute_tool` merge at call time.
- For the tool call wrapper, the function signature differs from built-in tools (no `ctx` param, just `arguments` dict). Handle this in the `execute_tool` routing for `mcp__` prefixed names.
- **SDK dependency size**: the `mcp` package pulls in pydantic, httpx-sse, etc. Verify this doesn't bloat excessively in Phase 3. If it does, consider making it an optional dependency.
- **SDK mocking**: wrap the SDK connection logic in a thin internal method that can be overridden/mocked in tests, rather than trying to mock the nested context managers directly.
- **HTTP headers**: verify that `streamable_http_client` accepts custom headers. If not, may need to construct a custom httpx client and pass it through.
- **Tool name `__` in MCP tools**: server names are validated (no `__`), but MCP tool names from the server could theoretically contain `__`. Document as a known limitation — `_parse_namespace` splits on the first two `__` delimiters.
