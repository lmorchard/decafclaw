# Plan: Web UI Agent Media & Unified Media Storage

## Phase 1: MediaHandler Interface — `save_media()` and `MediaSaveResult`

Extend the `MediaHandler` base class with the new unified method, without changing any existing behavior yet.

### Step 1.1: Add `MediaSaveResult` and `save_media()` to base class

**Prompt:**
In `src/decafclaw/media.py`, add a `MediaSaveResult` dataclass and a default `save_media()` method to `MediaHandler`:

```python
@dataclass
class MediaSaveResult:
    workspace_ref: str | None = None  # workspace:// path for text injection
    file_id: str | None = None        # platform file ID (Mattermost)
    saved_filename: str | None = None # actual filename after dedup
```

Add to `MediaHandler`:
```python
async def save_media(self, conv_id, filename, data, content_type) -> MediaSaveResult:
    """Save media and return a result describing where it went.
    Subclasses implement channel-specific behavior."""
    raise NotImplementedError
```

Keep all existing methods (`upload_file`, `send_with_media`, etc.) unchanged.

Add tests:
- `MediaSaveResult` fields default to None
- Base `save_media()` raises NotImplementedError

### Step 1.2: Implement `save_media()` on `TerminalMediaHandler`

**Prompt:**
Update `TerminalMediaHandler` in `src/decafclaw/media.py`. It currently stores `workspace_path`. Add `config` to its constructor (needed for `save_attachment()`).

Implement `save_media()`:
1. Call `save_attachment(self.config, conv_id, filename, data, content_type)`
2. The return dict has `path` (workspace-relative) and `filename` (actual saved name)
3. Return `MediaSaveResult(workspace_ref="workspace://" + path, saved_filename=filename)`

Update the call site in `agent.py` where `TerminalMediaHandler` is constructed (~line 792) to pass `config`.

Add tests:
- `save_media()` saves file to `workspace/conversations/{conv_id}/uploads/`
- Returned `workspace_ref` contains correct path
- Returned `saved_filename` may differ from input (timestamp dedup)

### Step 1.3: Create `WebMediaHandler` with `save_media()`

**Prompt:**
Add a `WebMediaHandler` class in `src/decafclaw/media.py`. It needs `config` in its constructor.

Implement `save_media()` identically to `TerminalMediaHandler` — both save via `save_attachment()` and return `workspace_ref`. (They could share a base, but keep it simple — they're small.)

Implement `upload_file()` and `send_with_media()` as no-ops for now (the old interface won't be used for web).

Add tests mirroring the terminal handler tests.

### Step 1.4: Implement `save_media()` on `MattermostMediaHandler`

**Prompt:**
Update `MattermostMediaHandler` in `src/decafclaw/media.py`. It currently stores `_http` (the httpx client). Add `channel_id` to its constructor — Mattermost needs this for uploads.

Implement `save_media()`:
1. Call the existing `upload_file(self.channel_id, filename, data, content_type)` to upload to Mattermost
2. Return `MediaSaveResult(file_id=file_id)`

Update the call site in `mattermost.py` (~line 332) where `MattermostMediaHandler` is constructed to pass `channel_id`.

Add tests:
- `save_media()` calls `upload_file()` and returns `file_id`

---

## Phase 2: Per-Tool-Call Media Processing in Agent Loop

Replace the `pending_media` accumulation pattern with per-tool-call processing.

### Step 2.1: Add `_process_tool_media()` helper

**Prompt:**
In `src/decafclaw/agent.py`, add a new async function:

```python
async def _process_tool_media(ctx, result: ToolResult) -> None:
```

This function:
1. If `result.media` is empty, return immediately
2. If `ctx.media_handler` is None, log a warning ("No media handler — {len(result.media)} media item(s) not delivered") and return
3. Get `conv_id` from `ctx.conv_id or ctx.channel_id or "unknown"`
4. For each item in `result.media`:
   a. Call `await ctx.media_handler.save_media(conv_id, item["filename"], item["data"], item["content_type"])`
   b. If result has `workspace_ref`:
      - Find the placeholder text in `result.text` matching the item's filename (pattern: `[file attached: {filename}` — match up to the closing `]`)
      - If content_type starts with `image/`: replace with `![{filename}]({workspace_ref})`
      - Otherwise: replace with `[{filename}]({workspace_ref})`
   c. If result has `file_id`: store on a list for the caller (we'll wire this to Mattermost's tool post attachment later)
   d. On exception: log warning, leave placeholder unchanged (fail-open)
5. Clear `result.media` after processing

Add tests:
- Workspace ref replacement for image content type
- Workspace ref replacement for non-image content type
- Missing media handler logs warning, leaves text unchanged
- Failed save logs warning, leaves placeholder unchanged
- Multiple media items in one result all get processed

### Step 2.2: Wire `_process_tool_media()` into `_execute_single_tool`

**Prompt:**
In `src/decafclaw/agent.py`, modify `_execute_single_tool()` (~line 293).

After `result = await execute_tool(call_ctx, fn_name, fn_args)` (line 313) and before the `finally` block publishes `tool_end`, add:

```python
await _process_tool_media(call_ctx, result)
```

This processes media before the tool_end event fires, so by the time Mattermost's subscriber sees `tool_end`, the media has already been handled.

Update the return value: `_execute_single_tool` currently returns `(tool_msg, result.media or [])`. Since media is now processed in-place and cleared, this will return an empty list. That's fine — we'll clean up `pending_media` in the next step.

Update existing tests to verify:
- Tool results with media get processed before tool_end event
- The tool message `content` field reflects the updated text (workspace refs, not placeholders)

### Step 2.3: Remove `pending_media` accumulation

**Prompt:**
In `src/decafclaw/agent.py`, remove the `pending_media` pattern:

1. In `run_agent_turn()` (~line 488): remove `pending_media = []`
2. In `_execute_tool_calls()` signature and body: remove `pending_media` parameter. Remove the `pending_media.extend(media)` line (~line 408). The `_execute_single_tool` return value for media can be simplified — it no longer needs to return media since it's processed in-place.
3. At end of turn (~line 747-754): make `extract_workspace_media()` **conditional**. Add a `strips_workspace_refs` property to `MediaHandler` (default `True`). `WebMediaHandler` and `TerminalMediaHandler` set it to `False` — their workspace:// refs render in-place (web via frontend, terminal as visible paths). `MattermostMediaHandler` keeps `True` — it needs extraction + upload. Only call `extract_workspace_media()` when `ctx.media_handler` is None or `ctx.media_handler.strips_workspace_refs` is True.
4. Update all call sites of `_execute_tool_calls()` to not pass `pending_media`.

**Why conditional**: `extract_workspace_media` strips `![](workspace://...)` refs from the agent's final text. Per-tool-call processing injects those refs into tool results; the LLM may repeat them in its response. If we strip them for web UI, the frontend can't render them and the media has nowhere to go. Mattermost still needs extraction to upload the referenced files.

Update tests to verify:
- `pending_media` is no longer accumulated
- `extract_workspace_media()` only runs when handler has `strips_workspace_refs=True`
- Web/terminal handlers preserve workspace:// refs in final text

---

## Phase 3: Wire Up Media Handlers on All Channels

### Step 3.1: Set `WebMediaHandler` on web UI context

**Prompt:**
In `src/decafclaw/web/websocket.py`, in `_run_agent_turn()` (~line 459), after creating the context:

```python
from ..media import WebMediaHandler
ctx.media_handler = WebMediaHandler(config)
```

This is the key fix for #141 — the web UI now has a media handler that saves to conversation uploads.

Add an integration-style test:
- Mock a tool returning media in a web context
- Verify media is saved to conversation uploads dir
- Verify tool result text contains workspace:// refs

### Step 3.2: Update `TerminalMediaHandler` construction

**Prompt:**
In `src/decafclaw/agent.py` (~line 792), the terminal handler is constructed as `TerminalMediaHandler(config.workspace_path)`. Update to `TerminalMediaHandler(config)` (per Step 1.2).

Also remove `process_media_for_terminal()` from `media.py` — it's the old path that saved to `workspace/media/`. Check all call sites:
- `agent.py` ~line 925 uses it for terminal display. Replace with just using `result.text` directly since media is now processed per-tool-call (text already has workspace refs).

Update tests.

### Step 3.3: Update `MattermostMediaHandler` construction

**Prompt:**
In `src/decafclaw/mattermost.py` (~line 332), the handler is constructed as `MattermostMediaHandler(self._http)`. Update to `MattermostMediaHandler(self._http, channel_id)` where `channel_id` is the conversation's channel.

Also in `mattermost.py` `on_tool_end()` (~line 1062-1076): this block currently creates a *new* `MattermostMediaHandler` and calls `upload_and_collect()` to handle tool media. With per-tool-call processing, `result.media` is already empty by the time `tool_end` fires. Remove this media-handling block from `on_tool_end()` — it's no longer needed.

The `tool_end` event still carries a `media` field but it will be empty. The file_ids from `save_media()` need to be attached to the tool post. Two options:
- Pass file_ids via the `tool_end` event (add a `file_ids` field)
- Or have the `_process_tool_media` step publish its own event

The simpler approach: in `_process_tool_media`, when `save_media()` returns `file_id`s, publish a `tool_media_uploaded` event with the file_ids and tool_call_id. The Mattermost subscriber handles attaching them to the tool post.

Add tests:
- `tool_end` no longer triggers media upload
- `tool_media_uploaded` event carries file_ids
- Mattermost subscriber attaches file_ids to tool post

---

## Phase 4: Frontend — `workspace://` Link Rewriting

### Step 4.1: Add `renderer.link` override in `assistant-message.js`

**Prompt:**
In `src/decafclaw/web/static/components/messages/assistant-message.js`, add a link renderer override alongside the existing image renderer (~line 8):

```javascript
const originalLink = renderer.link.bind(renderer);
renderer.link = function(token) {
  let href = token.href || '';
  if (href.startsWith('workspace://')) {
    href = '/api/workspace/' + href.slice('workspace://'.length);
    token = { ...token, href };
  }
  return originalLink(token);
};
```

Also update the existing `renderer.image` to specifically check for `workspace://` prefix (currently it rewrites all non-http, non-absolute paths — tighten to `workspace://` for consistency):

```javascript
renderer.image = function(token) {
  let href = token.href || '';
  if (href.startsWith('workspace://')) {
    href = '/api/workspace/' + href.slice('workspace://'.length);
    token = { ...token, href };
  }
  return originalImage(token);
};
```

Test manually:
- Agent response with `![img](workspace://conversations/abc/uploads/test.png)` renders as image with `/api/workspace/conversations/abc/uploads/test.png` src
- Agent response with `[file.pdf](workspace://conversations/abc/uploads/file.pdf)` renders as download link with correct href

Run `make check-js` to verify.

---

## Phase 5: Cleanup and Polish

### Step 5.1: Remove `workspace/media/` references

**Prompt:**
Search the codebase for remaining references to `workspace/media/`:
- Remove `process_media_for_terminal()` if not already removed in Step 3.2
- Remove `TerminalMediaHandler.upload_file()` — `save_media()` replaces it. Keep the method as a no-op or raise `NotImplementedError` if the base class requires it.
- Clean up any tests that reference `workspace/media/`

The `upload_and_collect()` utility in `media.py` — check if it's still used anywhere after the Mattermost `on_tool_end` cleanup. If not, remove it.

### Step 5.2: Lint, test, documentation

**Prompt:**
- Run `make check` (lint + typecheck + JS check) and fix any issues
- Run `make test` and fix any failures
- Update `CLAUDE.md`:
  - Key files: note `WebMediaHandler` in `media.py`
  - Conventions: note unified media storage in conversation uploads
  - Remove any references to `workspace/media/`
- Update `docs/` if there's a media-related doc page
- Update `README.md` if media handling is mentioned

---

## Review: Critical Gaps

1. **Placeholder regex matching**: The `_process_tool_media()` function needs to match `[file attached: {filename} ...]` in `result.text`. The exact pattern from `mcp_client.py` is `[file attached: {filename} ({mime_type}) — will appear as an attachment on your reply]`. Use a regex like `\[file attached: {re.escape(filename)}[^\]]*\]` to match flexibly.

2. **Mattermost file_id attachment to tool post**: Step 3.3 proposes a `tool_media_uploaded` event. This requires the Mattermost subscriber to PATCH the tool post with file_ids. Mattermost's PATCH API quirk: must include `message` when patching `props`/`file_ids`. Fetch existing message first.

3. **`extract_workspace_media` must be conditional**: At end-of-turn, this function strips `![](workspace://...)` from the agent's text. For web UI this is **harmful** — it removes refs the frontend would render, and the extracted media has nowhere to go. Fixed in Step 2.3: add `strips_workspace_refs` flag to `MediaHandler`, only run extraction when True (Mattermost). Web and Terminal set False.

4. **Terminal mode `conv_id`**: Terminal uses `conv_id="interactive"`. The `save_attachment()` call will create `workspace/conversations/interactive/uploads/`. This is fine and consistent.

5. **`WebMediaHandler` for non-web-turn contexts**: Heartbeat turns, scheduled tasks, and child agents may not have a conv_id. The `_process_tool_media()` check for `ctx.media_handler is None` handles this — those contexts won't have a handler set, so media falls through with a warning. This is acceptable.
