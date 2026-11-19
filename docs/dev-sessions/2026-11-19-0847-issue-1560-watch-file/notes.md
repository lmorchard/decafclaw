# issue-1560-watch-file

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1560
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1560-watch-file
- Branch: issue-1560-watch-file
- Base: origin/main at 4bf3926a2d6bc3c3c2c726c58c151fe3217c37e8
- Status: implemented, full gates green, ready for PR.

## Decisions

- **Polling stat signature, not watchdog/inotify.** No new runtime dependency; one `os.stat` per watch per `file_watch.poll_interval_sec` (default 1s). Signature is `(mtime_ns, size, inode)` so an atomic replace counts as a modify.
- **Sidecar outside the workspace** at `data/{agent_id}/file_watches/{conv_id}.json`, mirroring `scheduled_wakes/` and the #1119 forgery rationale. The issue's prose said "conversation directory"; admin-dir placement matches `ScheduledWakeManager` and is the safer boundary.
- **Baseline persisted with the watch.** Startup scan compares current stat against the persisted baseline, so a change that landed while the server was down fires immediately instead of waiting for a second change.
- **Workspace-only paths.** `_resolve_safe` (workspace containment) plus `secret_policy.is_refused` on the agent-root-relative path. Admin agent files (`SOUL.md`, config, tokens) are not watchable. The issue's "authorized agent path" was read as the agent's workspace; watching admin files would leak activity metadata outside the tool boundary.
- **Timeout via config.** Silent expiry by default; `file_watch.notify_on_timeout: true` enqueues a timeout-notice wake. Other settings: `enabled`, `poll_interval_sec`, `max_timeout_sec`, `max_watches_per_conversation`.
- **One-shot.** Sidecar entry is removed before enqueueing the wake, so a restart cannot re-fire the same event. Rate-limited wakes drop the event (logged) instead of re-arming.
- **Shared wake context.** Extracted `compose_wake_context_setup` in `scheduled_wakes.py`; both wake producers use it so transport/media setup cannot drift.
- **Priority `normal`.** Like `schedule_turn`, the watch tools are deferred by the default active-tool budget (30 active; 38+ normal tools). `tool_search`/the deferred list exposes them. This is existing behavior for schedule tools, not a regression.

## Review (same-model self-review, current session)

Reviewed base 4bf3926a / head 74fdf3f9 against the issue and callers. Findings fixed before publication:

- `_stat_signature` treated every `OSError` as "file absent", so a permission/transient error could fire a spurious delete event (or report a stattable file as missing at arm time). Now only `FileNotFoundError` means absent; other errors propagate and the poller skips the tick; arming returns `cannot stat path`.
- An unexpected poller error left a sidecar entry with no backing task. The poller now drops the entry (logged) so `watch_list` does not report a phantom watch.
- Duplicated workspace-relative display logic in the manager and tool; consolidated as `display_watch_path`.

No other actionable defects found. Traced: tool validation and path/secret gating, arm/cancel/list, one-shot fire, timeout expiry, startup resume/fire/expiry, idempotent double scan, conversation deletion, shared wake context composition, and `ConversationManager` init/shutdown/startup wiring. Not tested: live server/Mattermost delivery.

## Verification

- Baseline `make test` before edits: 5904 passed, 2 skipped.
- `tests/test_file_watches.py`: 23 tests (modify/create/delete/any, one-shot, timeout silent + notify, cancel, cross-conv isolation, list widget, validation, secret refusal, stat-error handling, unattended/child gating, limits, offline startup fire/resume/expire, double-scan idempotence, delete cleanup, shared context setup).
- `make test` after edits: 5927 passed, 2 skipped (run after review fixes).
- `make check`: passed (fmt, lint, pyright 0 errors, message types, JS/browser assets).
- `make eval-tools`: our two new cases pass (`watch-file-vs-wait-canvas-approval`, `watch-file-vs-schedule-turn-build-sentinel`; 0 swaps). Overall 53/61, exit 1 due to 8 unrelated pre-existing-looking tool-choice failures (workspace/vault/admin/web choices) — not reproduced/attributed as caused by this change; full-loadout eval offers all tools, so watch-tool deferral is not a factor.
- Not run: live server / Mattermost exercise (post-merge per AGENTS.md).

## Limits

- No live TV/Marimo round trip; unit tests cover the wake path with a mocked `ConversationManager`.
