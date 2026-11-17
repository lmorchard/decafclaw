# issue-1381-task-mode-enum

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1381 ([10.2] Replace raw `task_mode: str` with `TurnKind`/enum)
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1381-task-mode-enum
- Branch: issue-1381-task-mode-enum (base origin/main @ 3e7a6cee)
- Board: In progress (already moved before this session; no move needed).
- Identity: MokaGnome; writes via `ghflow exec --` / ghflow commands.

## Scope (from issue)

- **Problem.** `task_mode` is a bare string threaded from `TurnKind` through the
  transport boundary, free-form at the edge.
- **Result.** Task mode is an enum inside; free string only at the transport boundary.
- **Verify.** `make test` — existing turn-kind tests pass; update boundary-parse tests.

## Design (decided)

`TurnKind` and `task_mode` are different concepts with different values
(`heartbeat_section` vs `heartbeat`, `scheduled_task` vs `scheduled`, `wake` vs
`background_wake`; USER/WORKFLOW kinds have no task mode). Reusing `TurnKind`
would conflate them, so a dedicated **plain** `TaskMode(Enum)` is defined in
`context.py` (where the field and `UNATTENDED_TASK_MODES` live):

```python
class TaskMode(Enum):
    INTERACTIVE = ""            # USER / WORKFLOW turns (attended)
    HEARTBEAT = "heartbeat"
    SCHEDULED = "scheduled"
    CHILD_AGENT = "child_agent"
    BACKGROUND_WAKE = "background_wake"
```

Values are the existing wire strings, so the inbox JSONL format is unchanged.
Plain Enum (not str-mixing): string comparisons against it are a type error,
which is the point — free strings only at the boundary. Mirrors the existing
`TurnKind` (plain Enum) + `get_kind()` string→enum boundary-parse pattern.

### Boundary map (verified at base 3e7a6cee)

- `enqueue_turn(task_mode=...)` — internal API; now takes `TaskMode | None`.
- Inbox JSONL `"task_mode"` field — the only free-string surface: write stores
  `.value` (None stays None); drain parses via new module-level
  `parse_task_mode()` with INTERACTIVE fallback (mirrors `get_kind` → USER).
- `Context.for_task(task_mode=...)` — takes `TaskMode`.
- `ctx.task_mode: TaskMode = TaskMode.INTERACTIVE` — internal, always a member.
- Not serialized anywhere else: not in `PersistedTurnState`/`_CTX_DRIVEN_FIELDS`,
  not in context.json diagnostics, not in message_types.json, not in the TUI.

### Internal sites converted (string → member)

- `context.py`: field, `UNATTENDED_TASK_MODES`, `for_task` signature/docstring.
- `conversation_manager.py`: `KIND_TASK_MODE` (TurnKind→TaskMode), enqueue_turn /
  `_start_turn` signatures, inbox write `.value`, drain `parse_task_mode()`,
  confirmation log/reason render via `.value`.
- `agent.py`: `_TASK_MODE_TO_COMPOSER`, `_UNWATCHED_TASK_MODES` (frozensets of
  members), wake-trigger archive check (`is TaskMode.BACKGROUND_WAKE`), widget
  log via `.value`.
- `tools/confirmation.py`, `tools/shell_tools.py`, `tools/skill_tools.py`:
  log/reason renders via `.value` (log output unchanged).
- `tools/schedule_tools.py`: `_DISALLOWED_TASK_MODES` members; drop the
  `getattr(ctx, "task_mode", "")` fallback read (field is declared on Context).
- `skills/newsletter/tools.py`, `contrib/skills/kindle/tools.py`: member checks.
- Callers: `heartbeat.py`, `schedules.py` (enqueue_turn), `compaction.py`
  (for_task) pass members.

### Tests updated to the new path (CLAUDE.md: rewrite, don't preserve)

String assignments/assertions → `TaskMode` members across ~15 files
(test_context, test_conversation_manager, test_agent_turn,
test_agent_loop_breaker, test_unattended_approval, test_newsletter_skill,
test_scheduled_wakes, test_prompts, test_admin_tools, test_skills,
test_shell_guidance_modes, test_shell_approval_guidance,
test_widget_input_pause, test_background_wake_integration, kindle tests).
New: boundary-parse tests for `parse_task_mode` (known values, None, unknown →
INTERACTIVE) + inbox round-trip keeps the wire string.

## Checks

- Baseline `make test` at base: 5524 passed, 2 skipped (green).
- `make install-js` done in worktree; `.env` copied, HTTP_PORT=18962.

## Resume 2026-11-17 (delivered)

- Rebased the worktree branch onto origin/main 0b42bbcc (branch had no unique commits;
  the partial diff was reapplied 3-way). Only conflict: `TurnLifecycle` had moved to
  `events.py`, so `TaskMode` alone stayed in `context.py`.
- Finished the conversion: tools (`confirmation.py`, `schedule_tools.py`,
  `shell_tools.py`, `skill_tools.py`), callers (`compaction.py`, `heartbeat.py`,
  `schedules.py`), skills (`newsletter`, contrib `kindle` + its smoke script),
  and docs (`architecture.md`, `background-wake.md`, `context-composer.md`).
- Import cycle found and fixed: `context` → `context_composer` → vault tools →
  `tools/__init__` → `schedule_tools` imported `TaskMode` at module level while
  `context` was still initializing. `_is_disallowed_task_mode()` now imports
  `TaskMode` inside the function (the allowed cycle-breaking pattern).
- Tests: all raw-string sites converted; new boundary coverage for
  `parse_task_mode` (None, every wire string, unknown → INTERACTIVE) and an inbox
  round-trip test proving JSONL stores `"heartbeat"` and the drained ctx holds
  `TaskMode.HEARTBEAT`.
- Gates: `make fmt`, `make check` (ruff, pyright 0 errors, tsc, module graph),
  `make test` 5656 passed / 2 skipped.
- Status: implementation complete, ready to publish the PR (no merge authorization).
