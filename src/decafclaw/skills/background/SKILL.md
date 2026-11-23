---
name: background
description: Start, monitor, and stop long-running background processes (servers, watchers, builds). Returns immediately with a job ID you can poll for output.
auto-approve: true
always-loaded: true
---

# Background processes

Use this skill when you need to run something long-running without blocking the conversation — a dev server, a file watcher, a long build, or any command running longer than a few seconds.

## Workflow

1. `shell_background_start(command)` returns a `job_id` immediately. Use for commands running longer than a few seconds. The command runs under shell approval (same rules as the regular `shell` tool).
2. `shell_background_status(job_id)` polls output and status to monitor progress. Pair with the `wait` tool for in-turn pauses:
   - `wait(seconds=5)` then `shell_background_status(job_id)`, repeat.
   - For long tasks spanning across turns, schedule a check using `schedule_turn(delay_seconds=..., reason=...)`.
3. `shell_background_stop(job_id)` terminates the process (SIGTERM, then SIGKILL after 2s).
4. `shell_background_list()` shows all jobs in the current conversation.

## Limits

- Jobs expire after `background.default_max_lifetime_sec` (default 1 hour). Pass `max_lifetime_sec` to `shell_background_start` to override it; `0` disables expiry for a run whose duration you cannot bound. Expiry signals SIGTERM, then SIGKILL after 2s.
- `cwd` selects the starting directory inside the workspace. An invalid path fails before the process starts instead of silently running at the workspace root.
- On expiry or `shell_background_stop`, the partial stdout/stderr captured so far stays available through `shell_background_status` and the completion record. If nothing was captured, the status says so explicitly. Avoid piping a long job through `tail` (or similar): the pipe buffers until EOF, so a killed run loses the buffered lines.
- stdout/stderr buffers hold the last 500 lines each.
- Per-conversation job manager — jobs don't leak across conversations.
