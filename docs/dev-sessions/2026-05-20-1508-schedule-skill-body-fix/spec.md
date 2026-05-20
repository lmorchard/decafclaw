# Fix scheduled-task skill body delivery (#558)

## Problem

After PR #556 made `SCHEDULE.md` a thin trigger, scheduled tasks run with no skill instructions in context. The LLM receives only the one-line trigger, has no skill body, and can't recover because the schedule's `allowed-tools` allow-list strips `tool_search` and `activate_skill`.

Affected: bundled `dream` / `garden` / `newsletter`, contrib `kindle` / `linkding-ingest` / `mastodon-ingest`.

Root cause: `schedules.py:setup_schedule_ctx` calls `activate_skill_internal` and discards the returned body. The composer-rendered system prompt only contains always-loaded skill bodies; per-conversation activated bodies normally arrive as tool-result messages, which doesn't happen for pre-activated skills in a fresh scheduled-task conversation.

## Goals

1. Pre-activated `required-skills` deliver their full SKILL.md body to the LLM in scheduled tasks.
2. `tool_search` and `activate_skill` are not filtered out by the schedule allow-list, so future misconfigs aren't dead-ends.
3. Regression test prevents silent recurrence.

## Non-goals

- Re-architecting context composition or moving per-conversation activated bodies into the system prompt for the general case.
- Changing the command path (`commands.py`) — same gap exists in principle but the failure mode there is different and out of scope for this fix.
- HEARTBEAT_OK sentinel restructuring (#553).

## Design

### Body injection

Inject pre-activated skill bodies into the user prompt for scheduled tasks, before the trigger text, wrapped in the same `<loaded_skills><skill name="…">…</skill></loaded_skills>` pattern that `prompts/__init__.py` uses for always-loaded skills.

`$SKILL_DIR` substitution must match what `activate_skill_internal` does (uses `skill_info.location.resolve()`).

### Where to inject

`run_schedule_task` already builds the prompt before calling `manager.enqueue_turn`. The cleanest split:

- `setup_schedule_ctx` continues to call `activate_skill_internal` (tools wiring, init, mark activated).
- `run_schedule_task` separately resolves the SkillInfo objects for the task's required-skills and renders their bodies into a `<loaded_skills>` block prepended to the prompt.

That keeps the body string outside the context-setup callback (which runs after enqueue) and avoids re-encoding through the agent loop.

If a required skill name doesn't resolve to a discovered SkillInfo, log and skip — same fail-open behavior as the activation path.

### Escape hatch

In `setup_schedule_ctx` where `allowed_tools_set` is built, unconditionally add `tool_search` and `activate_skill` to the set before assigning to `ctx.tools.allowed`. These don't grant capabilities by themselves — they're meta-tools the model needs to recover from an under-spec'd task. Same `preapproved` shape (no confirmation needed).

### Test

Add to `tests/test_schedules.py`: build a fake SkillInfo with a recognizable body string, build a `ScheduleTask` with `required_skills=[name]` and a thin-trigger body, mock `manager.enqueue_turn` to capture the `prompt` arg, run `run_schedule_task`, assert the captured prompt contains both the `<loaded_skills>` wrapper and the skill body.

## Out of scope follow-ups

- Same gap may exist in `commands.py:execute_command` for fork-context commands with `required-skills` that don't restate the dependency's instructions. Worth a separate audit.

## Acceptance

- `make check` passes.
- `make test` passes.
- New regression test covers the thin-trigger + required-skills path.
- Manual verification: a scheduled run of mastodon-ingest produces a prompt with the SKILL.md body inline.
