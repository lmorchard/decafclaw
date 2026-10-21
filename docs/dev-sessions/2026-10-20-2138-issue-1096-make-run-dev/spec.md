# Spec: `make run-dev` — run the app from a worktree with no external side effects

Issue: https://github.com/lmorchard/decafclaw/issues/1096

## Problem

Running the app from a worktree for hands-on UI work is fiddly: the app
reads `DATA_HOME`/`AGENT_ID` from env, defaults `data_home` to `./data`, and
the worktree `.env` (copied from the main clone) carries a `DATA_HOME` that
points at the main clone's data. Getting a *side-effect-free* run — web UI
only, no Mattermost, no email, no DMs, no heartbeat — requires remembering a
six-variable env recipe (the facts file's old "Running the app" rule).

## Intended result

A `make run-dev` target that, from a linked worktree:

1. **Refuses to run from the main clone** (primary checkout) — exits with a
   message, non-zero code.
2. **Seeds a worktree-local data directory if missing** — `data-dev/` at the
   worktree root (gitignored since #1097), containing a copy of the main
   clone's `data/decafclaw/config.json` **only** (no vault, no conversations,
   no secrets).
3. **Runs `uv run decafclaw`** with `DATA_HOME` pointed at the seeded dir,
   `MATTERMOST_ENABLED=false`, `EMAIL_ENABLED=false`,
   `NOTIFICATIONS_CHANNELS_MATTERMOST_DM_ENABLED=false`,
   `HEARTBEAT_INTERVAL=0`, `HTTP_ENABLED=true`, and `HTTP_PORT` from the
   worktree's `.env`.

Acceptance: from a worktree, `make run-dev` serves the web UI on the
worktree's port with no Mattermost connection, email, DMs, or heartbeat; from
the main clone it exits with a message. The facts file's dev-run rule then
points at `make run-dev`.

## Scope limits

- No new app code: all six env vars map 1:1 to existing config fields
  (`config_types.py`), and `heartbeat.parse_interval("0")` already disables
  the timer. The change is Makefile + one script + tests + facts file.
- `CLAUDE.md` untouched: the dev-run rule's home is the facts file; the
  Running block lists common targets, not every one.
- The main clone's `.env` `DATA_HOME` is stale on this machine (points at
  `/Users/lorchard/...`, which does not exist), so the seed source is
  derived from the git common dir (main clone root + `data/{agent}/config.json`),
  not from `.env`.
- Agent id follows the app's own bootstrap (`AGENT_ID` env, default
  `decafclaw`) so the seed lands where `load_config()` looks.
