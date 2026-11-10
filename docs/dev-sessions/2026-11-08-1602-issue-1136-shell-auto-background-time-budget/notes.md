# issue-1136-shell-auto-background-time-budget

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1136
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1136-shell-auto-background-time-budget
- Branch: issue-1136-shell-auto-background-time-budget
- Base: origin/main at bc86283c (PR #1235 merged)
- Next action: push + open PR (see Log for state).

## Implementation summary

- `src/decafclaw/config_types.py` — `ShellConfig.foreground_wait_budget_sec: float | None = None` (opt-in; env `SHELL_FOREGROUND_WAIT_BUDGET_SEC`) and `ShellConfig.max_runtime_sec: float = 30.0` (env `SHELL_MAX_RUNTIME_SEC`; replaces the hardcoded 30s foreground timeout).
- `src/decafclaw/skills/background/tools.py`:
  - `BackgroundJob.foreground: bool = False` — claim flag set by the budgeted shell path BEFORE it waits. While True, `_run_reader`'s finally skips `_finalize_job`, so a foreground-settled command never fires the background completion sequence. Handoff clears it synchronously on budget expiry (no await between read and write → no interleaving race), so a job that becomes background settles exactly once.
  - `BackgroundJobManager.start(env=...)` — replaces the inherited environment (None = inherit, as before); foreground path uses it to expose `DECAFCLAW_WORKSPACE` like the legacy runner.
  - `BackgroundJobManager.abort(job_id)` — kill without settling (no archive record / notification / wake); idempotent, unknown IDs no-op. Reused for both the cancelled-foreground-wait path and the foreground-win drop (kill/reader-cancel are no-ops there; it pops the job so nothing leaks).
- `src/decafclaw/tools/shell_tools.py`:
  - `tool_shell` dispatches to `_execute_command_with_handoff` when the budget is set and > 0; legacy `_execute_command` (blocking, now bounded by `max_runtime_sec`) otherwise.
  - `_execute_command_with_handoff`: function-level import of `_get_job_manager` (module-level would cycle — the background module imports `check_shell_approval` from here); fail-open to legacy if the import fails. Claims `job.foreground = True`, then races `asyncio.wait_for(job.process.wait(), budget)`:
    - Timeout → clear claim, return job-handle ToolResult (job ID / PID / status/stop pointers).
    - Exited → drain `reader_task` (5s bound; a grandchild holding the pipes past exit proceeds with captured buffers), build legacy-shaped text from the deques, drop via `abort()`.
    - CancelledError → `manager.abort()` (kills the process group) + re-raise.
  - `shell` tool description updated: unknown-duration commands go through shell; auto-backgrounding noted.
- `tests/test_shell_auto_background.py` — 13 cases (see commit message). Autouse fixture cleans the module-level `_managers` registry via `bg_tools.shutdown()` after each test.
- Evals: `evals/tool_choice/core_overlaps.yaml` gains `shell-vs-background-start-unknown-duration` (one-shot usually-fast run-and-report → shell; pins the description change against drift back to "always guess shell_background_start"). First draft ("occasionally several minutes") failed — the model defensibly picked shell_background_start, since in the default eval config no budget is set. Sharpened to "usually finishes in a few seconds" + run-and-report intent: 3/3 isolated, passed both full runs.
- Docs: `docs/tools.md` new "Auto-backgrounding on a foreground time budget (#1136)" subsection; CLAUDE.md shell bullet.

## Log

- Setup: board In progress; worktree from origin/main bc86283c; venv synced; .env copied (HTTP_PORT=18993); baseline green 5235 passed / 2 skipped.
- Spec + plan written (see spec.md / plan.md). Key decisions: reuse the background skill's runner as the shared runner (no code move); three separate knobs (wait budget ≠ total runtime limit ≠ explicit-background lifetime 600s); cancellation ownership = pre-handoff cancel kills the process, post-handoff job is manager-owned.
- Phases 1–4 implemented; new tests green on first run, stable across 5 repeats (~2s each).
- Teeth check: mutating the reader's finalize guard (dropping `and not job.foreground`) fails exactly the two no-side-effect tests; revert green.
- Gates: `make fmt` / `lint` / `typecheck` clean; full suite 5248 passed, 2 skipped (baseline +13 = new file).
- `make eval-tools`: all shell-related cases pass (incl. the new one + pre-existing background-start-vs-shell-long-runner); 0% swaps on every shell pair. The suite has ~8-9 stochastic failures in unrelated tool pairs (workspace_edit, vault_read, web_fetch, canvas, admin_write, delegate) — the failure set changed between two consecutive runs and none picked `shell`, so they're pre-existing model noise, not a regression from the shell description change.
- Committed in three units: support (config + background skill), feature (handoff + tests), docs+evals.

## Retro

- The `foreground` claim flag is the whole exactly-once story: one boolean, set before waiting, cleared synchronously on handoff. The reader's finally is the single settlement point; every path (foreground win, handoff, cancel) routes through either "finalize once" or "skip finalize + drop". No locks needed because the flag transitions happen in synchronous sections of a single-threaded loop.
- Reusing `abort()` as the foreground-win drop (instead of reaching into `manager.jobs`) kept one "kill without settling" code path; `_kill_process`'s early return for exited processes made it safe.
- Test-speed discipline held: tiny real budgets (0.1–0.2s vs `sleep`) are the mechanism under test; completion awaited via reader tasks; cancellation-wait polls with `asyncio.sleep(0)` only (yields, no wall time).
