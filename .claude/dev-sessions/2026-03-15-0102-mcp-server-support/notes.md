# MCP Server Support — Session Notes & Retro

## What we built

MCP (Model Context Protocol) server support for DecafClaw:

- **Config parsing**: `mcp_servers.json` in Claude Code compatible format with `${VAR}` env expansion
- **Two transports**: stdio (subprocess) and HTTP (Streamable HTTP) via official `mcp` Python SDK
- **Tool namespacing**: `mcp__<server>__<tool>` matching Claude Code convention
- **Module-level global registry**: avoids the ctx/config propagation issue from skills session
- **Auto-restart**: exponential backoff (1s/2s/4s/8s), max 3 retries for crashed stdio servers
- **`mcp_status` tool**: status view + restart with config reload
- **Lifecycle management**: init on startup, graceful shutdown with 5s timeout, AsyncExitStack for SDK context managers
- **Documentation**: `docs/mcp-servers.md` with config, usage, and examples
- **Tested live** with oblique-strategies-mcp — connected, discovered 3 tools, called successfully

## What went well

- **Plan review caught the ctx propagation bug before coding.** Learned from the skills session — module-level global was the right call here. Saved debugging time.
- **Thin wrapper methods for testability.** `_connect_stdio`/`_connect_http` made mocking the SDK painless without complex context manager mocking.
- **Phase 1-2 pure logic first.** Config parsing and namespacing were easy to test with no SDK dependency, giving a solid foundation before the complex connection code.
- **SDK worked well.** Fully async, clean API. The nested context manager pattern was the only tricky part, and AsyncExitStack handled it cleanly.
- **Live test with oblique-strategies-mcp passed first try.** No debugging needed for the happy path.

## What could be better

- **SDK dependency is heavy.** `mcp` pulls in pydantic, starlette, uvicorn, and 30+ transitive packages. For a "minimal learning project" this is a lot. Could consider making it optional (`pip install decafclaw[mcp]`) in the future.
- **HTTP transport untested live.** We only verified stdio with oblique-strategies-mcp. HTTP/Streamable HTTP path is coded and has mocked tests but no live verification yet.
- **No integration test in the test suite.** The oblique-strategies test was manual. Could add a pytest integration test that spins up a simple MCP server subprocess, but it would need uvx/network access.

## Design decisions worth noting

- **Module-level global over config/ctx**: MCP registry is truly process-global, not per-conversation or per-request. Module global is the simplest correct answer.
- **Separate MCP routing in execute_tool**: `mcp__` prefix check routes to registry before the normal tool lookup. MCP tools have a different call signature (no `ctx` param) since they're just forwarding JSON to an external process.
- **Tool callers reference state.session, not a captured session**: This enables auto-reconnect — when a server reconnects, the new session is on the state object and existing callers pick it up.

## Lessons for CLAUDE.md / journal

- **Each major feature needs a docs/ page** — added this convention
- **Module-level globals are fine for truly global singletons** — don't force everything through ctx/config
- **Plan review is worth the time** — caught the ctx propagation issue, SDK mocking concern, and HTTP headers question before any code was written

## Stats

- 128 tests (37 new), 10 commits on mcp-server-support branch
- Also includes: skills session bug fixes, debug_context improvements, tabstack SKILL.md upgrade
