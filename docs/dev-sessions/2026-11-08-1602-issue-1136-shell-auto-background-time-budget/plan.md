# Plan: #1136 shell auto-backgrounding

Phases commit in order; `make fmt` + targeted tests before each commit.

## Phase 1 — Config knobs
- `config_types.py` `ShellConfig`: add `foreground_wait_budget_sec: float | None = None`
  (opt-in; env `SHELL_FOREGROUND_WAIT_BUDGET_SEC`) and `max_runtime_sec: float = 30.0`
  (env `SHELL_MAX_RUNTIME_SEC`).
- `shell_tools._execute_command`: replace hardcoded `timeout=30` with
  `ctx.config.shell.max_runtime_sec`.
- Tests: config resolution (default + env) — extend existing shell config tests if any,
  else assert in the new test file.

## Phase 2 — Background skill: foreground claim + abort
- `BackgroundJob`: add `foreground: bool = False` (claimed by the foreground shell path
  before it starts waiting; suppresses `_finalize_job` in the reader's `finally`).
- `_run_reader.finally`: `if not cancelled and not job.foreground: await _finalize_job(job)`.
- `BackgroundJobManager.start`: add optional `env: dict | None = None` passed to
  `create_subprocess_shell` (foreground path needs `DECAFCLAW_WORKSPACE`; default None
  keeps explicit-background behavior).
- `BackgroundJobManager.abort(job_id)`: kill process group (`_kill_process`), cancel +
  await reader, drop from `self.jobs`. No archive record / notification / wake. For the
  cancelled-foreground-wait path (and any future "kill without settling" need).

## Phase 3 — Foreground handoff in shell_tools
- `tool_shell`: after approval, if `foreground_wait_budget_sec` is set and > 0 →
  `_execute_command_with_handoff(ctx, command)`, else legacy `_execute_command`.
- `_execute_command_with_handoff`:
  - function-level import of `_get_job_manager` (module-level would cycle); fail-open to
    legacy path with a warning if the import itself fails.
  - `manager.start(command, workspace, max_lifetime=shell.max_runtime_sec, env={...
    DECAFCLAW_WORKSPACE}, config/conv_id/event_bus/manager from ctx,
    completion_tail_lines=background.default_completion_tail_lines)`.
  - claim `job.foreground = True`; race `asyncio.wait_for(job.process.wait(), budget)`:
    - Timeout → `job.foreground = False`; return job-handle ToolResult (text: moved to
      background, job ID, PID, command, status/stop pointers; data: job_id/status/pid).
    - Exited → drain `job.reader_task` (5s safety; on timeout proceed with buffers and
      set exit_code from `process.returncode` if unset); build foreground text from the
      deques (stdout, `[stderr]`, `[exit code: N]` — same shape as legacy); drop job from
      manager; return.
    - CancelledError → `manager.abort(job.job_id)`; re-raise.
  - Spawn failure from `manager.start` → `[error: failed to start command: ...]`.
- Update the `shell` tool description (LLM-visible): long commands auto-background when
  a foreground budget is configured.

## Phase 4 — Tests (`tests/test_shell_auto_background.py`)
Fixture: `ctx` + budget config; clean `_managers[conv_id]` after each test.
1. below_budget_returns_normal_result — `echo hi`, budget 5s: output present, manager
   empty afterwards, no wake (patch `enqueue_turn`), no background_event in archive.
2. above_budget_returns_job_handle_same_process — `sleep 3` + budget 0.1: handle has
   job_id/pid; process alive (os.kill(pid, 0)); `shell_background_status` shows running;
   side-effect file written exactly once (command appends a line before sleeping).
3. handoff_completion_fires_exactly_once — `sleep 0.2` + budget 0.1: await
   `job.reader_task`; assert one background_event record, one wake enqueue (patched),
   one notification (patched).
4. foreground_completion_fires_nothing — `sleep 0.1` + budget 2: normal result; zero
   wakes/records; manager empty.
5. approval_denied_spawns_nothing — patch `check_shell_approval` denied: error result,
   side-effect file absent.
6. spawn_failure_no_orphan — patch `asyncio.create_subprocess_shell` to raise OSError:
   error result, manager empty.
7. cancellation_during_wait_kills_process — `sleep 5` + budget 10: create task, wait
   until job appears in manager, cancel the task; assert process dead (pid gone), no
   record/wake, CancelledError surfaced.
8. handed_off_job_uses_max_runtime — job.max_lifetime == shell.max_runtime_sec (patched
   small) and `cleanup_expired` kills it.
9. legacy_path_unchanged — budget None: `_execute_command` used (patch it / assert
   blocking path via a marker), timeout from max_runtime_sec.

## Phase 5 — Evals + docs
- `evals/tool_choice/`: case where a long-running task should use `shell` (auto-
  backgrounds) — check existing shell cases first for the right shape.
- `docs/tools.md` shell section: auto-backgrounding behavior + config knobs +
  cancellation ownership.
- `CLAUDE.md`: one line in the shell-approval bullet area (convention-level: budget is
  opt-in, handoff reuses the background manager).
- Dev session notes + retro.

## Verification
- `make fmt`, `make lint`, `make typecheck`.
- New test file + existing shell/background tests:
  `tests/test_shell_tools.py` (if exists), background skill tests, test_agent_turn.
- Full `make test`.
- `make eval-tools` (tool description changed).
