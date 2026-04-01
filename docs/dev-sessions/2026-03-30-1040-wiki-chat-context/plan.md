# Wiki Chat Context — Plan

GitHub Issue: #168

## Architecture Overview

The feature adds wiki page content injection into chat conversations via two mechanisms:

1. **Auto-context**: Web client sends `wiki_page` field with `send` messages when a wiki page is open. Passed to agent via `ctx.wiki_page`.
2. **@[[PageName]]**: Agent parses `@[[...]]` patterns from user message text — works across all channels (web, Mattermost, terminal).

Both are handled in `agent.py` `_prepare_messages()`, sharing the same injection mechanism. Already-injected pages are detected by scanning conversation history for existing `wiki_context` role messages — no separate tracking state needed.

### Data Flow

```
Any channel sends user message
         ↓
agent.py: _prepare_messages()
  1. Parse @[[...]] from user message text
  2. Check ctx.wiki_page for open page (web only)
  3. Scan history for existing wiki_context messages → already-injected set
  4. Filter out already-injected pages
  5. Resolve remaining pages via _resolve_page()
  6. Inject wiki_context role messages into history
  7. Publish wiki_context events for UI display
         ↓
ROLE_REMAP: wiki_context → "user" for LLM
```

---

## Step 1: Wiki context injection in agent.py

**Goal**: Add `@[[PageName]]` parsing and wiki context injection to `_prepare_messages()`, following the `memory_context` pattern. This is the core feature and works across all channels.

### Prompt

In `src/decafclaw/agent.py`, add wiki page context injection to `_prepare_messages()`:

1. Add a helper function `_resolve_wiki_references(config, user_message, wiki_page=None)`:
   - Parse `@[[...]]` patterns from `user_message` using regex `r'@\[\[([^\]]+)\]\]'`.
   - Collect page names from matches. If `wiki_page` is provided and not already in the list, add it.
   - For each unique page name, call `_resolve_page(config, page)` from `decafclaw.skills.wiki.tools` to resolve the path, then read the file content.
   - Return a list of dicts: `{"page": name, "content": content_or_None, "source": "mention"|"open_page"}`.

2. Add a helper function `_get_already_injected_pages(history)`:
   - Scan `history` for messages with `role: "wiki_context"`.
   - Extract page names from these messages (parse from the formatted text or store in a `wiki_page` field on the message dict).
   - Return a `set` of page names.

3. In `_prepare_messages()`, before the memory context block (around line 690):
   - Call `_resolve_wiki_references(config, user_message, ctx.wiki_page)` if the wiki skill is available (check that wiki dir exists).
   - Call `_get_already_injected_pages(history)` to get the set of already-injected pages.
   - Filter out pages that are already injected.
   - For each remaining page, create a message with `role: "wiki_context"` and a `wiki_page` metadata field:
     - For `source: "open_page"`: format as `[Currently viewing wiki page: {page}]\n\n{content}`
     - For `source: "mention"`: format as `[Referenced wiki page: {page}]\n\n{content}`
     - For missing pages (content is None): format as `[Wiki page '{page}' not found]`
   - Append each to `history` and archive it.
   - Publish a `wiki_context` event for UI display.

4. Add `"wiki_context"` to `ROLE_REMAP` dict so it gets remapped to `"user"` for the LLM.

5. Add `wiki_page: str | None = None` field to `Context` in `src/decafclaw/context.py`.

---

## Step 2: Client sends wiki_page with messages

**Goal**: The web UI includes the currently open wiki page name in chat messages. Other channels don't need changes — `@[[PageName]]` works automatically.

### Prompt

In the web UI frontend, include the open wiki page name when sending chat messages:

1. In `src/decafclaw/web/static/app.js`:
   - Add a `getOpenWikiPage()` function that returns the current wiki page name from the `?wiki=` URL param (or `null` if no wiki page is open). Export or attach to `window` so the conversation store can access it.

2. In `src/decafclaw/web/static/lib/conversation-store.js`:
   - In `sendMessage()`, call the function to get the open wiki page name.
   - If a page is open, add `wiki_page: pageName` to the WebSocket `send` message object.
   - Same for the pending-message flow (when creating a new conversation then sending).

---

## Step 3: Server passes wiki_page through to agent context

**Goal**: The WebSocket handler reads `wiki_page` from the client message and sets it on the agent context.

### Prompt

In `src/decafclaw/web/websocket.py`:

1. In `_handle_send()`, extract `wiki_page` from the incoming message: `wiki_page = msg.get("wiki_page")`.
2. Pass `wiki_page` through `_start_agent_turn()` and `_run_agent_turn()` as a new parameter.
3. In `_run_agent_turn()`, set `ctx.wiki_page = wiki_page` on the context before calling `run_agent_turn()`.

---

## Step 4: Forward wiki_context events to WebSocket

**Goal**: Wiki context injection events appear in the web UI as status messages.

### Prompt

In `src/decafclaw/web/websocket.py`, in the `_run_agent_turn()` event forwarding:

1. In the `_forward` function within `on_turn_event`, add handling for `wiki_context` events.
2. Forward them as `tool_status` messages to the WebSocket: `{"type": "tool_status", "conv_id": conv_id, "tool": "wiki_context", "message": event["text"], "tool_call_id": ""}`.
3. Follow the same pattern used for `memory_context` events (around line 549-557).

---

## Step 5: System prompt addition

**Goal**: Add a brief note to the system prompt so the agent understands wiki context annotations.

### Prompt

Add wiki context documentation to the agent's system prompt:

1. In `src/decafclaw/prompts/AGENT.md`, add a section explaining:
   - Messages with role `wiki_context` contain wiki page content injected into the conversation.
   - `[Currently viewing wiki page: PageName]` means the user has that page open in the UI.
   - `[Referenced wiki page: PageName]` means the user used `@[[PageName]]` syntax.
   - `[Wiki page 'PageName' not found]` means the referenced page doesn't exist.
   - The agent can use wiki tools to edit pages or search for related content.
   - Users can use `@[[PageName]]` from any channel (web, Mattermost, terminal).

---

## Step 6: Lint, test, and manual verification

**Goal**: Ensure everything works end-to-end.

### Prompt

Verify the wiki chat context feature:

1. Run `make check` (lint + typecheck for Python and JS).
2. Run `make test` to ensure no regressions.
3. Write tests for:
   - `_resolve_wiki_references()` — given message text with `@[[PageName]]` patterns, returns correct page dicts.
   - `_get_already_injected_pages()` — given history with wiki_context messages, returns correct set.
   - Wiki context injection in `_prepare_messages()` — verify messages are added to history with correct formatting, and already-injected pages are skipped.
4. Manual testing checklist (for Les):
   - **Web**: Open a wiki page, send a message → page content appears as context
   - **Web**: Send another message with same page open → no re-injection
   - **Web**: Navigate to different page, send message → new page injected
   - **Any channel**: Type `@[[PageName]]` in message → page content injected
   - **Any channel**: Type `@[[NonExistent]]` → error note injected
   - **Web**: Refresh browser, send message with same page → no re-injection (history-based tracking)
   - **Mattermost**: Send message with `@[[PageName]]` → works without web UI
