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
- Addressed Copilot review feedback on PR #1119:
  1. Handled deleted conversation timers in `_fire_wake` and added `cancel_all_for_conversation` wired into `http_server.py`.
  2. Blocked child agents from scheduling wake turns (`is_child` check in `tool_schedule_turn`).
  3. Ensured `scheduled_wakes.shutdown()` runs on `ConversationManager.shutdown()` even when idle (`try ... finally`).
  4. Restored transport context (`user_id`, `channel_id`, `thread_id`) on wake dispatch and added `wake_delivery` fallback routing when no per-conversation subscribers exist.
  5. Propagated `OSError` in `save_scheduled_wakes` and tools.
  6. Prevented cross-conversation cancellation in `ScheduledWakeManager.cancel`.
  7. Retained wake in sidecar and task registry until `enqueue_turn` succeeds.
- Addressed further review feedback on PR #1119:
  1. Gated Mattermost wake delivery on connection readiness (`_connected_event`) and registered the delivery listener before `connect()`.
  2. Prevented caller-supplied `metadata` from overriding scheduler-owned routing fields (`metadata` unpacked first).
  3. Made `_start_turn` and wake turn handoff cancellation-safe with rollback of `state.busy` if interrupted during startup/status publication, and shielded enqueue in `_fire_wake`.
  4. Explicitly assigned `ctx.transport = "mattermost"` in Mattermost's `context_setup` so unthreaded/DM conversations (`conv_id == channel_id`) are correctly routed and delivered.
  5. Handled queued wake routing restoration in `ConversationManager._drain_pending` from durable inbox `metadata`.
- Addressed third round of Copilot review feedback:
  1. Rejected `schedule_turn` from unattended callers (`ctx.is_unattended` or `ctx.is_child`).
  2. Removed `asyncio.shield` so cancellation of timer tasks cancels in-flight enqueues cleanly without leaving unmanaged tasks.
  3. Rendered confirmation requests and uploaded tool media during display-less / autonomous wake turns in Mattermost.
  4. Rendered autonomous wake completions directly in `interactive_terminal.py` when idle at the prompt.
- Addressed fourth round of Copilot review feedback:
  1. Included originating `conv_id` in fallback `confirmation_request` event bus payload so un-subscribed / recovered wake confirmations can be properly resolved.
  2. Forwarded `tool_media_uploaded_fallback` events to the event bus when no conversation subscriber is live, and handled them in `MattermostClient`.
  3. Added conversation-scoped wake timer recovery in `interactive_terminal.py` upon launch and shutdown cleanup on exit.
- Applied higher-level architectural redesign per user decision:
  1. Moved persistent scheduled wake storage outside the agent-writable workspace to `data/{agent_id}/scheduled_wakes/{conv_id}.json` (scoped per-conversation). Enforced trust boundary: workspace file tools cannot read or forge scheduled wakes.
  2. Classified wake turns as unattended (`background_wake` in `UNATTENDED_TASK_MODES` and `request_confirmation` fails-fast when `is_unattended=True`). Auto-approvals (allow-patterns and aux-LLM auto-approvals) proceed as normal; dangerous tools fail-fast rather than stalling unattended conversations or competing for terminal stdin.
  3. Simplified wake timer recovery to scan `data/{agent_id}/scheduled_wakes/*.json` directly.






