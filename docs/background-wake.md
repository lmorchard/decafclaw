# Background Job Agent Wake

When a background process launched by the agent exits, the runtime fires a
**synthetic agent turn** on the originating conversation so the agent can
react to its own prior work — inspect output, write results to the vault,
notify the user, or silently close the loop.

This is the agent-facing half of background-job completion. The user-facing
half (inbox bell + Mattermost DM adapter) is unchanged; see
[docs/notifications.md](notifications.md). Both fire on completion and serve
different audiences — they are independent, not mutually exclusive.

## Flow

```
shell_background_start(...) on conv X
    │  process launches, _run_reader task begins
    ▼
_run_reader polls process.wait()
    ▼
Process exits (any status — completed, error, expired, stopped)
    ▼
_finalize_job(job):
    1. notify()         — user-facing inbox notification (existing behavior)
    2. _append_background_event(job)
                        — appends "background_event" record to conv X's archive
    3. _enqueue_wake(job)
                        — calls manager.enqueue_turn(conv_id=X, kind=TurnKind.WAKE)
                              │
                              ▼
                              CM checks wake rate limiter (per-conv window)
                              If limit exceeded: drop wake, log warning
                              Otherwise: queue behind in-flight turn via busy flag
                              │
                              ▼
                              _start_turn builds Context for conv X
                              Loads full history from archive
                              ContextComposer expands background_event records
                              into synthetic tool-call + tool-result pairs
                              │
                              ▼
                              run_agent_turn with wake nudge as user-role prompt
                              Agent sees history, output, and acts normally
```

## Archive record shape

`_finalize_job` appends a `background_event` record to the conversation archive
before enqueueing the wake turn:

```json
{
  "role": "background_event",
  "timestamp": "2026-04-23T18:02:00Z",
  "job_id": "abc123def456",
  "command": "python scripts/analyze.py",
  "status": "completed",
  "exit_code": 0,
  "stdout_tail": "...last N lines joined with \\n...",
  "stderr_tail": "...last N lines joined with \\n...",
  "elapsed_ms": 123456,
  "completion_tail_lines": 50
}
```

`status` mirrors `BackgroundJob.status`: `completed`, `error`, `expired`, or
`stopped`. Both the archive record and the inbox notification fire regardless
of whether the wake turn is rate-limited.

## History rendering — tool-result framing

The `background_event` archive role is not a native LLM role. At
LLM-input-assembly time the `ContextComposer` expands each record into a
**synthetic pair** of messages:

```json
{
  "role": "assistant",
  "tool_calls": [{
    "id": "bg-wake-{job_id}",
    "type": "function",
    "function": {
      "name": "shell_background_status",
      "arguments": "{\"job_id\": \"{job_id}\"}"
    }
  }]
}
```

```json
{
  "role": "tool",
  "tool_call_id": "bg-wake-{job_id}",
  "content": "<formatted output matching live shell_background_status>"
}
```

The tool-result content uses the same shared formatter as the live
`shell_background_status` tool (header, status line, command, PID, elapsed,
exit code, stdout/stderr fences). The agent sees an identical shape whether
it polled or was woken.

**Why tool-result framing?** Tail content is untrusted process output. Tool-
role placement matches the model's "this is external, treat with skepticism"
prior — the same posture as any shell-executed data. No new custom role is
introduced; the transcript stays compatible with every LLM provider.

## `completion_tail_lines` parameter

`shell_background_start` accepts an optional `completion_tail_lines` parameter:

```python
await tool_shell_background_start(
    ctx,
    command="python scripts/analyze.py",
    completion_tail_lines=50,   # default; how many lines to store at exit
)
```

- Default: 50.
- Legal range: 0–500 (0 = no tail; 500 matches the in-memory deque ceiling).
- Values outside the range are clamped.
- Each tail (stdout, stderr) is additionally capped at **4KB** after joining.
  If the joined string exceeds 4KB, the oldest lines are dropped until it fits.
  This prevents pathological single-line output (e.g. a 50MB binary blob) from
  bloating the archive.

The in-memory deques (`_OUTPUT_BUFFER_SIZE = 500`) remain the authoritative
source for richer data via a live `shell_background_status` call during the
job's lifetime.

## `BACKGROUND_WAKE_OK` sentinel

If the agent's final text response starts with `BACKGROUND_WAKE_OK`
(case-insensitive, checked against the first 300 characters), user-facing
transports suppress the outgoing chat message. This mirrors the `HEARTBEAT_OK`
convention used by heartbeat sections.

Use it when the job result requires no user-visible action:

```
BACKGROUND_WAKE_OK — log rotation completed, 3 files rotated.
```

Tool effects during the wake turn still stand — vault writes, `notify()` calls,
further background jobs, etc. Only the outgoing chat message is suppressed. The
`message_complete` event still fires (archive, event bus, and metrics work
normally) but carries `suppress_user_message: true`. Transports check this flag
and skip posting to Mattermost, web UI chat, and terminal output.

## Rate limiting

Wakes are rate-limited per conversation to prevent a burst of rapid job
completions from flooding the agent loop.

Defaults (configurable):

| Field | Default | Description |
|-------|---------|-------------|
| `wake_max_per_window` | 20 | Maximum wakes per window per conv |
| `wake_window_sec` | 60 | Window size in seconds |

When the N+1th wake arrives within the window, it is **dropped** with a log
warning. The archive record and inbox notification still fire — only the
synthetic turn is suppressed. The rate limiter is independent of the user
circuit breaker; they track separate failure modes.

## Interaction with notification inbox

Two things fire when a background job exits:

1. **Inbox notification** (user-facing) — appended by `_finalize_job` via
   `notify()`. Visible in the web UI bell dropdown and delivered by channel
   adapters (Mattermost DM, etc.). See [docs/notifications.md](notifications.md).

2. **Agent wake turn** (agent-facing) — queued by `_finalize_job` via
   `manager.enqueue_turn(kind=TurnKind.WAKE)`. Only the agent sees this;
   transports may suppress the outgoing message if the agent responds with
   `BACKGROUND_WAKE_OK`.

Both fire unconditionally on job completion, regardless of exit status. The
rate limiter can suppress the wake turn but not the notification.

## Unified turn orchestration (architectural note)

Implementing safe wake coordination required routing **all agent turns through
`ConversationManager`**. Before this change, heartbeat sections, scheduled
tasks, and child-agent delegations called `run_agent_turn` directly, bypassing
CM's per-conversation busy flag. Wakes arriving on the same conv_id would have
raced with those turns.

After the refactor, every turn kind — user messages, heartbeat sections,
scheduled tasks, child-agent delegations, and wake turns — enters via
`manager.enqueue_turn(kind=TurnKind.WAKE)`. The `TurnKind` enum drives
per-kind policy inside `_start_turn`:

| Policy | USER | HEARTBEAT_SECTION / SCHEDULED_TASK / CHILD_AGENT | WAKE |
|--------|------|--------------------------------------------------|------|
| Circuit breaker | yes | no | no (wake-specific rate limiter) |
| Emit `user_message` event | yes | no | no |
| Build `Context.for_task` | no | yes | yes (`task_mode="background_wake"`) |
| Save per-conv state on completion | yes | no | yes |
| Suppress outgoing message if sentinel matches | no | no | yes |

From a user perspective, heartbeat, schedule, and delegate turns are
behaviorally identical to before. The change is internal: they now share CM's
serialization machinery, so wakes can safely interleave.

## Configuration

The `background` config block (in `data/{agent_id}/config.json`):

```json
{
  "background": {
    "wake_max_per_window": 20,
    "wake_window_sec": 60,
    "default_completion_tail_lines": 50
  }
}
```

Env-var overrides follow the standard pattern:

| Env var | Config field |
|---------|-------------|
| `BACKGROUND_WAKE_MAX_PER_WINDOW` | `background.wake_max_per_window` |
| `BACKGROUND_WAKE_WINDOW_SEC` | `background.wake_window_sec` |
| `BACKGROUND_DEFAULT_COMPLETION_TAIL_LINES` | `background.default_completion_tail_lines` |

`default_completion_tail_lines` is the server-side default for the
`completion_tail_lines` tool parameter when the caller doesn't pass an explicit
value. Tool-level override always takes precedence.
