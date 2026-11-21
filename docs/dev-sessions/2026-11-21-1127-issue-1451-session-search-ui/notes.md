# issue-1451-session-search-ui

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1451
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1451-session-search-ui
- Branch: issue-1451-session-search-ui
- Base: origin/main at 76d2db578cd44dd52b9e11a031c89856454b6a6e (includes #1630)
- State: implementation and unit tests in progress; live browser check and gates pending.

## Decisions

- Reuse the #1630 ranking instead of a title-only UI search: extract it from
  `tools/session_tools.py` into `web/conversation_search.py`, shared by the
  agent tool and the new HTTP endpoint. No duplicate scoring.
- `GET /api/conversations/search` defaults to `include_archived=true` so old
  threads stay findable, tags archived rows, and scopes to the authenticated
  user. Empty `q` returns an empty list rather than an error.
- UI: a debounced (200 ms) search field at the top of the Chats tab replaces
  the folder/session listing while a query is active. Results show title,
  two-line excerpt, matched role, updated time, and archived badge. Click/Enter
  opens the session and clears the query; Escape or × clears.
- Stale responses are dropped with a request sequence number. The blocking
  archive scan runs in `asyncio.to_thread` on the endpoint.

## Evidence

- Python endpoint tests: 3 passed in `tests/test_web_conversations.py`.
- JS sidebar tests: 55 passed in `conversation-sidebar.test.js` (4 new search
  cases) with `npm test -- --run components/conversation-sidebar.test.js`.

## Log
