# Notes

## Summary

Fixed the regression filed as #558: thin-trigger SCHEDULE.md (introduced in #556) was failing because `setup_schedule_ctx` pre-activated `required-skills` but discarded their body strings. Activated-by-tool-call bodies normally arrive as a `activate_skill` tool-result message; in a fresh scheduled-task conversation that path never fires, so the LLM saw only the one-line trigger.

## Changes

- `src/decafclaw/schedules.py`
  - New `_render_required_skill_bodies(config, names)` helper builds a `<loaded_skills><skill name="…">…</skill></loaded_skills>` block, mirroring the always-loaded pattern in `prompts/__init__.py:107`. Substitutes `$SKILL_DIR` and HTML-escapes the skill name attribute. Returns `""` on no resolved bodies (unknown name → warning + skip; empty body → skip).
  - `run_schedule_task` prepends the rendered block to `prompt` between `preamble` and the trigger body.
  - `setup_schedule_ctx` allow-list union'd with `{tool_search, activate_skill}` so the model has an escape hatch if a future schedule misconfig drops the body. Not added to `preapproved` (they don't gate on confirmation).
- `tests/test_schedules.py` — three regression tests under `TestRunScheduleTask`:
  - `test_required_skill_body_injected_into_prompt` — asserts the SKILL.md body marker, `<loaded_skills>` wrapper, and ordering (body before trigger) appear in the user message routed to the agent loop.
  - `test_unknown_required_skill_skipped_gracefully` — no crash, no empty wrapper.
  - `test_escape_hatch_tools_exempt_from_allow_list` — both meta-tools land in `ctx.tools.allowed` alongside the schedule's explicit allow-list entries.
- `docs/schedules.md` — documented body injection + escape-hatch exemption.

## Test results

`make check` clean. `make test`: 2705 passed, ~12s.

## Out of scope (followups)

- Same body-discard gap *may* exist in `commands.py:execute_command` for fork-context commands with `required-skills` that don't restate their dependency's instructions. Different failure mode (fork commands often inline their full body), but worth a separate audit.
- The HEARTBEAT_OK sentinel issue (#553) is tangentially related — would have surfaced this regression louder if structured signaling existed. Not addressed here.

## Retrospective

### What went well

- Root cause was clean: the diagnostic walk (commit log → SCHEDULE.md diff → `setup_schedule_ctx` → `activate_skill_internal` return-value path → `context_composer.py:495` comment confirming the design assumption) landed on the bug in one pass. The composer comment about "tool-result delivery" was load-bearing — without it, the fix could have gone in five different places.
- Per-conversation activated bodies are explicitly *not* in the system prompt, so the fix had to live in the prompt assembly path for scheduled tasks specifically. Resisted the urge to generalize.
- Escape-hatch exemption bundled in with the body fix. Different mechanism, same root-cause class ("the task is under-spec'd and the model has no way out"), so it belongs in the same PR.

### Friction

- The `setup_schedule_ctx` closure structure forced the body injection to live in the outer `run_schedule_task` scope, not inside the context-setup callback. That was the right split (prompt is finalized before enqueue) but took a moment to see.
- No test caught this when #556 shipped. The existing `test_fork_required_skills_activated` only checks that `activate_skill_internal` was *called*, not that its return value reached the LLM. Worth a memory: when activation-by-side-effect is the design, test the body delivery, not just the call.

### Memory candidates

- **Pre-activated skill bodies are delivered via tool-result messages, not the system prompt.** Implication: any code path that pre-activates a skill in a fresh conversation must inject the body explicitly. Affects `setup_schedule_ctx`, possibly `commands.py:execute_command`. Anchor: `context_composer.py:495-497` comment.
