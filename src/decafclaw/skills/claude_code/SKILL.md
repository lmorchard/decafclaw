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

Creates a new Claude Code session for a specific working directory. Probes the environment and optionally runs a setup command.

**Parameters:**
- `cwd` (required) — path to the project/repository
- `description` (optional) — what this session is for
- `model` (optional) — override the Claude model
- `budget_usd` (optional) — per-session cost limit
- `setup_command` (optional) — shell command to run for environment setup (e.g., `uv sync`, `npm install`). Requires user confirmation.
- `instructions` (optional) — persistent instructions prepended to every `claude_code_send` in this session. Use for project conventions, coding style, constraints. Set once at start.

Only one session per working directory. Use `claude_code_sessions` to check active sessions.

**Returns structured data:**
- `session_id`, `cwd`, `model`, `budget_usd` — session info
- `environment.tools_available` — tools found on PATH (python3, node, go, uv, pip, npm, make, git, cargo, etc.)
- `environment.project_files` — config files found in cwd (Makefile, pyproject.toml, package.json, etc.)
- `environment.git` — branch name and clean/dirty status (null if not a git repo)
- `setup` — setup command result (command, exit_code, stdout, stderr, status) or null

Use the environment info to decide what setup command to run and how to structure tasks.

### 2. `claude_code_send` — Send a task to a session

Sends a prompt to an active Claude Code session. The session maintains context across multiple sends.

**Parameters:**
- `session_id` (required) — which session to use
- `prompt` (required) — the coding task or follow-up
- `context` (optional) — per-task context prepended to this send only. Use for relevant specs, vault pages, conversation excerpts.
- `include_diff` (optional, default true) — capture git diff of changes made during this send. Requires the cwd to be a git repo.

Context injection: if the session has `instructions` (from start) and/or this send has `context`, they are prepended to the prompt using XML-style `<instructions>` and `<context>` tags. The LLM sees them as structured preamble before the task.

**Returns structured data alongside a text summary:**
- `exit_status` — `success`, `error`, `budget_exhausted`, `timeout`, or `cancelled`
- `files_changed` — list of file paths modified
- `tools_used` — dict of tool name → call count
- `errors` — list of `{message}` dicts
- `cost_usd` — running session cost total
- `duration_ms` — wall time for this send
- `send_count` — total sends in this session
- `num_turns` — LLM turns in this send
- `result_text` — final text from Claude Code (truncated to 500 chars)
- `sdk_session_id` — for debugging
- `log_path` — path to JSONL log file
- `diff` — git diff of changes made during this send (`null` if not a git repo or diff capture failed; `""` if no changes were made)

The `diff` field captures three categories: committed changes (if Claude Code made commits), unstaged edits to tracked files, and a list of new untracked files. Use it to review what changed without separately reading files.

Use `exit_status` to branch programmatically: if `success`, move on; if `error`, send a fix prompt or run `claude_code_exec` to diagnose.

### 3. `claude_code_exec` — Run a shell command (no LLM turn)

Runs a shell command directly in a session's working directory. No LLM involved — direct subprocess execution. Use for quick verification between `claude_code_send` calls.

**Parameters:**
- `session_id` (required) — which session to use
- `command` (required) — shell command to run
- `timeout` (optional) — seconds before killing the process (default 30, max 120)

**Returns structured data:**
- `exit_code` — process exit code (null if timed out)
- `stdout` — captured standard output
- `stderr` — captured standard error
- `status` — `success`, `error`, or `timeout`
- `duration_ms` — wall time
- `command` — the command that was run

**Confirmation model:** Inherits from session approval. If the user has already approved a `claude_code_send` for this session, exec calls are auto-approved. Otherwise, requests its own confirmation.

### 4. `claude_code_stop` — End a session

Closes a session and reports final cost.

### 5. `claude_code_sessions` — List active sessions

Shows all active sessions with ID, working directory, age, and cost so far.

## Workflow

### Basic
1. **Start** a session with `claude_code_start` pointing at the repo
2. **Send** tasks with `claude_code_send` — iterate as needed
3. **Stop** when done with `claude_code_stop`

### With context and review
1. **Start** → `claude_code_start` with `instructions` for project conventions
2. **Send** → `claude_code_send` with `context` (specs, vault pages) and `include_diff=true`
3. **Review** → inspect the `diff` field in the structured result
4. **Fix** → if changes need adjustment, `claude_code_send` with feedback
5. **Verify** → `claude_code_exec` to run tests
6. **Stop** → `claude_code_stop` when satisfied

### Verify loop (TDD-style)
1. **Start** → `claude_code_start` with optional `setup_command`
2. **Implement** → `claude_code_send` with the coding task
3. **Verify** → `claude_code_exec` to run tests (`make test`, `pytest`, etc.)
4. **Fix** → if tests failed, `claude_code_send` with the failure output
5. **Verify** → `claude_code_exec` again to confirm the fix
6. **Stop** → `claude_code_stop` when satisfied

The exec tool makes the verify steps cheap (no LLM cost, no latency). Use this pattern for iterative development.

Sessions expire after 30 minutes of inactivity. If a session expires, start a new one and restate the context.

## Git Best Practice

**Always use git-initialized working directories for Claude Code sessions.** This enables:
- Reliable diff capture after each send (`include_diff=true`)
- Change tracking and rollback if needed
- The parent agent can review exactly what changed

If the cwd isn't already a git repo, consider running `claude_code_exec` with `git init && git add -A && git commit -m "initial"` before sending coding tasks.

## Cost Awareness

Each Claude Code interaction costs money (Anthropic API usage). The structured result after each `claude_code_send` includes the cost. Be mindful of:
- Keep tasks focused — one clear objective per send
- Use `claude_code_exec` for verification instead of burning a full `claude_code_send`
- Use `claude_code_stop` when done to free the session
- Default budget limit applies per session

## Permission Model

Claude Code tools require confirmation before executing. The first `claude_code_send` or `claude_code_exec` in a session requires user approval (via Mattermost reactions or web UI). Once approved, subsequent calls in the same session are auto-approved. Setup commands also require confirmation.

## Progress Reporting

During `claude_code_send`, the skill publishes `tool_status` events with richer detail:

- **Tool call count**: Each tool use is numbered — `"Tool call 5: Using Edit..."`. Counter resets per send.
- **Error snippets**: Tool failures include the tool name and first 100 chars of error text — `"Edit failed — SyntaxError: unexpected indent"`. Published as they happen, before the send completes.
- **Running cost**: Session cost updated on each SDK result — `"Session cost: $0.45 of $2.00 budget"`. Note: this is cumulative session cost, not per-send.
- **Budget warnings**: Published when session cost crosses 50%, 75%, and 90% of the session budget. Each threshold fires at most once per send.
