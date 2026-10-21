# issue-1096-make-run-dev

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1096 (`make run-dev`)
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1096-make-run-dev
- Branch: issue-1096-make-run-dev (base origin/main @ f200a4f0)
- Board: Ready → In progress (set by `ghflow worktree create`, readback matched)
- State: implementation + tests + facts-file update done; `make check` and
  `make test` green on the checkpoint commit (4637 passed, 2 skipped —
  baseline was 4628 + 9 new tests).
- Next action: live acceptance (worktree run on port 18881 + primary-checkout
  refusal probe), publish draft PR, then final self-review.

## Log

- 2026-10-20 21:38 — Worktree created from origin/main @ f200a4f0 via
  `ghflow worktree create` (--copy .env, --setup "uv sync", --notes,
  board move Ready → In progress). Added `HTTP_PORT=18881` to the worktree
  .env (port scan of existing worktrees: 18881 free).
- 2026-10-20 21:45 — Baseline `make test` on clean origin/main: 4628 passed,
  2 skipped (61s).
- Finding: main clone's .env `DATA_HOME` is stale
  (`/Users/lorchard/devel/decafclaw/data` — path does not exist), so the
  seed source must come from the git common dir (main clone root +
  `data/{agent}/config.json`), not from the .env. Real data dir is the
  gitignored `data/` at the clone root.
- Finding: all six env vars map 1:1 to config fields (MattermostConfig.enabled,
  EmailConfig.enabled, MattermostDMChannelConfig.enabled, HeartbeatConfig.interval,
  HttpConfig.enabled/port); `heartbeat.parse_interval("0")` → None → timer off.
  No app code needed. `data-dev/` already gitignored (added by #1097).
- Finding: `git rev-parse --absolute-git-common-dir` does not exist (rev-parse
  echoes it); `--git-common-dir` prints CWD-relative from the primary checkout.
  `locate_checkouts()` resolves against CWD. Caught by the subprocess tests,
  not by eyeballing.
- Finding: `os.execvpe` drops Python's unflushed pipe-buffered stdout — the
  script's seed/status prints vanished when output was piped. Fixed with an
  explicit flush in `launch_app()`; caught by `test_seed_runs_once_and_does_not_overwrite`.
- 2026-10-20 22:05 — `make check`: all sub-gates passed (install-js,
  check-npm-policy, check-message-types, fmt-check, lint, typecheck,
  check-js, check-browser-assets). `make test`: 4637 passed, 2 skipped (46.7s).
