# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/625
**Frozen at:** (to be recorded after freeze commit)
**Check files — read-only from Phase 1 onward:**
- `tests/test_canvas.py`
- `tests/test_canvas_tools.py`
- `tests/test_terminals.py`

## C1
CRITERION: WHEN `canvas.new_tab` is called with a widget whose descriptor sets `agent_createable: false`, THE SYSTEM SHALL reject the call and create no tab.
CHECK: `uv run pytest tests/test_canvas.py::test_new_tab_rejects_agent_uncreateable_widget` passes.
AT FREEZE: fails — `AssertionError: new_tab should reject agent_createable: false widgets` (correct reason: `canvas.new_tab` returned `ok=True` when attempting to create a terminal widget; the `agent_createable` flag is not yet checked)

## C2
CRITERION: `tool_canvas_close_tab` SHALL pass a non-`None` registry through to `canvas.close_tab`.
CHECK: `uv run pytest tests/test_canvas_tools.py::test_canvas_close_tab_passes_registry` passes.
AT FREEZE: fails — `AssertionError: tool_canvas_close_tab must pass 'registry' to canvas.close_tab` (correct reason: spying on `canvas.close_tab` shows `registry` not in captured kwargs; the tool doesn't yet pass the parameter through)

## C3
CRITERION: `canvas.clear_canvas` SHALL accept a registry and kill sessions for `terminal` tabs, fail-open per D3.
CHECK: `uv run pytest tests/test_canvas.py::test_clear_canvas_kills_terminal_ptys` passes.
AT FREEZE: fails — `TypeError: clear_canvas() got an unexpected keyword argument 'registry'` (correct reason: `canvas.clear_canvas` does not yet accept a `registry` parameter)

## C4
CRITERION: THE SYSTEM SHALL expose to agent tools an object providing `get` and `kill` and **no** PTY-access method (`spawn`, `attach`, `detach`, `write_input`, `set_viewport`, `shutdown_all`).
CHECK: `uv run pytest tests/test_terminals.py::test_agent_terminal_handle_exposes_no_pty_access` passes.
AT FREEZE: fails — `pytest.fail: C4 criterion not met: No agent-facing terminal handle found` (correct reason: neither `AgentTerminalHandle` class nor `get_agent_terminal_handle` function can be imported from `decafclaw.terminals`; the façade does not yet exist)

## Guards
(Pass today; must keep passing. Not criteria — they can't fail at freeze.)

- G1: `uv run pytest tests/test_canvas_tools.py::test_canvas_close_tab_returns_new_active` — non-terminal close behaviour for agent tools. Passed at freeze (1 passed).
- G2: `uv run pytest tests/test_canvas_tools.py::test_canvas_clear_with_tabs` — non-terminal clear behaviour for agent tools. Passed at freeze (1 passed).
- G3: `uv run pytest tests/test_canvas.py::test_close_tab_kills_terminal_pty` — existing module-level kill logic. Passed at freeze (1 passed).
- G4: `uv run pytest tests/test_canvas.py::test_close_tab_non_terminal_does_not_kill` — existing module-level no-kill logic. Passed at freeze (1 passed).
- G5: `uv run pytest tests/test_terminals.py::test_no_agent_side_imports` — terminals.py stays un-imported by tools/ or skills/. Passed at freeze (1 passed).
- G6: `uv run pytest tests/test_canvas_tools.py::test_canvas_new_tab_returns_tab_id` — normal widgets stay agent-createable. Passed at freeze (1 passed).
- G7: `uv run pytest tests/test_canvas.py::test_new_tab_creates_and_activates` — normal widgets stay agent-createable. Passed at freeze (1 passed).

## Adjudication
(Written at freeze step 4, before the freeze commit. One disposition per check AND per guard.)

- **C1:** accepted — Loads the real widget registry from bundled descriptors; cannot pass by hardcoding rejection for "terminal" without also implementing the `agent_createable` flag mechanism. The assertion requires both `ok=False` AND an error message mentioning "agent_createable", making widget-type-based hardcoding detectable.
- **C2:** accepted — Spies on `canvas.close_tab` kwargs via monkeypatch; the registry kwarg must be present and non-None. Cannot pass without actually modifying `tool_canvas_close_tab` to pass the parameter.
- **C3:** accepted — Creates a fake registry with a spy `kill` method, calls `clear_canvas` with that registry, and verifies `kill` was invoked with the terminal session. The registry must actually be called; you can't fake the invocation without implementing the logic.
- **C4:** accepted with clarification — The reviewer noted that C4 alone only checks object shape (the façade exists with `get`/`kill` and lacks PTY-access methods), not enforcement that tools use it. However, C4 is paired with C2 (which verifies the wiring through `tool_canvas_close_tab`) and G5 (which verifies `terminals.py` isn't imported by tools). The three criteria together enforce both the façade's restriction AND its actual use. C4's scope is deliberately narrow: it verifies the object exposed to agent tools is capability-restricted.
- **G1:** accepted — Tests non-terminal close behavior (tab switching) independent of PTY logic. Asserts against result text containing the new active tab ID; must run real close logic to produce it.
- **G2:** accepted — Tests non-terminal clear behavior with markdown_document tabs. Asserts against the success message "canvas cleared"; no PTY logic invoked.
- **G3:** accepted — Module-level terminal kill guard, same spy mechanism as C3. The registry's `kill` must be called with the correct session.
- **G4:** accepted — Guards against over-aggressive killing by closing a non-terminal tab when a terminal tab exists, asserting no kill occurred. Requires actual widget-type inspection.
- **G5:** accepted — Exhaustive directory walk of `tools/` and `skills/` with regex search for `terminals` imports. Cannot pass without avoiding the import. (Note: does not catch transitive imports, but direct imports are the primary risk vector.)
- **G6:** accepted — Basic happy path for normal widget creation. Asserts exact tab ID returned; must run real `new_tab` logic.
- **G7:** accepted — Module-level happy path with assertions against file-backed state and event records. All assertions are against real side effects.

## Amendments
(Append-only. Empty unless an amendment was made.)
