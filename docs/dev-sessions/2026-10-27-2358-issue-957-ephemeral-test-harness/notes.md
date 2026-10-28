# issue-957-ephemeral-test-harness

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/957
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-957-ephemeral-test-harness
- Branch: issue-957-ephemeral-test-harness
- Base: origin/main at f5fa81fee82123c0346e4cd8b89a43d72f43c0c9
- Status: implemented; gates green (`make check`, `make test` 5008 passed / 2 skipped); ready for PR.

## Log

- 2026-10-27: baseline `make test` green (4998 passed, 2 skipped, 60s).
- Implemented the launcher (`scripts/ephemeral_instance.py`), the child entry
  point (`src/decafclaw/testing/ephemeral_server.py`), and the global
  schedule-timer switch (`schedules.enabled`, env `SCHEDULES_ENABLED`).
- `src/decafclaw/__init__.py`: `main` and the ephemeral child share one
  `start_app()` (the spec's "same startup function"); the trust-tier
  consumer registry entry moved from `main` to `start_app` accordingly.
- `config.load_config` honors `DECACLAW_LOAD_DOTENV=0` to skip the
  python-dotenv import entirely (child never imports the checkout's `.env`).
- E2E tests in `tests/test_ephemeral_instance.py` drive the launcher as a
  subprocess (real child process, real `decafclaw-client`); readiness is
  polled via `/health`, no fixed sleeps. The confirmation-halt teardown is
  dominated by the existing 10s SIGTERM grace (child waits up to 15s for
  the in-flight, confirmation-paused turn, then the launcher SIGKILLs).
- `make check` initially failed on E741 (`l` comprehensions) and a pyright
  invariance error: widened `ScriptedProvider.__init__` /
  `register_scripted_provider` `responses` from `list[...]` to
  `Sequence[...]` in `testing/scripted_provider.py`.
- `make test` initially failed `test_discovered_skills_consumers.py`
  (consumer moved `main` -> `start_app`); updated the registry entry.
- Final: `make check` green, `make test` 5008 passed / 2 skipped,
  `make ephemeral-test` exit 0.
