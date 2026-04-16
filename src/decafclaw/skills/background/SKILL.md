---
name: background
description: Start, monitor, and stop long-running background processes (servers, watchers, builds). Returns immediately with a job ID you can poll for output.
auto-approve: true
---

# Background processes

Use this skill when you need to run something long-running without blocking the conversation — a dev server, a file watcher, a long build.

## Workflow

1. `shell_background_start(command)` returns a `job_id` immediately. The command runs under shell approval (same rules as the regular `shell` tool).
2. `shell_background_status(job_id)` polls output and status. Pair with the `wait` tool to avoid burning iterations in a tight loop:
   - `wait(seconds=5)` then `shell_background_status(job_id)`, repeat.
3. `shell_background_stop(job_id)` terminates the process (SIGTERM, then SIGKILL after 2s).
4. `shell_background_list()` shows all jobs in the current conversation.

## Limits

- Jobs auto-expire after 10 minutes.
- stdout/stderr buffers hold the last 500 lines each.
- Per-conversation job manager — jobs don't leak across conversations.
