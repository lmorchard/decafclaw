# issue-1554-restart-wake

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1554
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1554-restart-wake
- Branch: issue-1554-restart-wake
- Base: origin/main at 9072e8e8c99786db5bb0961442bc251bae18780f
- Status: implementation complete, gates green, ready to commit.
- Next action: commit, then submit PR.

## Decisions

- Persist records per conversation under `data/{agent_id}/restart_wakes/{conv_id}.json`
  (not a single `restart_wake.json` as the issue sketch suggested). Concurrent
  restarts from more than one conversation keep their own records; the directory
  is outside the workspace, matching `scheduled_wakes/` and `file_watches/`.
- The coordinator, not the tool, writes and deletes records: `request_restart(wake=...)`
  persists before any state change (a write error leaves the restart unrequested),
  and `cancel_restart()` removes tracked records.
- Startup scan consumes (reads and unlinks) each record before enqueueing the
  `TurnKind.WAKE` turn, so a failed wake can never replay.
- Capture `user_id`, `channel_id`, `thread_id`, and `transport` in the record so a
  Mattermost conversation resumes in the right channel/thread after restart.
- `wake_on_restart` defaults to true on `admin_restart`; slash-command and
  config-watcher restarts do not create wake records (the requester is a human or
  there is no conversation).

## Checks

- `make fmt` — clean.
- `make check` — passes (lint, pyright, JS/message/skill checks, static graph).
- `make test` — 5976 passed, 2 skipped.
- Targeted: `tests/test_restart_wake.py tests/test_restart.py tests/test_admin_tools.py` — 72 passed.

## Notes

- `make check` regenerates `openapi.json`/`openapi.yaml` and the JS API client with
  nondeterministic duplicate-operation-ID ordering. Those churn-only diffs were
  reverted; they are unrelated to this change.
- Live restart verification (actually restarting the daemon and observing the wake
  turn) was not performed; unit tests cover record persistence, single-shot
  consumption, enqueue shape, cancel cleanup, and enqueue-failure behavior.

## Log

- Created worktree from origin/main, board moved Backlog → In progress.
- Implemented `restart_wake.py`, path helper, coordinator wake tracking, admin tool
  option, manager startup scan, runner call, tests, docs.
