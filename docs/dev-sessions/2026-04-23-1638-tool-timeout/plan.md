# Plan: Generic Tool Execution Timeout

Working from `spec.md`. Five phases, each landing as its own commit. Order is chosen so each phase compiles & tests pass in isolation, and so later phases only build on public surface defined earlier.

---

## Phase 1 — Config field

**Goal:** add `AgentConfig.tool_timeout_sec: int = 180`, resolvable from env and `config.json`, visible in `make config`. No behavior change yet.

**Files:**
- `src/decafclaw/config_types.py` — add the field to `AgentConfig` alongside `max_concurrent_tools`, `max_active_tools`, etc.
- `src/decafclaw/config.py` — add the env alias in the explicit-alias dict around line 377: `"tool_timeout_sec": "TOOL_TIMEOUT_SEC"`. (Following the nearby `CHILD_TIMEOUT_SEC` precedent — no `AGENT_` prefix.)

**Verification:**
- `make lint` and `make typecheck` pass.
- `TOOL_TIMEOUT_SEC=42 make config | grep tool_timeout_sec` shows `42`.
- `make config` (no env override) shows `180`.

**Commit message:** `feat(tools): add agent.tool_timeout_sec config field (#7)`

---

## Phase 2 — Per-tool overrides (pre-landed)

**Goal:** declare the opt-outs BEFORE the wrapper goes live. `timeout: None` keys are harmless no-ops until the resolver exists in Phase 3, so this ordering means the moment Phase 3 flips the switch, every known long-runner is already opted out — no regression window for `delegate_task`/`conversation_compact`/`claude_code_send` between commits.

**Files:**
- `src/decafclaw/tools/delegate.py` — add `"timeout": None` to the `delegate_task` entry in `DELEGATE_TOOL_DEFINITIONS`.
- `src/decafclaw/tools/conversation_tools.py` — add `"timeout": None` to the `conversation_compact` entry in `CONVERSATION_TOOL_DEFINITIONS`.
- `src/decafclaw/skills/claude_code/tools.py` — add `"timeout": None` to the `claude_code_send` entry in `TOOL_DEFINITIONS`.

**Verification:**
- `make lint` / `make typecheck` pass.
- Existing tests still pass — these additions don't affect any existing code path.

**Commit message:** `feat(tools): opt long-running tools out of generic timeout (#7)`

---

## Phase 3 — Timeout wrapper + resolver + dispatcher wiring

**Goal:** plumb the timeout into `execute_tool` for the non-MCP branch. MCP branch untouched.

**Files:**
- `src/decafclaw/tools/__init__.py`:
  1. Extend `_run_with_cancel(coro, cancel_event, timeout_sec=None, tool_name="")` to race tasks in a single `asyncio.wait`: the tool task, the optional cancel-event waiter, and an optional `asyncio.sleep(timeout_sec)` timer. Return `(task, interrupted_or_timed_out_result_or_None)`.
     - Precedence on near-simultaneous fire: cancel beats timeout. After `asyncio.wait` returns, check membership in `done` in this order: cancel → timeout → tool-complete.
     - Preserve current contract when `cancel_event` is None and `timeout_sec` is None: just `await tool_task`.
     - When timeout fires: cancel the tool task, swallow `CancelledError`/`Exception`, return `ToolResult(text=f"[error: tool {tool_name} timed out after {timeout_sec}s]")`.
     - `tool_name` is used only for the timeout error message; interrupted message keeps its generic wording.
     - Clean up any un-done auxiliary tasks (cancel_task and timer_task) in all paths to avoid "Task was destroyed but it is pending" warnings — per the zero-tolerance-for-tracebacks convention.
  2. Add `_resolve_tool_timeout(ctx, name) -> int | None`. Lookup order: `ctx.tools.extra_definitions`, `TOOL_DEFINITIONS`, `SEARCH_TOOL_DEFINITIONS`. Walk each list, find the entry whose `function.name == name`.
     - Use an explicit sentinel for "key absent" vs. "key is None": `_MISSING = object()`; `val = entry.get("timeout", _MISSING)`. If `val is _MISSING` on every source, fall back to the config default.
     - If the found value is a number `<= 0`, normalize to `None` (disabled).
     - If the found value is `None` or a positive int, return it as-is.
     - Fallback (tool not found in any def source, or all sources lack the key): return `ctx.config.agent.tool_timeout_sec` (also normalized: `<= 0` → `None`).
  3. In `execute_tool`, for the non-MCP branch, compute `timeout_sec = _resolve_tool_timeout(ctx, name)` and pass it into `_run_with_cancel` alongside `cancel_event` and `tool_name=name`.
  4. MCP branch: keeps its existing call — explicitly NOT passing a timeout. MCP already wraps per-call in `mcp_client.py`.

**Verification:**
- `make lint` / `make typecheck` pass.
- Existing tests still pass (`pytest -q`).
- At this point a hanging tool WILL be cut off after 180s in any real run; dedicated regression tests come in Phase 4.

**Commit message:** `feat(tools): enforce per-call timeout in execute_tool (#7)`

---

## Phase 4 — Tests

**Goal:** cover acceptance criteria 2, 4, 5, 6, 7 in a dedicated test module. Criteria 3, 8, 11 are covered by the existing suite continuing to pass.

**Files:**
- `tests/test_tool_timeout.py` (new).

**Test helpers:**
- Register fake tools via `ctx.tools.extra` + `ctx.tools.extra_definitions` per-test. Cleanest path — exercises the skill-tool resolution branch and requires no global state mutation.
- For the "resolution from global `TOOL_DEFINITIONS`" path, use `monkeypatch` to temporarily insert a fake entry into both `TOOLS` and `TOOL_DEFINITIONS`; undo on teardown.
- Minimal ctx factory: look at existing patterns in `tests/test_skills.py` / `tests/test_tool_registry.py` and reuse. Must use `dataclasses.replace` for config overrides (per CLAUDE.md — never mutate shared config).
- Use **short sleeps everywhere** (0.1–0.3s for the "completes in time" cases; 0.3s timeouts on the "times out" cases). Wrap every test call in `asyncio.wait_for(execute_tool(...), timeout=3)` as a safety net so a broken wrapper fails loudly instead of hanging the suite.

**Tests to write:**
1. `test_fast_tool_returns_normally` — extra tool returns immediately, no timeout.
2. `test_hanging_tool_times_out_at_default` — default `tool_timeout_sec=0.3`, tool awaits `asyncio.sleep(3)`. Expect `"[error: tool X timed out after 0s]"` (int cast). Adjust rounding/int semantics: if we want a cleaner error message, set the default to `1` and the sleep to `3`.
3. `test_per_tool_short_override_wins` — default 300, tool declares `timeout: 1`, tool sleeps 3. Expect timeout error.
4. `test_per_tool_long_override_survives` — default 1, tool declares `timeout: 5`, tool sleeps 0.2. Expect normal result.
5. `test_timeout_none_disables_wrapper` — default 1, tool declares `timeout: None`, tool sleeps 0.2. Expect normal result. (Verify it's the default that *would* have fired without the opt-out.)
6. `test_timeout_zero_disables_wrapper` — default 0, tool sleeps 0.2. Expect normal result. Second variant: default `-1`.
7. `test_cancel_beats_timeout` — default 1, tool awaits a sleep longer than 1, schedule `ctx.cancelled.set()` via `asyncio.get_running_loop().call_later(0.1, cancel_event.set)`. Expect "interrupted" message, not "timed out".
8. `test_sync_tool_timeout` — sync tool that `time.sleep(3)`s, default 1. Expect timeout error. Accept that the thread keeps running after the test returns; do not await it. (The test should still finish in ~1s.)
9. `test_mcp_prefix_skipped_by_generic_wrapper` — monkeypatch `decafclaw.mcp_client.get_registry` to return a registry-like object whose `get_tools()` returns `{"mcp__foo__bar": fake_fn}` where `fake_fn` returns quickly. Call `execute_tool(ctx, "mcp__foo__bar", {})`. Verify the return is the fake's result (not a timeout error). Light smoke test — the goal is to prove we didn't accidentally route MCP through the new wrapper.

**Verification:**
- `make test` all pass.
- `pytest --durations=25 tests/test_tool_timeout.py` — target <500ms each for non-timeout tests; ~1s for tests that intentionally hit the timeout. Flag anything above those bounds and tighten.

**Commit message:** `test(tools): coverage for execute_tool timeout (#7)`

---

## Phase 5 — Docs + final verification

**Goal:** update `CLAUDE.md` Tools section and run the full verification gauntlet. Capture any execution surprises in `notes.md`.

**Files:**
- `CLAUDE.md` — add a bullet under **Conventions → Tools** (next to the existing priority bullet): something like *"Per-tool timeout via `timeout` key on TOOL_DEFINITIONS entries. Default `agent.tool_timeout_sec` (180s). `None` opts out; long-running tools like `delegate_task`, `conversation_compact`, `claude_code_send` are opted out."*

**Verification gauntlet:**
- `make lint`
- `make typecheck`
- `make test`
- `make config | grep tool_timeout_sec` — default 180.
- `git diff main...HEAD --stat` review.

**Retro:**
- Append an audit summary + any execution surprises to `notes.md`. Lean, not exhaustive.

**Commit message:** `docs(tools): document tool_timeout_sec + per-tool override key (#7)`

---

## Post-execution

- Push branch.
- Open PR titled `feat(tools): generic execute_tool timeout`. Body: summary, rationale, link "Progress on #7" (not "Closes" — #7 is an umbrella with three open children #324/#325/#326 still pending).
- Request Copilot review via `gh pr edit <N> --add-reviewer copilot-pull-request-reviewer`.

---

## Risks & rollback

- **False-positive timeouts on legitimate slow tools not covered by the audit.** Mitigation: 180s default is intentionally generous for fast tools; audit covered known long-runners. If a surprise shows up post-merge, trivially fixed by adding `timeout: None` or a larger override to the specific tool's def.
- **Resolver doesn't find skill-dynamic tools.** Mitigation: resolver reads `ctx.tools.extra_definitions` which is where dynamic-provider results are stored (per `context.py`). Tests exercise this path.
- **Rollback:** each phase is its own commit, so `git revert` any one phase cleanly. The config field from Phase 1 is safe even if wiring is reverted — field just sits unused.
