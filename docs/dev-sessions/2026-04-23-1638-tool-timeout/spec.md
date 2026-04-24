# Spec: Generic Tool Execution Timeout

## Problem

The generic tool dispatcher in `src/decafclaw/tools/__init__.py:execute_tool` has no per-call timeout. Any non-MCP tool that hangs (misbehaving HTTP call, buggy sync tool, wedged subprocess wrapper) stalls the agent turn indefinitely. MCP tools, `delegate_task`, and a couple of MCP-skill operations already wrap individual call sites in `asyncio.wait_for`; the broader tool surface does not.

This session addresses item (1) from the tool execution resilience umbrella (issue #7). Items (2)–(4) — cancel-scope isolation for anyio, per-tool circuit breaker, graceful degradation — are split out to #324 / #325 / #326 and are explicitly **out of scope** here.

## Goal

Add a single configurable wall-clock timeout to the non-MCP dispatcher, with per-tool overrides for tools that legitimately run long or manage their own timeouts.

## Design decisions

### Shape

Single global default with optional per-tool override. MCP tools are not touched — their existing per-server `wait_for` in `mcp_client.py` remains authoritative.

### Config

- New field on `AgentConfig` (in `src/decafclaw/config_types.py`):
  `tool_timeout_sec: int = 180`
- Resolvable via env var `AGENT_TOOL_TIMEOUT_SEC` and `data/{agent_id}/config.json` per the existing resolution chain.
- `make config` displays it.
- `0` or a negative value is treated as "disabled" — no wrapping, equivalent to every tool opting out.

### Per-tool override

Declared inline in each tool's entry in `TOOL_DEFINITIONS`, alongside `priority`:

```python
{
    "type": "function",
    "function": {...},
    "priority": "critical",
    "timeout": 3600,   # seconds; or None to opt out entirely
}
```

Semantics:

- Key absent → use `config.agent.tool_timeout_sec`.
- `timeout: <positive int>` → use this value instead of the default.
- `timeout: None` → no wrapper; tool manages its own timeout (or is trusted to return).
- `timeout: 0` or negative → same as `None` (disabled). Consistent with the global "disabled" semantics below.

The dispatcher resolves a tool's timeout by scanning the known definition sources in priority order: skill-provided definitions (per-turn dynamic tools via `get_tools(ctx)`, plus static skill defs registered at activation), the global `TOOL_DEFINITIONS`, and `SEARCH_TOOL_DEFINITIONS`. The plan step should identify the exact attributes on `ctx.tools` where skill defs live and thread them through a small `_resolve_tool_timeout(ctx, name, config) -> int | None` helper. If a tool name isn't found in any def source, fall back to the global default — unknown-tool errors are already caught earlier in `execute_tool`.

### Initial overrides

Audit completed during planning:

- `delegate_task`: `timeout: None` — already wraps its child agent in its own `asyncio.wait_for(timeout=config.agent.child_timeout_sec)` (300s default).
- `conversation_compact`: `timeout: None` — triggers an LLM summarization call whose own timeout (model.timeout, 300s default) already bounds it; a 180s wrapper would cut it off before the inner timeout could fire cleanly.
- `claude_code_send`: `timeout: None` — streams a multi-message Claude Code subprocess session; routinely runs many minutes and is bounded by the session's own idle/budget controls.
- `shell`: **no override** — subprocess.run inside `_execute_command` has a hardcoded 30s timeout, well below the 180s default. The default is sufficient belt-and-suspenders.
- All other tools (vault, workspace, checklist, core, attachment, heartbeat, health, web_fetch, http_request, other `claude_code_*`): inherit default (180s). Their internal bounds are below the default.

Audit results captured above; `notes.md` will carry any new findings that surface during execution.

### Composition with user-cancel

Extend the existing `_run_with_cancel(coro, cancel_event)` helper in `src/decafclaw/tools/__init__.py` to accept an optional `timeout_sec: int | None` parameter. The helper races three signals in a single `asyncio.wait`:

1. Tool task completes normally.
2. `cancel_event` fires → return the existing "interrupted" ToolResult.
3. `asyncio.sleep(timeout_sec)` completes → return a new "timed out" ToolResult.

If cancel and timeout both become ready in the same tick, cancel wins (user-initiated cancel takes priority over the clock).

The tool task is cancelled in both the interrupted and timed-out paths; its own cancel/exception is swallowed as today.

### Error message format

Mirrors the MCP convention:

```
[error: tool {name} timed out after {N}s]
```

Returned as `ToolResult(text=...)` so the existing `tool_end` event path surfaces it without any new event type.

### Sync-tool caveat (known, accepted)

Sync tools run via `asyncio.to_thread`. Cancelling the wrapping `Task` stops awaiting the thread but does **not** preempt the OS thread — Python can't. The thread continues until it returns naturally; the dispatcher has already moved on and the result is discarded. Pathological sync hangs would leak a thread but that risk is pre-existing and out of scope.

## Out of scope

- Cancel-scope isolation for anyio-based tools → #324.
- Per-tool failure tracking / circuit breaker / error budget → #325.
- Graceful degradation (hiding a repeatedly-failing tool from future turns) → #326.
- Changing MCP's per-server timeout mechanism — untouched.
- Dynamic timeout computation from tool arguments (e.g. reading `shell`'s per-call `timeout` parameter) — simpler static override is sufficient.
- A new event type for timeouts — the existing `tool_end` carrying the error ToolResult is enough.

## Acceptance criteria

1. `AgentConfig.tool_timeout_sec: int = 180` exists, resolves via env + config.json, and appears in `make config` output.
2. A tool that `await asyncio.sleep(999)` returns `ToolResult(text="[error: tool X timed out after 1s]")` within ~2s when `tool_timeout_sec=1`. Test covers this end-to-end via `execute_tool`.
3. A fast-returning tool continues to return its normal result. (Regression guard — existing tool tests still pass.)
4. Per-tool override behaviour:
   - Tool declared `timeout: 1` times out at ~1s even if global default is 300s.
   - Tool declared `timeout: 300` survives past a global default of 1s.
5. `timeout: None` opts out: declared tool running 2s does NOT time out under a global default of 1s.
6. Cancel-vs-timeout race: when `cancel_event` is set before the timeout expires, the returned result is the "interrupted" message, not "timed out".
7. `tool_timeout_sec <= 0` disables the wrapper globally — a sleeping tool is not cut off.
8. MCP-prefixed tools continue to flow through the existing MCP branch of `execute_tool` unchanged. Existing MCP tests pass.
9. `delegate_task`, `conversation_compact`, and `claude_code_send` carry `timeout: None` in their definitions.
10. (Removed — shell inherits the default; no shell-specific override.)
11. `make lint`, `make typecheck`, `make test` all pass.
12. Docs touched: a short bullet under **Conventions → Tools** in `CLAUDE.md` documenting the `timeout` key on tool defs and the config field. No new standalone doc page — feature is too small.

## Files expected to change

- `src/decafclaw/config_types.py` — add `tool_timeout_sec` to `AgentConfig`.
- `src/decafclaw/tools/__init__.py` — extend `_run_with_cancel`, add timeout resolution, thread timeout through `execute_tool` for the non-MCP branch.
- `src/decafclaw/tools/delegate.py` — add `"timeout": None` to the `delegate_task` entry in `DELEGATE_TOOL_DEFINITIONS`.
- `src/decafclaw/tools/conversation_tools.py` — add `"timeout": None` to the `conversation_compact` entry in `CONVERSATION_TOOL_DEFINITIONS`.
- `src/decafclaw/skills/claude_code/tools.py` — add `"timeout": None` to the `claude_code_send` entry in `TOOL_DEFINITIONS`.
- `tests/` — new test module (e.g. `tests/test_tool_timeout.py`) covering criteria 2, 4, 5, 6, 7.
- `CLAUDE.md` — one-line bullet under the Tools section.
