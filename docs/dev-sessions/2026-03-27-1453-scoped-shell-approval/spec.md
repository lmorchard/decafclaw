# Scoped Shell Tool Approval for Skills

## Problem

Skills with `allowed-tools: shell` get blanket pre-approval to run **any** shell command without user confirmation. A skill that only needs to run its own `fetch.sh` script can currently run arbitrary commands. This violates least-privilege.

Discovered with `linkding-ingest` and `mastodon-ingest` skills, which only need `$SKILL_DIR/fetch.sh` but get full shell access.

## Current Behavior

1. Skill frontmatter: `allowed-tools: shell, wiki_read, wiki_write`
2. On command execution: `ctx.preapproved_tools = {"shell", "wiki_read", "wiki_write"}`
3. In `tool_shell()`: `if "shell" in ctx.preapproved_tools` → auto-execute any command
4. Same for scheduled tasks: `ctx.preapproved_tools = set(task.allowed_tools)`

## Proposed Behavior

Allow scoped shell approval via parenthesized glob patterns:

```yaml
allowed-tools: shell($SKILL_DIR/fetch.sh), wiki_read, wiki_write
```

- `shell` (no parens) — blanket approval, same as today
- `shell($SKILL_DIR/fetch.sh)` — only approve commands matching that glob pattern
- `shell($SKILL_DIR/*.sh)` — approve any .sh script in the skill dir
- Multiple scoped entries allowed: `shell($SKILL_DIR/fetch.sh), shell(make build)`

### Pattern matching

- Uses `fnmatch` glob matching (already used by `shell_allow_patterns`)
- `$SKILL_DIR` is expanded to the skill's directory path at parse time
- The full command string is matched against the pattern (e.g., `$SKILL_DIR/fetch.sh --flag` matches `$SKILL_DIR/fetch.sh *`)

### Approval flow in `tool_shell()`

The existing approval chain stays the same, with scoped patterns inserted:

1. Heartbeat admin → auto-approve
2. `"shell"` in `preapproved_tools` → blanket auto-approve (unchanged)
3. **NEW:** Command matches a scoped shell pattern in `ctx.preapproved_shell_patterns` → auto-approve
4. Command matches admin allow patterns → auto-approve
5. User confirmation prompt

### Where parsing happens

- **`allowed-tools` parsing** in `skills/__init__.py`: detect `shell(...)` syntax, separate into regular tool names and shell patterns
- **`commands.py` `execute_command()`**: expand `$SKILL_DIR` in shell patterns, store in `ctx.preapproved_shell_patterns`
- **`schedules.py` `run_schedule_task()`**: same expansion and context setup

### Context changes

Add `preapproved_shell_patterns: list[str]` to `Context`, default `[]`. This is a list of glob patterns that the shell tool checks before falling through to admin patterns or user confirmation.

## Scope

- Parse `shell(pattern)` syntax from `allowed-tools` frontmatter
- Store expanded patterns on context
- Check patterns in shell tool approval chain
- Update the two existing contrib skills to use scoped syntax
- Tests for pattern parsing, expansion, and approval logic

## Out of scope

- Scoped patterns for tools other than shell (could generalize later)
- Changes to `shell_allow_patterns.json` (admin patterns are a separate mechanism)
- UI changes
