# Implementation Plan

## Phase 1: Heartbeat tool restrictions
**Advances:** C2

- Update `_split_sections` in `src/decafclaw/heartbeat.py` to parse section frontmatter using a regex that checks for YAML blocks (`^---\n.*?\n---$`).
- Parse `allowed-tools` and `disallowed-tools` keys from the frontmatter.
- Make `ScheduleTask` parser (in `src/decafclaw/schedules.py`) also read `disallowed-tools` from frontmatter and populate a `disallowed_tools` list field.
- In `run_heartbeat_turn` (`src/decafclaw/heartbeat.py`), pass `allowed_tools` and `disallowed_tools` to the `Context.for_task` method (if supported) or manually apply them to `ctx.tools.allowed` and block them.
- Run `uv run pytest tests/test_heartbeat.py::test_heartbeat_tool_restrictions`.

## Phase 2: Skill invocation flags
**Advances:** C3

- Extend `SkillInfo` in `src/decafclaw/skills/__init__.py` to parse `disable_model_invocation` from frontmatter. (Already added in discovery pass, make sure it's formally part of the code).
- In `src/decafclaw/tools/skill_tools.py`, update `tool_activate_skill` to check `skill_info.disable_model_invocation` and reject activation if true.
- Run `uv run pytest tests/test_skills.py::test_skill_invocation_flags_enforced`.

## Phase 3: Skill tool native resolution and restriction
**Advances:** C1, C4

- Update `activate_skill_internal` (`src/decafclaw/tools/skill_tools.py`) to enforce `allowed_tools`. If `skill.allowed_tools` is set, filter the native tools extracted from the skill's module so that only those with `function.name` in `allowed_tools` are exported.
- To handle C4 (conflict resolution): When `activate_skill_internal` registers tools into `ctx.tools.skill_contributions`, modify `src/decafclaw/tool_definitions.py` (`_build_tool_list`) so that if two skills provide a tool with the same name, instead of dropping it, we namespace the names (e.g. `skillname__toolname`).
- Run `uv run pytest tests/test_skills.py::test_skill_allowed_tools_enforced`
- Run `uv run pytest tests/test_skills.py::test_skill_tool_conflict_resolution`.
