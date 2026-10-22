# Dev Session Notes: Issues 1095, 1103, 1107

- Date: 2026-10-21
- Branch: `issue-1095-1103-1107-async-scheduling-waits`
- Worktree: `.claude/worktrees/issue-1095-1103-1107-async-scheduling-waits`

## Decisions & Discoveries
- Verified PR #1102 was already merged (`make run-dev`). Clarified with user that Issue #1103 ("Runner: First-class asynchronous turn scheduling") was intended to be tackled alongside #1095 and #1107.
- Baseline `make test` passed cleanly with 4655 passed, 2 skipped.
- Baseline `make check` passed with 0 errors.
- Phase 1 (#1107): Added `exempt_tools` (`("wait", "shell_background_status")`) to `LoopBreakerConfig`. Updated `_coerce` and `load_sub_config` in `config.py` for tuple field support. Updated `LoopBreaker.record` to bypass repeat count increments for exempt tools while continuing to track error surges. Added unit and agent-loop integration tests.
- Phase 2 (#1103): Added `scheduled_wakes.py` (`ScheduledWake`, `ScheduledWakeManager`), sidecar `.scheduled_wakes.json`, and tools `schedule_turn`, `schedule_cancel`, `schedule_list`. Wired into `ConversationManager` startup scan and shutdown. Added unit tests and startup recovery tests.
- Phase 3 (#1095): Added `## Background Operations & Lifecycle` section to `src/decafclaw/prompts/AGENT.md`. Sharpened tool descriptions for `wait`, `shell_background_start`, and `shell_background_status`. Added preemptive tool search tests and tool choice disambiguation cases in `evals/tool_choice/core_overlaps.yaml`.
- Phase 4: Updated documentation in `docs/tools.md`, `docs/background-wake.md`, `docs/loop-breaker.md`, and `CLAUDE.md`. Full test suite: 4675 passed, 2 skipped. Full `make check` passed.

