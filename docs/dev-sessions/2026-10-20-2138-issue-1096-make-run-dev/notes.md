# issue-1096-make-run-dev

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1096 (`make run-dev`)
- PR: https://github.com/lmorchard/decafclaw/pull/1102 (draft, not ready —
  do not mark ready or request review; merge not authorized)
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1096-make-run-dev
- Branch: issue-1096-make-run-dev (base origin/main @ f200a4f0)
- Board: Ready → In progress (set by `ghflow worktree create`, readback matched);
  stays In progress while the PR is a draft.
- State: complete. Code commits: d1d162d3 (implementation + tests + facts +
  session docs), 52ac0fe8 (worktree-detection hardening, self-review).
  Hosted CI green at 52ac0fe8 (lint-and-test, js-test, check-tui all
  passed; mergeable, base not behind).
- Next action (a later run, after this handoff returns): independent local
  review in a fresh context (same local model per the user-approved
  exception), then `pr ready` with the body file at
  /Users/lmorchard/.local/state/ghflow/notes/2026-10-20-1949-burndown-ready-queue/pr-1096-body.md
  (body will need a final pass against the reviewed head) and board → In review.

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
- 2026-10-20 22:35 — Committed checkpoint d1d162d3; published draft PR
  #1102 (head verified via verify-commit --pr-head).
- 2026-10-20 22:46 — Live acceptance #1 (worktree): `make run-dev` on port
  18881. Seeded data-dev/decafclaw/config.json from the main clone; log:
  "HTTP server enabled on 0.0.0.0:18881", GET / → 200, GET /health → 200;
  zero Mattermost/email/DM lines; explicit "Heartbeat disabled (interval
  not set)"; clean startup, graceful SIGTERM shutdown. Observed: on a fresh
  data-dev, bundled scheduled skills (dream/garden/newsletter) fire their
  never-run → due catch-up turns at the first 60s poll (standard app
  behavior; not on the issue's off-list); they failed fast against a stale
  Vertex SA path in the seeded config and were handled fail-open (error
  result + local notification, no tracebacks, no external delivery).
- 2026-10-20 22:52 — Live acceptance #2 (main clone): fresh local clone of
  the branch (primary checkout, `git rev-parse --git-dir` = `.git`) →
  refusal message, exit 1, no data-dev created. (The main clone on this
  machine predates the target, so the probe uses a primary checkout of the
  branch — the guard is the entire behavior.)
- 2026-10-20 22:58 — Self-review of the full merge-base diff: hardened
  locate_checkouts() to compare both git dirs resolved (symlink
  consistency); targeted tests 9/9 + ruff + pyright green. Committed
  52ac0fe8, pushed to the same draft (head verified).
- 2026-10-20 23:02 — Hosted CI at 52ac0fe8: green (lint-and-test passed,
  js-test passed, check-tui passed; MERGEABLE, base_behind_by 0). Updated
  the draft body with the live-acceptance results and remaining work
  (independent review, then ready).
