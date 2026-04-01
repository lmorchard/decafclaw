# Scoped Shell Tool Approval — Implementation Plan

## Step 1: Parse `shell(pattern)` syntax in skill frontmatter

**Files:** `src/decafclaw/skills/__init__.py`

- In `allowed-tools` parsing, detect entries matching `shell(...)`
- Split into two outputs: regular tool names (including bare `shell` if present) and shell patterns (the glob strings inside parens)
- Store shell patterns in a new `SkillInfo.shell_patterns: list[str]` field
- `$SKILL_DIR` stays unexpanded at this point — expanded later when the skill's location is known in context

**Test:** Unit test parsing various `allowed-tools` strings with and without scoped shell entries.

## Step 2: Add `preapproved_shell_patterns` to Context

**Files:** `src/decafclaw/context.py`

- Add `preapproved_shell_patterns: list[str] = []` field
- No other changes needed — `fork_for_tool_call` already copies all fields via `__dict__.update`

## Step 3: Expand patterns and set on context in commands and schedules

**Files:** `src/decafclaw/commands.py`, `src/decafclaw/schedules.py`

- In `execute_command()`: expand `$SKILL_DIR` in `skill.shell_patterns` using `skill.location`, set `ctx.preapproved_shell_patterns`
- In `run_schedule_task()`: same expansion using `task.skill_dir` (need to propagate skill location to ScheduleTask)
- For schedule tasks created from skills, store the skill's location path on the task

## Step 4: Check scoped patterns in shell tool

**Files:** `src/decafclaw/tools/shell_tools.py`

- After the blanket `"shell" in preapproved_tools` check, add a new check:
  - If `ctx.preapproved_shell_patterns` is non-empty, check command against patterns using `_command_matches_pattern()`
  - If match → auto-approve with log message

## Step 5: Update contrib skills to use scoped syntax

**Files:** `contrib/skills/linkding-ingest/SKILL.md`, `contrib/skills/mastodon-ingest/SKILL.md`

- Change `shell` to `shell($SKILL_DIR/fetch.sh)` in their `allowed-tools` frontmatter

## Step 6: Tests

**Files:** `tests/test_scoped_shell.py` (new)

- Parse `shell(pattern)` from allowed-tools string
- Parse mixed entries: `shell($SKILL_DIR/fetch.sh), wiki_read, shell(make build)`
- Bare `shell` still works as blanket approval
- `$SKILL_DIR` expansion
- Shell tool approval: scoped pattern matches, scoped pattern rejects, blanket still works
- End-to-end: command with scoped shell runs matching command without confirmation

## Step 7: Lint, typecheck, commit

- `make check` + `make test`
- Commit with descriptive message
