# Wiki Chat Context — Notes

## Implementation Summary

All 6 steps complete. Two commits on `feat/wiki-chat-context`:

1. `0bb6c57` — feat: wiki page context injection (core implementation)
2. `9a60e92` — test: add wiki context injection tests (15 tests)

### Files Changed

- `src/decafclaw/context.py` — added `wiki_page` field
- `src/decafclaw/agent.py` — `_resolve_wiki_references()`, `_get_already_injected_pages()`, injection in `_prepare_messages()`, `wiki_context` in `ROLE_REMAP`
- `src/decafclaw/web/static/app.js` — `window.getOpenWikiPage()` function
- `src/decafclaw/web/static/lib/conversation-store.js` — sends `wiki_page` in WebSocket `send` messages
- `src/decafclaw/web/websocket.py` — threads `wiki_page` through handler → agent, forwards `wiki_context` events
- `src/decafclaw/prompts/AGENT.md` — wiki context annotation docs for agent
- `tests/test_wiki_context.py` — 15 tests

### Design Decisions

- `@[[PageName]]` parsing is in `agent.py` (channel-agnostic) — works in web, Mattermost, terminal
- Open wiki page (`ctx.wiki_page`) is web-only, set by WebSocket handler
- Already-injected tracking uses history scan (no separate state) — survives restarts, works with compaction (re-injects after compaction, which is desirable since content was lost)
- `wiki_context` role messages carry a `wiki_page` metadata field for reliable page name extraction

### Manual Testing Checklist

- [ ] Web: Open wiki page, send message → page content appears as context
- [ ] Web: Send another message with same page open → no re-injection
- [ ] Web: Navigate to different page, send message → new page injected
- [ ] Any channel: Type `@[[PageName]]` → page content injected
- [ ] Any channel: Type `@[[NonExistent]]` → error note injected
- [ ] Web: Refresh browser, send message with same page → no re-injection
