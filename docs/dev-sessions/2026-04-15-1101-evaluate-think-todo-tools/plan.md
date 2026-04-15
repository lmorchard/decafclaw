# Implementation Plan: Checklist Execution Loop & Tool Cleanup

## Overview

Three phases — small, incremental. Each leaves the system working.

---

## Phase 1: Checklist Backend + Tools

**What this builds:** Replace `todos.py` with a checklist backend, replace `todo_tools.py` with checklist tools. The new tools use `end_turn=True` for mechanical step advancement.

**Codebase state after:** Checklist tools are always-loaded and functional. Old todo tools gone.

### Prompt

**Rewrite `src/decafclaw/todos.py`** as the checklist backend. Keep the same file (it's the storage layer). Changes:

- Rename to reflect checklist semantics (or keep filename — it's internal)
- Storage format: markdown checkboxes, same path (`{workspace}/todos/{conv_id}.md`), same `_UNCHECKED`/`_CHECKED` markers
- Add completion note support: `- [x] Step description [done: note text]`
- New functions:
  - `checklist_create(config, conv_id, steps: list[str]) -> list[dict]` — write steps, return items
  - `checklist_get_current(config, conv_id) -> dict | None` — return the first unchecked item (with index)
  - `checklist_complete_current(config, conv_id, note: str = "") -> dict | None` — mark current done, return next item (or None if all done)
  - `checklist_abort(config, conv_id) -> None` — delete the file
  - `checklist_status(config, conv_id) -> list[dict]` — read all items with done/text/note
- Remove old `todo_add`, `todo_complete`, `todo_list`, `todo_clear`

**Rewrite `src/decafclaw/tools/todo_tools.py`** as checklist tools:

- `tool_checklist_create(ctx, steps: list[str])` — calls `checklist_create`, returns formatted first step. No `end_turn`.
- `tool_checklist_step_done(ctx, note: str = "")` — calls `checklist_complete_current`. Returns `ToolResult(end_turn=True)` with next step text, or "all steps complete" message.
- `tool_checklist_abort(ctx, reason: str = "")` — calls `checklist_abort`. Returns confirmation. No `end_turn`.
- `tool_checklist_status(ctx)` — calls `checklist_status`. Returns formatted progress.

Tool definitions with descriptions per spec:
- `checklist_create`: "When you have a task that involves 3 or more distinct steps, create a checklist before starting work. This ensures methodical execution and prevents skipping steps. Pass an array of step descriptions. Overwrites any existing checklist."
- `checklist_step_done`: "Mark the current checklist step as complete and advance to the next one. Call this after finishing each step. Optionally include a brief note about what was done."
- `checklist_abort`: "Abandon the current checklist. Use when the plan needs rethinking or the task is no longer relevant."
- `checklist_status`: "Show current checklist progress without advancing."

Update exports: `CHECKLIST_TOOLS`, `CHECKLIST_TOOL_DEFINITIONS` (replacing `TODO_TOOLS`, `TODO_TOOL_DEFINITIONS`).

**Update `src/decafclaw/tools/__init__.py`:** Replace todo imports with checklist imports.

**Update tests:** Rewrite `tests/test_todos.py` and `tests/test_todo_tools.py` for the new checklist API. Test:
- Create checklist, verify file written
- Complete steps one at a time, verify progression
- Complete all steps, verify "all done" state
- Abort clears the checklist
- Status shows correct progress
- `end_turn=True` on `step_done` result
- Empty checklist handling

Lint, type-check, run tests.

---

## Phase 2: Think Tool Cleanup + Docs

**What this builds:** Clean up the stale think tool reference, update CLAUDE.md and docs.

**Codebase state after:** No stale references, docs current.

### Prompt

- Fix `src/decafclaw/tools/core.py` docstring: remove "think" and "compaction" from the module description (neither exists as a tool).
- Update `CLAUDE.md`:
  - Key files: update `todos.py` description to reflect checklist
  - Key files: update `tools/todo_tools.py` entry (now checklist tools)
  - Conventions: note the checklist execution loop pattern
- Verify no other references to `todo_add`, `todo_complete`, `todo_list`, `todo_clear` in the codebase (grep for orphaned references).
- Verify no references to a "think" tool beyond the fixed docstring.

Lint, type-check, run tests. Commit.

---

## Risk Notes

- **Tool description tuning is empirical.** The `checklist_create` description is the key control surface for unprompted usage. We'll iterate on wording based on manual testing — the first version may need adjustment.
- **`end_turn=True` behavior.** The agent loop makes one final no-tools LLM call after `end_turn=True`. The returned text from `checklist_step_done` (with the next step) becomes the tool result the LLM sees in that final call. On the NEXT iteration, the LLM sees the full history including the step instruction and should start working on it. This is the same pattern the project skill uses.
