# Plan: `make run-dev` (#1096)

## Design

- **`scripts/run_dev.py`** (new) — the whole recipe, stdlib-only, launched by
  a thin `make run-dev` target (`uv run python scripts/run_dev.py`), matching
  the existing script-per-target pattern (`check_npm_policy.py`,
  `prune_agent_worktrees.py`, ...).
  - `locate_checkouts()` — `git rev-parse --show-toplevel` +
    `--absolute-git-dir` + `--git-common-dir`. Primary checkout: git dir ==
    common dir → refuse. Linked worktree: common dir's parent is the main
    clone root. (Gotcha found while testing: `--absolute-git-common-dir`
    does not exist — rev-parse echoes it; and `--git-common-dir` prints a
    CWD-relative path from the primary checkout, so it is resolved.)
  - `seed_data_dir()` — copies `<main>/data/{agent_id}/config.json` to
    `<worktree>/data-dev/{agent_id}/config.json` only when the target is
    missing (a user may have edited the seeded copy); config.json only.
  - `http_port_from_env_file()` — plain `KEY=value` scan of the worktree
    `.env`; quoted / non-numeric / out-of-range values fall back to 18880
    (the `HttpConfig.port` default).
  - `launch_app()` — `os.execvpe("uv", ["uv", "run", "decafclaw"], env)` so
    Ctrl-C / make foreground semantics are unchanged. **Flushes stdio first**
    — exec keeps the fds but drops Python's unflushed pipe buffer, so the
    seed/status lines would vanish from `make run-dev | tee log`.
  - Env applied to the app: `DATA_HOME=<worktree>/data-dev`,
    `MATTERMOST_ENABLED=false`, `EMAIL_ENABLED=false`,
    `NOTIFICATIONS_CHANNELS_MATTERMOST_DM_ENABLED=false`,
    `HEARTBEAT_INTERVAL=0`, `HTTP_ENABLED=true`, `HTTP_PORT=<from .env>`.
    Real env vars win over the `.env` file (dotenv loads without override),
    which also defeats the stale `DATA_HOME` line in the copied `.env`.

- **`tests/test_run_dev.py`** (new) — 9 subprocess end-to-end tests against
  throwaway `git init` repos + detached worktrees in `tmp_path`; the app
  launch is intercepted with a fake `uv` on PATH that prints args + env.
  Covers: main-clone refusal, non-git dir, seed + full env assertions,
  config.json-only seeding, no-overwrite on re-run, missing seed source,
  port fallback (no .env / quoted / invalid), AGENT_ID bootstrap parity.

- **`Makefile`** — `run-dev` target after the run family, with a comment
  block describing the behavior.

- **`.ghflow/project.md`** — "Running the app" rule now points at
  `make run-dev`; the Makefile source hash in the frontmatter is updated to
  the new blob so `ghflow facts` stays consistent with the tree (the
  parallel refresh PR #1098 only touches the AGENTS.md/CLAUDE.md hash
  lines — no hunk overlap).

## Evidence per acceptance condition

| Condition | Check |
|---|---|
| Worktree: web UI on worktree's port, no MM/email/DMs/heartbeat | `make run-dev` in the worktree (port 18881) → startup log shows `HTTP server enabled on 0.0.0.0:18881` and no "Mattermost client starting" / heartbeat-timer lines; `curl` the port; kill |
| Main clone: exits with a message | primary-checkout probe (fresh local clone of the branch) → refusal message, exit 1 (also covered by `test_refuses_to_run_from_main_clone`) |
| Facts rule points at `make run-dev` | diff of `.ghflow/project.md` |
| Gates | `make check` (all sub-gates) + `make test` on the branch head |
