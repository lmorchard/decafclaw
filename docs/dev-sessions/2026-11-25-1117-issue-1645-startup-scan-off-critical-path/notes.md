# issue-1645-startup-scan-off-critical-path

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1645
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1645-startup-scan-off-critical-path
- Branch: issue-1645-startup-scan-off-critical-path
- Base: origin/main at d0232c14d36981484d56c9680d28a4acbbf6cb26
- Next action: review, then draft PR.

## Log

### Changes

- `conversation_paths.iter_conversation_dirs()`: one enumeration of both
  roots; `iter_conversation_archives()` now derives from it.
- `ConversationManager.startup_scan(recover_workflows=False)` is now a single
  pass per conversation dir: archive tail (confirmation), `inbox.jsonl`,
  and `workflow.json`. Runner passes `recover_workflows=True` and no longer
  calls `startup_scan_workflows()` separately.
- Archive tail reads are skipped when the archive is empty or its mtime is
  older than 24h + 2h margin. The confirmation staleness rule already
  discarded requests older than 24h, so this preserves recovery behavior
  while removing the 64KB read per historical conversation.
- Startup recovery gate: `begin_startup_recovery()` closes an `asyncio.Event`,
  `finish_startup_recovery()` opens it (also in `finally` on failure).
  `enqueue_turn`, `respond_to_confirmation`, and
  `cancel_pending_confirmation` wait on it. The event starts open so tests
  and one-off managers are unaffected.
- Runner starts recovery as a background task before the HTTP listener;
  shutdown gives it 5s to finish before cancelling.
- Startup phase timing logs: `Startup phase: ConversationManager created`,
  `HTTP listener task started`, `transports ready`; recovery logs
  `Startup recovery: scanned N conversation(s) in X.XXs (...)`.
- Docs: `docs/architecture.md` startup sequence + confirmation note.

### Verification

- `pytest tests/test_conversation_paths.py tests/test_conversation_manager_enqueue.py tests/test_conversation_manager_confirmations.py tests/test_runner.py` — 123 passed.
- `make lint`, `make typecheck` — clean.
- `make check` — passed (after `make fmt`).
- `make test` — 7159 passed, 4 failed: `tests/test_host_metrics.py`
  (pre-existing macOS host-environment failures; unrelated).

### Evidence limits

- No live boot timing on the production corpus from this host (no
  `~/.decafclaw` here). Timing is covered by the phase logs and the
  regression tests, not by a measured production boot.
- Review was same-model (single-session local-model run); not independent.
