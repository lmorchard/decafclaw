# Spec: Shell auto-backgrounding on foreground time budget (#1136)

## Problem

`tool_shell` runs commands with a blocking `subprocess.run(timeout=30)`: the event
loop is blocked for the whole command, and a command that unexpectedly runs long is
**killed at 30s** with an error — the agent must then rerun it via
`shell_background_start`, re-approving and restarting from scratch. The agent has to
*predict* foreground vs background up front.

## Intended result

An opt-in **foreground waiting budget**: `tool_shell` starts the command through the
same managed runner as background jobs and waits up to the budget.

- Command finishes within the budget → ordinary foreground result (output text, same
  shape as today). No job handle, no wake.
- Budget expires → the **same process** continues as a managed background job; the tool
  returns a job handle (job ID, PID) with pointers to `shell_background_status` /
  `shell_background_stop`. The existing completion path (archive record + inbox
  notification + WAKE turn) fires exactly once when it exits.

Approval happens **once, before process creation** (existing `check_shell_approval`);
the handoff neither bypasses nor repeats it.

## Design decisions

### Shared managed command runner (no code move)

The "shared runner" is the background skill's existing machinery:
`BackgroundJobManager.start()` (spawn via `asyncio.create_subprocess_shell`,
`start_new_session=True`) + `_run_reader` (bounded output deques, exit capture) +
`_finalize_job` (exactly-once completion sequence). `tool_shell` reuses it via a
function-level import of `_get_job_manager` from `decafclaw.skills.background.tools`
(the reverse-direction import already exists: that module imports
`check_shell_approval` from `shell_tools`, so a module-level import would cycle).

One process is spawned per command, on every path — handoff cannot restart it.

### Config (ShellConfig)

- `foreground_wait_budget_sec: float | None = None` — **opt-in**. `None`/`<=0` →
  today's blocking behavior, unchanged. When set, foreground commands race the budget.
- `max_runtime_sec: float = 30.0` — the command's **total runtime limit** (replaces
  the hardcoded 30 in `_execute_command`). For a handed-off job this becomes the job's
  `max_lifetime`, so the existing expiry policy (`cleanup_expired`) enforces it.

Three distinct knobs, per the issue: wait budget (new) ≠ total runtime limit
(configurable now) ≠ background-job expiry policy (existing `max_lifetime` default 600s
for explicit `shell_background_start`, unchanged).

### Atomic handoff boundary (one process, one final result, one completion)

`BackgroundJob` gains `foreground: bool = False`. In the budgeted foreground path:

1. `job = await manager.start(...)` (approval already done).
2. **Claim before waiting:** `job.foreground = True`.
3. `await asyncio.wait_for(job.process.wait(), timeout=budget)`:
   - **Timeout (handoff wins):** set `job.foreground = False` synchronously, return the
     job-handle result. The reader's `finally` later sees `foreground=False` and runs
     the normal `_finalize_job` → exactly one archive record + notification + wake.
   - **Process exits first (foreground wins):** await `job.reader_task` briefly so the
     output deques are fully drained (the reader sets `exit_code`); its `finally` sees
     `foreground=True` and **skips** `_finalize_job`. Build the foreground result from
     the deques (same text shape as today: stdout, `[stderr]`, `[exit code: N]`), drop
     the job from the manager (it was never a background job). Zero wakes, zero records.
   - **CancelledError (turn cancelled during the wait):** no handoff happened, so the
     process must not be left unmanaged — kill the process group, cancel + await the
     reader (its `finally` skips finalize for a cancelled reader and closes the
     transport), drop from manager, re-raise. The tool result becomes the existing
     `[cancelled: shell]` via `execute_single_tool`. No archive record, no wake.

The event loop is single-threaded and the flag transitions happen in synchronous
sections (no `await` between read and write), so there is no interleaving race: a
completion at the boundary settles exactly one way.

### Cancellation ownership (defined)

- **Stopping a foreground wait** = turn cancellation *before* handoff → the process is
  killed with the turn (above). It never becomes a job.
- **Stopping a job** = explicit `shell_background_stop` (or expiry) *after* handoff.
  Turn cancellation after handoff does not touch the job — it is owned by the manager,
  same as an explicitly started background job.

### Failure paths leave no orphans

- Approval denied → nothing spawned (existing path).
- Spawn failure (`create_subprocess_shell` raises, e.g. resource exhaustion) → error
  result; no process exists.
- Cancellation during wait → killed (above).
- Total runtime limit → handed-off job's `max_lifetime` + existing `cleanup_expired`.
- Shutdown → existing `shutdown()`/`cleanup_all`.

### Output collection stays bounded

Foreground results are built from the same 500-line deques background jobs use
(`_OUTPUT_BUFFER_SIZE`), so a chatty short command can no longer produce an unbounded
tool result. Remaining collection-limit work (UI payloads, transcript truncation) is
#922's scope; this change only guarantees the shared buffers are used.

### LLM-visible changes

- `shell` tool description: note that commands exceeding the configured foreground
  budget are automatically moved to background with a job ID (only when enabled).
- Handoff result text: job ID, PID, command, and the two follow-up tools.
- `tool_choice` eval case guarding shell vs shell_background_start selection.

## Acceptance mapping

| Criterion | Where |
|---|---|
| Below budget → normal result | test: `echo` with generous budget; assert output, no job in manager, no wake |
| Above budget → usable job ID, same process, inspectable/stoppable | test: `sleep` + tiny budget; assert handle, PID alive, status works, stop works |
| Executes once across handoff; races don't lose/duplicate results/wakes | side-effect counter file (one line); race tests both directions assert exactly 0 or 1 record/wake |
| Denial/exhaustion/cancellation/total-timeout/shutdown → no orphans, no transport warnings | tests per path; `PytestUnraisableExceptionWarning` is already promoted to error by pytest config |
| Deterministic tests (signals/patched clock); bounded evals for LLM-visible changes | tiny real budgets + `sleep` commands (the timeout is the mechanism under test); no fixed sleeps waiting on work — completion awaited via `reader_task`/events; tool_choice eval |

## Out of scope

- #922 (bounding UI payloads / transcript truncation) — coordinated, separate.
- Argument-dependent or per-command budget overrides.
- Periodic expiry sweep (existing on-demand `cleanup_expired` policy is reused as-is).
