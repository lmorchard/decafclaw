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
