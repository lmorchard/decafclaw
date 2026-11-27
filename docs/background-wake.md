# Background Job Agent Wake

When a background process launched by the agent exits, the runtime fires a
**synthetic agent turn** on the originating conversation so the agent can
react to its own prior work — inspect output, write results to the vault,
notify the user, or silently close the loop.

This is the agent-facing half of background-job completion. The user-facing
half (inbox bell + channel adapters) fires only for jobs started on an
**unattended** turn; see [docs/notifications.md](notifications.md). A job
started in a live conversation relies on its wake turn instead, so a shell
command the user already watched does not add a bell entry (#1842).

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
    1. notify()         — user-facing inbox notification (unattended jobs only)
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
                              _build_context builds Context for conv X
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

`status` mirrors `BackgroundJob.status` (a `JobStatus` enum in
`skills/background/tools.py`): `completed`, `error`, `expired`, or
`stopped`. The archive record always fires; an unattended job also appends its
inbox notification, regardless of whether the wake turn is rate-limited.

## Scheduled Turn Wakes (`schedule_turn`)

In addition to process-exit wakes, agents can schedule future autonomous wake turns on demand via `schedule_turn(delay_seconds=..., reason=...)` ([#1103](https://github.com/lmorchard/decafclaw/issues/1103)).

These also fire as `TurnKind.WAKE` turns:
- Timers are persisted per-conversation in `data/{agent_id}/scheduled_wakes/{conv_id}.json` outside the workspace, so they survive server restarts and cannot be forged by workspace tools.
- Wake turns execute in unattended mode (`is_unattended = True`): pre-approved or aux-LLM auto-approved commands execute, while interactive confirmations fail-fast rather than hanging.
- When the timer expires, `ConversationManager.enqueue_turn(kind=TurnKind.WAKE, ...)` triggers an autonomous wake with the schedule's reason.
- During the wake turn, the agent can inspect history/output and notify the user, or reply with `BACKGROUND_WAKE_OK` to remain silent if no action is needed.
- Pending wake timers can be inspected via `schedule_list` and cancelled via `schedule_cancel(schedule_id=...)`.

## Post-Restart Wakes (`admin_restart`, #1554)

`admin_restart` can wake the conversation that requested it once the daemon is
back. Before signalling shutdown it writes
`data/{agent_id}/restart_wakes/{conv_id}.json` with the reason, requesting
conversation, transport addresses, and request time; the startup scan reads
and deletes the record before enqueueing a `TurnKind.WAKE` turn. The agent can
then verify the restart (health, MCP connections, the change it made) and
report to the user instead of leaving the turn cut off at the process exit.
Cancelling a pending restart removes the record. `wake_on_restart=false` opts
out.

## File Watch Wakes (`watch_file`, #1560)

Timer wakes are blind to *when* an external event actually happens. A file
watch waits for one: the agent arms it on a workspace file and a matching
filesystem event wakes it with a `TurnKind.WAKE` turn, the same unattended
mode as `schedule_turn`.

- **One-shot.** The watch disarms on the first matching event (`modify`,
  `create`, `delete`, or `any`), or when its timeout expires. Anything else
  requires a new `watch_file` call — so a wake cannot loop on its own.
- **Durable sidecar.** Pending watches live in
  `data/{agent_id}/file_watches/{conv_id}.json` outside the workspace.
  The baseline stat signature (mtime/size/inode) is stored with the watch, so
  the startup scan fires immediately when a file changed while the server was
  down. Expired watches are dropped; `file_watch.notify_on_timeout: true`
  turns an expiry into a wake notice instead.
- **Polling, not inotify.** A per-watch task stats the file every
  `file_watch.poll_interval_sec` (default 1s). This is a stat call — no new
  runtime dependency and no platform-specific watcher — and keeps wake
  latency within about a second.
- **Wake prompt.** `File modified: {path}. Reason: {reason}.` followed by the
  usual reminder that the agent may reply with `BACKGROUND_WAKE_OK` to stay
  silent. Metadata carries `watch_id`, `path`, and the observed event.
- **Scope.** Paths are resolved inside the workspace, and protected secret
  paths (`secret_policy`) are refused. `watch_file` is unavailable to child
  agents, heartbeat/scheduled runs, and unattended parents.

Use it for companion-app round trips: a Television artifact's
`content.json` after a user interaction, a Marimo notebook save, a build
sentinel file. `watch_list`/`watch_cancel` mirror the schedule tools.

Configuration (`config.json`, env prefix `FILE_WATCH_`):

| Field | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Global switch for arming watches |
| `poll_interval_sec` | `1.0` | Stat interval per watch |
| `max_timeout_sec` | `86400` | Longest allowed timeout (1 day) |
| `max_watches_per_conversation` | `16` | Cap on pending watches per conversation |
| `notify_on_timeout` | `false` | Wake on expiry instead of expiring silently |

## Calendar Event Wakes (#1896)

Calendar events are absolute-time one-shot reminders. They complement timer wakes and file watches: the user or agent names a calendar time instead of a relative delay or a filesystem change.

- **Durable sidecar.** Events live in `data/{agent_id}/calendar/{conv_id}.json` outside the workspace.
- **Target conversation.** An event targets its origin conversation or a dedicated web session. A dedicated session is upserted by a stable session key.
- **Firing.** At the fire time the manager enqueues a `TurnKind.WAKE` turn with the conversation history. The event is marked `fired` after the manager accepts the turn.
- **No rate-limit drop.** Calendar wakes pass `bypass_wake_rate_limit=True` because a reminder must not be dropped.
- **No sentinel suppression.** Calendar prompts do not offer `BACKGROUND_WAKE_OK`, and the turn metadata sets `suppressible=False`.
- **Non-duplicating delivery.** Calendar wakes keep the existing Mattermost `wake_delivery` fallback and write an inbox-only `calendar` notification for the Web UI.
- **Restart recovery.** The startup scan fires events that became due while the server was offline. The prompt names the original due time.

See [Calendar Events](calendar.md) for the event model, tools, and dedicated-session rules.

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

## `pin_sticky` and `progress_parser` parameters

`shell_background_start` accepts optional `pin_sticky` and `progress_parser`
parameters ([#1104](https://github.com/lmorchard/decafclaw/issues/1104), [#1137](https://github.com/lmorchard/decafclaw/issues/1137)):

```python
await tool_shell_background_start(
    ctx,
    command="python scripts/train.py",
    pin_sticky=True,  # stream live progress to the web UI sticky slot
    progress_parser="auto",  # derive structured progress from command output
)
```

With `pin_sticky=True`, the job's output is mirrored into the conversation's
sticky slot above the conversation controls as a `progress_tracker` widget
([widgets.md](widgets.md#sticky-slot)) — out-of-band over WebSocket, with no
conversational turns, no LLM tokens, and no archive writes during streaming:

- **On start:** an initial `in_progress` pin ("Running: …"), so the slot
  appears even before the first output line.
- **While running:** new output lines re-pin the tracker, throttled to at
  most one update per 0.5 s; the collapsed slot line shows the latest output
  line.
- **On exit:** the slot shows a final `done` (exit 0) or `failed` (non-zero
  exit) state with the exit code. A job that is stopped or expires clears the
  slot.

### `progress_parser` presets

When `pin_sticky=True`, the reader task applies a deterministic log output
parser (`src/decafclaw/skills/background/parsers.py`; preset names are the
`ProgressParserPreset` enum in that module) to convert streaming output lines
into structured `progress_tracker` snapshots:

- **`auto`** (default): detects output format in order (`pytest` → `step` → `percent`),
  falling back cleanly to line count and raw output on unrecognized output.
- **`step`**: extracts `[N/M]`, `(N/M)`, or `Step N of M` to display current and
  total steps with optional step labels.
- **`percent`**: extracts `N%` for percentage-based progress.
- **`pytest`**: tracks test collection (`collected N items`), per-test pass/fail
  lines (`PASSED`/`FAILED`/`SKIPPED`), and summary lines (`=== N passed... ===`).
- **`none`**: displays the raw line count and latest line without format parsing.

All parsers strip ANSI escape codes, cap regex line lengths to prevent ReDoS,
and fail open to raw line counting if an output line is malformed or shifts format.

All sticky mirroring is fail-open: a sticky failure never affects job
monitoring, the archive record, the inbox notification, or the wake turn.
Non-web transports ignore sticky events (the `sticky.json` sidecar is still
written). External scripts can push the same surface without a job:
`POST /api/sticky/{conv_id}` / `DELETE /api/sticky/{conv_id}`.

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

**The suppression is persisted, not re-derived on replay (#1852).** The final
assistant row is archived with `suppress_user_message: true` and the sentinel
stripped from its content; any trailing body stays for the expandable row. A
re-rendered web conversation shows `◇ wake · silent` as a collapsed status row,
and conversation export renders the same row. Mattermost has no transcript
re-render; its thread-fork history comes from `restore_history`, which reads
the normalized row. The provider-history assembler drops the archive-only flag
before an LLM call. Compaction skips `wake_trigger` rows (the synthetic prompt
contains the literal sentinel) and summarizes the stripped body, so the model
never learns the marker from its own summary. Archives written before the flag
existed are normalized on read using the surrounding `wake_trigger` turn, not a
bare text match outside a wake.

## Rate limiting

Wakes are rate-limited per conversation to prevent a burst of rapid job
completions from flooding the agent loop.

Defaults (configurable):

| Field | Default | Description |
|-------|---------|-------------|
| `wake_max_per_window` | 20 | Maximum wakes per window per conv |
| `wake_window_sec` | 60 | Window size in seconds |

When the N+1th wake arrives within the window, it is **dropped** with a log
warning. The archive record still fires, and an unattended job's inbox
notification still fires — only the synthetic turn is suppressed. The rate
limiter is independent of the user circuit breaker; they track separate
failure modes.

## Interaction with notification inbox

A background job exit has two potential user-facing paths:

1. **Inbox notification** — appended by `_finalize_job` via `notify()` only
   when the job started on an unattended turn. Visible in the web UI bell
   dropdown and delivered by channel adapters (Mattermost DM, etc.). See
   [docs/notifications.md](notifications.md).

2. **Agent wake turn** — queued by `_finalize_job` via
   `manager.enqueue_turn(kind=TurnKind.WAKE)`. Only the agent sees this;
   transports may suppress the outgoing message if the agent responds with
   `BACKGROUND_WAKE_OK`.

The wake turn fires for both kinds, regardless of exit status. Attended jobs
skip the inbox because the wake turn already reports the result in the live
conversation; a bell entry there is duplicate noise (#1842). The rate limiter
can suppress the wake turn, but it never changes whether the inbox record is
written.

## Unified turn orchestration (architectural note)

Implementing safe wake coordination required routing **all agent turns through
`ConversationManager`**. Before this change, heartbeat sections, scheduled
tasks, and child-agent delegations called `run_agent_turn` directly, bypassing
CM's per-conversation busy flag. Wakes arriving on the same conv_id would have
raced with those turns.

After the refactor, every turn kind — user messages, heartbeat sections,
scheduled tasks, child-agent delegations, and wake turns — enters via
`manager.enqueue_turn(kind=TurnKind.WAKE)`. The `TurnKind` enum drives
per-kind policy across `_start_turn` / `_build_context` (#1347):

| Policy | USER | HEARTBEAT_SECTION / SCHEDULED_TASK / CHILD_AGENT | WAKE |
|--------|------|--------------------------------------------------|------|
| Circuit breaker | yes | no | no (wake-specific rate limiter) |
| Emit `user_message` event | yes | no | no |
| Build `Context.for_task` | no | yes | yes (`task_mode=TaskMode.BACKGROUND_WAKE`) |
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
