# issue-1442-midturn-steering

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1442
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1442-midturn-steering
- Branch: issue-1442-midturn-steering
- Base: origin/main at 6dff151c59eed38c395065f1dbf951213ed2e262
- Status: implementation complete; all specified gates green; draft PR published.
- PR: https://github.com/lmorchard/decafclaw/pull/1448 (draft; do not mark ready or request review in this session).
- Changes: `send` wire type gains optional `steering`; web `sendMessage` sends it only when the turn was busy before the optimistic `#busy = true`; Mattermost reads `manager.get_state(conv_id).busy` at send time and passes `steering=True/False`; docs updated.
- Checks (head 2e5eefc7): baseline `make test` 5525 passed / 2 skipped (53.09s). Post-change `make test` 5529 passed / 2 skipped (37.75s); `make test-js` 715 passed across 59 files; `make check` passed (message types, fmt, ruff, pyright, tsc, module graph).
- Pre-fix failures demonstrated: 2 Mattermost tests (KeyError 'steering'), 2 vitest cases (missing `steering: true`). The two new `tests/test_ws_queue.py` steering tests already passed before the change — the server half was ready (#1442 triage), so they are regression coverage.

## Log

- Baseline `make test` on base 6dff151c: 5525 passed, 2 skipped.
- `make install-js` in worktree (npm ci) before vitest.
- Implemented web/mattermost/schema/docs changes; `make gen-message-types` regenerated `message_types.py`, `docs/websocket-messages.md`, `tui/src/types.generated.ts` (constants file unchanged).
- Commits: 9bea81c4 web + wire type + ws tests; f48eb47d Mattermost + tests; 2e5eefc7 docs.
