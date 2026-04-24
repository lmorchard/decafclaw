# Background Job Agent Wake — Spec

Closes #241.

## Problem

Background process tools (`shell_background_start/status/stop/list`) are poll-based. An agent that launches a background job has to actively call `shell_background_status` to learn whether the job has finished. There is no agent-facing signal when a process exits.

Phase 1 of #292 added a user-facing notification when a background job completes (inbox bell + Mattermost DM channel adapter). That's the user alert. The agent still has to poll.

This spec closes the agent-alert loop: when a background job exits, the agent that launched it is explicitly woken with a synthetic turn on the originating conversation so it can react to its own prior work.

## Goals

1. When a background process exits, fire a **synthetic agent turn** on the conversation that launched it, *within that conversation's history and context*. The agent sees its own prior tool calls, the user's original ask, and the new completion event.
2. Apply uniformly to **all conversation types** — interactive user convs (Mattermost, web UI, terminal), heartbeat sections, scheduled tasks, and child-agent delegations. A heartbeat that launches 5 parallel log-audits gets 5 continuation turns on its own `heartbeat-{ts}-{i}` conv_ids.
3. Treat completion output as **untrusted tool-result content** (prompt-injection posture), not authoritative system text.
4. Let the agent **silently end** a wake turn if the job result doesn't warrant a user-visible message (following heartbeat's `HEARTBEAT_OK` pattern).

## Non-goals

- Changing the user-facing inbox notification behavior (Phase 1). It stays as-is.
- Adding channel-adapter changes (email, Slack, etc.). Out of scope.
- Coalescing multiple rapid wakes on the same conv into a single turn. v1 fires one wake per completion and relies on the turn-queue to serialize; revisit if noisy.
- Wake-on-status-change for other long-running things (MCP servers, tools with progress events). This spec is scoped to `shell_background_start` jobs.

## Scope note — unification of turn orchestration

Doing this safely requires a prerequisite refactor: **all agent turns must route through `ConversationManager`.** Today, heartbeat, scheduled tasks, and child-agent delegations bypass CM and call `run_agent_turn` directly. For wakes to coordinate safely with those turns (same conv_id → same busy flag), they must share CM's turn-serialization machinery. So this spec includes the migration of heartbeat / schedules / delegate to go through CM.

This was a latent architectural split that this issue surfaces; fixing it is cleaner than working around it. Scope increase acknowledged up front.

## Design

### Flow

```
shell_background_start(...) on conv X
    │  launches process, _run_reader task starts
    ▼
_run_reader waits for exit
    ▼
Process exits
    ▼
Post-exit hook (ordered):
    1. Append `background_event` record to conv X's archive.
    2. Emit inbox notification (existing Phase 1 behavior; unchanged).
    3. Call ConversationManager.enqueue_turn(
           conv_id=X,
           kind=TurnKind.WAKE,
           prompt=<wake nudge>,
           metadata={"job_id": ...},
       )
         │
         ▼
         CM respects the per-conv `busy` flag — queues behind any
         in-flight turn, serializes with subsequent wakes.
         │
         ▼
         _start_turn builds Context.for_task(
             task_mode="background_wake", conv_id=X, ...
         )
         Loads history from conv X's archive (background_event records
         expand into synthetic tool-call + tool-result pairs).
         Runs run_agent_turn with the wake nudge as the user-role prompt.
         │
         ▼
         Agent turn runs normally.
         Output events stream to per-conv subscribers if any.
         If the agent's response starts with BACKGROUND_WAKE_OK,
         transports suppress the user-facing message.
```

### The `background_event` archive record

New archive role. Written by `_run_reader` post-exit via `archive.append_message(...)`:

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

`status` mirrors `BackgroundJob.status`: one of `completed`, `error`, `expired`, `stopped`.

Each tail is built by taking the last `completion_tail_lines` items from the in-memory deque (capped at `_OUTPUT_BUFFER_SIZE = 500`) and joining with `\n`. If the joined string exceeds a 4KB hard ceiling, the oldest lines are dropped until it fits. This prevents pathological single-line output (e.g. a 50MB binary blob on one line) from bloating the archive. The 500-item in-memory deques remain the authoritative source of richer data via `shell_background_status`.

### `completion_tail_lines` parameter

`shell_background_start` gains an optional parameter, default 50:

```python
async def tool_shell_background_start(
    ctx,
    command: str,
    completion_tail_lines: int = 50,
) -> ToolResult: ...
```

The value rides on `BackgroundJob` and is read by `_run_reader` when constructing the archive record. Legal range: 0–500 (0 = no tail; 500 matches the deque ceiling). Values outside the range clamp.

### History rendering — tool-result pair

Expansion happens at **LLM-input-assembly time** (`ContextComposer`), not at archive-read time. `archive.restore_history` continues returning raw records including any `background_event` entries. When the composer builds the message list for an LLM call, each `background_event` record expands into a pair of standard messages:

```
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

{
  "role": "tool",
  "tool_call_id": "bg-wake-{job_id}",
  "content": "<formatted output matching tool_shell_background_status>"
}
```

The tool-result `content` is produced by a shared formatter extracted from the current `tool_shell_background_status` implementation (header, status line, command, PID, elapsed, exit code, stdout/stderr fences). Both the live tool and the wake-time rendering call the same helper, so the agent sees an identical shape whether it polled or was woken. Extraction of this helper into a reusable function is a small refactor included in Phase 5.

Rationale for this framing:
- Tail content is untrusted process output; tool-role placement matches the model's "this is external, treat with skepticism" prior.
- No new custom role introduced — transcript stays compatible with every provider.
- Agent reads history as "I polled and got this result," which is morally what happened (runtime polled on its behalf).

The synthetic `tool_call_id` is namespaced with `bg-wake-{job_id}` — no collision with real tool_call_ids (which are provider-assigned opaque tokens).

### Wake-turn trigger prompt

The user-role prompt that actually starts the wake turn is short and carries no untrusted content:

```
A background job you started has completed. Its status and output are
in your history above. Review the result and take any follow-up action
(respond to the user, call other tools, or reply with
BACKGROUND_WAKE_OK to end the turn silently if no action is needed).
```

### `BACKGROUND_WAKE_OK` silent-end sentinel

If the agent's final text response starts (case-insensitive, first 300 chars) with `BACKGROUND_WAKE_OK`, user-facing transports suppress the chat message. Modeled on `is_heartbeat_ok(response)` in `heartbeat.py` — a parallel `is_background_wake_ok(response)` helper lives next to it.

Tool effects during the turn (vault writes, `notify()` calls, further background jobs, etc.) still stand — only the outgoing chat message is suppressed. The `message_complete` event is still emitted (so subscribers, archive, and event bookkeeping work normally) but carries a `suppress_user_message: True` field (set by CM when the turn is `kind=WAKE` and the text matches the sentinel). Transports check this field on `message_complete` and skip posting to Mattermost / web UI chat / terminal accordingly.

### Unification — ConversationManager refactor

**New concept: `TurnKind`.** Added alongside `ConversationState` in `conversation_manager.py`:

```python
from enum import Enum

class TurnKind(Enum):
    USER = "user"
    HEARTBEAT_SECTION = "heartbeat_section"
    SCHEDULED_TASK = "scheduled_task"
    CHILD_AGENT = "child_agent"
    WAKE = "wake"
```

**New public API on `ConversationManager`:**

```python
async def enqueue_turn(
    self,
    conv_id: str,
    *,
    kind: TurnKind,
    prompt: str,
    history: list | None = None,       # None → load from archive
    task_mode: str | None = None,
    context_setup: Callable | None = None,
    user_id: str = "",
    archive_text: str = "",
    attachments: list[dict] | None = None,
    command_ctx: Any = None,
    wiki_page: str | None = None,
    metadata: dict | None = None,
) -> asyncio.Future:
    """Submit a turn of any kind. Returns an awaitable that resolves
    when the turn completes (so callers that need results can await)."""
```

`send_message` becomes a thin wrapper: `await self.enqueue_turn(conv_id, kind=TurnKind.USER, prompt=text, ...)`. Behavior preserved bit-for-bit.

**Per-kind policy matrix** (centralized inside `_start_turn`):

| Policy | USER | HEARTBEAT_SECTION / SCHEDULED_TASK / CHILD_AGENT | WAKE |
|---|---|---|---|
| Circuit breaker check on start | yes | no | no (uses wake-specific limiter) |
| Circuit-breaker `record()` on completion | yes | no | no |
| `pending_messages` queueing semantics | full (cancel-on-new-message option applies) | queue behind in-flight turn | queue behind in-flight turn |
| Emit `user_message` event | yes | no | no |
| Build `Context.for_task` (vs plain `Context`) | no | yes (task_mode=kind.value or caller-supplied) | yes (`task_mode="background_wake"`) |
| Save per-conv state on completion | yes | no | yes |
| Mark as wake-suppressible in message_complete event | no | no | yes (transports read and suppress if prefix matches) |

**Wake-specific rate limiter.** Independent of the user circuit breaker. Tracks wakes per conv per window. Configurable:

```python
# config_types.py — new sub-dataclass field on an existing config area
@dataclass
class BackgroundConfig:
    wake_max_per_window: int = 20
    wake_window_sec: int = 60
```

When exceeded, the N+1th `enqueue_turn(kind=WAKE)` is dropped with a logged warning. The inbox notification still fires upstream (different code path); the archive record still gets written (already done before `enqueue_turn` is called). Only the synthetic turn is suppressed.

### Migration of existing callers

**`heartbeat.py::run_section_turn`** — currently:

```python
ctx = Context.for_task(config, event_bus, ...)
result = await run_agent_turn(ctx, prompt, history=[])
```

Becomes:

```python
future = await manager.enqueue_turn(
    conv_id=f"heartbeat-{timestamp}-{index}",
    kind=TurnKind.HEARTBEAT_SECTION,
    prompt=build_section_prompt(section),
    history=[],
    task_mode="heartbeat",
    metadata={"source": section.get("source", "workspace")},
)
result = await future
```

Heartbeat continues to process the result (HEARTBEAT_OK detection, per-section notifications). The fact that the turn is now orchestrated by CM instead of fired directly is transparent to heartbeat's outer loop.

**`schedules.py::run_schedule_task`** — same pattern with `kind=TurnKind.SCHEDULED_TASK`, `task_mode="schedule"`.

**`tools/delegate.py::delegate_task`** — same pattern with `kind=TurnKind.CHILD_AGENT`, `task_mode="child_agent"`. Caller awaits the future; child-agent lifetime is unchanged.

### Hook point in `_run_reader`

`src/decafclaw/skills/background/tools.py`, in `_run_reader`'s `else:` clause (process exited cleanly, not cancelled), after `_notify_job_exit(job)`:

```python
await _append_background_event(job)
await _enqueue_wake(job)
```

`_append_background_event(job)` — appends the archive record. Imports `archive.append_message` and a new formatter that pulls `stdout_tail` / `stderr_tail` from the deques and clamps to the job's `completion_tail_lines` + 4KB ceiling.

`_enqueue_wake(job)` — reads the `ConversationManager` from `job.config` (new field, plumbed in alongside the existing `config`/`conv_id`/`event_bus`), and calls `manager.enqueue_turn(...)`. Fail-open: if the manager is missing (e.g. legacy code path, test fixture), log a warning and skip the wake. The archive record and inbox notification both landed first, so no data is lost.

Also — `cleanup_expired()` and `stop()` paths need the same treatment: a job that gets killed due to max-lifetime or a manual stop should also fire a wake. Unified by calling `_append_background_event` + `_enqueue_wake` at the point where `job.status` transitions out of `"running"`, regardless of exit cause. Extract a small helper `_finalize_job(job)` that wraps the three post-exit actions (notify, append event, enqueue wake) and call it from all terminal paths.

## Data structures

- `BackgroundJob.completion_tail_lines: int = 50` — new field, default matches the tool parameter.
- `BackgroundJob.manager: Any = None` — new field, set by `BackgroundJobManager.start()` from `ctx.manager` (see plumbing below).
- `ConversationState.pending_messages` — stays as the name (minimal diff, avoid a cosmetic rename), but semantics widen: each entry carries its `TurnKind` alongside the existing fields. The dict shape gains a `"kind": TurnKind` field.
  - `_drain_pending` behavior: if the queue contains a contiguous run of `USER` kinds at the head, they are combined into a single next turn (current behavior preserved). If the head is any other kind, or if a USER run is followed by a non-USER entry, the drain fires turns one at a time in FIFO order — no cross-kind combining. This keeps the rapid-user-typing case unchanged while correctly handling interleaved wakes.
- `TurnKind` — new enum.
- Archive record role `background_event` — new.

## Configuration

New config section (likely on an existing `BackgroundConfig` or as a new one):

```json
{
  "background": {
    "wake_max_per_window": 20,
    "wake_window_sec": 60,
    "default_completion_tail_lines": 50
  }
}
```

Env-var overrides follow the existing pattern.

## Plumbing concerns

### `ctx.manager` reference

`BackgroundJobManager.start()` takes `manager` as an explicit argument (alongside the existing `config`, `conv_id`, `event_bus`). `tool_shell_background_start` passes `ctx.manager`. The `Context` gains a `manager: ConversationManager | None = None` field, set by `ConversationManager._start_turn` when it builds the Context. Heartbeat and scheduled tasks that currently build Context directly will, after migration, have their Context built by CM — so `ctx.manager` is consistently populated.

### Runner wiring

`runner.py` already instantiates `ConversationManager` at startup. All turn-firing sites need a reference to it. For heartbeat / schedules, this means `run_heartbeat_timer` / `run_schedule_timer` gain a `manager` parameter. For `delegate_task`, the parent agent's `ctx.manager` is the source.

## Testing

Per CLAUDE.md: test speed discipline. Avoid `asyncio.sleep`; patch `run_agent_turn` rather than firing real scheduled work.

### Unit tests

- **CM `enqueue_turn` per-kind policy matrix.** For each `TurnKind`, verify: circuit-breaker behavior, event emission, Context construction flags, queuing behavior.
- **Archive record expansion.** Given a history containing `background_event` records, verify `restore_history` / context composer produces the expected `assistant`+`tool` pair.
- **Wake rate limiter.** Fire N+1 wakes in the window; assert N+1th is dropped; assert archive + inbox still fire.
- **`BACKGROUND_WAKE_OK` sentinel detection.** Function parallel to `is_heartbeat_ok`.
- **`completion_tail_lines` parameter clamping and archive-record truncation** (4KB ceiling).
- **Migration tests for heartbeat / schedules / delegate.** Refactored callers still produce equivalent results. Existing heartbeat-cycle / schedule-run / delegate-task test fixtures adapt; no behavior regressions.

### Integration tests

- **End-to-end wake.** User-mode conv starts a background job (mocked subprocess). Job exits. Assert:
  1. Inbox record appears.
  2. Archive contains `background_event` record.
  3. CM fires a wake turn (verify via a mock `run_agent_turn`).
  4. Wake turn's context includes the expanded tool-call pair in history.
  5. If agent's response starts with `BACKGROUND_WAKE_OK`, transport subscriber receives suppression signal.

- **Heartbeat-originated wake.** Heartbeat section starts a background job, section ends. Job exits. Assert the wake fires on the `heartbeat-{ts}-{i}` conv_id (not on any user conv).

- **Wake while user is mid-turn.** User turn in progress (`busy=True`). Wake fires → queues. User turn completes → wake fires. Order preserved.

### Live smoke (post-implementation)

- `make dev`, start a short-running background job in the web UI, watch the agent wake and respond.
- Same in Mattermost.
- Same via heartbeat: contrive a heartbeat section that `shell_background_start`s a `sleep 10 && echo done`; verify the continuation turn fires and the agent responds.

## Acceptance criteria

- All existing tests pass (heartbeat, schedules, delegate, background, confirmation, etc.).
- New unit and integration tests added per the testing section all pass.
- `make check` and `make test` green.
- Live smoke in web UI and Mattermost shows agent receiving and acting on wake turns.
- The agent can silently end a wake turn with `BACKGROUND_WAKE_OK`, and user-facing transports honor it.
- Heartbeat / scheduled / child-agent turns still behave identically from a user perspective after the CM migration.

## Files changed (anticipated)

- `src/decafclaw/conversation_manager.py` — `TurnKind`, `enqueue_turn`, policy matrix, pending-turns semantics.
- `src/decafclaw/context.py` — `manager` field on Context.
- `src/decafclaw/heartbeat.py` — migrate `run_section_turn` to CM.
- `src/decafclaw/schedules.py` — migrate `run_schedule_task` to CM.
- `src/decafclaw/tools/delegate.py` — migrate child-agent launch to CM.
- `src/decafclaw/skills/background/tools.py` — `completion_tail_lines` param, `BackgroundJob.completion_tail_lines`, `BackgroundJob.manager`, `_finalize_job` helper, reader/stop/cleanup call sites.
- `src/decafclaw/archive.py` — new archive record role recognition (if anything besides passthrough is needed).
- `src/decafclaw/context_composer.py` (or wherever history is built) — `background_event` → tool-call-pair expansion.
- `src/decafclaw/config_types.py` — `BackgroundConfig` extension.
- `src/decafclaw/mattermost_display.py`, `src/decafclaw/web/websocket.py`, `src/decafclaw/interactive_terminal.py` — `BACKGROUND_WAKE_OK` suppression on message_complete for wake turns.
- `src/decafclaw/runner.py` — plumb `manager` through heartbeat/schedules wiring.
- `tests/...` — new and updated tests.
- `docs/background-wake.md` — new doc page.
- `docs/notifications.md` — cross-link.
- `docs/index.md` — link to new doc.
- `CLAUDE.md` — architecture note on unified turn orchestration; key files update if needed.

## Phase plan (one PR, one commit per phase)

1. **CM refactor.** Introduce `TurnKind` and `enqueue_turn`. Preserve `send_message` behavior. Unit tests for each kind.
2. **Migrate heartbeat** to `enqueue_turn`. Tests pass, no behavioral change.
3. **Migrate schedules** to `enqueue_turn`. Tests pass, no behavioral change.
4. **Migrate delegate** to `enqueue_turn`. Tests pass, no behavioral change.
5. **Archive record + history rendering.** New `background_event` role, expansion into synthetic tool-call pair, unit tests.
6. **Wake dispatch.** `_finalize_job` helper, wake rate limiter, `completion_tail_lines` parameter, `BACKGROUND_WAKE_OK` sentinel and transport suppression.
7. **Docs + CLAUDE.md.** New `docs/background-wake.md`, cross-links, conventions notes.
8. **Integration + smoke polish.** End-to-end test pass; live smoke notes in session `notes.md`.
