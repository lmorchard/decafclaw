# Spec: Web UI: @ references for workspace files and MCP resources + autocomplete

**Goal:** Let a user reference workspace files and MCP resources from the composer, and autocomplete those references.

## Approved Design Decisions

### 1. Mention Syntax
- **Workspace files:** Bare paths prefixed with `@`, e.g., `@src/agent.py` or `@tests/test_agent.py` (any path under the workspace).
- **MCP resources:** Segment-prefixed bare names, e.g., `@mcp/server_name/resource_name`.
- **Vault pages:** Keep the existing bracket-wrapped syntax `@[[PageName]]`. When selected from the `@` autocomplete dropdown list, it should insert `@[[PageName]]`.

### 2. Autocomplete Server API
- Build a unified backend endpoint `/api/autocomplete?q=...` that queries:
  - **Workspace files:** Recursive prefix search on filenames/paths under the workspace tree (ignoring hidden or ignored directories like `.git`, `.venv`, `.ocx`, `node_modules`).
  - **MCP resources:** Matches from active MCP servers' resources.
  - **Vault pages:** Matches from the vault.
- Returns segment/reference type (`file`, `mcp`, `vault`) and labels for UI rendering.

### 3. Context Injection & Truncation Strategy
- In `ContextComposer.compose()`, parse bare mentions from the user's message.
- Retrieve the full contents of the referenced workspace file or MCP resource and inline them directly into the conversation context as a system/user-reference message.
- **Oversize Strategy:** If a file/resource is larger than **8KB (~200 lines)**, truncate the content and append a clear truncation notice: `[Truncated: only first 8KB of src/agent.py inlined]`.
