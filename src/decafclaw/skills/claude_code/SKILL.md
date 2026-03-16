---
name: claude_code
description: "Delegate coding tasks to Claude Code as a subagent. Use when asked to fix bugs, add features, refactor code, write tests, review code, or do any work that requires reading and editing files in a codebase. Triggers on: 'fix this bug', 'add a feature', 'refactor', 'write a test', 'review this code', 'update the config', 'clean up', or any request involving code changes in a repository."
requires:
  env:
    - ANTHROPIC_API_KEY
---

# Claude Code — Coding Subagent

Claude Code is a powerful coding agent that can read, edit, and execute code in repositories. Use this skill to delegate coding tasks that require file manipulation.

## Available Tools

### 1. `claude_code_start` — Start a new coding session

Creates a new Claude Code session for a specific working directory.

**Parameters:**
- `cwd` (required) — path to the project/repository
- `description` (optional) — what this session is for
- `model` (optional) — override the Claude model
- `budget_usd` (optional) — per-session cost limit

Only one session per working directory. Use `claude_code_sessions` to check active sessions.

### 2. `claude_code_send` — Send a task to a session

Sends a prompt to an active Claude Code session. The session maintains context across multiple sends.

**Parameters:**
- `session_id` (required) — which session to use
- `prompt` (required) — the coding task or follow-up

Returns a summary of what Claude Code did. Full logs are saved to disk.

### 3. `claude_code_stop` — End a session

Closes a session and reports final cost.

### 4. `claude_code_sessions` — List active sessions

Shows all active sessions with ID, working directory, age, and cost so far.

## Workflow

1. **Start** a session with `claude_code_start` pointing at the repo
2. **Send** tasks with `claude_code_send` — iterate as needed
3. **Stop** when done with `claude_code_stop`

Sessions expire after 30 minutes of inactivity. If a session expires, start a new one and restate the context.

## Cost Awareness

Each Claude Code interaction costs money (Anthropic API usage). The summary after each `claude_code_send` includes the cost. Be mindful of:
- Keep tasks focused — one clear objective per send
- Use `claude_code_stop` when done to free the session
- Default budget limit applies per session

## Permission Model

Claude Code tools require confirmation before executing. Read-only tools (Read, Glob, Grep) are auto-approved. File edits and shell commands need user approval via Mattermost reactions.
