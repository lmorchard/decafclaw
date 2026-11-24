# issue-1805-mcp-2026-07-28

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1805
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1805-mcp-2026-07-28
- Branch: issue-1805-mcp-2026-07-28
- Base: origin/main at 249612fc9f39b6cbb8a6e85d735dff111f6b8096
- Status: implementation + local gates complete; PR not yet opened.

## Decisions

- Upgrade `mcp` from 1.26.0 to 2.3.0. The v2 SDK implements the 2026-07-28
  envelope/headers and exposes `ClientSession.discover()` / `server_capabilities`.
  Reimplementing the transport was unnecessary.
- `httpx-sse` became a direct dependency. mcp 1.x pulled it transitively, but
  `llm/providers/openai_responses.py`, `openai_compat.py`, and `vertex.py` import
  it directly. Without the explicit dependency the whole test suite failed at
  import.
- `auto` policy is legacy-first for HTTP: `initialize()`, then step up to
  `discover()` only on a `-32022` whose `supported` list names a modern
  revision. This avoids probing `server/discover` against the legacy fleet.
  stdio `auto` stays legacy. Explicit `2024-11-05` / `2026-07-28` pins are
  honored.
- The SDK wraps handshake failures in nested task-group `ExceptionGroup`s, so
  `_find_mcp_error` unwraps them before checking for `-32022`.
- SDK v2 models use snake_case attributes with camelCase JSON aliases. `_ga`
  now falls back to the snake_case attribute; `_convert_tool_definition`,
  media/resource conversion, and `skills/mcp/tools.py` therefore work with
  both v1-style mocks and v2 objects.
- Modern connections have no legacy `notifications/*/list_changed`; docs state
  that lists are discovered at connect time and `mcp_status(action="restart")`
  refreshes them.

## Evidence

- Baseline `make test` on the worktree before changes: 4 known
  `tests/test_host_metrics.py` failures (macOS 27 host environment), 6824 passed.
- `make check`: pass (ruff, pyright, generated types, JS checks).
- `make test`: 4 same host_metrics failures, 6832 passed, 3 skipped.
- New `tests/test_mcp_protocol.py` (6 cases) runs a fake Streamable HTTP server
  and asserts:
  - legacy `auto` sends `initialize`, never `server/discover`;
  - modern-only `auto` sends `initialize`, then `server/discover`, then
    `tools/list` / `tools/call`;
  - modern requests carry `MCP-Protocol-Version: 2026-07-28`, matching
    `Mcp-Method`, `Mcp-Name` on `tools/call`, and the `_meta` envelope;
  - explicit pins skip the unwanted handshake;
  - configured HTTP headers ride on every request.
- Live check against the issue's server:
  `MCPServerConfig(name="autofile", type="http", url="http://myriad-docker:7319/mcp")`
  connected with `auto`, discovered 11 tools, and
  `mcp__autofile__list_types` returned the collection and homelab-service
  types. Log also shows a non-fatal `resources/list: Method not found` warning
  from that server.

## Remaining

- Commit and open PR; no merge without Les's authorization.
