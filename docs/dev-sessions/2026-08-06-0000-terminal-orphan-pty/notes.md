# Notes

## Implementation Summary

Completed all 7 phases (Phases 0-7) per the plan:

- **Phase 0 (Freeze):** Acceptance checks frozen at commit `e6288dd`
- **Phase 1:** Created `AgentTerminalHandle` façade in `terminals.py`
- **Phase 2:** Wired façade into `ConversationManager` via `ctx.terminal_registry`
- **Phase 3:** Passed registry through `tool_canvas_close_tab`
- **Phase 4:** Added registry parameter to `clear_canvas` with terminal PTY cleanup
- **Phase 5:** Passed registry through `tool_canvas_clear`
- **Phase 6:** Added `agent_createable` flag to `WidgetDescriptor` and terminal widget.json
- **Phase 7:** Enforced `agent_createable` check in `canvas.new_tab`

## Verification Results

### Independent Verification (post-implementation)

All four acceptance criteria pass:
- **C1:** `test_new_tab_rejects_agent_uncreateable_widget` - PASS
- **C2:** `test_canvas_close_tab_passes_registry` - PASS
- **C3:** `test_clear_canvas_kills_terminal_ptys` - PASS
- **C4:** `test_agent_terminal_handle_exposes_no_pty_access` - PASS

All seven guards pass:
- **G1:** `test_canvas_close_tab_returns_new_active` - PASS
- **G2:** `test_canvas_clear_with_tabs` - PASS
- **G3:** `test_close_tab_kills_terminal_pty` - PASS
- **G4:** `test_close_tab_non_terminal_does_not_kill` - PASS
- **G5:** `test_no_agent_side_imports` - PASS
- **G6:** `test_canvas_new_tab_returns_tab_id` - PASS
- **G7:** `test_new_tab_creates_and_activates` - PASS

### Tamper Check

```bash
git diff e6288dd -- tests/test_canvas.py tests/test_canvas_tools.py tests/test_terminals.py
```

Result: **CLEAN** (no output - frozen check files unchanged)

### Full Test Suite

All 59 tests in the three frozen files pass (tests/test_canvas.py, tests/test_canvas_tools.py, tests/test_terminals.py).

### Lint

`ruff check` on modified Python files: PASS

## Implementation Notes

- Used `getattr(descriptor, 'agent_createable', True)` in canvas.py to handle test mocks that use SimpleNamespace instead of real WidgetDescriptor objects
- Per D1, the façade pattern ensures agent tools can only `.get()` and `.kill()` terminal sessions, with no access to spawn/attach/write methods
- Per D2, the `agent_createable` flag defaults to `true` for backwards compatibility
- Per D3, PTY kill failures are logged but don't block canvas operations (fail-open)

## 2026-08-06 — express resume: regression found and fixed by `make test`

Resumed after decafclaw#765 (the pytest-collection bug that parked the two prior driver
attempts) merged; this branch's freeze commit already post-dates #765's fix on `origin/main`,
so no rebase was needed. Recorded the freeze sha in `checks.md` (a sanctioned append that the
prior session's Phase 0 skipped).

**The prior session's "Full Test Suite" claim above was scoped, not aggregate** — it ran only
the three frozen files (59 tests), never `make test`. Running `make test` for real surfaced a
genuine regression Phase 7 introduced: `canvas.new_tab`'s unconditional `agent_createable`
check doesn't distinguish *who* is calling it. `canvas.new_tab` has three callers — the
agent-facing `canvas_new_tab` tool (should be blocked from creating `terminal` tabs, per C1),
the human-only `/terminal` websocket command handler (`web/websocket.py`), and the
human-authenticated "Open in Canvas" HTTP endpoint (`http_server.py::post_canvas_new_tab`,
backing the code_block/markdown_document widgets' UI button) — and Phase 7 blocked all three
identically. That broke the `/terminal` command outright: `tests/web/test_terminal_command.py`
went 4 (then 6, once the mock-signature mismatch surfaced) red.

This is the exact behavior the issue's own "What we're NOT doing" section protects — *"Not
changing the `/terminal` command path. Spawning stays server-side and human-initiated"* — so
the fix restores spec intent rather than changing it, and touches no frozen check: C1's check
calls the bare `canvas.new_tab()` with no extra kwarg and still gets the default (enforcing)
behavior.

**Fix:** added `enforce_agent_createable: bool = True` to `canvas.new_tab`, defaulting to the
behavior C1 exercises. The two human-only call sites (`web/websocket.py`'s `/terminal` handler,
`http_server.py::post_canvas_new_tab`) now pass `enforce_agent_createable=False`; the
agent-facing `tools/canvas_tools.py::tool_canvas_new_tab` call site is unchanged (still
enforces by default). Also updated two test doubles in `tests/web/test_terminal_command.py`
(`spy_new_tab`, `failing_new_tab`) to accept/forward `**kwargs` — they had fixed signatures that
didn't survive the new keyword arg; that file is ordinary test scaffolding, not a frozen check
file.

**Re-verified after the fix:** all 4 criteria + 7 guards pass individually (11/11 collected,
none silently skipped); tamper diff (`git diff e6288dd -- <check files>`) is empty; `make
check` (ruff + pyright + tsc) is clean; `make test` is fully green (3764 passed, 2 skipped, 0
failed) — the first time this branch has actually run the full suite.
