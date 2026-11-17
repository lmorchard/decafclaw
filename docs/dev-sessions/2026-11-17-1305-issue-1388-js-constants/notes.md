# issue-1388-js-constants

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1388 ([10.9] Centralize JS status/command constants)
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1388-js-constants
- Branch: issue-1388-js-constants
- Base: origin/main at 0b42bbcc331c6fed7017c6b70879b5cd9e5b1250
- Status: implementation complete, gates green, ready to publish the PR (no merge authorization).

## Design

New `src/decafclaw/web/static/lib/constants.js` exports:

- `STATUS_LABELS` — subagent badge labels (idle/busy/waiting/finished)
- `STATUS_TITLE_LABELS` — tooltip wording, intentionally different from the badges
- `SIDE_EFFECT_COMMANDS` — named human-only commands: `/terminal`, `/retitle`,
  `!retitle`, `/update`, `!update`
- `CHILD_ID_SEPARATOR = '--child-'`
- `isSideEffectCommand(text)` — exact command or command + space + args (the old
  inline predicate, now derived from the map so a new command is one edit)

Plain JS module, not `message_types.json`: these values never cross the WebSocket
wire. Migrated consumers: `lib/conversation-store.js`, `app.js`,
`components/chat-controls.js`, `components/conversation-sidebar.js`.

Tests: `lib/constants.test.js` covers the maps, the helper edge cases, and
freeze/uniqueness invariants; `lib/conversation-store.test.js` adds one case per
`SIDE_EFFECT_COMMANDS` entry (bare and with args: no busy state, no optimistic
echo, exactly one send) plus a `CHILD_ID_SEPARATOR` parent-id case. Source-text
scans were deliberately not used (repo rule: tests must not assert source text).

## Verification

- `make test-js`: 61 files, 748 tests passed.
- `npx tsc --noEmit` passed.
