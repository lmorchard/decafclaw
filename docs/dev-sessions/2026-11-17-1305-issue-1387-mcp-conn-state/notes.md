# issue-1387-mcp-conn-state

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1387 ([10.8] Add MCP `ConnState` enum)
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1387-mcp-conn-state
- Branch: issue-1387-mcp-conn-state
- Base: origin/main at 0b42bbcc331c6fed7017c6b70879b5cd9e5b1250
- Status: implementation complete, gates green, ready to publish the PR (no merge authorization).

## Design

`ConnState(Enum)` in `mcp_client.py`: DISCONNECTED/CONNECTING/CONNECTED/FAILED with the
existing wire strings as values (plain Enum, matching `TurnKind`/`TaskMode`).
`MCPServerState.status` is a member. `.value` is used only at serialization boundaries
(`mcp_status` text, health JSON, health markdown). `is_ready` compares `status == CONNECTED`.
The dead `"ready"` branch is deleted from `is_ready` and from health's connected/failed
classification; the equally dead `"error"` alternative went with it.

## Files

- src: `mcp_client.py`, `commands.py`, `context_composer.py`, `skills/mcp/tools.py`, `tools/health.py`
- tests: `test_mcp.py`, `test_mcp_client.py`, `test_health.py`, `test_mentions_injection.py`, `test_runner.py`
- No docs change: `docs/mcp-servers.md` does not name internal status strings.

## Verification

- `make fmt` (no reformat needed), `make check` passed (ruff, pyright 0 errors, tsc, module graph).
- `make test`: 5648 passed, 2 skipped.
- Grep proves no remaining `"ready"`/`"error"` MCP status branch.
