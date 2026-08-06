# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/625
**Frozen at:** (to be recorded after freeze commit)
**Check files — read-only from Phase 1 onward:**
- `tests/test_canvas.py`
- `tests/test_canvas_tools.py`
- `tests/test_terminals.py`

## C1 — the agent cannot create a widget marked un-createable

CRITERION: WHEN `canvas.new_tab` is called with a widget whose descriptor sets `agent_createable: false`, THE SYSTEM SHALL reject the call and create no tab.

CHECK: `uv run pytest tests/test_canvas.py::test_new_tab_rejects_agent_uncreateable_widget -v` passes.

AT FREEZE: FAILED - AssertionError: new_tab should reject agent_createable: false widgets (terminal tab was created with ok=True when it should have been rejected). Correct reason: behavior is genuinely absent.

## C2 — closing a terminal tab through the agent tool kills its PTY

CRITERION: `tool_canvas_close_tab` SHALL pass a non-`None` registry through to `canvas.close_tab`.

CHECK: `uv run pytest tests/test_canvas_tools.py::test_canvas_close_tab_passes_registry -v` passes.

AT FREEZE: FAILED - AssertionError: tool_canvas_close_tab must pass 'registry' to canvas.close_tab (captured kwargs only contained 'emit', registry was missing). Correct reason: behavior is genuinely absent.

## C3 — clearing the canvas kills terminal PTYs

CRITERION: `canvas.clear_canvas` SHALL accept a registry and kill sessions for `terminal` tabs, fail-open per D3.

CHECK: `uv run pytest tests/test_canvas.py::test_clear_canvas_kills_terminal_ptys -v` passes.

AT FREEZE: FAILED - TypeError: clear_canvas() got an unexpected keyword argument 'registry' (function signature doesn't accept registry parameter yet). Correct reason: behavior is genuinely absent.

## C4 — the object reachable from agent-side code cannot touch a PTY

CRITERION: THE SYSTEM SHALL expose to agent tools an object providing `get` and `kill` and **no** PTY-access method (`spawn`, `attach`, `detach`, `write_input`, `set_viewport`, `shutdown_all`).

CHECK: `uv run pytest tests/test_terminals.py::test_agent_terminal_handle_exposes_no_pty_access -v` passes.

AT FREEZE: FAILED - pytest.fail("AgentTerminalHandle not yet implemented"). Correct reason: the agent-facing façade doesn't exist yet.

## Guards

(Pass today; must keep passing. Not criteria — they can't fail at freeze.)

- **G1:** `uv run pytest tests/test_canvas_tools.py::test_canvas_close_tab_returns_new_active tests/test_canvas_tools.py::test_canvas_clear_with_tabs -v` — non-terminal close/clear behaviour for the agent tools. Passed at intake.
- **G2:** `uv run pytest tests/test_canvas.py::test_close_tab_kills_terminal_pty tests/test_canvas.py::test_close_tab_non_terminal_does_not_kill -v` — the existing module-level kill / no-kill logic. Passed at intake.
- **G3:** `uv run pytest tests/test_terminals.py::test_no_agent_side_imports -v` — `terminals.py` stays un-imported by anything under `decafclaw/tools/` or `decafclaw/skills/`. Passed at intake.
- **G4:** `uv run pytest tests/test_canvas_tools.py::test_canvas_new_tab_returns_tab_id tests/test_canvas.py::test_new_tab_creates_and_activates -v` — normal widgets stay agent-createable. Passed at intake.

## Adjudication

(Written at freeze step 4, before the freeze commit. One disposition per check AND per guard. To be replaced after review.)

## Amendments

(Append-only. Empty unless an amendment was made.)
