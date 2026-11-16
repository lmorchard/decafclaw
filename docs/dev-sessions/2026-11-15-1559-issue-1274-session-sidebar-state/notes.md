# issue-1274-session-sidebar-state

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1274 — "Session sidebar updates seem to not follow state changes"
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1274-session-sidebar-state
- Branch: issue-1274-session-sidebar-state (base origin/main @ 159c0b93)
- Status: ADDRESS-PR-REVIEW in progress. Draft PR #1287 (head 61dbb1c). Review verdict comment
  (MokaGnome, ghflow-review head=61dbb1c verdict=findings) has 1 blocking finding + 2 notes.
  Next: apply the delete cleanup, run make test-js + make check, commit+push to #1287, post reply.

### address-pr-review findings (review of head 61dbb1c)
- **Blocking #1 — unarchive refreshes `#currentFolder` (DISPUTED, no code change).**
  The active section renders the single `#sessions` slot; its displayed folder is `#currentFolder`.
  Correct display requires `#sessions` to match `#currentFolder` (the invariant every mutation site
  keeps). Unarchive is reachable ONLY from the archived section, where the component does not render
  `#sessions`; switching to active re-syncs via `#navigateChatFolder`→`listSessions(folder)`.
  Archive and unarchive are inverse ops using the identical `#currentFolder` refresh — the review
  concedes archive is "correct as-is," so unarchive is equally correct. The restored conv appears
  when the user navigates to its (persistent) folder, where it belongs. The review's proposed fix
  (`listSessions(#archivedCurrentFolder)`) would break the invariant (active section shows a
  different folder's hierarchy than it displays). Server confirms: `list_for_user` excludes archived,
  children of archived roots skipped. → Dispute with evidence; leave open for Les.
- **Note #1 — moveConversation dead code (KEPT, reported).** No UI call site at base OR head
  (only tests call it); folder-move is not wired anywhere. Fix is correct store-level future-proofing
  (consistent with siblings), so kept; flagged keep/drop as Les's decision. Not a no-op (would change
  the conv's session group if reachable), unlike delete.
- **Note #2 — delete's `listSessions(#currentFolder)` redundant (FIXED: dropped).** Delete is
  reachable only from archived; the conv was already out of the active hierarchy when archived, so
  the refetch is a no-op (and its emission causes a redundant re-render). Dropped it; the
  `listArchivedConversations` emission alone suffices to update the archived section. Updated the
  lifecycle describe.each (parameterized fetch/emit counts) + removed the delete test from the #1274
  block + docs.

### Done (this session)

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
