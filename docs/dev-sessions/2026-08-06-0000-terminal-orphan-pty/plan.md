# Web Terminal PTY Cleanup Implementation Plan

**Goal:** Prevent the agent from orphaning terminal PTYs or creating dead terminal tabs through canvas tools.

**Source issue:** https://github.com/lmorchard/decafclaw/issues/625 — **Tier:** `needs-review` (authorization: diff governs what agent-side code can do to human-only PTYs)

**Approach:** Per D1, expose terminal kill capability to agent tools via a narrowed façade on `Context` (not the full `TerminalRegistry`). Per D2, enforce the terminal widget's `agent_createable: false` flag in `canvas.new_tab`. Per D3, fail-open on PTY kill errors to avoid blocking canvas operations.

**Criteria:** C1 agent cannot create un-createable widgets · C2 close_tab passes registry · C3 clear_canvas kills terminal PTYs · C4 agent-facing handle exposes only get/kill

---

## Phase 0: Freeze the acceptance checks ✓

**COMPLETED.** `checks.md` written, tests authored, all checks verified failing for correct reasons, all guards verified passing, check-reviewer ran read-only adjudication, freeze commit `e6288dd` created and SHA recorded.

---

## Phase 1: Create the agent-facing terminal handle façade

Create the restricted capability object that agent tools will use, exposing only `get` and `kill` methods from the `TerminalRegistry`.

**Advances:** C4 — provides the façade with the required interface and no PTY-access methods

**Files:**
- Modify: `src/decafclaw/terminals.py` — add `AgentTerminalHandle` class
- Modify: `src/decafclaw/context.py` — add `terminal_registry` field

**Key changes:**

```python
# terminals.py
class AgentTerminalHandle:
    """Restricted terminal registry interface for agent tools.

    Exposes only get() and kill() methods. The agent cannot spawn,
    attach to, write to, or read from terminals.
    """

    def __init__(self, registry: TerminalRegistry):
        self._registry = registry

    def get(self, conv_id: str, tab_id: str):
        """Get a terminal session by conversation and tab ID."""
        return self._registry.get(conv_id, tab_id)

    async def kill(self, session, grace: float = 1.0):
        """Kill a terminal session.

        Args:
            session: TerminalSession to kill
            grace: Grace period in seconds before SIGKILL
        """
        await self._registry.kill(session, grace=grace)
```

```python
# context.py
@dataclass
class Context:
    # ... existing fields ...
    terminal_registry: Any = None  # AgentTerminalHandle, set by ConversationManager
```

**Verification — automated:**
- [ ] C4's check passes: `uv run pytest tests/test_terminals.py::test_agent_terminal_handle_exposes_no_pty_access`
- [ ] G5 still passes: `uv run pytest tests/test_terminals.py::test_no_agent_side_imports`
- [ ] `make lint` passes
- [ ] `make test` passes (no regression)

---

## Phase 2: Wire the façade into ConversationManager

Initialize the `AgentTerminalHandle` and set it on `Context` instances created by `ConversationManager`.

**Advances:** C4 (completion) — ensures the façade is actually available to tools

**Files:**
- Modify: `src/decafclaw/conversation_manager.py` — create façade and assign to context
- Modify: `src/decafclaw/http_server.py` — pass `TerminalRegistry` to manager (if not already available)

**Key changes:**

In `conversation_manager.py`, near where `Context` is created (around line 1471 where `request_confirmation` is set):

```python
# Create the agent-facing terminal handle
from decafclaw.terminals import AgentTerminalHandle
terminal_handle = AgentTerminalHandle(self.terminal_registry) if self.terminal_registry else None

# Fork context with the handle
ctx = ctx.fork_with(
    # ... existing fork params ...
    terminal_registry=terminal_handle
)
```

In `http_server.py`, verify `app.state.terminal_registry` is set (it already is, near line 1800) and passed to `ConversationManager.__init__`.

**Verification — automated:**
- [ ] C4's check still passes: `uv run pytest tests/test_terminals.py::test_agent_terminal_handle_exposes_no_pty_access`
- [ ] G5 still passes: `uv run pytest tests/test_terminals.py::test_no_agent_side_imports`
- [ ] `make lint` passes
- [ ] `make test` passes (no regression)

**Verification — manual:**
- [ ] Inspect `ConversationManager` to confirm the façade is created and assigned to `ctx.terminal_registry`

---

## Phase 3: Pass registry through tool_canvas_close_tab

Modify the agent-facing close tool to pass `ctx.terminal_registry` to `canvas.close_tab`.

**Advances:** C2 — ensures the tool passes the registry parameter

**Files:**
- Modify: `src/decafclaw/tools/canvas_tools.py` — update `tool_canvas_close_tab`

**Key changes:**

```python
async def tool_canvas_close_tab(ctx: "Context", tab_id: str) -> ToolResult:
    """Close a canvas tab by ID."""
    emit = emit_for_ctx(ctx)
    result = await canvas_mod.close_tab(
        ctx.config,
        ctx.conv_id,
        tab_id,
        emit=emit,
        registry=ctx.terminal_registry  # NEW: pass the façade
    )
    # ... rest unchanged ...
```

**Verification — automated:**
- [ ] C2's check passes: `uv run pytest tests/test_canvas_tools.py::test_canvas_close_tab_passes_registry`
- [ ] G1 still passes: `uv run pytest tests/test_canvas_tools.py::test_canvas_close_tab_returns_new_active`
- [ ] G3 still passes: `uv run pytest tests/test_canvas.py::test_close_tab_kills_terminal_pty`
- [ ] G4 still passes: `uv run pytest tests/test_canvas.py::test_close_tab_non_terminal_does_not_kill`
- [ ] `make lint` passes
- [ ] `make test` passes (no regression)

---

## Phase 4: Add registry parameter to clear_canvas and implement terminal cleanup

Modify `canvas.clear_canvas` to accept a registry parameter and kill terminal PTYs before clearing tabs.

**Advances:** C3 — enables terminal cleanup when clearing the canvas

**Files:**
- Modify: `src/decafclaw/canvas.py` — update `clear_canvas` signature and add terminal cleanup loop

**Key changes:**

```python
async def clear_canvas(
    config,
    conv_id: str,
    emit: EmitFn | None = None,
    registry=None  # NEW: optional terminal registry
) -> CanvasOpResult:
    """Clear all canvas tabs, optionally killing terminal sessions."""
    state = _load_canvas_state(config, conv_id)

    # NEW: Kill terminal PTYs before clearing (fail-open per D3)
    if registry and state.tabs:
        widget_registry = widgets_mod.get_widget_registry()
        for tab in state.tabs.values():
            if tab.widget_type == "terminal":
                session = registry.get(conv_id, tab.tab_id)
                if session:
                    try:
                        await registry.kill(session)
                        log.debug(f"Killed terminal PTY for tab {tab.tab_id}")
                    except Exception as exc:
                        # D3: fail-open — log but don't block canvas clear
                        log.warning(f"Failed to kill terminal PTY for {tab.tab_id}: {exc}")

    # Existing clear logic
    state.tabs = {}
    state.active_tab = None
    _save_canvas_state(config, conv_id, state)

    if emit:
        await emit("canvas_cleared", {"conv_id": conv_id})

    return CanvasOpResult(ok=True, text="canvas cleared")
```

**Verification — automated:**
- [ ] C3's check passes: `uv run pytest tests/test_canvas.py::test_clear_canvas_kills_terminal_ptys`
- [ ] G2 still passes: `uv run pytest tests/test_canvas_tools.py::test_canvas_clear_with_tabs`
- [ ] `make lint` passes
- [ ] `make test` passes (no regression)

---

## Phase 5: Pass registry through tool_canvas_clear

Modify the agent-facing clear tool to pass `ctx.terminal_registry` to `canvas.clear_canvas`.

**Advances:** C3 (completion) — wires the terminal cleanup into the agent tool

**Files:**
- Modify: `src/decafclaw/tools/canvas_tools.py` — update `tool_canvas_clear`

**Key changes:**

```python
async def tool_canvas_clear(ctx: "Context") -> ToolResult:
    """Clear all canvas tabs."""
    emit = emit_for_ctx(ctx)
    result = await canvas_mod.clear_canvas(
        ctx.config,
        ctx.conv_id,
        emit=emit,
        registry=ctx.terminal_registry  # NEW: pass the façade
    )
    return ToolResult(text=result.text)
```

**Verification — automated:**
- [ ] C3's check still passes: `uv run pytest tests/test_canvas.py::test_clear_canvas_kills_terminal_ptys`
- [ ] G2 still passes: `uv run pytest tests/test_canvas_tools.py::test_canvas_clear_with_tabs`
- [ ] `make lint` passes
- [ ] `make test` passes (no regression)

---

## Phase 6: Add agent_createable flag to widget descriptors

Extend `WidgetDescriptor` with an optional `agent_createable` boolean (default `true`) and update the widget loading to read it from `widget.json`.

**Advances:** C1 (foundation) — provides the metadata flag

**Files:**
- Modify: `src/decafclaw/widgets.py` — add `agent_createable: bool = True` to `WidgetDescriptor`
- Modify: `src/decafclaw/web/static/widgets/terminal/widget.json` — add `"agent_createable": false`

**Key changes:**

```python
# widgets.py
@dataclass
class WidgetDescriptor:
    name: str
    tier: str
    description: str
    modes: list[str]
    accepts_input: bool
    agent_createable: bool = True  # NEW: whether agent tools can create this widget
    data_schema: dict
    js_path: Path
    tier_root: Path
    mtime: float
```

In `_load_widget_descriptor()`, read the flag from JSON with a default:

```python
descriptor = WidgetDescriptor(
    # ... existing fields ...
    agent_createable=data.get("agent_createable", True),  # NEW
    # ...
)
```

```json
// terminal/widget.json
{
  "name": "terminal",
  "description": "...",
  "modes": ["canvas"],
  "accepts_input": false,
  "agent_createable": false,
  "data_schema": { ... }
}
```

**Verification — automated:**
- [ ] `make lint` passes
- [ ] `make test` passes (no regression)
- [ ] G6 still passes: `uv run pytest tests/test_canvas_tools.py::test_canvas_new_tab_returns_tab_id`
- [ ] G7 still passes: `uv run pytest tests/test_canvas.py::test_new_tab_creates_and_activates`

---

## Phase 7: Enforce agent_createable in canvas.new_tab

Check the `agent_createable` flag in `canvas.new_tab` and reject creation if it's `false`.

**Advances:** C1 — enforces the restriction

**Files:**
- Modify: `src/decafclaw/canvas.py` — add check in `new_tab` before creating the tab

**Key changes:**

In `new_tab()`, after loading the widget registry and before creating the tab:

```python
async def new_tab(config, conv_id: str, widget_type: str, widget_data: dict, emit: EmitFn | None = None) -> CanvasOpResult:
    """Create a new canvas tab."""
    widget_registry = widgets_mod.get_widget_registry()
    if not widget_registry:
        return CanvasOpResult(ok=False, error="widget registry not initialized")

    descriptor = widget_registry.get(widget_type)
    if not descriptor:
        return CanvasOpResult(ok=False, error=f"unknown widget type: {widget_type}")

    # NEW: Check agent_createable flag
    if not descriptor.agent_createable:
        return CanvasOpResult(
            ok=False,
            error=f"widget type '{widget_type}' is not agent_createable"
        )

    # ... rest of existing logic ...
```

**Verification — automated:**
- [ ] C1's check passes: `uv run pytest tests/test_canvas.py::test_new_tab_rejects_agent_uncreateable_widget`
- [ ] G6 still passes: `uv run pytest tests/test_canvas_tools.py::test_canvas_new_tab_returns_tab_id`
- [ ] G7 still passes: `uv run pytest tests/test_canvas.py::test_new_tab_creates_and_activates`
- [ ] `make lint` passes
- [ ] `make test` passes (no regression)

---

## Phase 8: Final verification

Run all acceptance checks and guards together to confirm the complete implementation.

**Advances:** All criteria (final validation)

**Verification — automated:**
- [ ] All four criteria pass:
  - [ ] C1: `uv run pytest tests/test_canvas.py::test_new_tab_rejects_agent_uncreateable_widget`
  - [ ] C2: `uv run pytest tests/test_canvas_tools.py::test_canvas_close_tab_passes_registry`
  - [ ] C3: `uv run pytest tests/test_canvas.py::test_clear_canvas_kills_terminal_ptys`
  - [ ] C4: `uv run pytest tests/test_terminals.py::test_agent_terminal_handle_exposes_no_pty_access`
- [ ] All seven guards still pass:
  - [ ] G1: `uv run pytest tests/test_canvas_tools.py::test_canvas_close_tab_returns_new_active`
  - [ ] G2: `uv run pytest tests/test_canvas_tools.py::test_canvas_clear_with_tabs`
  - [ ] G3: `uv run pytest tests/test_canvas.py::test_close_tab_kills_terminal_pty`
  - [ ] G4: `uv run pytest tests/test_canvas.py::test_close_tab_non_terminal_does_not_kill`
  - [ ] G5: `uv run pytest tests/test_terminals.py::test_no_agent_side_imports`
  - [ ] G6: `uv run pytest tests/test_canvas_tools.py::test_canvas_new_tab_returns_tab_id`
  - [ ] G7: `uv run pytest tests/test_canvas.py::test_new_tab_creates_and_activates`
- [ ] Tamper check clean: `git diff e6288dd -- tests/test_canvas.py tests/test_canvas_tools.py tests/test_terminals.py`
- [ ] `make check` passes (lint + typecheck)
- [ ] `make test` passes (full suite)
