# User-Invokable Commands

Commands are skills with `user-invocable: true` that users trigger by name — `!command` in Mattermost, `/command` in the web UI.

## Quick Start

Create a SKILL.md with command frontmatter:

```yaml
---
name: migrate-todos
description: "Move unchecked to-dos from yesterday to today"
user-invocable: true
allowed-tools: vault_read, vault_write, vault_search
context: fork
---

Migrate unchecked to-do items from yesterday's daily note to today's.
$ARGUMENTS
```

Then type `!migrate-todos` in Mattermost or `/migrate-todos` in the web UI.

## Trigger Syntax

| Platform | Prefix | Example |
|----------|--------|---------|
| Mattermost | `!` | `!weather Portland` |
| Web UI | `/` | `/weather Portland` |
| Interactive | `!` | `!weather Portland` |

`!help` / `/help` lists all available commands. The web UI also autocompletes
them: typing `/` or `!` at the start of a line opens a suggestion menu — see
[Web UI](web-ui.md#chat).

## Arguments

Text after the command name is available as arguments:

- `$ARGUMENTS` — full argument string
- `$0`, `$1`, `$2` — positional (whitespace-separated)

If no placeholder is in the body, arguments are appended as `ARGUMENTS: <value>`.

## Frontmatter Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | required | Command name (trigger) |
| `description` | string | required | Shown in help |
| `user-invocable` | bool | true | Must be true |
| `allowed-tools` | string | "" | Comma-separated tool names pre-approved without confirmation |
| `context` | string | "inline" | `"fork"` for isolated subagent, `"inline"` for current conversation |
| `argument-hint` | string | "" | Placeholder hint, shown in `!help` and in the web UI's command autocomplete |

## Execution Modes

**Inline** (default): the command body (with argument substitutions) becomes the user message in the current conversation. The agent has full history context.

**Fork** (`context: fork`): the command runs in an isolated subagent via `delegate_task`. No conversation history — the command body is the entire prompt. Good for self-contained tasks.

## Pre-Approved Tools

`allowed-tools` lists tools that bypass confirmation during the command. This is the key workflow streamlining feature — `!migrate-todos` can use vault tools without prompting.

Special case: `allowed-tools: shell` pre-approves ALL shell commands for the command.

## Discovery

Commands are found in the same locations as skills:
1. Workspace (`data/{agent_id}/workspace/skills/`)
2. Agent-level (`data/{agent_id}/skills/`)
3. Bundled (`src/decafclaw/skills/`)

## Built-in side-effect commands

Some commands are handled by the server before skill dispatch. They run no agent turn in the current conversation, so the client sends them without the busy flag or a user echo. The table below is the web UI's set; Mattermost intercepts `/update` and `/restart` separately and does not have the tab-based commands.

| Command | What it does |
|---|---|
| `/fork [instruction]` (`/branch`, `!fork`, `!branch`) | Fork the conversation into a new clean context window; see [forked conversations](conversations.md#forked-conversations) |
| `/dissect [instruction]` (`!dissect`) | Fork the last failure into a seeded dissection conversation; see [dissection template](conversations.md#dissection-template) |
| `/retitle` (`!retitle`) | Regenerate the conversation title |
| `/terminal [cwd]` | Spawn a PTY in a canvas tab (web only) |
| `/update` (`!update`) | Self-update DecafClaw |
| `/restart` (`!restart`) | Restart the service, optionally on idle |

These share the command prefix but are not SKILL.md commands and do not appear in the skill catalog. `/fork` and `/dissect` accept free-form text after the command as the branch instruction or investigation focus.
