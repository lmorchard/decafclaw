# File Attachments & Rich Media — Implementation Plan

## Overview

8 phases, each ending with lint + test + commit. The trickiest part is Phase 4 (changing `execute_tool` return type and updating the agent loop) — everything else builds on top.

---

## Phase 1: ToolResult Dataclass and MediaHandler Interface

**Goal:** Define the core types. No behavior changes yet — just the data structures.

**Prompt:**

Create `src/decafclaw/media.py` with `ToolResult`, `MediaHandler` base class, and workspace image reference scanning.

Requirements:
1. Define `ToolResult` dataclass:
   - `text: str`
   - `media: list[dict] = field(default_factory=list)`
   - Helper: `ToolResult.from_text(text)` class method for easy migration

2. Define `MediaHandler` base class (abstract-ish, not ABC — keep it simple):
   - `async def upload_file(self, channel_id, filename, data, content_type) -> str`
   - `async def send_with_media(self, channel_id, message, media_refs, root_id=None) -> str`
   - `def format_image_url(self, url) -> str`
   - `def format_attachment_card(self, title, text, image_url=None, thumb_url=None) -> dict`
   - Default implementations raise `NotImplementedError`

3. Write `extract_workspace_media(text, workspace_path) -> tuple[str, list[dict]]`:
   - Scans text for `![...](workspace://...)` patterns using regex
   - For each match: reads the file, adds to media list, strips from text
   - Returns `(cleaned_text, media_items)`
   - Leaves public URL images (`![](https://...)`) untouched
   - Uses `mimetypes.guess_type` for content type

4. Create `tests/test_media.py` with tests:
   - `ToolResult` creation, `from_text` helper
   - `extract_workspace_media`: finds workspace refs, strips them, leaves public URLs, handles missing files

Lint and test after.

---

## Phase 2: TerminalMediaHandler

**Goal:** Interactive mode implementation — saves files to workspace, returns paths.

**Prompt:**

Add `TerminalMediaHandler` to `src/decafclaw/media.py`.

Requirements:
1. `TerminalMediaHandler(workspace_path: Path)`:
   - `upload_file`: saves to `workspace_path/media/{filename}`, returns the path string
   - `send_with_media`: not applicable in terminal, returns empty string
   - `format_image_url`: returns `![image]({url})`
   - `format_attachment_card`: returns a text representation

2. `process_media_for_terminal(result: ToolResult, workspace_path: Path) -> str`:
   - Helper that takes a ToolResult and returns text with media paths appended
   - For each file media item: saves to `workspace_path/media/`, appends `[file saved: media/{filename}]`
   - For URL media items: appends `[image: {url}]`

3. Tests:
   - `TerminalMediaHandler.upload_file` creates file on disk
   - `process_media_for_terminal` saves files and appends paths

Lint and test after.

---

## Phase 3: MattermostMediaHandler

**Goal:** Mattermost implementation — upload via API, send with file_ids, attachment cards.

**Prompt:**

Add `MattermostMediaHandler` to `src/decafclaw/media.py`.

Requirements:
1. `MattermostMediaHandler(http_client: httpx.AsyncClient)`:
   - `upload_file(channel_id, filename, data, content_type) -> str`:
     - `POST /api/v4/files?channel_id={id}` with multipart form data
     - Returns `file_infos[0].id`
   - `send_with_media(channel_id, message, media_refs, root_id=None) -> str`:
     - If ≤10 media refs: single post with `file_ids` and message
     - If >10: first post with message + first 10, subsequent posts as thread replies with remaining batches of 10
     - Returns the post ID of the first message
   - `format_image_url(url) -> str`: returns `![image]({url})`
   - `format_attachment_card(title, text, image_url, thumb_url) -> dict`:
     - Returns the `props.attachments` structure for a Slack-style card

2. `async def upload_and_collect(handler, channel_id, media_items) -> list[str]`:
   - Standalone helper: uploads each file media item, returns list of file_ids
   - URL media items are skipped (handled differently)

3. Tests (mocked HTTP):
   - `upload_file` sends correct multipart request, returns file_id
   - `send_with_media` with ≤10 files: single post
   - `send_with_media` with >10 files: overflow into additional posts
   - `format_attachment_card` returns correct structure

Lint and test after.

---

## Phase 4: Update execute_tool and Agent Loop for ToolResult

**Goal:** The big contract change. `execute_tool` returns `ToolResult`. Agent loop accumulates media and passes it to the posting layer.

**Prompt:**

Update `execute_tool` to return `ToolResult` and modify the agent loop to handle media.

Requirements:
1. In `src/decafclaw/tools/__init__.py`, update `execute_tool`:
   - Import `ToolResult` from `media.py`
   - Wrap all return values in `ToolResult`:
     - Built-in tools: `ToolResult(text=result)` where result was the old string
     - MCP tools: already returning string, wrap same way (Phase 5 changes MCP to return real media)
     - Error strings: `ToolResult(text=error_string)`
   - Return type annotation changes to `ToolResult`

2. In `src/decafclaw/agent.py`, update `run_agent_turn`:
   - After `execute_tool` call, use `result.text` for the tool message content
   - Accumulate `result.media` items in a `pending_media: list[dict]` for the turn
   - Change `log.debug(f"Tool result: {result[:200]}...")` to use `result.text`
   - When returning the final response, also return accumulated media:
     - Change return type from `str` to `ToolResult`
     - Scan final response text for workspace refs via `extract_workspace_media`
     - Combine workspace media with accumulated tool media
     - Return `ToolResult(text=cleaned_text, media=all_media)`

3. Update all callers of `run_agent_turn`:
   - **Mattermost** (`mattermost.py`): `response` is now `ToolResult`. Use `response.text` for the edit/send. Handle `response.media` — upload and attach. For now, just use `response.text` (media wiring in Phase 6).
   - **Interactive** (`agent.py`): same — use `response.text` for printing. Media handling in Phase 6.
   - **Heartbeat** (`heartbeat.py`, `heartbeat_tools.py`): use `response.text`. Heartbeat media deferred.

4. Update existing tests that assert on `execute_tool` or `run_agent_turn` return values.

Lint and test after. This is the largest phase — take care with the migration.

---

## Phase 5: MCP Response Conversion to ToolResult with Media

**Goal:** MCP image/audio content becomes real media items in ToolResult.

**Prompt:**

Update `_convert_mcp_response` in `mcp_client.py` to return `ToolResult` with media.

Requirements:
1. Change `_convert_mcp_response(result) -> ToolResult`:
   - `text` items: concatenated as before
   - `image` items: decode base64 data, add to media list as `{"type": "file", "filename": "mcp-image-{i}.{ext}", "data": decoded_bytes, "content_type": mime_type}`. Get `mimeType` from the MCP item, fall back to `image/png`. Derive extension from mime type via `mimetypes`.
   - `audio` items: same pattern, fall back to `audio/wav`
   - For text description: include "Image attached." / "Audio attached." instead of byte-count placeholder
   - `isError`: wrap text in `[error: ...]`

2. Update the MCP tool caller wrapper (`_make_tool_caller`) to return `ToolResult` directly (it currently returns the string from `_convert_mcp_response`).

3. Update `execute_tool`'s MCP routing to handle `ToolResult` from MCP callers (don't double-wrap).

4. Tests:
   - `_convert_mcp_response` with image content returns ToolResult with media
   - `_convert_mcp_response` with mixed text + image returns both
   - `_convert_mcp_response` with audio returns correct content type
   - Existing MCP response tests updated

Lint and test after.

---

## Phase 6: Wire Media into Mattermost and Interactive Posting

**Goal:** Media actually gets uploaded and attached in Mattermost, saved in interactive mode.

**Prompt:**

Wire media handling into the posting layer for both Mattermost and interactive modes.

Requirements:
1. In `src/decafclaw/mattermost.py`:
   - Set `ctx.media_handler = MattermostMediaHandler(self._http)` when forking request contexts
   - Where the response is posted (edit placeholder or send):
     - If `response.media` is non-empty:
       - Upload all file media items via `upload_and_collect`
       - Use `send_with_media` instead of `edit_message`/`send`
     - If no media: existing behavior (edit/send text only)
   - Need `channel_id` available at posting time — it already is in the handler scope

2. In `src/decafclaw/agent.py` (`run_interactive`):
   - Set `ctx.media_handler = TerminalMediaHandler(config.workspace_path)`
   - When printing the response: use `process_media_for_terminal` to append file paths
   - Print the augmented text

3. Tests:
   - Mock MattermostMediaHandler: verify upload called for media items
   - Terminal: verify files saved to workspace

Lint and test after.

---

## Phase 7: file_share Tool and debug_context Upgrade

**Goal:** Agent can share workspace files. debug_context attaches files.

**Prompt:**

Add `file_share` tool and upgrade `debug_context` to use file attachments.

Requirements:
1. Create `file_share` tool in `src/decafclaw/tools/workspace_tools.py` (alongside existing workspace tools):
   - `async def tool_file_share(ctx, path: str, message: str = "") -> ToolResult`:
     - Resolve path safely (same sandbox as `workspace_read`)
     - Read file as bytes
     - Guess content type via `mimetypes.guess_type`
     - Return `ToolResult(text=message or f"Sharing {path}", media=[{"type": "file", "filename": basename, "data": data, "content_type": ct}])`
   - Tool definition with description about sharing workspace files as attachments
   - Register in `WORKSPACE_TOOLS` and `WORKSPACE_TOOL_DEFINITIONS`

2. Upgrade `debug_context` in `src/decafclaw/tools/core.py`:
   - Still writes files to workspace (backward compat for interactive mode)
   - Additionally, return media items for the JSON and system prompt files:
     - `ToolResult(text=summary, media=[json_file, prompt_file])`
   - In Mattermost, these get uploaded as attachments alongside the summary

3. Tests:
   - `file_share` reads workspace file, returns ToolResult with media
   - `file_share` rejects path escape
   - `debug_context` returns ToolResult with media items

Lint and test after.

---

## Phase 8: Integration, Documentation, and Cleanup

**Goal:** End-to-end verification, docs, backlog cleanup.

**Prompt:**

Final integration and documentation.

Requirements:
1. **Manual verification** (Mattermost):
   - Ask agent to use an MCP tool that returns an image (e.g., tarot-mcp if it generates images, or find a test)
   - Ask agent to `file_share` a workspace file — verify it appears as attachment
   - Ask agent to `debug_context` — verify JSON and prompt files attached
   - Test with >10 files (may need to fabricate) — verify overflow splitting
   - Test `![alt](workspace://file.png)` in agent response (may need prompting)

2. **Manual verification** (interactive):
   - Same tools — verify files saved to workspace with paths printed

3. **Documentation**:
   - Create `docs/file-attachments.md` — media handler, ToolResult, file_share, MCP media, workspace refs
   - Update `docs/index.md`
   - Update `CLAUDE.md` key files
   - Update `docs/backlog/mattermost.md` — remove file attachments item

4. Run full test suite, lint. Commit.

---

## Summary of Phases

| Phase | What | Key Files | Tests |
|-------|------|-----------|-------|
| 1 | ToolResult + MediaHandler + workspace ref scanner | `media.py` | ~6 tests |
| 2 | TerminalMediaHandler | `media.py` | ~3 tests |
| 3 | MattermostMediaHandler | `media.py` | ~4 tests |
| 4 | execute_tool + agent loop migration | `tools/__init__.py`, `agent.py`, `mattermost.py` | update existing |
| 5 | MCP response → ToolResult with media | `mcp_client.py` | ~4 tests |
| 6 | Wire media into posting layer | `mattermost.py`, `agent.py` | ~2 tests |
| 7 | file_share tool + debug_context upgrade | `workspace_tools.py`, `core.py` | ~3 tests |
| 8 | Integration + docs | docs | manual |

## Implementation Notes

- **Phase 4 is the riskiest** — it changes the return type of `execute_tool` and `run_agent_turn`, touching many files. Take care to update all callers and tests.
- **`ctx.media_handler`** is set by the mode (Mattermost or interactive) before calling `run_agent_turn`. The handler is runtime state, not config.
- **MCP tools return `ToolResult` directly** from Phase 5 onward. The MCP routing in `execute_tool` needs to not double-wrap.
- **Mattermost placeholder editing with media** is tricky — you can't edit a post to add file_ids. If there's media, we need to delete the placeholder and create a new post with file_ids. Or: send a new post with media and delete the placeholder.
- **Overflow handling** in `send_with_media` should use the same thread (root_id) for continuation posts.
