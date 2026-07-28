# progress_tracker Switch/Create Clear Implementation Plan

**Goal:** Clear the sticky slot when leaving an EXECUTING project via `project_switch` or `project_create`.

**Source issue:** https://github.com/lmorchard/decafclaw/issues/656 — **Tier:** `auto-ok` (both criteria reduce to concrete assertions, oracle exists, no risk-gated paths)

**Approach:** Per D1 from the spec, call `_clear_project_progress(ctx)` when `project_switch` or `project_create` moves away from an EXECUTING project. This matches the existing pattern used on transition to DONE (line 335) and on `project_advance` out of EXECUTING (line 615).

**Criteria:** C1 — switch away clears · C2 — create while executing clears

---

## Phase 0: Freeze the acceptance checks

Write `checks.md` and author the tests the checks name, per `references/frozen-checks.md`.
No implementation in this phase.

**Files:**
- Create: `docs/dev-sessions/2026-07-27-1528-progress-tracker-switch/checks.md`
- Modify: `tests/test_project_tools.py` — add `test_switch_away_from_executing_clears` and `test_create_while_executing_clears`

**Verification — automated:**
- [x] C1's check runs and fails for the expected reason: `AssertionError: assert 0 >= 1` (clear_sticky not called)
- [x] C2's check runs and fails for the expected reason: `AssertionError: assert 0 >= 1` (clear_sticky not called)
- [x] Guards G1a, G1b, G2 run and pass (3 passed in 1.73s)
- [x] Freeze commit made; sha c49752e recorded in `checks.md`

---

## Phase 1: Implement the fix

Add calls to `_clear_project_progress(ctx)` in `tool_project_switch` and `tool_project_create` when the outgoing project is EXECUTING.

**Advances:** C1, C2 — both fully satisfied by this phase

**Files:**
- Modify: `src/decafclaw/skills/project/tools.py` — add clear logic to `tool_project_switch` and `tool_project_create`

**Key changes:**

In `tool_project_switch` (currently lines 413-420), before switching to the new project, check if the current project is EXECUTING and clear its sticky:

```python
async def tool_project_switch(ctx, project: str) -> str | ToolResult:
    """Switch the current project context."""
    # Check if outgoing project is EXECUTING and clear its sticky
    outgoing_slug = _get_current_project(ctx)
    if outgoing_slug:
        outgoing_info = load_project(ctx.config, outgoing_slug)
        if outgoing_info and outgoing_info.status == ProjectState.EXECUTING:
            await _clear_project_progress(ctx)

    result = _load_or_error(ctx.config, project)
    if isinstance(result, ToolResult):
        return result
    info = result
    _set_current_project(ctx, info.slug)
    return f"Switched to project '{info.slug}' ({info.status.value}). Call project_next_task."
```

In `tool_project_create` (currently lines 166-175), before setting the new project as current, check if the current project is EXECUTING and clear its sticky:

```python
async def tool_project_create(ctx, description: str, slug: str = "") -> str | ToolResult:
    """Create a new structured project."""
    # Check if outgoing project is EXECUTING and clear its sticky
    outgoing_slug = _get_current_project(ctx)
    if outgoing_slug:
        outgoing_info = load_project(ctx.config, outgoing_slug)
        if outgoing_info and outgoing_info.status == ProjectState.EXECUTING:
            await _clear_project_progress(ctx)

    info = create_project(ctx.config, description, slug=slug)
    _set_current_project(ctx, info.slug)
    return (
        f"Project created: {info.slug}\n"
        f"Directory: {info.directory}\n"
        f"Status: {info.status.value}\n\n"
        f"Now call **project_next_task** to get your first instruction."
    )
```

**Verification — automated:**
- [ ] C1's check passes: `uv run pytest tests/test_project_tools.py::TestProgressTrackerEmit::test_switch_away_from_executing_clears`
- [ ] C2's check passes: `uv run pytest tests/test_project_tools.py::TestProgressTrackerEmit::test_create_while_executing_clears`
- [ ] G1a still passes: `uv run pytest tests/test_project_tools.py::TestProgressTrackerEmit::test_done_clears_sticky`
- [ ] G1b still passes: `uv run pytest tests/test_project_tools.py::TestProgressTrackerEmit::test_advance_out_of_executing_clears`
- [ ] G2 still passes: `uv run pytest tests/test_project_tools.py::TestProgressTrackerEmit::test_update_step_emits_during_executing`
- [ ] `make lint` passes
- [ ] `make test` passes (no regression)
