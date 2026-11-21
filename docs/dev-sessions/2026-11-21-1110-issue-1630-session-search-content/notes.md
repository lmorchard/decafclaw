# issue-1630-session-search-content

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1630
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1630-session-search-content
- Branch: issue-1630-session-search-content
- Base: origin/main at 56f8c1c65cdf1ed709c7a00aaaf626c197499787
- State: implementation complete; gates and PR next.

## Decisions

- Scope: only conversations from `ConversationIndex.list_for_user`, never the
  system/heartbeat archive sweep `conversation_search` performs.
- Scoring: title (10 substring / 3 per token) > user message (8/2) >
  assistant message (4/1). Best user message and best assistant message each
  contribute once, so a long archive cannot accumulate a score by repetition.
- Roles: only `user` and `assistant` text counts. `tool` and metadata content
  would match incidental tokens without reflecting the discussion.
- Recency: a multiplier from 1.0 to 1.2 over a 30-day window, then
  `updated_at` breaks exact ties. A bounded multiplier reorders sessions with
  similar evidence; it cannot let a fresh weak match outrank a strong one.
- Snippets: ~70 chars each side of the first substring (or token) hit,
  whitespace-collapsed, capped at 180 chars. User text is preferred for the
  excerpt; the reply is the fallback.
- I/O: `archive.iter_archive_messages` streams JSONL and skips corrupt lines;
  the scan runs in `asyncio.to_thread` so blocking reads stay off the event
  loop. Measured local data: 153 indexed user sessions, 282 web archives,
  4.6 MB total (~20–45 ms per search), so no candidate cap was needed.
  Streaming keeps memory bounded if the archives grow.

## Evidence

- New tests fail against the title-only implementation (5 failures), then
  `uv run pytest tests/test_session_tools.py -n 0 -q` → 17 passed.
- Tool-choice eval cases added for session_search vs conversation_search
  routing.

## Log
