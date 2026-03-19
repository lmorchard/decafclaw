# User-Invokable Commands — Spec

## Status: Ready

## Background

Users frequently repeat multi-step workflows — migrating to-dos between daily notes, starting dev sessions, checking weather across cities. Currently these require explaining the intent to the agent each time. Commands let users trigger predefined workflows by name, with arguments.

Claude Code solved this with slash commands (now unified as "skills"). We follow the same approach: commands are skills with `user_invocable: true`, triggered by `!name` in Mattermost or `/name` in the web UI.

## Goals

1. Let users invoke named workflows with a single trigger + optional arguments
2. Reuse the existing skill infrastructure — no parallel system
3. Keep the trigger syntax in the chat layer, command execution in the agent layer
4. Support tool pre-approval for streamlined workflows

## Design

### Commands Are Skills

A command is a skill with `user_invocable: true` in its SKILL.md frontmatter (this field already exists). No new file format, no new discovery mechanism. Any existing or new skill can be a command.

```yaml
---
name: migrate-todos
description: "Move unchecked to-dos from yesterday's daily note to today's"
user_invocable: true
allowed-tools: vault_set_path, vault_daily_path, vault_move_items
context: fork
argument-hint: "[source-date]"
---

Migrate unchecked to-do items from yesterday's daily note to today's.

1. Use vault_daily_path with offset=-1 to get yesterday's path
2. Use vault_daily_path to get today's path
3. Move unchecked items from sections "today", "tonight", "this week"

$ARGUMENTS
```

### Trigger Syntax

- **Mattermost**: `!command-name [arguments]` — bang prefix (Mattermost reserves `/`)
- **Web UI**: `/command-name [arguments]` — slash prefix (natural for chat UIs)

The chat layer (mattermost.py, websocket.py) detects the trigger prefix, parses the command name and arguments, and calls the agent layer with structured data. The agent layer never sees `!` or `/`.

### Trigger Detection

Detection happens in the chat layer before the message reaches the agent:

1. Check if the message starts with `!` (Mattermost) or `/` (web UI)
2. Parse: `command_name` = first word after prefix, `arguments` = rest of message
3. Look up `command_name` in discovered skills where `user_invocable: true`
4. If not found: return error to user immediately — "Unknown command: {name}. Type !help for available commands."
5. If found: proceed to command execution

### Argument Substitution

The command's SKILL.md body supports argument placeholders:

- `$ARGUMENTS` — the full argument string after the command name
- `$0`, `$1`, `$2`, ... — positional arguments (whitespace-separated)

If `$ARGUMENTS` does not appear anywhere in the body, the arguments are appended to the end: `ARGUMENTS: <value>`.

Example: `!migrate-todos from yesterday` with body containing `$ARGUMENTS` → body with "from yesterday" substituted in.

### Command Execution

When a command is triggered:

1. **Auto-activate the skill** — load SKILL.md body and native tools (if any), same as `activate_skill` but without permission prompts (user explicitly invoked it)
2. **Substitute arguments** into the body
3. **Apply `allowed-tools`** — tools listed in the frontmatter are pre-approved for this invocation (e.g. shell patterns, vault tools). No confirmation prompts for these tools.
4. **Choose execution mode**:
   - `context: fork` → run via `delegate_task` — isolated subagent, no conversation history, command body is the prompt
   - Default (no `context` or `context: inline`) → the command body (with argument substitutions applied) becomes the entire user message sent to the agent. The agent sees the command instructions in the current conversation with full history.

### allowed-tools

The `allowed-tools` frontmatter field lists tool names that should be usable without confirmation during this command's execution. This is the key workflow streamlining feature.

For the agent layer, this means:
- Tools in the list are added to a per-turn "pre-approved" set
- Shell tool confirmation checks the pre-approved set before prompting
- Skill activation is auto-approved for skills referenced in the command

Format: comma-separated tool names.
```yaml
allowed-tools: vault_set_path, vault_daily_path, vault_move_items
```

For shell commands specifically, use the existing shell allow-pattern system — `shell_patterns add <pattern>` — rather than embedding patterns in `allowed-tools`. The `allowed-tools` field pre-approves tool *calls* (bypassing confirmation), not shell command patterns.

Special case: `allowed-tools: shell` pre-approves ALL shell commands for this command invocation. Use with caution.

**`context: fork` interaction**: when a command runs in a forked subagent, the `allowed-tools` pre-approved set is passed to the child context. The child can use those tools without confirmation.

### Built-in: help

`!help` / `/help` is a built-in command (not a skill) that lists all available user-invokable commands. The built-in `help` takes precedence over any skill named `help`.

```
Available commands:
  migrate-todos — Move unchecked to-dos from yesterday's daily note to today's
  weather — Get weather for a location
  ...

Type !command-name [arguments] to invoke.
```

Implemented directly in the chat layer — no LLM call needed.

### Discovery

Commands are discovered from the same locations as skills, in the same priority order:

1. **Workspace** (`data/{agent_id}/workspace/skills/`) — highest priority, agent-editable
2. **Agent-level** (`data/{agent_id}/skills/`) — admin-managed
3. **Bundled** (`src/decafclaw/skills/`) — shipped with code

A workspace command with the same name as a bundled one overrides it.

### Supported Frontmatter Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | required | Command name (used as trigger) |
| `description` | string | required | One-line description (shown in help) |
| `user_invocable` | bool | true | Must be true for the skill to be a command |
| `allowed-tools` | string | "" | Comma-separated tool names pre-approved for this command |
| `context` | string | "inline" | `"fork"` runs in isolated subagent, `"inline"` runs in current conversation |
| `argument-hint` | string | "" | Placeholder hint for arguments (future: UI autocomplete) |

### What This Does NOT Change

- Skills that are not `user_invocable` work exactly as before (agent activates them autonomously)
- The `activate_skill` tool continues to work for autonomous skill activation
- The skill catalog in the system prompt continues to show all skills
- The existing skill permission system is unchanged for autonomous activation

## Future Work (out of scope)

- `argument-hint` display in UI autocomplete / command palette
- Tab completion for command names in the web UI input
- Command aliases (multiple names for the same command)
- `model` frontmatter field to override the LLM model for a command
- `agent` frontmatter field to select a specialized agent type
