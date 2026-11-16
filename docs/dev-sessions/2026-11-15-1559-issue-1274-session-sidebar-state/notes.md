# issue-1274-session-sidebar-state

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1274 — "Session sidebar updates seem to not follow state changes"
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1274-session-sidebar-state
- Branch: issue-1274-session-sidebar-state (base origin/main @ 159c0b93)
- Status: IMPLEMENTATION COMPLETE + all gates green. Committed; publishing draft PR.

### Done (this session)
- Fix in `lib/conversation-store.js`: all 6 mutation sites now refresh the session hierarchy
  (archive/rename/unarchive/delete/move → immediate `listSessions(#currentFolder)`; CONV_RENAMED
  bg auto-title → debounced `#scheduleSessionsRefresh()` gated on the conv being in `#sessions`).
  deleteConversation's redundant explicit emit was replaced by the sessions refetch (emission count unchanged).
- Tests: new `session hierarchy follows conversation mutations (#1274)` describe (7 tests, written
  failing first → pass after fix); updated lifecycle + patch call/emission-count assertions.
- Docs: `docs/web-ui.md` — conv_renamed + session-hierarchy refresh-trigger sections updated (#1274).

### Checks (all green, on this tree)
- `make test-js`: 681 passed / 59 files.
- `make check` (npm-policy, message-types drift, fmt-check, lint, pyright 0 err, tsc, module-graph): PASS.
- `make test` (pytest): 5499 passed, 2 skipped.

### Root cause (confirmed by reading code)
The web sidebar's **active** section renders from the session hierarchy:
`conversation-sidebar.js #renderSessionGroups()` uses `store.sessions` (server-grouped,
`GET /api/sessions`) when non-empty, falling back to client-side grouping of the flat list.

But in `lib/conversation-store.js`, every conversation mutation updates ONLY the flat
`#conversations` list — never `#sessions`:
- `archiveConversation` → re-fetches flat list only. Archived conv stays in hierarchy. **BUG**
- `renameConversation` → patches flat list only. New title not in hierarchy. **BUG** (reported)
- `unarchiveConversation` → re-fetches archived list only. Conv doesn't return to hierarchy. **BUG**
- `deleteConversation` → re-fetches archived list only. **BUG**
- `moveConversation` (folder) → re-fetches flat list only. **BUG**
- `CONV_RENAMED` ws handler (#1072 bg auto-title) → patches flat list only. **BUG**

Server side is correct: `list_sessions` reads the index (excludes archived) + archive-discovered
children, so a re-fetch of `/api/sessions` always reflects the mutation. The gap is purely client:
the hierarchy (a second copy of conv state) is never refreshed after mutations.

### Fix plan
After each mutation, refresh the session hierarchy via `listSessions(this.#currentFolder)`:
- archive / rename / unarchive / delete / move → immediate `await this.listSessions(#currentFolder)`.
- CONV_RENAMED (pushed, batchable) → debounced `#scheduleSessionsRefresh()`, gated on the conv
  being present in `#sessions` (avoids a pending real timer leaking into existing #1072 tests,
  where `#sessions` is empty).

Sidebar needs no change: it re-renders from `store.sessions` on the `change` event that
`listSessions` emits.

### Tests to add/update (test-first)
- NEW: hierarchy is refreshed after archive / rename / unarchive / delete / move (assert `/api/sessions` fetch).
- NEW: CONV_RENAMED schedules a debounced hierarchy refresh when the conv is in `#sessions`.
- UPDATE existing call/emission-count assertions that my change shifts:
  - conversation-store.test.js lifecycle describe.each (archive/unarchive/delete): success fetch count 2→3, add sessions URL; events archive 1→2, unarchive 1→2, delete 2→3.
  - conversation-patch.test.js rename + move: queue a sessions response; changed 1→2; fetch count +1.

### Gates
- JS: `make test-js` (vitest), `check-js` (tsc). Python (`make test`) unaffected (JS-only change) but run for baseline.
- `make fmt` before commit; `make check` full gate.

## Log

- 2026-11-15: Resumed empty worktree (prior run only did setup + this notes stub). Read issue,
  conversation-store.js, conversation-sidebar.js, http_server.py endpoints (list_sessions/rename/
  archive/unarchive/delete), message-store.js, existing tests. Confirmed root cause above.
